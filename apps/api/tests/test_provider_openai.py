"""Mocked wire-contract checks; no credentials or live provider traffic."""
import socket

import pytest

from academy import provider


def capture_transport(monkeypatch, *, status=200):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    calls = []

    class Response:
        status_code = status

        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_bytes(self):
            yield b'{"choices":[],"usage":{"completion_tokens":80,"completion_tokens_details":{"reasoning_tokens":79}}}'

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, url, **kwargs):
            assert method == "POST" and url.host == "8.8.8.8"
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(provider.httpx, "Client", Client)
    return calls


@pytest.mark.parametrize(("model", "effort", "temperature"), [
    ("gpt-6-luna", "none", 0),
    ("gpt-6-sol", "none", 0),
    ("gpt-6.1-terra", "none", 0),
    ("gpt-6.1-sol", "low", None),
    ("gpt-6-astra", "low", None),
])
def test_openai_exact_models_use_supported_bounded_chat_payload(monkeypatch, model, effort, temperature):
    calls = capture_transport(monkeypatch)
    prompt = [{"role": "system", "content": "Choose only an approved source ID."},
              {"role": "user", "content": "Synthetic tutoring question."}]
    result = provider.classify({"endpoint": "https://api.openai.com/v1"}, "synthetic-not-a-key", model, prompt, 80)
    assert len(calls) == 1
    url, request = calls[0]
    assert url.path == "/v1/chat/completions"
    assert request["headers"]["Host"] == "api.openai.com"
    assert request["extensions"] == {"sni_hostname": "api.openai.com"}
    expected = {"model": model, "messages": prompt, "max_completion_tokens": 80,
                "reasoning_effort": effort, "stream": False, "store": False}
    if temperature is not None:
        expected["temperature"] = temperature
    assert request["json"] == expected
    # Completion usage already includes reasoning; do not hide, add, or retry it.
    assert result["usage"]["completion_tokens"] == 80


@pytest.mark.parametrize("cap", [30, 80, 200, 700, 1200, 3000])
def test_luna_never_increases_the_callers_approved_token_cap(monkeypatch, cap):
    calls = capture_transport(monkeypatch)
    provider.classify({"endpoint": "https://api.openai.com/v1"}, "synthetic-not-a-key", "gpt-6-luna", [], cap)
    assert calls[0][1]["json"]["max_completion_tokens"] == cap
    assert calls[0][1]["json"]["reasoning_effort"] == "none"


@pytest.mark.parametrize(("endpoint", "model"), [
    ("https://models.example.test/v1", "gpt-6-luna"),
    ("https://api.openai.com.example.test/v1", "gpt-6-luna"),
    ("https://api.openai.com/custom/v1", "gpt-6-luna"),
    ("https://api.openai.com/v1", "gpt-4.1"),
    ("https://api.openai.com/v1", "gpt-5.6-mini"),
    ("https://api.openai.com/v1", "gpt-6-luna-custom"),
    ("https://api.openai.com/v1", "gpt-6-luna-2026-01-01"),
])
def test_compatible_endpoints_and_unreviewed_models_keep_existing_payload(monkeypatch, endpoint, model):
    calls = capture_transport(monkeypatch)
    provider.classify({"endpoint": endpoint}, "synthetic-not-a-key", model, [], 80)
    assert calls[0][1]["json"] == {"model": model, "messages": [], "temperature": 0,
                                     "max_tokens": 80, "stream": False, "store": False}


def test_openai_failure_is_one_send_without_parameter_or_model_fallback(monkeypatch):
    calls = capture_transport(monkeypatch, status=400)
    with pytest.raises(provider.ProviderFailure, match="no retry"):
        provider.classify({"endpoint": "https://api.openai.com/v1"}, "synthetic-not-a-key", "gpt-6-luna", [], 80)
    assert len(calls) == 1
    assert calls[0][1]["json"]["model"] == "gpt-6-luna"
    assert calls[0][1]["json"]["max_completion_tokens"] == 80
