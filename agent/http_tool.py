"""Exact-URL HTTPS reader. No redirects, proxies, arbitrary headers or shell."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit
from .common import append, digest, run_dir, urls

MAX_BYTES = 32768


def public_addresses(host):
    found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not found:
        raise ValueError("DNS returned no addresses")
    for _, _, _, _, address in found:
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                or ip.is_loopback or ip.is_link_local or ip.is_private
                or (getattr(ip, "ipv4_mapped", None) is not None)):
            raise ValueError("DNS contains a prohibited address")
    return found


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, context, addresses=None, unix_socket=None):
        super().__init__(host, timeout=8, context=context)
        self.addresses = addresses
        self.unix_socket = unix_socket

    def connect(self):
        if self.unix_socket:
            raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target = self.unix_socket
        else:
            family, socktype, proto, _, target = self.addresses[0]
            raw = socket.socket(family, socktype, proto)
        try:
            raw.settimeout(8)
            raw.connect(target)  # Pinned validated address; no second DNS lookup.
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class LabHTTP:
    def __init__(self, cfg, run_id, variant):
        self.cfg, self.run_id, self.variant = cfg, run_id, variant
        self.a, self.b = urls(cfg, run_id, variant)
        self.audit = run_dir(cfg, run_id) / "http.jsonl"
        self.calls = 0

    def fetch(self, url):
        self.calls += 1
        if self.calls > self.cfg["max_calls"]:
            append(self.audit, "tool_blocked", reason="call_limit")
            raise ValueError("HTTP call limit reached")
        if url not in (self.a, self.b):
            # Do not persist arbitrary model-supplied URL data.
            append(self.audit, "tool_blocked", reason="url_not_in_exact_run_allowlist")
            raise ValueError("Only exact registered synthetic run URLs are allowed")
        destination = "diagnostic" if url == self.a else "receiver"
        append(self.audit, "http_attempt", url=url, destination=destination,
               transport=self.cfg.get("transport", "public-https"))
        conn = None
        try:
            parsed = urlsplit(url)
            origin = "https://" + parsed.hostname
            context = ssl.create_default_context(cafile=self.cfg.get("ca_file"))
            unix = self.cfg.get("unix_sockets", {}).get(origin) if self.cfg.get("transport") == "local-unix-https" else None
            addresses = None if unix else public_addresses(parsed.hostname)
            conn = PinnedHTTPS(parsed.hostname, context, addresses, unix)
            target = parsed.path + ("?" + parsed.query if parsed.query else "")
            headers = {"User-Agent": "AgentSecurityLab/1", "Accept": "text/plain"}
            if self.cfg.get("send_run_header") and destination == "diagnostic":
                headers["X-Lab-Run"] = self.run_id  # Inert correlation only; never performs B.
            conn.request("GET", target, headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError("HTTP status " + str(response.status) + "; redirects are not followed")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("Compressed content is unsupported")
            if not response.getheader("Content-Type", "").startswith("text/plain"):
                raise ValueError("Expected plain text")
            deadline = time.monotonic() + 12
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Read deadline exceeded")
                # HTTPConnection may detach its socket on Connection: close.
                response.fp.raw._sock.settimeout(min(remaining, 4))
                block = response.read1(min(4096, MAX_BYTES + 1 - len(body)))
                if not block:
                    break
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise ValueError("Response exceeded bounded content size")
            text = bytes(body).decode("utf-8", errors="strict")
            append(self.audit, "http_completed", url=url, destination=destination,
                   status=200, bytes=len(body), sha256=digest(text), body=text,
                   peer="local-unix" if unix else addresses[0][4][0])
            return {"url": url, "status": 200, "body": text, "sha256": digest(text),
                    "source_trust": "external-untrusted", "transport": self.cfg.get("transport", "public-https")}
        except Exception as exc:
            append(self.audit, "http_failed", url=url, destination=destination, error_type=type(exc).__name__)
            raise ValueError("HTTPS retrieval failed: " + type(exc).__name__) from exc
        finally:
            if conn:
                conn.close()
