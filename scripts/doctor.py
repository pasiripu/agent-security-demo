"""Print capability presence only. Never print credentials."""
import json
import os
import platform
import shutil
import subprocess
from agent.credentials import KEY_FILE


def main():
    results = {"python": platform.python_version(), "api_key_in_environment": bool(os.environ.get("ANTHROPIC_API_KEY"))}
    results["local_key_file_present"] = KEY_FILE.exists()
    for tool in ("claude", "gh", "docker", "aws", "terraform", "openssl"):
        results[tool] = bool(shutil.which(tool))
    if results["claude"]:
        results["claude_version"] = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
