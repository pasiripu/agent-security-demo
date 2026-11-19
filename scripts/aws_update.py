"""Update the existing EC2 runtime in place; never apply Terraform or replace EBS."""
import argparse
import json
import re
import time
from .aws_admin import deployment
from .aws_cli import aws


def install_script(commit, repository):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use a full published commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid source repository")
    # Only validated operator-controlled commit/repository enter this program.
    return '''set -eu
python3 - <<'PYUPDATE'
import hashlib,io,json,os,pathlib,shutil,stat,subprocess,tempfile,time,urllib.request,zipfile
commit=COMMIT
repository=REPOSITORY
base=pathlib.Path('/opt/agent-security-demo')
private=pathlib.Path('/var/lib/agent-lab-private')
backup=private/('source-backup-'+str(int(time.time())))
backup.mkdir(mode=0o700)
services=['agent-lab-diagnostic','agent-lab-receiver','agent-lab-dashboard']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('https://codeload.github.com/'+repository+'/zip/'+commit,timeout=30) as r:
    raw=r.read(8*1024*1024+1)
if len(raw)>8*1024*1024: raise RuntimeError('Source exceeds update limit')
entries={}
prefix=repository.split('/')[1]+'-'+commit+'/'
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    for info in z.infolist():
        if info.is_dir(): continue
        if not info.filename.startswith(prefix): raise RuntimeError('Unexpected archive prefix')
        rel=pathlib.PurePosixPath(info.filename[len(prefix):])
        if '..' in rel.parts or rel.is_absolute() or stat.S_ISLNK(info.external_attr>>16): raise RuntimeError('Unsafe archive entry')
        if rel.parts[0] not in ('agent','endpoints','scripts','dashboard','demo-data'): continue
        if info.file_size>1024*1024: raise RuntimeError('Unexpected runtime file size')
        entries[str(rel)]=z.read(info)
if not {'agent/repository.py','agent/task-public-web.txt','demo-data/internal-notes.txt'}<=entries.keys():
    raise RuntimeError('Commit does not contain the public-web demo')
subprocess.run(['systemctl','stop',*services],check=True)
replaced=[]
configs=[pathlib.Path('/etc/agent-lab/config.template.json'),pathlib.Path('/etc/agent-lab/config.json')]
try:
    for rel,data in entries.items():
        target=base/rel
        if target.is_symlink(): raise RuntimeError('Unexpected runtime symlink')
        saved=backup/rel
        if target.exists():
            saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
        replaced.append(rel)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data);target.chmod(0o444)
    for i,path in enumerate(configs):
        shutil.copy2(path,backup/('config-'+str(i)+'.json'))
        cfg=json.loads(path.read_text())
        cfg.update(url_policy='public-web',max_calls=10,runtime_source_commit=commit)
        path.write_text(json.dumps(cfg));path.chmod(0o644)
    subprocess.run(['python3','-m','compileall','-q','agent','endpoints','scripts'],cwd=base,check=True)
    subprocess.run(['systemctl','start',*services],check=True)
    subprocess.run(['systemctl','is-active','--quiet',*services],check=True)
except BaseException:
    for rel in replaced:
        target=base/rel;saved=backup/rel
        if saved.exists(): shutil.copy2(saved,target)
        else: target.unlink(missing_ok=True)
    for i,path in enumerate(configs):
        saved=backup/('config-'+str(i)+'.json')
        if saved.exists(): shutil.copy2(saved,path)
    subprocess.run(['systemctl','start',*services])
    raise
manifest={'commit':commit,'url_policy':'public-web','files':{r:hashlib.sha256(d).hexdigest() for r,d in entries.items()},'backup':str(backup)}
(private/'runtime-update.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({'commit':commit,'url_policy':'public-web','updated_files':len(entries),'services':'active'}))
PYUPDATE'''.replace('COMMIT',repr(commit)).replace('REPOSITORY',repr(repository))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--commit',required=True)
    a=p.parse_args()
    data=deployment();region=data['aws_region']
    if aws(region,'sts','get-caller-identity')['Account']!=data['aws_account_id']:
        raise ValueError('AWS login and deployment account differ')
    command=install_script(a.commit,data['github_repository'])
    sent=aws(region,'ssm','send-command',{'DocumentName':'AWS-RunShellScript',
        'InstanceIds':[data['instance_id']],'Parameters':{'commands':[command]},
        'Comment':'Owner-requested public-web demo update; existing EC2 only'})
    command_id=sent['Command']['CommandId']
    print('Update command: '+command_id,flush=True)
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        time.sleep(3)
        result=aws(region,'ssm','get-command-invocation',{'CommandId':command_id,'InstanceId':data['instance_id']})
        if result['Status'] in ('Pending','InProgress','Delayed'): continue
        if result['Status']!='Success':
            raise RuntimeError('Update failed: '+result['Status']+' '+result.get('StandardErrorContent','')[-2000:])
        print(result['StandardOutputContent'].strip());return
    raise TimeoutError('Inspect SSM update command '+command_id)


if __name__=='__main__': main()
