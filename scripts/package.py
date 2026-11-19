"""Export source and explicitly curated evidence, never runtime secrets."""
import hashlib
from pathlib import Path
import zipfile
from agent.common import ROOT


def main():
    destination=ROOT.parent/'agent-security-demo.zip'
    roots={'.github','agent','endpoints','experiments','tests','dashboard','infrastructure','scripts','docs','demo-data'}
    root_files={'README.md','.gitignore','config.example.json'}
    files=[]
    for path in ROOT.rglob('*'):
        relative=path.relative_to(ROOT)
        if (not path.is_file() or path.is_symlink() or any(p in {'.git','.build','.terraform','__pycache__'} for p in relative.parts)
                or path.suffix in ('.pyc','.pem','.key','.tfvars','.tfplan') or '.tfstate' in path.name
                or path.name in ('anthropic-api-key','config.json') or path.name.endswith('.tfvars.json')):
            continue
        if relative.parts[0] in roots or len(relative.parts)==1 and relative.name in root_files:
            files.append(path)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path,Path(ROOT.name)/path.relative_to(ROOT))
    print(str(destination))
    print('SHA256 '+hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__=='__main__':
    main()
