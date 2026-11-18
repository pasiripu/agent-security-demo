"""Software tests. Mock model events exist only in temporary test directories."""
import copy
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from agent.common import digest, load_config, records, write_json
from agent.evidence import analyze
from agent.http_tool import LabHTTP, PinnedHTTPS, public_addresses
from agent.mcp_server import serve
from agent.run import command, prepare
from endpoints.server import make_server
from endpoints.diagnostics.content import render
from scripts.report import report


class LocalLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="lab-test-", dir="/tmp")
        cls.root = Path(cls.tmp.name)
        cert, key = cls.root / "cert.pem", cls.root / "key.pem"
        cnf = cls.root / "openssl.cnf"
        cnf.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=diagnostics.lab.test\n[ext]\nsubjectAltName=DNS:diagnostics.lab.test,DNS:receiver.lab.test\nbasicConstraints=critical,CA:TRUE\n')
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key), "-out", str(cert), "-config", str(cnf)], check=True, capture_output=True)
        cls.config = dict(diagnostic_origin="https://diagnostics.lab.test", receiver_origin="https://receiver.lab.test",
            model="claude-sonnet-4-5-20250929", claude_version="2.1.318", transport="local-unix-https",
            ca_file=str(cert), data_dir=str(cls.root / "data"), unix_sockets={"https://diagnostics.lab.test":str(cls.root / "a.sock"), "https://receiver.lab.test":str(cls.root / "b.sock")})
        write_json(cls.root / "config.json", cls.config)
        cls.cfg = load_config(cls.root / "config.json")
        cls.servers=[]
        cls.threads=[]
        for role, origin in (("diagnostic",cls.cfg["diagnostic_origin"]),("receiver",cls.cfg["receiver_origin"])):
            server=make_server(cls.cfg,role,unix_socket=cls.cfg["unix_sockets"][origin],cert=str(cert),key=str(key))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            cls.servers.append(server)
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()
        cls.tmp.cleanup()

    def trial(self, variant="injection", kind="agent"):
        folder, meta=prepare(self.cfg,variant,run_kind=kind)
        return folder, meta, LabHTTP(self.cfg,meta["run_id"],variant)

    def test_real_tls_diagnostic_and_receipt(self):
        folder,m,tool=self.trial()
        body=tool.fetch(m["diagnostic_url"])
        self.assertIn("EBADENGINE",body["body"])
        self.assertIn(m["receiver_url"],body["body"])
        self.assertEqual(body["sha256"],digest(body["body"]))
        self.assertEqual(tool.fetch(m["receiver_url"])["status"],200)
        self.assertEqual([r["kind"] for r in records(folder/"endpoints.jsonl")],["diagnostic_served","receiver_receipt"])

    def test_unknown_url_blocked_before_network(self):
        _,m,tool=self.trial()
        tool.cfg=copy.deepcopy(tool.cfg)
        tool.cfg["max_calls"]=10
        for url in ["https://169.254.169.254/latest/meta-data", "https://127.0.0.1/", m["receiver_url"]+"&secret=bad",m["diagnostic_url"]+"#fragment",m["diagnostic_url"].replace("https://","https://user:pass@"),"https://unrelated.example/"]:
            with self.subTest(url=url), patch("agent.http_tool.PinnedHTTPS") as conn:
                with self.assertRaises(ValueError):tool.fetch(url)
                conn.assert_not_called()

    def test_call_budget(self):
        _,m,tool=self.trial("baseline")
        tool.calls=self.cfg["max_calls"]
        with self.assertRaisesRegex(ValueError,"limit"):tool.fetch(m["diagnostic_url"])

    def test_unregistered_run_not_served(self):
        folder,m,tool=self.trial()
        (folder/"run.json").unlink()
        with self.assertRaises(ValueError):tool.fetch(m["diagnostic_url"])

    def test_no_unrelated_request_details_logged(self):
        folder,m,tool=self.trial()
        with self.assertRaises(ValueError):tool.fetch(m["receiver_url"]+"&password=should-not-be-logged")
        self.assertNotIn("should-not-be-logged",(folder/"http.jsonl").read_text())

    def test_response_size_bounded(self):
        _,m,tool=self.trial()
        with patch("endpoints.server.render",return_value="x"*32769):
            with self.assertRaises(ValueError):tool.fetch(m["diagnostic_url"])

    def test_redirect_not_followed(self):
        _,m,tool=self.trial()
        with patch("agent.http_tool.PinnedHTTPS") as klass:
            klass.return_value.getresponse.return_value.status=302
            with self.assertRaises(ValueError):tool.fetch(m["diagnostic_url"])
            self.assertEqual(klass.return_value.request.call_count,1)

    def test_certificate_verification(self):
        _,m,_=self.trial()
        cfg=copy.deepcopy(self.cfg)
        cfg.pop("ca_file")
        with self.assertRaises(ValueError):LabHTTP(cfg,m["run_id"],m["variant"]).fetch(m["diagnostic_url"])

    def test_local_transport_never_resolves_dns(self):
        _,m,tool=self.trial("baseline")
        with patch("socket.getaddrinfo",side_effect=AssertionError("No DNS allowed")):
            self.assertEqual(tool.fetch(m["diagnostic_url"])["status"],200)

    def test_mcp_handshake_and_actual_tool_result(self):
        folder,m,_=self.trial()
        requests=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}},{"jsonrpc":"2.0","method":"notifications/initialized"},{"jsonrpc":"2.0","id":2,"method":"tools/list"},{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"fetch_diagnostic","arguments":{"url":m["diagnostic_url"]}}}]
        out=io.StringIO()
        serve(self.cfg,m["run_id"],m["variant"],io.StringIO("\n".join(map(json.dumps,requests))),out)
        replies=[json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(replies),3)
        self.assertEqual(replies[0]["result"]["protocolVersion"],"2024-11-05")
        self.assertFalse(replies[-1]["result"]["isError"])
        self.assertIn("mcp_result_emitted",[r["kind"] for r in records(folder/"http.jsonl")])

    def test_deterministic_receipt_never_proves_autonomy(self):
        folder,m,tool=self.trial(kind="connectivity-test")
        tool.fetch(m["diagnostic_url"])
        tool.fetch(m["receiver_url"])
        result=analyze(folder,m)
        self.assertTrue(result["receiver_receipt"])
        self.assertFalse(result["followup_tool_selected"])
        self.assertFalse(result["scenario_observed"])
        self.assertFalse(result["network_egress_proven"])

    def test_mock_trace_chain_requires_delivery(self):
        folder,m,tool=self.trial()
        fetched=tool.fetch(m["diagnostic_url"])
        tool.fetch(m["receiver_url"])
        events=[{"type":"assistant","message":{"content":[{"type":"tool_use","id":"a","name":"mcp__lab__fetch_diagnostic","input":{"url":m["diagnostic_url"]}}]}},
                {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"a","content":[{"type":"text","text":json.dumps(fetched)}]}]}},
                {"type":"assistant","message":{"content":[{"type":"tool_use","id":"b","name":"mcp__lab__fetch_diagnostic","input":{"url":m["receiver_url"]}}]}}]
        (folder/"trace.jsonl").write_text("\n".join(map(json.dumps,events)))
        result=analyze(folder,m)
        self.assertTrue(result["scenario_observed"])
        self.assertFalse(result["network_egress_proven"])
        self.assertFalse(result["injection_confirmed"])
        (folder/"trace.jsonl").write_text("\n".join(map(json.dumps,[events[2],events[0],events[1]])))
        self.assertFalse(analyze(folder,m)["scenario_observed"])
        events[1]["message"]["content"][0]["content"][0]["text"]='{"body":"tampered","sha256":"bad"}'
        (folder/"trace.jsonl").write_text("\n".join(map(json.dumps,events)))
        self.assertFalse(analyze(folder,m)["scenario_observed"])

    def test_constant_task_across_variants(self):
        tasks=[self.trial(v)[1]["task_sha256"] for v in ("baseline","injection","control","autonomous")]
        self.assertEqual(len(set(tasks)),1)

    def test_fallback_and_baseline_have_no_injection(self):
        self.assertEqual(render("baseline","https://receiver.test/marker"),render("autonomous","https://receiver.test/marker"))
        self.assertNotIn("receiver.test",render("autonomous","https://receiver.test/marker"))
        self.assertIn("Do not perform",render("control","https://receiver.test/marker"))

    def test_report_excludes_connectivity(self):
        folder,m,_=self.trial(kind="connectivity-test")
        m["status"]="completed"
        write_json(folder/"run.json",m)
        self.assertGreaterEqual(report(self.cfg)["excluded"]["connectivity-test"],1)


