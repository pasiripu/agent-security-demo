"""Bounded HTTPS reader: historical exact URLs or public-web demo mode."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit
from .common import append, digest, run_dir, urls

MAX_BYTES = 32768


def validate_public_url(url):
    if (not isinstance(url, str) or len(url) > 4096 or
            any(ord(c) < 33 or ord(c) > 126 for c in url) or "\\" in url):
        raise ValueError("Use an ASCII HTTPS URL of at most 4096 characters")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443)
            or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", parsed.hostname)):
        raise ValueError("Use public HTTPS on port 443, without credentials or a fragment")
    # DNS validation and pinning happen immediately before each connection.
    return parsed


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
        self.meta = {}
        meta_path = run_dir(cfg, run_id) / "run.json"
        if meta_path.exists():
            self.meta = json.loads(meta_path.read_text())
            if self.meta.get("variant") == variant:
                self.a = self.meta["diagnostic_url"]
        self.audit = run_dir(cfg, run_id) / "http.jsonl"
        self.calls = 0

    def consume(self):
        self.calls += 1
        if self.calls > self.cfg["max_calls"]:
            append(self.audit, "tool_blocked", reason="call_limit")
            raise ValueError("HTTP call limit reached")

    def fetch(self, url):
        self.consume()
        public_web = self.cfg.get("url_policy") == "public-web"
        if not public_web and url not in (self.a, self.b):
            # Do not persist arbitrary model-supplied URL data.
            append(self.audit, "tool_blocked", reason="url_not_in_exact_run_allowlist")
            raise ValueError("Only exact registered synthetic run URLs are allowed")
        if public_web:
            try:
                validate_public_url(url)
            except ValueError:
                append(self.audit, "tool_blocked", reason="invalid_public_https_url")
                raise
        destination = ("diagnostic" if url == self.a else "receiver"
                       if urlsplit(url).hostname == urlsplit(self.b).hostname else "external")
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
            if self.cfg.get("send_run_header") and (origin == self.cfg["diagnostic_origin"] or
                    (public_web and origin == self.cfg["receiver_origin"])):
                headers["X-Lab-Run"] = self.run_id  # Inert correlation only; never performs B.
            conn.request("GET", target, headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError("HTTP status " + str(response.status) + "; redirects are not followed")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("Compressed content is unsupported")
            mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            accepted = ("text/plain", "text/html", "application/json") if public_web else ("text/plain",)
            if mime not in accepted:
                raise ValueError("Expected UTF-8 plain text, HTML or JSON")
            deadline = time.monotonic() + 12
            body = bytearray()
            while True:
                # Newer Python closes fp as soon as Content-Length bytes are consumed.
                if response.isclosed():
                    break
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
                   content_type=mime,
                   peer="local-unix" if unix else addresses[0][4][0])
            return {"url": url, "status": 200, "body": text, "sha256": digest(text),
                    "source_trust": "external-untrusted", "transport": self.cfg.get("transport", "public-https")}
        except Exception as exc:
            append(self.audit, "http_failed", url=url, destination=destination, error_type=type(exc).__name__)
            raise ValueError("HTTPS retrieval failed: " + type(exc).__name__) from exc
        finally:
            if conn:
                conn.close()
