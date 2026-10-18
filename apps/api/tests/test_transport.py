import json
import socket
import pytest

from academy import provider
from conftest import model_assignment, login
from test_academy import turn


def test_chunked_body_size_is_bounded_without_content_length(client):
    response = client.post("/api/auth/local", content=(b"x" * 80000 for _ in range(2)), headers={"Origin": "http://localhost:8090", "Content-Type": "application/json"})
    assert response.status_code == 413


@pytest.mark.parametrize("result", [None, [], "invalid", 42])
def test_malformed_top_level_provider_result_terminally_withheld(client, app, result):
    headers, _ = model_assignment(client, app)
    app.state.classify = lambda *_: result
    first = turn(client, headers).json()
    assert first["status"] == "withheld" and first["approved_text"] is None
    assert turn(client, headers).json()["id"] == first["id"]


def test_provider_metadata_and_known_key_echo_redacted_before_audit(client, app):
    headers, _ = model_assignment(client, app)
    app.state.classify = lambda *_: {"id": float("nan"), "model": {"thinking": "hidden-one"}, "choices": [{"message": {"role": "assistant", "content": '{"selection_id":"uncertainty"}', "refusal": "Echo unit-test-key-never-live", "reasoning_content": "hidden-two"}, "finish_reason": float("inf")}], "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40, "private_reasoning": "hidden-three"}}
    result = turn(client, headers).json()
    assert result["status"] == "released"
    login(client, "demo-admin")
    exported = client.get("/api/admin/audit/export")
    assert exported.status_code == 200
    assert "unit-test-key-never-live" not in exported.text
    assert all(secret not in exported.text for secret in ("hidden-one", "hidden-two", "hidden-three"))


def test_private_dns_answer_never_sends_provider_request(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(provider.ProviderFailure):
        provider.classify({"endpoint": "https://models.example.test/v1"}, "unit-secret", "id", [], 80)


@pytest.mark.parametrize("status", [200, 302])
def test_provider_transport_pins_dns_sni_and_disables_store_redirects_and_retries(monkeypatch, status):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    captured = []
    class Response:
        status_code = status
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_bytes(self): yield b'{"choices":[]}'
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, url, **kwargs):
            assert method == "POST" and url.host == "8.8.8.8"
            assert kwargs["headers"]["Host"] == "models.example.test"
            assert kwargs["extensions"]["sni_hostname"] == "models.example.test"
            assert kwargs["json"]["store"] is False and kwargs["json"]["stream"] is False
            assert "tools" not in kwargs["json"]
            captured.append(kwargs)
            return Response()
    monkeypatch.setattr(provider.httpx, "Client", Client)
    if status == 200:
        assert provider.classify({"endpoint": "https://models.example.test/v1"}, "unit-secret", "id", [], 80) == {"choices": []}
    else:
        with pytest.raises(provider.ProviderFailure):
            provider.classify({"endpoint": "https://models.example.test/v1"}, "unit-secret", "id", [], 80)
    assert len(captured) == 1
