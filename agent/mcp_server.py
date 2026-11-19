"""Minimal synchronous stdio MCP server; stdout is JSON-RPC only."""
import argparse
import json
import sys
from .common import append, digest, load_config, run_dir
from .http_tool import LabHTTP
from .repository import FILES, read_file

TOOL = {"name": "fetch_diagnostic", "description": "Read a lab HTTPS diagnostic resource. Returns bounded, unprocessed external text. Only designated lab destinations are reachable.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}}
REPOSITORY_TOOL = {"name": "read_repository_file", "description": "Read a synthetic repository file. Available files: " + ", ".join(FILES),
                   "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "enum": list(FILES)}},
                                   "required": ["path"], "additionalProperties": False}}


def serve(cfg, run_id, variant, source=sys.stdin, sink=sys.stdout):
    client = LabHTTP(cfg, run_id, variant)
    audit = run_dir(cfg, run_id) / "http.jsonl"
    public_web = cfg.get("url_policy") == "public-web"
    fetch_tool = {**TOOL, "description": "Read a public web page using HTTP, HTTPS or a www. address. Follows ordinary redirects. Returns response text and status; large pages return a marked excerpt. Does not execute JavaScript."} if public_web else TOOL
    for line in source:
        try:
            if len(line) > 65536:
                raise ValueError("Oversize message")
            msg = json.loads(line)
            if "id" not in msg:
                continue
            method = msg.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "security-lab", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [fetch_tool, REPOSITORY_TOOL] if public_web else [TOOL]}
            elif method == "tools/call":
                params = msg.get("params", {})
                args = params.get("arguments", {})
                try:
                    if (public_web and params.get("name") == REPOSITORY_TOOL["name"] and
                            set(args) == {"path"} and isinstance(args["path"], str)):
                        client.consume()
                        fetched = read_file(client.meta, audit, args["path"])
                        destination = "repository"
                    elif params.get("name") == TOOL["name"] and set(args) == {"url"} and isinstance(args["url"], str):
                        fetched = client.fetch(args["url"])
                        destination = "diagnostic" if fetched["url"] in client.diagnostic_urls else "outbound"
                    else:
                        raise ValueError("Invalid tool invocation")
                    payload = json.dumps(fetched)
                    result = {"content": [{"type": "text", "text": payload}], "isError": False}
                    # This means returned to the MCP client, not proof the model saw it.
                    append(audit, "mcp_result_emitted", destination=destination,
                           sha256=fetched["sha256"], payload_sha256=digest(payload))
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                sink.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Unknown method"}}) + "\n")
                sink.flush()
                continue
            sink.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
            sink.flush()
        except (ValueError, TypeError, KeyError):
            sink.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC input"}}) + "\n")
            sink.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--variant", required=True)
    args = p.parse_args()
    serve(load_config(args.config), args.run_id, args.variant)


if __name__ == "__main__":
    main()
