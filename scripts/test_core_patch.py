"""Apply the proposed onboarding patch only to a temporary local HEAD export.

Requires the already-installed offline Core test image. Never fetches a repository,
changes the original checkout, builds/pulls an image or contacts AWS.
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

ROOT = Path(__file__).resolve().parents[1]
TEST_RUNNER = r'''
import os, pathlib, unittest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
p = pathlib.Path('/tmp/test-oidc.key')
p.write_bytes(rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
pathlib.Path('/tmp/test-django.key').write_text('local-test-only-' * 4)
os.environ.update(CORE_DEBUG='1', CORE_SECRET_KEY_FILE='/tmp/test-django.key',
    CORE_OIDC_KEY_FILE=str(p), DJANGO_SETTINGS_MODULE='core.settings', CORE_ISSUER='http://localhost:8000')
import django
django.setup()
from django.core.management import call_command
call_command('test', 'platform_services.tests.test_academy_fargate',
    'platform_services.tests.test_cloud_fargate', 'platform_services.tests.test_cloud_aws',
    'platform_services.tests.test_cloud_ci', 'platform_services.tests.test_cloud_portal',
    'platform_services.tests.test_cloud_deployments', 'platform_services.tests.test_cloud_recovery',
    'platform_services.tests.test_cloud_release_config', interactive=False)
call_command('makemigrations', check=True, dry_run=True)
result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromName('ci.tests.test_github_gate'))
if not result.wasSuccessful(): raise SystemExit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, default=ROOT.parent / 'NCE Core')
    parser.add_argument('--image', default='nce-core-ci-tests:20261016')
    args = parser.parse_args()
    baseline = json.loads((ROOT / 'nce/core-patch-baseline.json').read_text())
    patch = ROOT / 'nce/core-onboarding.patch'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != baseline['patch_sha256']:
        raise SystemExit('Onboarding patch differs from its reviewed baseline hash.')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=args.core, text=True).strip()
    if actual != baseline['core_commit']:
        raise SystemExit('Core HEAD differs from the patch baseline; review/rebase the patch first.')
    subprocess.run(['docker', 'image', 'inspect', args.image], check=True, stdout=subprocess.DEVNULL)
    archive = subprocess.check_output(['git', 'archive', actual], cwd=args.core)
    with tempfile.TemporaryDirectory(prefix='ncacademy-core-test-') as temporary:
        folder = Path(temporary)
        source = folder / 'source'
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(source, filter='data')
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=source, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=source, check=True)
        runner = folder / 'run-tests.py'
        runner.write_text(TEST_RUNNER)
        subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
            '--tmpfs', '/tmp:size=256m', '-v', f'{source}:/source:ro', '-v', f'{runner}:/run-tests.py:ro',
            '-w', '/source', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/source',
            '--entrypoint', '/app/.venv/bin/python', args.image, '/run-tests.py'], check=True)
    print('Core onboarding patch applied and tested only in a temporary export.')


if __name__ == '__main__':
    main()
