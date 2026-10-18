"""Dedicated OAuth configuration is disabled and secret-free by default."""
import pytest

from academy.config import Settings
from test_agent_auth import core_environment


CLIENT = '123456789012-synthetic-client.apps.googleusercontent.com'
SECRET = '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cret'


@pytest.fixture(autouse=True)
def clean_classroom_environment(monkeypatch):
    for name in ('GOOGLE_CLASSROOM_ENABLED', 'GOOGLE_CLASSROOM_CLIENT_ID', 'GOOGLE_CLASSROOM_CLIENT_SECRET', 'GOOGLE_CLASSROOM_CLIENT_SECRET_FILE'):
        monkeypatch.delenv(name, raising=False)


def configured(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLASSROOM_ENABLED', 'true')
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_ID', CLIENT)
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_SECRET', SECRET)


def test_disabled_classroom_does_not_read_credentials_or_file(core_environment, monkeypatch):
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_SECRET_FILE', '/does-not-exist/classroom-secret')
    value = Settings.load()
    assert not value.google_classroom_enabled
    assert value.google_classroom_client_secret == value.google_classroom_client_id == ''


def test_classroom_is_independent_of_core_chat_agents_and_models(core_environment, monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv('ALLOW_AGENT_TEACHERS', 'false')
    monkeypatch.setenv('CORE_CHAT_ENABLED', 'false')
    monkeypatch.setenv('ALLOW_LIVE_MODELS', 'false')
    value = Settings.load()
    assert value.google_classroom_enabled and value.google_classroom_client_id == CLIENT
    assert value.google_classroom_client_secret == SECRET and SECRET not in repr(value)
    assert not value.allow_agent_teachers and not value.allow_live_models and not value.core_chat_enabled
    assert value.google_classroom_redirect_uri == value.app_url + '/api/google-classroom/callback'


@pytest.mark.parametrize('flag', ['1', 'yes', 'on', '', 'true '])
def test_malformed_flag_is_rejected(core_environment, monkeypatch, flag):
    monkeypatch.setenv('GOOGLE_CLASSROOM_ENABLED', flag)
    with pytest.raises(ValueError, match='GOOGLE_CLASSROOM_ENABLED'):
        Settings.load()


@pytest.mark.parametrize('client', ['', 'other.example.test', 'client.apps.googleusercontent.com/redirect', 'client\n.apps.googleusercontent.com'])
def test_classroom_requires_dedicated_client_id(core_environment, monkeypatch, client):
    configured(monkeypatch)
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_ID', client)
    with pytest.raises(ValueError) as caught:
        Settings.load()
    assert str(caught.value) == 'Google Classroom requires dedicated web OAuth client credentials'
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize('secret', ['', 'short', 'x' * 513, 'long-but-invalid secret', 'synthetic-\n-secret-with-controls'])
def test_invalid_secret_is_sanitized(core_environment, monkeypatch, secret):
    configured(monkeypatch)
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_SECRET', secret)
    with pytest.raises(ValueError) as caught:
        Settings.load()
    assert str(caught.value) == 'Google Classroom requires dedicated web OAuth client credentials'


def test_private_secret_file_and_conflict(core_environment, monkeypatch, tmp_path):
    configured(monkeypatch)
    path = tmp_path / 'secret'
    path.write_text(SECRET + '\n')
    path.chmod(0o600)
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_SECRET_FILE', str(path))
    with pytest.raises(ValueError, match='one Google Classroom'):
        Settings.load()
    monkeypatch.delenv('GOOGLE_CLASSROOM_CLIENT_SECRET')
    assert Settings.load().google_classroom_client_secret == SECRET
    path.write_bytes(b'\xff')
    with pytest.raises(ValueError, match='file unavailable'):
        Settings.load()


def test_local_demonstration_rejects_live_google_before_reading_file(monkeypatch):
    monkeypatch.setenv('AUTH_MODE', 'local')
    monkeypatch.setenv('GOOGLE_CLASSROOM_ENABLED', 'true')
    monkeypatch.setenv('GOOGLE_CLASSROOM_CLIENT_SECRET_FILE', '/not-a-real-secret')
    with pytest.raises(ValueError, match='Local demo authentication cannot enable live Google Classroom'):
        Settings.load()
