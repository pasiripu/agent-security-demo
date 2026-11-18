"""Shared configuration, run identity and append-only lab evidence."""
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import fcntl

ROOT = Path(__file__).resolve().parents[1]
RUN_RE = re.compile(r"demo-[0-9a-f]{32}\Z")
VARIANTS = {"baseline", "injection", "control", "autonomous"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temp, 0o600)
    temp.replace(path)


def append(audit_path, kind, **fields):
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(dict(time=now(), kind=kind, **fields)) + "\n")
        handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)


def records(path):
    try:
        lines = Path(path).read_text().splitlines()
    except FileNotFoundError:
        return []
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # A concurrent append may be incomplete until next dashboard refresh.
    return result


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    for key in ("diagnostic_origin", "receiver_origin"):
        url = urlsplit(cfg[key])
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.path or url.query or url.fragment or url.port not in (None, 443)):
            raise ValueError(key + " must be an HTTPS origin without path or credentials")
        if not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", url.hostname):
            raise ValueError("ASCII DNS hostname required")
        if cfg[key] != "https://" + url.hostname:
            raise ValueError("Use canonical HTTPS origins, without explicit port")
    if cfg["diagnostic_origin"] == cfg["receiver_origin"]:
        raise ValueError("Use two distinct origins")
    cfg["data_dir"] = str((path.parent / cfg.get("data_dir", "data")).resolve())
    if cfg.get("ca_file"):
        cfg["ca_file"] = str((path.parent / cfg["ca_file"]).resolve())
    mode = cfg.get("transport", "public-https")
    if mode not in ("public-https", "local-unix-https"):
        raise ValueError("Unknown transport")
    if mode == "local-unix-https":
        sockets = cfg["unix_sockets"]
        if set(sockets) != {cfg["diagnostic_origin"], cfg["receiver_origin"]}:
            raise ValueError("Local socket map must contain only the two configured origins")
        cfg["unix_sockets"] = {k: str((path.parent / v).resolve()) for k, v in sockets.items()}
        if not cfg.get("ca_file"):
            raise ValueError("Local TLS requires an explicit CA certificate")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", cfg["model"]) or cfg["model"] in ("sonnet", "opus", "haiku"):
        raise ValueError("Pin a full model ID, not an alias")
    if not re.fullmatch(r"\d+\.\d+\.\d+", cfg["claude_version"]):
        raise ValueError("Pin the Claude Code version")
    cfg["max_calls"] = int(cfg.get("max_calls", 6))
    cfg["timeout_seconds"] = int(cfg.get("timeout_seconds", 180))
    cfg["max_budget_usd"] = float(cfg.get("max_budget_usd", 1))
    if not (1 <= cfg["max_calls"] <= 10 and 1 <= cfg["timeout_seconds"] <= 600
            and 0 < cfg["max_budget_usd"] <= 10):
        raise ValueError("Lab limits exceeded")
    if cfg.get("diagnostic_path_template", "/logs/{variant}/{run_id}.txt") not in ("/logs/{variant}/{run_id}.txt", "/scenarios/{variant}.txt"):
        raise ValueError("Unsupported diagnostic path template")
    cfg["config_path"] = str(path)
    return cfg


def urls(cfg, run_id, variant):
    if not RUN_RE.fullmatch(run_id) or variant not in VARIANTS:
        raise ValueError("Invalid synthetic run identity")
    diagnostic_path = cfg.get("diagnostic_path_template", "/logs/{variant}/{run_id}.txt").format(variant=variant, run_id=run_id)
    return (cfg["diagnostic_origin"] + diagnostic_path,
            f'{cfg["receiver_origin"]}/collect/{run_id}?marker={run_id}')


def run_dir(cfg, run_id):
    if not RUN_RE.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    return Path(cfg["data_dir"]) / "runs" / run_id
