"""Create the exact GitHub upload bundle from an explicit source allowlist."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
TOP = ['README.md', '.gitignore', 'requirements.txt', 'requirements-automation.txt',
       'serve.py', 'Run DoT Collector.command']
FOLDERS = ['src', 'scripts', 'tests', '.github', 'deployment']


def main():
    files = [ROOT / name for name in TOP]
    for folder in FOLDERS:
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file()
                     and not p.is_symlink() and '__pycache__' not in p.parts
                     and p.name != '.DS_Store' and p.suffix != '.pyc')
    files = sorted(set(files))
    DIST.mkdir(exist_ok=True)
    manifest = []
    destination = DIST / 'dot-blocking-order-collector-github.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            if path.is_symlink():
                raise ValueError(f'Refusing symlink: {path.name}')
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            manifest.append({'path': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            bundle.writestr(relative, data)
    (DIST / 'upload-manifest.json').write_text(json.dumps(manifest, indent=2))
    print(f'{destination}\n{len(files)} source files; no virtual environment, downloaded orders or credentials')


if __name__ == '__main__':
    main()
