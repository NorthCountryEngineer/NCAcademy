"""Synthetic Game Studio DNS/HTTP only; no service or model calls."""
import json
import socket

import pytest

from academy import game_studio_transport as transport


ORIGIN = "https://studio.example"
GAME = "12345678-1234-1234-1234-123456789abc"


def fake(monkeypatch, *, addresses=None, raw=b'{"games":[]}', headers=None, status=200, chunks=None):
    calls = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in (["8.8.8.8"] if addresses is None else addresses)])
    class Response:
        status_code = status
        def __init__(self): self.headers = {"content-type": "application/json; charset=utf-8", **(headers or {})}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_raw(self, chunk_size):
            assert chunk_size == 4096
            yield from [raw] if chunks is None else chunks
    class Client:
        def __init__(self, **kw):
            assert kw["trust_env"] is False and kw["follow_redirects"] is False
            assert not kw["cookies"]._policy.set_ok(None, None)
            assert not kw["cookies"]._policy.return_ok(None, None)
            assert kw["timeout"].read <= transport.DEADLINE_SECONDS and kw["timeout"].connect <= 3
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, target, **kw):
            calls.append((method, target, kw));return Response()
    monkeypatch.setattr(transport.httpx, "Client", Client)
    return calls


@pytest.mark.parametrize("origin", [None, 42, "", " https://studio.example", "https://studio.example\n", "http://studio.example", "https://localhost", "https://x.local", "https://x.internal", "https://metadata.google.internal", "https://127.0.0.1", "https://169.254.169.254", "https://10.0.0.1", "https://[::1]", "https://studio.example:443", "https://studio.example:", "https://user@studio.example", "https://studio.example/path", "https://studio.example?key=secret", "https://studio.example#", "https://studio.example%2f.evil", "https://studio.example\\evil", "https://studío.example", "https://studio..example", "https://studio.example.", "https://2130706433", "https://127.1"])
def test_only_public_https_origins_are_admitted(origin):
    with pytest.raises(ValueError):transport.validate_origin(origin)


@pytest.mark.parametrize("path", ["/integration", "/games/published", "/games/published?cursor=opaque%2Bpage%3D", "/games/published/" + GAME])
def test_single_public_get_pins_tls_and_never_transmits_host_identity(monkeypatch, path):
    calls = fake(monkeypatch)
    assert transport.fetch_public(ORIGIN, path) == {"games": []}
    assert len(calls) == 1
    method, target, options = calls[0]
    assert method == "GET" and target.host == "8.8.8.8" and target.scheme == "https"
    assert options["extensions"] == {"sni_hostname": "studio.example"}
    assert options["headers"] == {"Host": "studio.example", "Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "NCAcademy-GameStudio/1.0"}
    assert set(options) == {"headers", "extensions"}


@pytest.mark.parametrize("path", ["https://evil.example/games/published", "//evil.example/games/published", "/games", "/games/settings", "/oauth/token", "/games/published/../settings", "/games/published/" + GAME + "/publish", "/games/published#", "/integration?cursor=a", "/games/published?cursor=", "/games/published?cursor=a&cursor=b", "/games/published?cursor=%0a", "/games/published?cursor=" + "x" * 4097, "/games/published?unknown=a"])
def test_unregistered_or_ambiguous_routes_fail_before_http(monkeypatch, path):
    calls = fake(monkeypatch)
    with pytest.raises(transport.GameStudioFailure):transport.fetch_public(ORIGIN, path)
    assert not calls


@pytest.mark.parametrize("addresses", [[], ["127.0.0.1"], ["169.254.169.254"], ["10.0.0.1"], ["::1"], ["fc00::1"], ["8.8.8.8", "192.168.1.1"], ["8.8.8.8", "invalid"]])
def test_every_dns_answer_must_be_public_before_any_http(monkeypatch, addresses):
    calls = fake(monkeypatch, addresses=addresses)
    with pytest.raises(transport.GameStudioFailure):transport.fetch_public(ORIGIN, "/games/published")
    assert not calls


@pytest.mark.parametrize("raw,headers,status", [(b'{}', {}, 302), (b'{}', {}, 404), (b'{}', {"content-type": "text/html"}, 200), (b'{}', {"content-encoding": "gzip"}, 200), (b'{}', {"content-length": "99999999"}, 200), (b'{}', {"content-length": "-1"}, 200), (b'[]', {}, 200), (b'{"games":[],"games":[]}', {}, 200), (b'{"revision":NaN}', {}, 200), (b'{"revision":Infinity}', {}, 200), (b'not json', {}, 200), (b'\xff', {}, 200)])
def test_untrusted_responses_fail_with_generic_error(monkeypatch, raw, headers, status):
    calls = fake(monkeypatch, raw=raw, headers=headers, status=status)
    with pytest.raises(transport.GameStudioFailure, match="unavailable or returned unsupported public data") as failure:transport.fetch_public(ORIGIN, "/integration")
    assert len(calls) == 1 and str(raw) not in str(failure.value)


def test_body_cap_applies_to_streamed_response(monkeypatch):
    fake(monkeypatch, chunks=[b"x" * 4096] * (transport.MAX_BYTES // 4096 + 1))
    with pytest.raises(transport.GameStudioFailure):transport.fetch_public(ORIGIN, "/integration")


def test_resolution_deadline_rejects_without_http(monkeypatch):
    calls = fake(monkeypatch)
    clock = iter([0, transport.DEADLINE_SECONDS + 1])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(clock))
    with pytest.raises(transport.GameStudioFailure):transport.fetch_public(ORIGIN, "/integration")
    assert not calls
