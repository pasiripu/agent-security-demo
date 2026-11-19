"""Offline single-EC2 demo contracts; never claim live AWS acceptance from these."""
import base64
import http.client
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
import zipfile
from unittest.mock import MagicMock,patch
from agent.common import urls
from agent.http_tool import LabHTTP
from scripts.aws_instance_job import validate_issue,reserve,decode_job
from scripts.github_setup import public_files

RUN='demo-'+'a'*32
A='https://diagnostics.8-8-8-8.sslip.io'
B='https://receiver.8-8-8-8.sslip.io'


class AWSContracts(unittest.TestCase):
    def test_private_export_requires_scan_and_preserves_original_bytes(self):
        from dashboard.server import Handler
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'runs'/RUN;folder.mkdir(parents=True)
            meta={'run_id':RUN};(folder/'run.json').write_text(json.dumps(meta))
            original=b'{"event":"authentic fixture"}\n'
            (folder/'trace.jsonl').write_bytes(original)
            (folder/'unlisted.txt').write_text('not exported')
            (folder/'stderr.txt').symlink_to(folder/'unlisted.txt')
            request=Handler.__new__(Handler)
            request.headers={'Host':'127.0.0.1:8765'}
            request.path='/api/export/'+RUN
            request.server=types.SimpleNamespace(cfg={'data_dir':tmp})
            request.reply=MagicMock()
            request.do_GET();self.assertEqual(request.reply.call_args.args[0],403)
            meta['secret_scan_passed']=True;(folder/'run.json').write_text(json.dumps(meta))
            request.do_GET();status,data,mime=request.reply.call_args.args
            self.assertEqual((status,mime),(200,'application/zip'))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertEqual(set(archive.namelist()),{'run.json','trace.jsonl'})
                self.assertEqual(archive.read('trace.jsonl'),original)

    def test_issue_validation_preserves_untrusted_text_as_data(self):
        cfg={'github_repository':'pasiripu/agent-security-demo','diagnostic_origin':A}
        job={'repository':cfg['github_repository'],'number':42,'url':'https://github.com/'+cfg['github_repository']+'/issues/42',
             'title':'$(never execute this)','body':'Diagnostics: '+A+'/scenarios/injection.txt\n`touch /tmp/never`'}
        variant,issue=validate_issue(job,cfg,42)
        self.assertEqual(variant,'injection'); self.assertEqual(issue['title'],job['title'])
        for body in (job['body'].replace('.txt','.txt?x=1'),job['body'].replace(A,A+'.evil.test'),
                     job['body']+' '+A+'/scenarios/control.txt'):
            with self.assertRaises(ValueError): validate_issue({**job,'body':body},cfg,42)
        with self.assertRaises(ValueError): validate_issue({**job,'number':43},cfg,42)

    def test_one_attempt_per_issue_and_persistent_daily_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'quota.sqlite'; reserve(db,1,2)
            with self.assertRaises(ValueError): reserve(db,1,2)
            reserve(db,2,2)
            with self.assertRaises(ValueError): reserve(db,3,2)

    def test_public_source_excludes_secrets_state_and_original_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            bad=['local/anthropic-api-key','docs/validation/runs/demo/trace.jsonl',
                 'infrastructure/aws/.build/deployment.json','infrastructure/aws/.terraform/provider',
                 'infrastructure/aws/terraform.tfvars.json','infrastructure/aws/terraform.tfstate',
                 '.github/workflows/investigate.yml','agent/config.json']
            good=['README.md','agent/run.py','infrastructure/aws/versions.tf','.github/workflows/aws-triage.yml']
            for name in bad+good:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
            names={str(rel) for rel,_ in public_files(root)}
            self.assertEqual(names,set(good))

    def test_stable_issue_url_has_unique_per_run_followup(self):
        cfg={'diagnostic_origin':A,'receiver_origin':B,'diagnostic_path_template':'/scenarios/{variant}.txt'}
        one=urls(cfg,RUN,'injection');two=urls(cfg,'demo-'+'b'*32,'injection')
        self.assertEqual(one[0],two[0]);self.assertNotEqual(one[1],two[1])

    def test_content_length_eof_and_run_header_only_on_diagnostic(self):
        def response():
            raw=io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 3\r\n\r\nlog')
            raw._sock=MagicMock()
            sock=types.SimpleNamespace(makefile=lambda *args:io.BufferedReader(raw))
            result=http.client.HTTPResponse(sock);result.begin();return result
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'diagnostic_origin':A,'receiver_origin':B,'diagnostic_path_template':'/scenarios/{variant}.txt',
                 'send_run_header':True,'max_calls':6,'transport':'public-https','data_dir':tmp}
            with patch('agent.http_tool.public_addresses',return_value=[(2,1,6,'',('8.8.8.8',443))]), \
                 patch('agent.http_tool.PinnedHTTPS') as connection:
                connection.return_value.getresponse.side_effect=lambda:response()
                tool=LabHTTP(cfg,RUN,'injection')
                self.assertEqual(tool.fetch(tool.a)['body'],'log')
                a_headers=connection.return_value.request.call_args.kwargs['headers']
                self.assertEqual(a_headers['X-Lab-Run'],RUN)
                self.assertEqual(connection.return_value.request.call_count,1)
                self.assertEqual(tool.fetch(tool.b)['body'],'log')
                b_headers=connection.return_value.request.call_args.kwargs['headers']
                self.assertNotIn('X-Lab-Run',b_headers)

    def test_ssm_payload_is_bounded_json_data(self):
        job={'number':42,'title':'$(never execute)'}
        payload=base64.b64encode(json.dumps(job).encode()).decode()
        self.assertEqual(decode_job(payload),(job,42))
        for bad in ("'; touch /tmp/never",'A'*32001,base64.b64encode(b'{"number":true}').decode()):
            with self.assertRaises((ValueError,UnicodeError)):decode_job(bad)


if __name__=='__main__': unittest.main()
