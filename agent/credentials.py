"""One explicit local model credential; never log its value."""
import os
import stat
from .common import ROOT

KEY_FILE = ROOT / "local" / "anthropic-api-key"


def api_key():
    value = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if value:
        return value
    try:
        descriptor = os.open(str(KEY_FILE), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return ""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Local API key file must be owned by this user with mode 0600")
        with os.fdopen(descriptor, "r") as handle:
            descriptor = None
            value = handle.read(8193).strip()
        if not value or len(value) > 8192 or any(c.isspace() for c in value):
            raise RuntimeError("Local API key file has an invalid format")
        return value
    finally:
        if descriptor is not None:
            os.close(descriptor)
