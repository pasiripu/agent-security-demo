"""Record a human evidence assessment, not automatic inferred intent."""
import argparse
import json
from agent.common import load_config, now, run_dir, write_json
from agent.evidence import analyze


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run_id")
    p.add_argument("--config", default="local/config.json")
    p.add_argument("--interpretation", choices=("data", "instructions", "unclear"), required=True)
    p.add_argument("--refusal", choices=("yes", "no", "unclear"), required=True)
    p.add_argument("--evidence", required=True, help="Cite trace tool-use IDs / explicit summary text; no hidden reasoning")
    a = p.parse_args()
    folder = run_dir(load_config(a.config), a.run_id)
    meta = json.loads((folder / "run.json").read_text())
    write_json(folder / "review.json", dict(time=now(), interpretation=a.interpretation, model_refusal=a.refusal, evidence=a.evidence))
    write_json(folder / "outcome.json", analyze(folder, meta))


if __name__ == "__main__":
    main()
