"""Trusted SSM controller. No issue text is executed as code or CLI arguments."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import sqlite3
import subprocess
import tempfile
import time
from agent.common import load_config, records, write_json
from agent.evidence import analyze
from agent.run import prepare
from endpoints.diagnostics.content import render
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
    allowed = {cfg['diagnostic_origin']+'/scenarios/'+v+'.txt':v
               for v in ('baseline','injection','control','autonomous')}
    matches = [allowed[u] for u in links if u in allowed]
    if len(matches)!=1:
        raise ValueError('Include exactly one controlled diagnostic URL from the issue template')
    return matches[0], {'number':number,'url':job['url'],'title':title,'body':body}


def reserve(db_path, number, limit):
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS attempts (issue INTEGER PRIMARY KEY, day TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM attempts WHERE issue=?',(number,)).fetchone():
            raise ValueError('This issue already consumed its single attempt; open a new issue')
        if db.execute('SELECT COUNT(*) FROM attempts WHERE day=?',(day,)).fetchone()[0]>=limit:
            raise ValueError('Daily run limit reached; try a new issue tomorrow UTC')
        db.execute('INSERT INTO attempts VALUES (?,?)',(number,day))


def blob(cfg, operation, key, path):
    args=['aws','--region',cfg['aws_region'],'s3api',operation,'--bucket',cfg['bucket'],'--key',key]
    args += ['--body',str(path)] if operation=='put-object' else [str(path)]
    result=subprocess.run(args,capture_output=True,timeout=45)
    if result.returncode:
        raise RuntimeError('Private lab object transfer failed')


def value(v):
    if 'S' in v: return v['S']
    if 'N' in v: return int(v['N'])
    return None


def run(cfg, number):
    PRIVATE.mkdir(mode=0o700,exist_ok=True)
    with (PRIVATE/'active.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(dir=PRIVATE) as scratch:
            source=Path(scratch)/'issue.json'
            blob(cfg,'get-object',f'jobs/{number}.json',source)
            if source.stat().st_size>80000: raise ValueError('Oversized issue envelope')
            job=json.loads(source.read_text())
            variant, issue=validate_issue(job,cfg,number)
            reserve(PRIVATE/'attempts.sqlite',number,int(cfg['max_daily_runs']))
            folder,meta=prepare(cfg,variant,issue=issue)
            meta['workflow_invocation']=str(job.get('workflow_invocation',''))[:30]
            meta['source_commit']=str(job.get('source_commit',''))[:64]
            write_json(folder/'run.json',meta)
            body=render(variant,meta['receiver_url'])
            log=Path(scratch)/'log.txt'; log.write_text(body)
            log_key='logs/'+meta['run_id']+'.txt'
            blob(cfg,'put-object',log_key,log)
            item={'run_id':{'S':meta['run_id']},'event_id':{'S':'META'},'variant':{'S':variant},
                  'log_key':{'S':log_key},'sha256':{'S':hashlib.sha256(body.encode()).hexdigest()},
                  'active_until':{'N':str(int(time.time())+600)},
                  'expires_at':{'N':str(int(time.time())+86400*int(cfg['retention_days']))}}
            aws(cfg['aws_region'],'dynamodb','put-item',{'TableName':cfg['run_table'],'Item':item,
                'ConditionExpression':'attribute_not_exists(run_id)'})
            user=pwd.getpwnam('agentlab')
            os.chown(folder,user.pw_uid,user.pw_gid)
            os.chown(folder/'run.json',user.pw_uid,user.pw_gid)
            secret=aws(cfg['aws_region'],'ssm','get-parameter',{'Name':cfg['api_key_parameter'],'WithDecryption':True})['Parameter']['Value'].strip()
            if not secret or len(secret)>4096: raise ValueError('Invalid API key parameter')
            env={'PATH':'/home/agentlab/.local/bin:/usr/local/bin:/usr/bin:/bin','HOME':'/home/agentlab',
                 'PYTHONPATH':'/opt/agent-security-demo','ANTHROPIC_API_KEY':secret,'LANG':'en_US.UTF-8'}
            try:
                subprocess.run(['runuser','-u','agentlab','--','python3','-m','agent.aws_worker',
                    '--config',CONFIG,'--run-id',meta['run_id']],cwd='/opt/agent-security-demo',
                    env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    timeout=int(cfg['timeout_seconds'])+60,check=True)
            except subprocess.SubprocessError:
                # Kill a timed-out worker and all same-UID children before releasing the single-run lock.
                subprocess.run(['pkill','-KILL','-u',str(user.pw_uid)],capture_output=True)
                current=json.loads((folder/'run.json').read_text())
                current.update(status='error',execution_error='worker_failed_or_timed_out')
                write_json(folder/'run.json',current)
            # Close registration before posting a public run marker; late visits are not evidence.
            aws(cfg['aws_region'],'dynamodb','update-item',{'TableName':cfg['run_table'],
                'Key':{'run_id':{'S':meta['run_id']},'event_id':{'S':'META'}},
                'UpdateExpression':'SET active_until = :zero','ExpressionAttributeValues':{':zero':{'N':'0'}}})
            # Independent server receipts are joined only after the model process exits.
            rows=aws(cfg['aws_region'],'dynamodb','query',{'TableName':cfg['run_table'],
                'KeyConditionExpression':'run_id = :r','ExpressionAttributeValues':{':r':{'S':meta['run_id']}},
                'ConsistentRead':True})
            if rows.get('LastEvaluatedKey'): raise RuntimeError('Receipt volume exceeds lab limit')
            events=[{k:value(v) for k,v in row.items()} for row in rows.get('Items',[]) if row['event_id']['S'].startswith('EVENT#')]
            (folder/'endpoints.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            meta=json.loads((folder/'run.json').read_text())
            outcome=analyze(folder,meta); write_json(folder/'outcome.json',outcome)
            for path in folder.iterdir():
                if path.is_file(): os.chown(path,user.pw_uid,user.pw_gid)
            final=next((e.get('result','') for e in reversed(records(folder/'trace.jsonl'))
                        if e.get('type')=='result' and not e.get('is_error')), '')
            report={'run_id':meta['run_id'],'outcome':outcome,'variant':variant,
                    'summary':str(final)[:1800] or 'Investigation incomplete; inspect the private run evidence.',
                    'label':'build-failure' if outcome['diagnostic_content_in_agent_trace'] else 'needs-information',
                    'artifact_prefix':'evidence/'+meta['run_id']+'/'}
            report_file=Path(scratch)/'report.json'; write_json(report_file,report)
            candidates=[folder/name for name in EVIDENCE if (folder/name).exists()]+[report_file]
            if any(secret.encode() in f.read_bytes() for f in candidates):
                raise RuntimeError('Secret detected in output; evidence upload refused')
            del secret
            for path in candidates[:-1]: blob(cfg,'put-object',report['artifact_prefix']+path.name,path)
            blob(cfg,'put-object',f'reports/{number}.json',report_file)
            print(json.dumps({'issue':number,'run_id':meta['run_id'],'status':meta['status']}))


def main():
    p=argparse.ArgumentParser(); p.add_argument('issue_number',type=int); a=p.parse_args()
    if not 1<=a.issue_number<=9999999999: p.error('Invalid issue number')
    cfg=load_config(CONFIG)
    try: run(cfg,a.issue_number)
    except Exception as exc:
        # No issue body, API response, traceback, environment, or key enters SSM stdout.
        print('Lab job failed: '+type(exc).__name__+'. See the AWS setup troubleshooting guide.')
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
