"""Release pins and runtime-data exclusion protect the reviewable source export."""
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from check_package import ROOT, PLACEHOLDER, validate
from package_source import source_files


def test_source_preparation_allows_explicit_pending_pin_but_release_fails():
    manifest = validate()
    if manifest['backend']['image'] == PLACEHOLDER:
        with pytest.raises(ValueError, match='immutable private ECR digest'):
            validate(release=True)


def test_export_excludes_private_env_database_dependencies_and_builds(tmp_path):
    for name in ('README.md', '.env', '.env.example', 'apps/web/src/main.tsx',
                 'apps/web/node_modules/private.txt', 'apps/web/dist/private.txt', 'var/student.db'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic fixture')
    names = {relative.as_posix() for relative, _ in source_files(tmp_path)}
    assert names == {'README.md', '.env.example', 'apps/web/src/main.tsx'}


def test_export_refuses_source_symlinks_and_runtime_files(tmp_path):
    (tmp_path / 'apps').mkdir()
    (tmp_path / 'apps/student.db').write_text('synthetic fixture')
    with pytest.raises(ValueError, match='Runtime or secret-like'):
        list(source_files(tmp_path))
    (tmp_path / 'apps/student.db').unlink()
    (tmp_path / 'apps/link').symlink_to(tmp_path / 'README.md')
    with pytest.raises(ValueError, match='symlinks'):
        list(source_files(tmp_path))
