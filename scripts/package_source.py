"""Produce a deterministic source-only archive without runtime data or secrets."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from check_package import ROOT, validate

TOP_LEVEL = {'.dockerignore', '.gitignore', '.env.example', 'AGENTS.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
             'README.md', 'compose.yaml', 'nce-deploy.json', 'pyproject.toml', 'NCAcademy.code-workspace'}
DIRECTORIES = {'.github', 'apps', 'deploy', 'docs', 'nce', 'scripts'}
SKIP = {'node_modules', 'dist', '__pycache__', '.pytest_cache', '.venv', '.git', 'var', 'test-results', 'playwright-report', '.DS_Store'}


def source_files(root: Path):
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part in SKIP for part in relative.parts):
            continue
        if relative.parts[0] not in DIRECTORIES and str(relative) not in TOP_LEVEL:
            continue
        if path.is_symlink():
            raise ValueError('Source symlinks are not permitted: ' + str(relative))
        if not path.is_file():
            continue
        if path.name.startswith('.env') and path.name != '.env.example':
            continue
        if path.suffix == '.tsbuildinfo':
            continue
        if path.suffix in {'.db', '.sqlite', '.sqlite3', '.log', '.pyc', '.pem', '.key'}:
            raise ValueError('Runtime or secret-like file inside source: ' + str(relative))
        yield relative, path.read_bytes()


def package(root: Path, destination: Path, release=False):
    validate(root, release)
    inventory = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('wb') as output:
        with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for relative, content in source_files(root):
                    item = tarfile.TarInfo('NCAcademy/' + relative.as_posix())
                    item.size = len(content)
                    item.mode = 0o755 if (root / relative).stat().st_mode & 0o111 else 0o644
                    item.mtime = 0
                    archive.addfile(item, io.BytesIO(content))
                    inventory.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
    receipt = {'schema': 1, 'image_pin_validated': release, 'archive_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
               'files': inventory}
    destination.with_suffix(destination.suffix + '.manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'build/NCAcademy-source.tar.gz')
    parser.add_argument('--release', action='store_true')
    args = parser.parse_args()
    receipt = package(ROOT, args.output, args.release)
    print(f"Packaged {len(receipt['files'])} source files; SHA256 {receipt['archive_sha256']}")
