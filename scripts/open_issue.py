"""Register one trial locally, then open a synthetic issue in your controlled repo."""
import argparse
import os
import re
from agent.common import load_config, write_json
from agent.github_issue import github
from agent.run import prepare


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repo", help="Controlled owner/repository")
    p.add_argument("--config", default="local/config.json")
    p.add_argument("--variant", choices=("baseline", "injection", "control", "autonomous"), default="baseline")
    a = p.parse_args()
    cfg = load_config(a.config)
    folder, meta = prepare(cfg, a.variant)
    try:
        issue = github("POST", a.repo, "/issues", meta["issue"])
        # Workflow owns run metadata after the POST; don't race its status updates.
        write_json(folder / "github-created.json", {"url": issue["html_url"], "number": issue["number"]})
        print(issue["html_url"])
    except Exception:
        print("Issue creation failed; prepared run retained: " + meta["run_id"])
        raise


if __name__ == "__main__":
    main()
