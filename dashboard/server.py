"""Read-only localhost dashboard, with explicit live-observation and replay modes."""
import argparse
import json
import io
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from agent.common import ROOT, RUN_RE, load_config, records, run_dir
from agent.evidence import analyze


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        host = self.headers.get("Host", "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return self.reply(403, b"Local dashboard only", "text/plain")
        path = urlsplit(self.path).path
        cfg = self.server.cfg
        if path == "/api/runs":
            rows = []
            for p in sorted((Path(cfg["data_dir"]) / "runs").glob("*/run.json"), reverse=True):
                meta = json.loads(p.read_text())
                rows.append({"meta": meta, "outcome": analyze(p.parent, meta)})
            return self.reply(200, json.dumps(rows).encode(), "application/json")
        if path.startswith("/api/export/") and RUN_RE.fullmatch(path.rsplit("/", 1)[1]):
            from scripts.aws_instance_job import EVIDENCE
            folder=run_dir(cfg,path.rsplit("/",1)[1])
            try: meta=json.loads((folder/'run.json').read_text())
            except (FileNotFoundError,ValueError):return self.reply(404,b'Unknown run','text/plain')
            if not meta.get('secret_scan_passed'):return self.reply(403,b'Run has not passed the controller secret scan','text/plain')
            files=[folder/n for n in EVIDENCE if (folder/n).is_file() and not (folder/n).is_symlink()]
            if sum(p.stat().st_size for p in files)>20*1024*1024:return self.reply(413,b'Archive exceeds demo size limit','text/plain')
            data=io.BytesIO()
            with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
                for file in files:archive.write(file,file.name)
            return self.reply(200,data.getvalue(),'application/zip')
        if path.startswith("/api/run/") and RUN_RE.fullmatch(path.rsplit("/", 1)[1]):
            folder = run_dir(cfg, path.rsplit("/", 1)[1])
            if not (folder / "run.json").exists():
                return self.reply(404, b"Unknown run", "text/plain")
            meta = json.loads((folder / "run.json").read_text())
            value = {"meta": meta, "outcome": analyze(folder, meta), "trace": records(folder / "trace.jsonl"),
                     "http": records(folder / "http.jsonl"), "endpoints": records(folder / "endpoints.jsonl")}
            return self.reply(200, json.dumps(value).encode(), "application/json")
        if path in ("/", "/app.js", "/style.css"):
            file = ROOT / "dashboard" / ({"/": "index.html"}.get(path, path[1:]))
            mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix]
            return self.reply(200, file.read_bytes(), mime)
        self.reply(404, b"Not found", "text/plain")

    def reply(self, status, data, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="local/config.json")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--evidence-dir", help="Read a curated archive directory containing runs/")
    a = p.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    server.cfg = {"data_dir": str(Path(a.evidence_dir).resolve())} if a.evidence_dir else load_config(a.config)
    suffix = "/?replay=1" if a.evidence_dir else ""
    print(f"Dashboard at http://127.0.0.1:{a.port}{suffix}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
