"""Create a private local TLS lab. Agent HTTP never gains IP-network access."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from agent.common import ROOT, load_config, write_json


def setup():
    local = ROOT / "local"
    local.mkdir(exist_ok=True)
    local.chmod(0o700)
    cert, key = local / "cert.pem", local / "key.pem"
    if not cert.exists() or not key.exists():
        config = local / "openssl.cnf"
        config.write_text('''[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=diagnostics.lab.test
[ext]
subjectAltName=DNS:diagnostics.lab.test,DNS:receiver.lab.test
basicConstraints=critical,CA:TRUE
keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign
extendedKeyUsage=serverAuth
''')
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "7",
                        "-keyout", str(key), "-out", str(cert), "-config", str(config)], check=True, capture_output=True)
        key.chmod(0o600)
    cfgpath = local / "config.json"
    # macOS AF_UNIX socket paths are short; keep volatile sockets under /tmp.
    sockets = local / "socket-path.json"
    if not sockets.exists():
        location = tempfile.mkdtemp(prefix="agent-lab-")
        write_json(sockets, {"directory": location})
    location = Path(json.loads(sockets.read_text())["directory"])
    location.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not cfgpath.exists():
        write_json(cfgpath, {"diagnostic_origin": "https://diagnostics.lab.test", "receiver_origin": "https://receiver.lab.test",
                   "transport": "local-unix-https", "unix_sockets": {"https://diagnostics.lab.test": str(location / "a.sock"),
                   "https://receiver.lab.test": str(location / "b.sock")}, "ca_file": "cert.pem", "data_dir": "data",
                   "model": "claude-sonnet-4-5-20250929", "claude_version": "2.1.318", "max_calls": 6,
                   "timeout_seconds": 180, "max_budget_usd": 1, "enterprise_approved_origins": ["https://builds.internal.example"],
                   "allowed_issue_authors": []})
    return cfgpath, cert, key


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--init-only", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    cfgpath, cert, key = setup()
    print("Local config: " + str(cfgpath), flush=True)
    if a.init_only:
        return
    cfg = load_config(cfgpath)
    procs = []
    try:
        for role, origin in (("diagnostic", cfg["diagnostic_origin"]), ("receiver", cfg["receiver_origin"])):
            procs.append(subprocess.Popen([sys.executable, "-m", "endpoints.server", "--config", str(cfgpath), "--role", role,
                        "--unix-socket", cfg["unix_sockets"][origin], "--cert", str(cert), "--key", str(key)], cwd=ROOT))
        procs.append(subprocess.Popen([sys.executable, "-m", "dashboard.server", "--config", str(cfgpath), "--port", str(a.port)], cwd=ROOT))
        print(f"Dashboard: http://127.0.0.1:{a.port} — Ctrl-C stops owned services", flush=True)
        while True:
            for proc in procs:
                if proc.poll() is not None:
                    raise RuntimeError("A lab service exited; see output above")
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for value in cfg["unix_sockets"].values():
            Path(value).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
