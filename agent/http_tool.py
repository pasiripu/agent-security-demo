"""HTTP/HTTPS web reader, preserving the historical exact-URL experiment."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from .common import append, digest, run_dir, urls

MAX_BYTES = 32768


PUBLIC_MAX_BYTES = 2 * 1024 * 1024
PUBLIC_TEXT_CHARS = 60000
MAX_REDIRECTS = 5


def normalize_public_url(url):
    if not isinstance(url, str):
        raise ValueError("Expected a web address")
    url = url.strip()
    if url.lower().startswith("www."):
        url = "https://" + url
    if (len(url) > 8192 or any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url):
        raise ValueError("Invalid web address")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError("Use an HTTP or HTTPS web address without embedded credentials")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("The demo's web connection uses standard HTTP/HTTPS ports")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = "[" + host + "]"
    return urlunsplit((parsed.scheme, host,
                      quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
                      quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~"), ""))


def validate_public_url(url):
    return urlsplit(normalize_public_url(url))


def public_addresses(host, port=443):
    found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
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


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, addresses):
        super().__init__(host, timeout=8)
        self.addresses = addresses

    def connect(self):
        family, socktype, proto, _, target = self.addresses[0]
        raw = socket.socket(family, socktype, proto)
        try:
            raw.settimeout(8)
            raw.connect(target)
            self.sock = raw
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
        self.diagnostic_urls = set(self.meta.get("issue", {}).get("diagnostic_urls", [self.a]))
        self.audit = run_dir(cfg, run_id) / "http.jsonl"
        self.calls = 0

    def consume(self):
        self.calls += 1
        if self.calls > self.cfg["max_calls"]:
            append(self.audit, "tool_blocked", reason="call_limit")
            raise ValueError("HTTP call limit reached")

    def fetch(self, url):
        self.consume()
        if self.cfg.get("url_policy") == "public-web":
            return self._fetch_public(url)
        return self._fetch_exact(url)

    def _fetch_exact(self, url):
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
                    "transport": self.cfg.get("transport", "public-https")}
        except Exception as exc:
            append(self.audit, "http_failed", url=url, destination=destination, error_type=type(exc).__name__)
            raise ValueError("HTTPS retrieval failed: " + type(exc).__name__) from exc
        finally:
            if conn:
                conn.close()


    def _fetch_public(self, url):
        try:
            requested = normalize_public_url(url)
        except ValueError:
            append(self.audit, "tool_blocked", reason="invalid_web_address")
            raise
        destination = ("diagnostic" if requested in self.diagnostic_urls else "receiver"
                       if urlsplit(requested).hostname == urlsplit(self.b).hostname else "external")
        current, redirects = requested, []
        deadline = time.monotonic() + 30
        try:
            for hop in range(MAX_REDIRECTS + 1):
                conn = None
                try:
                    parsed = urlsplit(current)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Web request timed out")
                    origin = parsed.scheme + "://" + parsed.netloc
                    unix = self.cfg.get("unix_sockets", {}).get(origin) if self.cfg.get("transport") == "local-unix-https" else None
                    # Revalidate and pin every redirect target, including same-host hops.
                    addresses = None if unix else (public_addresses(parsed.hostname) if parsed.scheme == "https"
                                                  else public_addresses(parsed.hostname, 80))
                    if parsed.scheme == "https":
                        context = ssl.create_default_context(cafile=self.cfg.get("ca_file"))
                        conn = PinnedHTTPS(parsed.hostname, context, addresses, unix)
                    else:
                        conn = PinnedHTTP(parsed.hostname, addresses)
                    append(self.audit, "http_attempt", url=requested, request_url=current,
                           destination=destination, transport=self.cfg.get("transport", "public-https"))
                    headers = {"User-Agent": "AgentSecurityLab/2", "Accept": "text/html,text/plain,application/json,*/*;q=0.5",
                               "Accept-Encoding": "gzip, deflate"}
                    if self.cfg.get("send_run_header") and origin in (self.cfg["diagnostic_origin"], self.cfg["receiver_origin"]):
                        headers["X-Lab-Run"] = self.run_id
                    target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                    conn.request("GET", target, headers=headers)
                    response = conn.getresponse()
                    if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                        if hop == MAX_REDIRECTS:
                            raise ValueError("Too many redirects")
                        target = normalize_public_url(urljoin(current, response.getheader("Location")))
                        redirects.append({"url": current, "status": response.status, "location": target})
                        append(self.audit, "http_redirect", url=requested, request_url=current,
                               destination=destination, status=response.status, location=target)
                        current = target
                        continue
                    encoding = response.getheader("Content-Encoding", "identity").strip().lower()
                    decoder = (zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else
                               zlib.decompressobj() if encoding == "deflate" else None)
                    if encoding not in ("identity", "", "gzip", "deflate"):
                        raise ValueError("Unsupported response compression: " + encoding)
                    body, truncated = bytearray(), False
                    while not response.isclosed():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Web request timed out")
                        response.fp.raw._sock.settimeout(min(remaining, 4))
                        block = response.read1(16384)
                        if not block:
                            break
                        room = PUBLIC_MAX_BYTES + 1 - len(body)
                        body.extend(decoder.decompress(block, room) if decoder else block[:room])
                        if len(body) > PUBLIC_MAX_BYTES:
                            truncated = True
                            break
                    raw = bytes(body[:PUBLIC_MAX_BYTES])
                    content_type = response.getheader("Content-Type", "")
                    charset = re.search(r"charset=[\"']?([a-zA-Z0-9._-]+)", content_type)
                    try:
                        text = raw.decode(charset.group(1) if charset else "utf-8", errors="replace")
                    except LookupError:
                        text = raw.decode("utf-8", errors="replace")
                    truncated = truncated or len(text) > PUBLIC_TEXT_CHARS
                    text = text[:PUBLIC_TEXT_CHARS]
                    payload = {"url": requested, "final_url": current, "status": response.status,
                               "body": text, "sha256": digest(text), "truncated": truncated,
                               "redirects": redirects, "content_type": content_type,
                               "transport": self.cfg.get("transport", "public-https")}
                    append(self.audit, "http_completed", **{k: v for k, v in payload.items() if k != "transport"},
                           destination=destination, bytes=len(raw), peer="local-unix" if unix else addresses[0][4][0])
                    return payload
                finally:
                    if conn:
                        conn.close()
        except Exception as exc:
            append(self.audit, "http_failed", url=requested, request_url=current,
                   destination=destination, error_type=type(exc).__name__, reason=str(exc)[:200])
            raise ValueError("Web retrieval failed: " + str(exc)[:200]) from exc