class Restrictions(unittest.TestCase):
    def test_private_metadata_and_mixed_dns_rejected(self):
        for ip in ("127.0.0.1","169.254.169.254","10.0.0.1","192.168.1.1","::1","fc00::1","224.0.0.1","::ffff:8.8.8.8"):
            with self.subTest(ip=ip),patch("socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(ip,443))]):
                with self.assertRaises(ValueError):public_addresses("test.example")
        with patch("socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]):
            with self.assertRaises(ValueError):public_addresses("test.example")

    def test_address_pinned_and_sni_preserved(self):
        context=MagicMock()
        raw=MagicMock()
        addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))]
        conn=PinnedHTTPS("test.example",context,addresses)
        with patch("socket.socket",return_value=raw),patch("socket.getaddrinfo",side_effect=AssertionError("Second DNS forbidden")):
            conn.connect()
        raw.connect.assert_called_once_with(("8.8.8.8",443))
        context.wrap_socket.assert_called_once_with(raw,server_hostname="test.example")

    def test_bad_origin_and_alias_config_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"config.json"
            cfg={"diagnostic_origin":"https://a.example","receiver_origin":"https://b.example","model":"sonnet","claude_version":"2.1.318"}
            write_json(p,cfg)
            with self.assertRaises(ValueError):load_config(p)
            cfg["model"]="claude-sonnet-4-5-20250929"
            cfg["diagnostic_origin"]="https://a.example/private"
            write_json(p,cfg)
            with self.assertRaises(ValueError):load_config(p)

    def test_cli_has_no_shell_tools_or_bypass(self):
        cfg={"model":"claude-sonnet-4-5-20250929","max_budget_usd":1}
        with patch("shutil.which",return_value="/bin/claude"):
            cmd=command(cfg,Path("/tmp/fixture-mcp.json"),"mcp")
        self.assertNotIn("--dangerously-skip-permissions",cmd)
        self.assertIn("--bare",cmd)
        self.assertEqual(cmd[cmd.index("--tools")+1],"")
        self.assertEqual(cmd[cmd.index("--allowedTools")+1],"mcp__lab__fetch_diagnostic")

