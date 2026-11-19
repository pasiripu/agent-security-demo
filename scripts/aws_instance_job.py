"""Trusted SSM controller. No issue text is executed as code or CLI arguments."""
import argparse
from contextlib import closing
import datetime
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import sqlite3
import subprocess
import time
from agent.common import load_config, records, write_json
from agent.evidence import analyze
from agent.run import prepare
from agent.common import PUBLIC_VARIANTS
from agent.web_links import issue_links
from scripts.aws_cli import aws

CONFIG = '/etc/agent-lab/config.json'
PRIVATE = Path('/var/lib/agent-lab-private')
EVIDENCE = ('run.json','trace.jsonl','stderr.txt','http.jsonl','endpoints.jsonl','outcome.json')


def validate_issue(job, cfg, number):
    if job.get('repository') != cfg['github_repository'] or job.get('number') != number:
        raise ValueError('Issue repository or number mismatch')
    if job.get('url') != f"https://github.com/{cfg['github_repository']}/issues/{number}":
        raise ValueError('Issue URL mismatch')
    title, body = job.get('title'), job.get('body')
    if not isinstance(title,str) or not isinstance(body,str) or len(title)>500 or len(body)>16000:
        raise ValueError('Issue exceeds lab size limits')
    # Extract complete URLs before comparing; suffixes/queries cannot turn a prefix into an allowed URL.
    links = re.findall(r'https://[^\s<>"\x27)\]]+', body)
    if cfg.get('url_policy') == 'public-web':
        links = issue_links(body)
        controlled = {cfg['diagnostic_origin']+'/scenarios/'+v+'.txt': v for v in PUBLIC_VARIANTS}
        primary = links[0] if links else ''
        return controlled.get(primary, 'external'), {'number':number,'url':job['url'],
                'title':title,'body':body,'diagnostic_url':primary,'diagnostic_urls':links}
    allowed = {cfg['diagnostic_origin']+'/scenarios/'+v+'.txt':v
               for v in ('baseline','injection','control','autonomous')}
    matches = [allowed[u] for u in links if u in allowed]
    if len(matches)!=1:
        raise ValueError('Include exactly one controlled diagnostic URL from the issue template')
    return matches[0], {'number':number,'url':job['url'],'title':title,'body':body}


def reserve(db_path, number, limit):
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS attempts (issue INTEGER PRIMARY KEY, day TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM attempts WHERE issue=?',(number,)).fetchone():
            raise ValueError('This issue already consumed its single attempt; open a new issue')
        if db.execute('SELECT COUNT(*) FROM attempts WHERE day=?',(day,)).fetchone()[0]>=limit:
            raise ValueError('Daily run limit reached; try a new issue tomorrow UTC')
        db.execute('INSERT INTO attempts VALUES (?,?)',(number,day))


def decode_job(payload):
    import base64
    if len(payload)>32000: raise ValueError('Oversized issue envelope')
    raw=base64.b64decode(payload,validate=True)
    if len(raw)>24000: raise ValueError('Oversized issue envelope')
    job=json.loads(raw)
    number=job.get('number')
    if type(number) is not int or not 1<=number<=9999999999:
        raise ValueError('Invalid issue number')
    return job,number


def run(cfg, job, number):
    PRIVATE.mkdir(mode=0o700,exist_ok=True)
    with (PRIVATE/'active.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        variant,issue=validate_issue(job,cfg,number)
        reserve(PRIVATE/'attempts.sqlite',number,int(cfg['max_daily_runs']))
        folder,meta=prepare(cfg,variant,issue=issue,diagnostic_url=issue.get('diagnostic_url'))
        meta['runtime_source_commit'] = cfg.get('runtime_source_commit')
        meta.update(workflow_invocation=str(job.get('workflow_invocation',''))[:30],
                    source_commit=str(job.get('source_commit',''))[:64],active_until=int(time.time())+600)
        write_json(folder/'run.json',meta)
        user=pwd.getpwnam('agentlab')
        os.chown(folder,user.pw_uid,user.pw_gid)
        os.chown(folder/'run.json',user.pw_uid,user.pw_gid)
        secret=aws(cfg['aws_region'],'ssm','get-parameter',{'Name':cfg['api_key_parameter'],'WithDecryption':True})['Parameter']['Value'].strip()
        if not secret or len(secret)>4096: raise ValueError('Invalid API key parameter')
        env={'PATH':'/home/agentlab/.local/bin:/usr/local/bin:/usr/bin:/bin','HOME':'/home/agentlab',
             'PYTHONPATH':'/opt/agent-security-demo','ANTHROPIC_API_KEY':secret,'LANG':'en_US.UTF-8'}
        try:
            subprocess.run(['/usr/sbin/runuser','-u','agentlab','--','python3','-m','agent.aws_worker',
                '--config',CONFIG,'--run-id',meta['run_id']],cwd='/opt/agent-security-demo',env=env,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=int(cfg['timeout_seconds'])+60,check=True)
        except subprocess.SubprocessError:
            subprocess.run(['pkill','-KILL','-u',str(user.pw_uid)],capture_output=True)
            current=json.loads((folder/'run.json').read_text())
            current.update(status='error',execution_error='worker_failed_or_timed_out')
            write_json(folder/'run.json',current)
        meta=json.loads((folder/'run.json').read_text())
        meta['active_until']=0
        write_json(folder/'run.json',meta)
        outcome=analyze(folder,meta);write_json(folder/'outcome.json',outcome)
        final=next((e.get('result','') for e in reversed(records(folder/'trace.jsonl'))
                    if e.get('type')=='result' and not e.get('is_error')), '')
        report={'run_id':meta['run_id'],'outcome':outcome,'variant':variant,
                'summary':str(final)[:1800] or 'Investigation incomplete; inspect the private run evidence.',
                'label':'build-failure' if outcome['diagnostic_content_in_agent_trace'] else 'needs-information'}
        encoded=json.dumps(report,ensure_ascii=False)
        candidates=[folder/name for name in EVIDENCE if (folder/name).exists()]
        if secret in encoded or any(secret.encode() in f.read_bytes() for f in candidates):
            raise RuntimeError('Secret detected; report and export refused')
        del secret
        meta['secret_scan_passed']=True;write_json(folder/'run.json',meta)
        for path in folder.iterdir():
            if path.is_file(): os.chown(path,user.pw_uid,user.pw_gid)
        if len(encoded.encode())>20000: raise RuntimeError('Report too large for SSM output')
        # Only this bounded public summary enters SSM stdout; traces remain on the EBS disk.
        print(encoded)


def main():
    p=argparse.ArgumentParser();p.add_argument('issue_b64');a=p.parse_args()
    try:
        job,number=decode_job(a.issue_b64)
        run(load_config(CONFIG),job,number)
    except Exception as exc:
        print('Lab job failed: '+type(exc).__name__+'. Check the setup guide and private evidence.')
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
