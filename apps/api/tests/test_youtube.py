"""All YouTube HTTP/DNS are synthetic; these tests never contact YouTube."""
from datetime import datetime
import json
import socket
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from academy import youtube


VIDEO = "aB3dE5gH7_-"
CANONICAL = "https://www.youtube.com/watch?v=" + VIDEO


@pytest.mark.parametrize("url", [
    CANONICAL, "https://youtube.com/watch?v=" + VIDEO,
    "https://m.youtube.com/watch?v=" + VIDEO,
    "https://www.youtube.com/shorts/" + VIDEO,
    "https://youtube.com/embed/" + VIDEO,
    "https://youtu.be/" + VIDEO,
    "https://youtu.be/" + VIDEO + "?si=harmless-share_marker",
    CANONICAL + "&feature=shared&si=harmless",
])
def test_only_explicit_video_forms_are_canonicalized(url):
    assert youtube.parse_youtube_url(url) == {"video_id": VIDEO, "canonical_url": CANONICAL}


@pytest.mark.parametrize("url", [
    None, 42, "", " " + CANONICAL, CANONICAL + "\n", CANONICAL + "#t=5",
    CANONICAL + "&t=30", CANONICAL + "&start=30", CANONICAL + "&end=40",
    CANONICAL + "&list=PL123", CANONICAL + "&v=" + VIDEO,
    CANONICAL + "&si=one&si=two", CANONICAL + "&unknown=one",
    CANONICAL + "&si=", CANONICAL + "&si=a+b", CANONICAL + "&feature=" + "a" * 129,
    "https://youtu.be/" + VIDEO + "?v=" + VIDEO,
    "https://youtube.com/watch?v=" + VIDEO + ";list=PL123",
    "https://youtube.com/watch?v=" + VIDEO + "&", "https://youtube.com/watch?v",
    "http://www.youtube.com/watch?v=" + VIDEO,
    "https://www.youtube.com:443/watch?v=" + VIDEO,
    "https://www.youtube.com:/watch?v=" + VIDEO,
    "https://www.youtube.com:bad/watch?v=" + VIDEO,
    "https://user@www.youtube.com/watch?v=" + VIDEO,
    "https://www.youtube.com@evil.example/watch?v=" + VIDEO,
    "https://www.youtube.com.evil.example/watch?v=" + VIDEO,
    "https://www.youtube.com./watch?v=" + VIDEO,
    "https://youtube-nocookie.com/embed/" + VIDEO,
    "https://youtube.com\\@evil.example/watch?v=" + VIDEO,
    "https://youtu.be//" + VIDEO, "https://youtu.be/" + VIDEO + "/",
    "https://youtube.com/playlist?list=PL123", "https://youtube.com/live/" + VIDEO,
    "https://youtube.com/watch?v=%61B3dE5gH7_-",
    "https://youtube.com/%77atch?v=" + VIDEO,
    "https://youtube.com/watch?v=" + "é" * 11,
    "https://youtube.com/watch?v=" + "a" * 10,
    "https://youtube.com/watch?v=" + "a" * 12,
])
def test_ambiguous_or_non_video_urls_are_rejected(url):
    with pytest.raises(ValueError):
        youtube.parse_youtube_url(url)


def metadata(**changes):
    return {"type": "video", "provider_name": "YouTube", "title": "Carbon and ecosystems", "author_name": "Example educator", "author_url": "https://www.youtube.com/@example", "width": 560, "height": 315, "html": '<iframe src="https://evil.example">', "thumbnail_url": "https://evil.example/track", "vendor_reasoning": "ignored", **changes}


def transport(monkeypatch, *, payload=None, raw=None, status=200, headers=None, addresses=None, chunks=None):
    calls = []
    monkeypatch.setattr(youtube.socket, "getaddrinfo", lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in (addresses if addresses is not None else ["8.8.8.8"])])
    body = raw if raw is not None else json.dumps(metadata() if payload is None else payload).encode()
    class Response:
        status_code = status
        def __init__(self): self.headers = {"content-type": "application/json; charset=utf-8", **(headers or {})}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_raw(self, chunk_size):
            assert chunk_size == 4096
            yield from chunks if chunks is not None else [body]
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            assert kwargs["cookies"]._policy.set_ok(None, None) is False
            assert kwargs["cookies"]._policy.return_ok(None, None) is False
            assert kwargs["timeout"].read <= 10 and kwargs["timeout"].connect <= 3
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def stream(self, method, target, **kwargs):
            calls.append((method, target, kwargs))
            return Response()
    monkeypatch.setattr(youtube.httpx, "Client", Client)
    return calls


