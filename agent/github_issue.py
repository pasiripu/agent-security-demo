"""Trusted GitHub wrapper. Issue data is never interpolated into shell code."""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from .common import RUN_RE, load_config, run_dir, write_json
from .run import execute


def github(method, repo, path, data=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid controlled repository")
    request = urllib.request.Request("https://api.github.com/repos/" + repo + path,
        data=None if data is None else json.dumps(data).encode(), method=method,
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main():
    cfg = load_config(os.environ.get("LAB_CONFIG", "config.json"))
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = event["issue"]
    repo = os.environ["GITHUB_REPOSITORY"]
    if event["repository"]["full_name"] != repo or event.get("action") != "opened":
        raise ValueError("Unexpected issue event")
    if issue["user"]["login"] not in cfg.get("allowed_issue_authors", []):
        raise ValueError("Issue author is outside the controlled lab allowlist")
    body = issue.get("body") or ""
    matches = re.findall(re.escape(cfg["diagnostic_origin"]) + r"/logs/(baseline|injection|control|autonomous)/(demo-[0-9a-f]{32})\.txt", body)
    if len(matches) != 1:
        raise ValueError("Issue must reference exactly one pre-registered synthetic diagnostic log")
    variant, run_id = matches[0]
    folder = run_dir(cfg, run_id)
    meta = json.loads((folder / "run.json").read_text())
    if meta["status"] != "prepared" or meta["variant"] != variant or meta["run_kind"] != "agent":
        raise ValueError("Only fresh registered lab runs may be executed")
    meta["issue"] = {"title": issue["title"][:500], "body": body[:16000], "url": issue["html_url"], "number": issue["number"]}
    meta["workflow_invocation"] = os.environ.get("GITHUB_RUN_ID")
    outcome = execute(cfg, folder, meta)
    # Fixed label vocabulary; do not let diagnostic content set arbitrary labels or API paths.
    label = "needs-information" if outcome["execution_error"] or not outcome["diagnostic_content_in_agent_trace"] else "build-failure"
    traces = [json.loads(line) for line in (folder / "trace.jsonl").read_text().splitlines() if line.startswith("{")] if (folder / "trace.jsonl").exists() else []
    final = next((e.get("result", "") for e in reversed(traces) if e.get("type") == "result" and not e.get("is_error")), "")
    summary = final[:1800] or "The lab investigation could not complete. See the runner evidence for the execution error."
    number = int(issue["number"])
    github("POST", repo, f"/issues/{number}/labels", {"labels": [label]})
    github("POST", repo, f"/issues/{number}/comments", {"body": "### Synthetic lab investigation\n" + summary + "\n\nRun: `" + run_id + "`\nTransport: `" + meta["transport"] + "`\nObserved claim: `" + outcome["claim"] + "`"})
    return 1 if outcome["execution_error"] else 0


if __name__ == "__main__":
    sys.exit(main())
