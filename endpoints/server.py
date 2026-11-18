"""Diagnostic and receiver services. Only pre-registered synthetic runs are logged."""
import argparse
import json
import re
import socketserver
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from agent.common import RUN_RE, append, digest, load_config, run_dir, urls
from endpoints.diagnostics.content import render


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass  # Never persist arbitrary request lines or headers.

    def reply(self, code, body):
        body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cfg, role = self.server.cfg, self.server.role
        parsed = urlsplit(self.path)
        match = re.fullmatch(r"/logs/(baseline|injection|control|autonomous)/(demo-[0-9a-f]{32})\.txt", parsed.path)
        receiver = re.fullmatch(r"/collect/(demo-[0-9a-f]{32})", parsed.path)
        run_id = match.group(2) if match else receiver.group(1) if receiver else None
        if not run_id or parsed.fragment:
            return self.reply(404, "Unregistered lab resource\n")
        folder = run_dir(cfg, run_id)
        try:
            meta = json.loads((folder / "run.json").read_text())
        except (FileNotFoundError, ValueError):
            return self.reply(404, "Unregistered lab resource\n")
        a, b = urls(cfg, run_id, meta["variant"])
        if match and role == "diagnostic" and self.path == urlsplit(a).path:
            body = render(meta["variant"], b)
            append(folder / "endpoints.jsonl", "diagnostic_served", run_id=run_id,
                   method="GET", path=parsed.path, sha256=digest(body), peer=self.peer())
            return self.reply(200, body)
        if receiver and role == "receiver" and self.path == urlsplit(b).path + "?" + urlsplit(b).query:
            append(folder / "endpoints.jsonl", "receiver_receipt", run_id=run_id,
                   method="GET", path=parsed.path, marker=run_id, peer=self.peer(),
                   transport=cfg.get("transport", "public-https"))
            return self.reply(200, "Synthetic marker recorded: " + run_id + "\n")
        self.reply(404, "Unregistered lab resource\n")

    def peer(self):
        return "local-unix" if isinstance(self.server, socketserver.UnixStreamServer) else self.client_address[0]

    def do_POST(self):
        self.reply(405, "Only GET is supported\n")


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def make_server(cfg, role, bind="127.0.0.1", port=0, unix_socket=None, cert=None, key=None):
    if unix_socket:
        path = Path(unix_socket)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse to replace existing sockets: avoid taking over another session.
        server = UnixHTTPServer(str(path), Handler)
        path.chmod(0o600)
    else:
        server = ThreadingHTTPServer((bind, port), Handler)
        server.daemon_threads = True
    server.cfg, server.role = cfg, role
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--role", choices=("diagnostic", "receiver"), required=True)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("--unix-socket")
    p.add_argument("--cert")
    p.add_argument("--key")
    a = p.parse_args()
    with make_server(load_config(a.config), a.role, a.bind, a.port, a.unix_socket, a.cert, a.key) as server:
        print("Serving " + a.role, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
