"""Verify the supplemental school/agent Core patch in an isolated baseline export.

Uses the installed offline Core test image; never fetches, pulls, changes the
original checkout or contacts Core, providers or AWS.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from test_core_patch import TEST_RUNNER

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True,
        help='Clean reviewed Core checkout at the supplemental patch baseline.')
    parser.add_argument('--image', default='nce-core-ci-tests:20261016')
    parser.add_argument('--apply-only', action='store_true', help='Verify hash and clean patch application without running tests.')
    args = parser.parse_args()
    baseline = json.loads((ROOT / 'nce/school-agent-patch-baseline.json').read_text())
    patch = ROOT / 'nce/school-agent-permissions.patch'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != baseline['patch_sha256']:
        raise SystemExit('School/agent patch differs from its reviewed baseline hash.')
    if hashlib.sha256((ROOT / 'nce/core-onboarding.patch').read_bytes()).hexdigest() != baseline['frozen_onboarding_patch_sha256']:
        raise SystemExit('Frozen onboarding patch differs from the recorded original.')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.core, text=True).strip()
    if actual != baseline['core_commit']:
        raise SystemExit('Core HEAD differs from the reviewed supplemental baseline; review/rebase first.')
    archive = subprocess.check_output(['git', 'archive', actual], cwd=args.core)
    with tempfile.TemporaryDirectory(prefix='ncacademy-school-core-test-') as temporary:
        folder = Path(temporary)
        source = folder / 'source'
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(source, filter='data')
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=source, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=source, check=True)
        subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=source, check=True)
        if not args.apply_only:
            subprocess.run(['docker', 'image', 'inspect', args.image], check=True, stdout=subprocess.DEVNULL)
            runner = folder / 'run-tests.py'
            runner.write_text(TEST_RUNNER)
            subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
                '--tmpfs', '/tmp:size=256m', '-v', f'{source}:/source:ro', '-v', f'{runner}:/run-tests.py:ro',
                '-w', '/source', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/source',
                '--entrypoint', '/app/.venv/bin/python', args.image, '/run-tests.py'], check=True)
    print('Supplemental school/agent patch applied only to an isolated Core baseline export.' +
          (' Core tests were not run (--apply-only).' if args.apply_only else ' Offline Core checks passed.'))


if __name__ == '__main__':
    main()
