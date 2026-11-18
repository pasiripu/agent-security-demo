"""Export actual run evidence only; fail closed if the configured key appears."""
import hashlib
import json
from pathlib import Path
import shutil
from agent.common import ROOT, load_config, write_json
from agent.credentials import api_key
from agent.evidence import analyze
from scripts.report import report


def main():
    cfg=load_config(ROOT/'local/config.json')
    target=ROOT/'docs/validation'
    target.mkdir(exist_ok=True)
    key=api_key().encode()
    count=0
    for source in (Path(cfg['data_dir'])/'runs').glob('*/run.json'):
        meta=json.loads(source.read_text())
        if meta['status'] not in ('completed','error'):
            continue
        destination=target/'runs'/source.parent.name
        destination.mkdir(parents=True,exist_ok=True)
        for name in ('run.json','trace.jsonl','http.jsonl','endpoints.jsonl','stderr.txt','review.json'):
            path=source.parent/name
            if path.is_file():
                content=path.read_bytes()
                if key and key in content:
                    raise RuntimeError('Credential detected in evidence; export aborted')
                (destination/name).write_bytes(content)
        write_json(destination/'outcome.json',analyze(source.parent,meta))
        count+=1
    write_json(target/'report.json',report(cfg))
    check_path=target/'checks.json'
    if check_path.exists():
        checks=json.loads(check_path.read_text())
        checks['credential_export_scan']='passed; key files excluded'
        write_json(check_path,checks)
    manifest={str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file() and p.name!='sha256.json'}
    write_json(target/'sha256.json',manifest)
    print(f'Exported {count} authentic run records; credential check passed. Key files excluded.')


if __name__=='__main__':
    main()
