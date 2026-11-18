"""Run in your own Terminal. Input is hidden; the value is never printed."""
import getpass
import os
import tempfile
from agent.credentials import KEY_FILE


def main():
    value = getpass.getpass("Paste your Anthropic API key (hidden): ").strip()
    if not value or len(value) > 8192 or any(c.isspace() for c in value):
        raise SystemExit("No valid key entered; no file was changed")
    KEY_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    KEY_FILE.parent.chmod(0o700)
    descriptor, name = tempfile.mkstemp(prefix=".key-", dir=KEY_FILE.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(value + "\n")
        os.chmod(name, 0o600)
        os.replace(name, KEY_FILE)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print("API key saved privately. Reply 'ready' in Codex; do not share the key.")


if __name__ == "__main__":
    main()
