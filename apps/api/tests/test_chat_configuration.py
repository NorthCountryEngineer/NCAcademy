"""Chat enablement is independent of agent admission and never exposes secrets."""
import pytest

from academy.config import Settings
from test_agent_auth import core_environment, WORKLOAD

INSTALLATION = "55555555-5555-4555-8555-555555555555"


def test_chat_defaults_off_and_never_reads_a_disabled_secret_file(core_environment, monkeypatch):
    monkeypatch.delenv("CORE_CHAT_ENABLED", raising=False)
    monkeypatch.setenv("CORE_WORKLOAD_TOKEN_FILE", "/does/not/exist/chat-fixture")
    settings = Settings.load()
    assert not settings.core_chat_enabled and not settings.allow_agent_teachers
    assert settings.core_workload_token == ""


def test_human_chat_can_be_enabled_without_agents_or_models(core_environment, monkeypatch, tmp_path):
    secret = tmp_path / "synthetic-chat-workload"
    secret.write_text(WORKLOAD + "\n")
    monkeypatch.setenv("CORE_CHAT_ENABLED", "true")
    monkeypatch.setenv("CORE_CHAT_INSTALLATION_ID", INSTALLATION)
    monkeypatch.setenv("CORE_WORKLOAD_TOKEN_FILE", str(secret))
    settings = Settings.load()
    assert settings.core_chat_enabled and settings.core_chat_installation_id == INSTALLATION
    assert not settings.allow_agent_teachers and not settings.allow_live_models
    assert settings.core_workload_token == WORKLOAD
    assert WORKLOAD not in repr(settings)


@pytest.mark.parametrize("installation", ["", "not-an-installation", "55555555555545558555555555555555"])
def test_chat_requires_canonical_own_installation_configuration(core_environment, monkeypatch, installation):
    monkeypatch.setenv("CORE_CHAT_ENABLED", "true")
    monkeypatch.setenv("CORE_CHAT_INSTALLATION_ID", installation)
    monkeypatch.setenv("CORE_WORKLOAD_TOKEN", WORKLOAD)
    with pytest.raises(ValueError, match="canonical application installation") as caught:
        Settings.load()
    assert WORKLOAD not in str(caught.value)


@pytest.mark.parametrize("flag", ["enabled", "1", " true"])
def test_chat_rejects_ambiguous_enablement(core_environment, monkeypatch, flag):
    monkeypatch.setenv("CORE_CHAT_ENABLED", flag)
    with pytest.raises(ValueError, match="must be true or false"):
        Settings.load()


def test_chat_requires_own_workload_secret_without_echo(core_environment, monkeypatch):
    monkeypatch.setenv("CORE_CHAT_ENABLED", "true")
    monkeypatch.setenv("CORE_CHAT_INSTALLATION_ID", INSTALLATION)
    with pytest.raises(ValueError, match="separate Core workload"):
        Settings.load()
    monkeypatch.setenv("CORE_WORKLOAD_TOKEN", "synthetic-malformed-credential")
    with pytest.raises(ValueError) as caught:
        Settings.load()
    assert "synthetic-malformed-credential" not in str(caught.value)


def test_local_auth_cannot_enable_live_core_chat(core_environment, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_URL", "http://localhost:8090")
    monkeypatch.setenv("CORE_CHAT_ENABLED", "true")
    monkeypatch.setenv("CORE_CHAT_INSTALLATION_ID", INSTALLATION)
    monkeypatch.setenv("CORE_WORKLOAD_TOKEN", WORKLOAD)
    with pytest.raises(ValueError, match="cannot enable live Core Chat"):
        Settings.load()
