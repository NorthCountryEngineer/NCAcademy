"""Mocked wire-contract checks; no credentials or live provider traffic."""
import socket
import hashlib

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


def test_fast_processing_is_an_explicit_official_openai_request(monkeypatch):
    calls = capture_transport(monkeypatch)
    provider.classify({"endpoint": "https://api.openai.com/v1", "processing_tier": "fast"},
                      "synthetic-not-a-key", "gpt-6-luna", [], 400)
    assert calls[0][1]["json"]["service_tier"] == "fast"
    assert calls[0][1]["json"]["reasoning_effort"] == "none"
    assert calls[0][1]["json"]["max_completion_tokens"] == 400
    assert calls[0][1]["json"]["store"] is False


@pytest.mark.parametrize("endpoint", ["https://models.example.test/v1",
    "https://api.openai.com.example.test/v1", "https://api.openai.com/custom/v1"])
def test_fast_processing_rejects_unreviewed_destinations_without_sending(monkeypatch, endpoint):
    calls = capture_transport(monkeypatch)
    with pytest.raises(provider.ProviderFailure, match="no retry"):
        provider.classify({"endpoint": endpoint, "processing_tier": "fast"},
                          "synthetic-not-a-key", "gpt-6-luna", [], 400)
    assert calls == []


def test_unknown_processing_tier_is_rejected_without_sending(monkeypatch):
    calls = capture_transport(monkeypatch)
    with pytest.raises(provider.ProviderFailure, match="no retry"):
        provider.classify({"endpoint": "https://api.openai.com/v1", "processing_tier": "unknown"},
                          "synthetic-not-a-key", "gpt-6-luna", [], 400)
    assert calls == []


def test_student_openai_requests_pin_project_and_opaque_safety_identity(monkeypatch):
    calls = capture_transport(monkeypatch)
    identity = "academy_" + "a" * 64
    provider.classify({"endpoint": "https://api.openai.com/v1", "openai_organization": "org_synthetic",
                       "openai_project": "proj_synthetic", "safety_identifier": identity,
                       "openai_inference_key_sha256": hashlib.sha256(b"synthetic-not-a-key").hexdigest()},
                      "synthetic-not-a-key", "gpt-6-luna", [], 400)
    request = calls[0][1]
    assert request["headers"]["OpenAI-Organization"] == "org_synthetic"
    assert request["headers"]["OpenAI-Project"] == "proj_synthetic"
    assert request["json"]["safety_identifier"] == identity
    assert request["json"]["store"] is False
    assert "openai_inference_key_sha256" not in request["json"]
    assert hashlib.sha256(b"synthetic-not-a-key").hexdigest() not in str(request)


@pytest.mark.parametrize("change", [{"endpoint": "https://models.example.test/v1"},
    {"openai_project": "proj_test\r\nInjected: yes"}, {"openai_organization": "org_test\n"},
    {"safety_identifier": "private-child-email@example.test"}, {"openai_inference_key_sha256": "b" * 64}])
def test_student_openai_context_cannot_leak_to_other_providers_or_headers(monkeypatch, change):
    calls = capture_transport(monkeypatch)
    connection = {"endpoint": "https://api.openai.com/v1", "openai_organization": "org_synthetic",
                  "openai_project": "proj_synthetic", "safety_identifier": "academy_" + "a" * 64,
                  "openai_inference_key_sha256": hashlib.sha256(b"synthetic-not-a-key").hexdigest()}
    with pytest.raises(provider.ProviderFailure):
        provider.classify({**connection, **change}, "synthetic-not-a-key", "gpt-6-luna", [], 400)
    assert calls == []
