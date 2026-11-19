"""DETERMINISTIC network-path check. Never an autonomous agent decision."""
import argparse
import time
from agent.common import load_config, write_json
from agent.http_tool import LabHTTP
from agent.run import prepare
from agent.evidence import analyze


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="local/config.json")
    args = p.parse_args()
    cfg = load_config(args.config)
    folder, meta = prepare(cfg, "injection", run_kind="connectivity-test")
    if cfg.get('diagnostic_path_template') == '/scenarios/{variant}.txt':
        meta['active_until']=int(time.time())+120
        write_json(folder/'run.json',meta)
    try:
        tool = LabHTTP(cfg, meta["run_id"], meta["variant"])
        for url in (meta["diagnostic_url"], meta["receiver_url"]):
            tool.fetch(url)
        meta["status"] = "completed"
    except Exception as exc:
        meta.update(status="error", execution_error=str(exc))
        raise
    finally:
        meta['active_until']=0
        write_json(folder / "run.json", meta)
        write_json(folder / "outcome.json", analyze(folder, meta))
    print("DETERMINISTIC connectivity test only: " + meta["run_id"])


if __name__ == "__main__":
    main()
