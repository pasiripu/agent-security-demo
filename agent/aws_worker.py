"""Unprivileged entry point for a run pre-registered by the trusted AWS controller."""
import argparse
import json
from .common import load_config, run_dir
from .run import execute


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',required=True)
    p.add_argument('--run-id',required=True)
    a=p.parse_args()
    cfg=load_config(a.config)
    folder=run_dir(cfg,a.run_id)
    meta=json.loads((folder/'run.json').read_text())
    if meta['status']!='prepared':
        raise SystemExit('Run is not fresh')
    execute(cfg,folder,meta)


if __name__=='__main__':
    main()