def test_metadata_transport_pins_fixed_host_dns_sni_and_discards_upstream_html(monkeypatch):
    calls = transport(monkeypatch)
    result = youtube.fetch_youtube_metadata(VIDEO)
    assert set(result) == {"video_id", "canonical_url", "type", "provider_name", "title", "author_name", "author_url", "width", "height", "metadata_sha256", "fetched_at"}
    assert result["canonical_url"] == CANONICAL and result["video_id"] == VIDEO
    assert len(result["metadata_sha256"]) == 64
    assert datetime.fromisoformat(result["fetched_at"]).tzinfo is not None
    assert len(calls) == 1
    method, target, kwargs = calls[0]
    assert method == "GET" and target.scheme == "https" and target.host == "8.8.8.8" and target.path == "/oembed"
    assert dict(target.params) == {"url": CANONICAL, "format": "json"}
    assert kwargs["extensions"] == {"sni_hostname": "www.youtube.com"}
    assert kwargs["headers"]["Host"] == "www.youtube.com"
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert set(kwargs) == {"headers", "extensions"}
    assert "evil.example" not in json.dumps(result) and "vendor_reasoning" not in result


def test_metadata_hash_stable_except_relevant_reviewed_fields(monkeypatch):
    transport(monkeypatch)
    first = youtube.fetch_youtube_metadata(VIDEO)
    transport(monkeypatch, payload=metadata(html="changed script", thumbnail_url="changed tracker", vendor_reasoning="changed hidden"))
    second = youtube.fetch_youtube_metadata(VIDEO)
    assert first["fetched_at"] != second["fetched_at"]
    assert first["metadata_sha256"] == second["metadata_sha256"]
    transport(monkeypatch, payload=metadata(title="Changed video title"))
    assert youtube.fetch_youtube_metadata(VIDEO)["metadata_sha256"] != first["metadata_sha256"]


@pytest.mark.parametrize("addresses", [[], ["127.0.0.1"], ["169.254.169.254"], ["10.0.0.1"], ["::1"], ["fc00::1"], ["8.8.8.8", "192.168.1.1"], ["8.8.8.8", "not-ip"]])
def test_all_dns_answers_must_be_public_before_http(monkeypatch, addresses):
    calls = transport(monkeypatch, addresses=addresses)
    with pytest.raises(youtube.YouTubeFailure):
        youtube.fetch_youtube_metadata(VIDEO)
    assert not calls


@pytest.mark.parametrize("payload", [
    [], None, "not object", {"type": "link"}, metadata(type="link"), metadata(provider_name="YouTube spoof"),
    metadata(title=""), metadata(title="x" * 501), metadata(title="evil\nline"), metadata(title="\u202ephishing"),
    metadata(author_name=False), metadata(author_name="x" * 201), metadata(author_url="https://evil.example/@example"),
    metadata(author_url="https://www.youtube.com@evil.example/@example"), metadata(author_url="https://www.youtube.com:443/@example"),
    metadata(author_url="https://www.youtube.com/@example?redirect=evil"), metadata(author_url="https://www.youtube.com/@example/path"),
    metadata(author_url="https://www.youtube.com/@example%2fextra"), metadata(author_url="https://www.youtube.com/channel/.."),
    metadata(width=True), metadata(width=1.0), metadata(height=0), metadata(width=8193),
])
def test_untrusted_metadata_is_validated_fail_closed(monkeypatch, payload):
    # Null is explicitly encoded rather than treated as the fixture's default.
    transport(monkeypatch, raw=json.dumps(payload).encode())
    with pytest.raises(youtube.YouTubeFailure):
        youtube.fetch_youtube_metadata(VIDEO)


