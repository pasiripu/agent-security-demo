"""Create/configure the authorized public repo; publish only an explicit sanitized source set."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from agent.common import ROOT

REPO='pasiripu/agent-security-demo'
CHECKOUT='fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09'
AWS_ACTION='61815dcd50bd041e203e49132bacad1fd04d2708'


def gh(path,method='GET',payload=None):
    cmd=['gh','api',path,'--method',method]
    if payload is not None: cmd+=['--input','-']
    result=subprocess.run(cmd,input=None if payload is None else json.dumps(payload),
                          text=True,capture_output=True,check=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}


def public_files(root=ROOT):
    roots={'.github','agent','endpoints','experiments','tests','dashboard','infrastructure','scripts','docs','demo-data'}
    top={'README.md','.gitignore','config.example.json','LICENSE'}
    suffixes={'.py','.md','.json','.yml','.yaml','.js','.html','.css','.txt','.tf','.tftpl','.hcl'}
    for path in sorted(root.rglob('*')):
        rel=path.relative_to(root)
        if not path.is_file() or path.is_symlink(): continue
        if not (rel.parts[0] in roots or rel.as_posix() in top): continue
        if any(p in {'.git','.terraform','.build','__pycache__','validation','local','data','aws-data'} for p in rel.parts): continue
        if rel.as_posix()=='.github/workflows/investigate.yml': continue # Legacy self-hosted workflow is private-lab only.
        if path.name not in top and path.name not in ('Dockerfile','Caddyfile','.terraform.lock.hcl') and path.suffix not in suffixes: continue
        if path.name in ('config.json','anthropic-api-key') or '.tfstate' in path.name or path.name.endswith(('.tfvars','.tfvars.json','.tfplan')): continue
        yield rel,path


def stage(destination):
    from agent.credentials import api_key
    secret=api_key()
    destination.mkdir(parents=True,exist_ok=False)
    for rel,source in public_files():
        data=source.read_bytes()
        if secret and secret.encode() in data: raise RuntimeError('Secret detected; public staging aborted')
        if str(Path.home()).encode() in data: raise RuntimeError('Private home path detected; public staging aborted')
        target=destination/rel; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
    return destination


def protect(repo,owner_id):
    env=gh(f'repos/{repo}/environments/demo','PUT',{
        'wait_timer':0,'prevent_self_review':False,'reviewers':[{'type':'User','id':int(owner_id)}],
        'deployment_branch_policy':{'protected_branches':False,'custom_branch_policies':True}})
    reviewers=env.get('protection_rules',[])
    if not any(r.get('type')=='required_reviewers' and any(x.get('reviewer',{}).get('id')==int(owner_id)
        for x in r.get('reviewers',[])) for r in reviewers):
        raise RuntimeError('Required reviewer was not confirmed; publishing is blocked')
    policies=gh(f'repos/{repo}/environments/demo/deployment-branch-policies').get('branch_policies',[])
    if not any(p['name']=='main' for p in policies):
        gh(f'repos/{repo}/environments/demo/deployment-branch-policies','POST',{'name':'main','type':'branch'})


def create():
    me=gh('user')
    if me['login']!='pasiripu': raise RuntimeError('Sign in as pasiripu before creating this repository')
    try: repo=gh('repos/'+REPO)
    except subprocess.CalledProcessError as exc:
        # Never treat a failed lookup as proof of availability except an explicit 404.
        if 'HTTP 404' not in exc.stderr: raise
        repo=gh('user/repos','POST',{'name':'agent-security-demo','private':False,'auto_init':False,
            'description':'Synthetic autonomous issue-triage and prompt-injection evidence lab',
            'has_issues':True,'has_wiki':False,'has_projects':False})
    if repo['private'] or repo['owner']['login']!='pasiripu': raise RuntimeError('Unexpected repository identity or visibility')
    protect(REPO,me['id'])
    for name,color in [('build-failure','d73a4a'),('needs-information','fbca04')]:
        try: gh('repos/'+REPO+'/labels','POST',{'name':name,'color':color,'description':'Synthetic lab triage result'})
        except subprocess.CalledProcessError as exc:
            if 'HTTP 422' not in exc.stderr: raise
    print(json.dumps({'repository':REPO,'owner_id':me['id'],'repository_id':repo['id'],
                      'approval_environment':'demo','url':repo['html_url']},indent=2))


def publish():
    repo=gh('repos/'+REPO); protect(REPO,repo['owner']['id'])
    # Reuse a local checkout for updates; never force-push or overwrite unrelated remote work.
    checkout=ROOT/'infrastructure/aws/.build/public-source'
    remote='https://github.com/'+REPO+'.git'
    if not checkout.exists():
        checkout.parent.mkdir(parents=True,exist_ok=True)
        heads=subprocess.run(['git','ls-remote','--heads',remote,'main'],check=True,capture_output=True,text=True)
        if heads.stdout.strip():
            subprocess.run(['git','clone','--single-branch','--branch','main',remote,str(checkout)],check=True)
    if checkout.exists():
        subprocess.run(['git','-C',str(checkout),'pull','--ff-only'],check=True)
        temp=Path(tempfile.mkdtemp(prefix='public-review-'))/'source'
        stage(temp)
        for item in list(checkout.iterdir()):
            if item.name=='.git': continue
            if item.is_dir(): shutil.rmtree(item)
            else: item.unlink()
        shutil.copytree(temp,checkout,dirs_exist_ok=True)
        shutil.rmtree(temp.parent)
    else:
        checkout.parent.mkdir(parents=True,exist_ok=True)
        stage(checkout)
        subprocess.run(['git','init','-b','main',str(checkout)],check=True)
        subprocess.run(['git','-C',str(checkout),'remote','add','origin',remote],check=True)
    # Bot identity is local to this demo checkout; user's global Git config is untouched.
    subprocess.run(['git','-C',str(checkout),'config','user.name','Agent Security Demo'],check=True)
    subprocess.run(['git','-C',str(checkout),'config','user.email','68029303+pasiripu@users.noreply.github.com'],check=True)
    subprocess.run(['git','-C',str(checkout),'add','--all'],check=True)
    changed=subprocess.run(['git','-C',str(checkout),'diff','--cached','--quiet']).returncode
    if changed:
        subprocess.run(['git','-C',str(checkout),'commit','-m','Add approval-gated AWS agent security lab'],check=True)
    subprocess.run(['gh','auth','setup-git'],check=True)
    subprocess.run(['git','-C',str(checkout),'push','-u','origin','main'],check=True)
    print('Published sanitized source: https://github.com/'+REPO)


def configure(deployment):
    repo=gh('repos/'+REPO)
    if deployment['github_repository']!=REPO or str(repo['id'])!=str(deployment['github_repository_id']):
        raise ValueError('Terraform outputs refer to a different repository')
    protect(REPO,repo['owner']['id'])
    variables={'AWS_ROLE_ARN':deployment['aws_role_arn'],'AWS_REGION':deployment['aws_region'],
        'LAB_INSTANCE_ID':deployment['instance_id'],
        'SSM_DOCUMENT':deployment['ssm_document'],'DIAGNOSTIC_ORIGIN':deployment['diagnostic_origin']}
    existing=gh(f'repos/{REPO}/environments/demo/variables').get('variables',[])
    for name,value in variables.items():
        path=f'repos/{REPO}/environments/demo/variables'
        if any(v['name']==name for v in existing): gh(path+'/'+name,'PATCH',{'name':name,'value':value})
        else: gh(path,'POST',{'name':name,'value':value})
    # Gate remains false until all deployment values and protection rules have been verified.
    try: gh(f'repos/{REPO}/actions/variables','POST',{'name':'LAB_ENABLED','value':'true'})
    except subprocess.CalledProcessError as exc:
        if 'HTTP 409' not in exc.stderr and 'HTTP 422' not in exc.stderr: raise
        gh(f'repos/{REPO}/actions/variables/LAB_ENABLED','PATCH',{'name':'LAB_ENABLED','value':'true'})
    print('Configured environment variables; each issue still requires your approval in Actions.')


def main():
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['create','publish','configure','stage'])
    p.add_argument('--deployment',default='infrastructure/aws/.build/deployment.json')
    a=p.parse_args()
    if a.action=='create': create()
    elif a.action=='publish': publish()
    elif a.action=='configure': configure(json.loads(Path(a.deployment).read_text()))
    else:
        dest=ROOT/'infrastructure/aws/.build/public-review'
        if dest.exists(): shutil.rmtree(dest)
        stage(dest); print('Staged reviewable public source at '+str(dest))


if __name__=='__main__': main()
