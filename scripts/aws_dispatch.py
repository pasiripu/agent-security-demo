"""GitHub-hosted dispatcher; the GitHub token never reaches EC2 or the model."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from agent.github_issue import github
from scripts.aws_cli import aws
from scripts.aws_instance_job import validate_issue


def transfer(region,bucket,operation,key,path):
    cmd=['aws','--region',region,'s3api',operation,'--bucket',bucket,'--key',key]
    cmd+=['--body',str(path)] if operation=='put-object' else [str(path)]
    subprocess.run(cmd,check=True,capture_output=True,timeout=45)


def main():
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    repo=os.environ['GITHUB_REPOSITORY']; region=os.environ['AWS_REGION']
    if event.get('action')!='opened' or event['repository']['full_name']!=repo: raise ValueError('Unexpected event')
    issue=event['issue']; number=issue['number']
    job={'repository':repo,'number':number,'title':issue['title'],'body':issue.get('body') or '',
         'url':issue['html_url'],'workflow_invocation':os.environ['GITHUB_RUN_ID'],
         'source_commit':os.environ['GITHUB_SHA']}
    validate_issue(job,{'github_repository':repo,'diagnostic_origin':os.environ['DIAGNOSTIC_ORIGIN']},number)
    with tempfile.TemporaryDirectory() as scratch:
        path=Path(scratch)/'issue.json'; path.write_text(json.dumps(job))
        transfer(region,os.environ['LAB_BUCKET'],'put-object',f'jobs/{number}.json',path)
        sent=aws(region,'ssm','send-command',{'DocumentName':os.environ['SSM_DOCUMENT'],
            'InstanceIds':[os.environ['LAB_INSTANCE_ID']],'Parameters':{'IssueNumber':[str(number)]},
            'TimeoutSeconds':120,'Comment':'Approved synthetic issue triage'})
        command=sent['Command']['CommandId']; status='Pending'; misses=0
        deadline=time.monotonic()+660
        while time.monotonic()<deadline:
            time.sleep(5)
            try:
                result=aws(region,'ssm','get-command-invocation',{'CommandId':command,'InstanceId':os.environ['LAB_INSTANCE_ID']})
                status=result['Status']; misses=0
            except RuntimeError:
                misses+=1
                if misses>6: raise RuntimeError('SSM invocation unavailable; verify instance is running and online')
                continue
            if status not in ('Pending','InProgress','Delayed','Cancelling'): break
        if status!='Success': raise RuntimeError('SSM job did not succeed: '+status+'; inspect its invocation in Systems Manager')
        transfer(region,os.environ['LAB_BUCKET'],'get-object',f'reports/{number}.json',path)
        report=json.loads(path.read_text())
    outcome=report['outcome']
    label=report['label']
    if label not in ('build-failure','needs-information'): raise ValueError('Unexpected fixed label')
    body=('### Synthetic lab investigation\n'+report['summary']+'\n\nRun: `'+report['run_id']+'`'
          '\n\nDiagnostic content delivered: **'+str(outcome['diagnostic_content_in_agent_trace'])+'**'
          '\nFollow-up selected: **'+str(outcome['followup_tool_selected'])+'**'
          '\nReceiver receipt: **'+str(outcome['receiver_receipt'])+'**'
          '\n\nInterpretation requires review of the private trace. A diagnostic fetch alone does not establish injection success.')
    github('POST',repo,f'/issues/{number}/labels',{'labels':[label]})
    github('POST',repo,f'/issues/{number}/comments',{'body':body})
    print(json.dumps({'issue':number,'run_id':report['run_id'],'claim':outcome['claim']}))


if __name__=='__main__': main()