@pytest.mark.parametrize("kwargs", [
    {"status": 302}, {"status": 429}, {"headers": {"content-type": "text/html"}},
    {"headers": {"content-encoding": "gzip"}}, {"headers": {"content-length": "32769"}},
    {"headers": {"content-length": "-1"}}, {"headers": {"content-length": "1e2"}},
    {"raw": b'{"type":"video","type":"link"}'}, {"raw": b'{"type":NaN}'},
    {"raw": b'\xff'}, {"raw": b'{invalid'}, {"chunks": [b"x" * 16384, b"x" * 16385]},
])
def test_response_bounds_no_redirects_no_decompression_no_retry(monkeypatch, kwargs):
    calls = transport(monkeypatch, **kwargs)
    with pytest.raises(youtube.YouTubeFailure, match="unavailable or invalid"):
        youtube.fetch_youtube_metadata(VIDEO)
    assert len(calls) == 1


def test_network_failure_is_sanitized_without_retry(monkeypatch):
    calls = transport(monkeypatch)
    class FailingClient:
        def __init__(self, **_): pass
        def __enter__(self): raise httpx.ConnectError("private upstream body and URL")
        def __exit__(self, *_): pass
    monkeypatch.setattr(youtube.httpx, "Client", FailingClient)
    with pytest.raises(youtube.YouTubeFailure) as error:
        youtube.fetch_youtube_metadata(VIDEO)
    assert "private" not in str(error.value) and not calls


@pytest.mark.parametrize("timings,expected_calls", [([0, 11], 0), ([0, 0, 11], 1), ([0, 0, 0, 11], 1), ([0, 0, 0, 0, 11], 1)])
def test_elapsed_deadline_covers_dns_headers_body_and_completion(monkeypatch, timings, expected_calls):
    calls = transport(monkeypatch)
    values = iter(timings)
    monkeypatch.setattr(youtube.time, "monotonic", lambda: next(values))
    with pytest.raises(youtube.YouTubeFailure):
        youtube.fetch_youtube_metadata(VIDEO)
    assert len(calls) == expected_calls


@pytest.mark.parametrize("origin", ["https://ncacademy.app", "https://ncacademy.app/", "http://localhost:8093", "http://127.0.0.1:8094", "http://[::1]:8090"])
def test_embed_is_fixed_nocookie_player_with_only_origin_and_reviewed_segment(origin):
    result = youtube.youtube_embed_url(VIDEO, origin, 30, 90)
    parsed = urlsplit(result)
    assert parsed.scheme == "https" and parsed.netloc == "www.youtube-nocookie.com" and parsed.path == "/embed/" + VIDEO
    assert parse_qs(parsed.query) == {"controls": ["1"], "autoplay": ["0"], "rel": ["0"], "playsinline": ["1"], "origin": [origin.rstrip("/")], "start": ["30"], "end": ["90"]}
    assert "student" not in result and "session" not in result


@pytest.mark.parametrize("origin", ["javascript:alert(1)", "http://ncacademy.app", "https://user:pass@ncacademy.app", "https://ncacademy.app/student/1", "https://ncacademy.app?student=1", "https://ncacademy.app#session", "https://ncacademy.app:\n", "https://ncacademy.app:", "https://ncacademy.app /", "https://ncacademy..app", "https://ncacademy.app%2fevil", "https://ncacademy.app\\evil"])
def test_embed_origin_cannot_convey_credentials_paths_identity_or_fragments(origin):
    with pytest.raises(ValueError):
        youtube.youtube_embed_url(VIDEO, origin)


@pytest.mark.parametrize("start,end", [(True, None), (0.0, None), (-1, None), (86401, None), (0, True), (10, 10), (20, 10), (0, 86401), (0, 10.5)])
def test_embed_accepts_only_bounded_whole_reviewed_seconds(start, end):
    with pytest.raises(ValueError):
        youtube.youtube_embed_url(VIDEO, "https://ncacademy.app", start, end)


def test_invalid_video_id_is_rejected_before_dns_in_all_entry_points():
    with pytest.raises(ValueError):
        youtube.fetch_youtube_metadata("../not-a-video")
    with pytest.raises(ValueError):
        youtube.youtube_embed_url("../not-a-video", "https://ncacademy.app")
