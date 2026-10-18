"""Validate local source packaging; --release additionally requires a real image pin."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = 'REQUIRES_IMMUTABLE_PRIVATE_ECR_DIGEST'
IMAGE = re.compile(r'[0-9]{12}\.dkr\.ecr\.[a-z]{2}(?:-[a-z]+)+-[0-9]\.amazonaws\.com/[a-z0-9][a-z0-9._/-]{0,255}@sha256:[a-f0-9]{64}\Z')


def validate(root: Path = ROOT, release: bool = False) -> dict:
    manifest = json.loads((root / 'nce-deploy.json').read_text())
    if set(manifest) != {'schema', 'archetype', 'frontend', 'backend', 'identity'}:
        raise ValueError('Unsupported manifest fields.')
    if manifest['schema'] != 1 or manifest['archetype'] != 'amplify-academy-fargate':
        raise ValueError('Use the fixed Academy recipe.')
    if manifest['frontend'] != {'directory': 'apps/web', 'output': 'dist'}:
        raise ValueError('Unexpected frontend contract.')
    if set(manifest['backend']) != {'image'}:
        raise ValueError('The Academy recipe accepts one image only.')
    pin = manifest['backend']['image']
    if not isinstance(pin, str) or not (IMAGE.fullmatch(pin) or (not release and pin == PLACEHOLDER)):
        raise ValueError('Release requires a real immutable private ECR digest.')
    identity = manifest['identity']
    if set(identity) != {'callback_path', 'permissions'} or identity['callback_path'] != '/api/auth/core/callback':
        raise ValueError('Unexpected Core callback contract.')
    permissions = identity['permissions']
    if (not isinstance(permissions, list) or any(set(p) != {'codename', 'name'} for p in permissions)
            or {p['codename'] for p in permissions} != {'academy-use', 'academy-teach', 'academy-audit'}):
        raise ValueError('Unexpected permission vocabulary.')
    if len(permissions) != 3:
        raise ValueError('Duplicate application permissions.')
    required = ('apps/api/Dockerfile', 'apps/api/requirements.lock.txt', 'apps/web/Dockerfile',
                'apps/web/package-lock.json', 'compose.yaml', 'nce/core-onboarding.patch', 'nce/core-patch-baseline.json')
    for path in required:
        if not (root / path).is_file() or (root / path).is_symlink():
            raise ValueError('Missing regular package input: ' + path)
    package = json.loads((root / 'apps/web/package.json').read_text())
    if 'build' not in package.get('scripts', {}) or 'test' not in package.get('scripts', {}):
        raise ValueError('Frontend build and test commands are required.')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', action='store_true', help='Refuse unresolved image references.')
    args = parser.parse_args()
    try:
        validate(release=args.release)
    except (ValueError, OSError, TypeError) as error:
        raise SystemExit(str(error)) from None
    print('Package inputs validated.' + ('' if args.release else ' Source preparation permits the documented unresolved image pin.'))