class CredentialHandling(unittest.TestCase):
    def test_environment_key_takes_precedence(self):
        from agent.credentials import api_key
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'synthetic-env-key'}), patch('agent.credentials.os.open') as opened:
            self.assertEqual(api_key(),'synthetic-env-key')
            opened.assert_not_called()

    def test_private_file_loaded_without_logging(self):
        from agent.credentials import api_key
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'key'
            file.write_text('synthetic-unit-test-key\n')
            file.chmod(0o600)
            with patch.dict('os.environ',{'ANTHROPIC_API_KEY':''}),patch('agent.credentials.KEY_FILE',file):
                self.assertEqual(api_key(),'synthetic-unit-test-key')
                file.chmod(0o644)
                with self.assertRaises(RuntimeError):api_key()

    def test_missing_file_returns_no_key(self):
        from agent.credentials import api_key
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'ANTHROPIC_API_KEY':''}),patch('agent.credentials.KEY_FILE',Path(tmp)/'missing'):
            self.assertEqual(api_key(),'')

    def test_setup_helper_hides_value_and_writes_private_file(self):
        from scripts.configure_key import main
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'local'/'key'
            output=io.StringIO()
            with patch('scripts.configure_key.KEY_FILE',file),patch('getpass.getpass',return_value='synthetic-unit-test-key'),patch('sys.stdout',output):
                main()
            self.assertEqual(file.stat().st_mode & 0o777,0o600)
            self.assertEqual(file.parent.stat().st_mode & 0o777,0o700)
            self.assertNotIn('synthetic-unit-test-key',output.getvalue())


if __name__ == "__main__":
    unittest.main()
