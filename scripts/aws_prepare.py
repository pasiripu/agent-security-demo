"""Build deterministic, explicit source archives; never package runtime data."""
from pathlib import Path
import zipfile
from agent.common import ROOT


def archive(destination, entries):
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(entries.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED; info.external_attr=0o644<<16
            z.writestr(info,data)


def main():
    build=ROOT/'infrastructure/aws/.build'
    roots=('agent','dashboard')
    names=[p.relative_to(ROOT).as_posix() for root in roots for p in (ROOT/root).rglob('*')
           if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts
           and p.suffix in ('.py','.txt','.html','.js','.css')]
    names+=['endpoints/__init__.py','endpoints/diagnostics/content.py','scripts/__init__.py',
            'scripts/aws_cli.py','scripts/aws_instance_job.py']
    archive(build/'runtime.zip',{n:(ROOT/n).read_bytes() for n in names})
    print('Built local runtime.zip from explicit source files. EC2 bootstrap uses the pinned public Git commit.')



if __name__=='__main__': main()
