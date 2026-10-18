"""Strict YouTube identifiers and a bounded, metadata-only public transport.

oEmbed metadata does not prove the contents or future behavior of a video. It
supplies a change detector for the separate transcript and human-viewing review.
No returned oEmbed HTML, thumbnail, captions or player code is consumed here.
"""
from datetime import datetime, timezone
from hashlib import sha256
from http.cookiejar import CookieJar, DefaultCookiePolicy
import ipaddress
import json
import re
import socket
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}", re.ASCII)
_HOST = "www.youtube.com"
_MAX_BYTES = 32768
_DEADLINE_SECONDS = 10


class YouTubeFailure(Exception):
    """Metadata unavailable or invalid; no upstream text belongs in the error."""


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def _video_id(value):
    if not isinstance(value, str) or not VIDEO_ID.fullmatch(value):
        raise ValueError("YouTube video ID must contain exactly 11 ASCII ID characters")
    return value


def _url(value, *, maximum=2048):
    if not isinstance(value, str) or not value or len(value) > maximum or value != value.strip() or "\\" in value or any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        raise ValueError("A clean absolute HTTPS YouTube URL is required")
    try:
        parsed = urlsplit(value)
        # Accessing .port also rejects malformed/non-numeric/out-of-range ports.
        port = parsed.port
    except ValueError:
        raise ValueError("A clean absolute HTTPS YouTube URL is required") from None
    if parsed.username is not None or parsed.password is not None or port is not None:
        raise ValueError("YouTube URLs cannot contain credentials or ports")
    return parsed


def parse_youtube_url(value):
    """Accept one exact video, never playlists, redirects or arbitrary URLs.

    Shared timestamps are deliberately rejected: the independently reviewed
    start/end segment is an explicit resource field, not an ignored URL hint.
    """
    parsed = _url(value)
    if not value.isascii() or "%" in value or parsed.scheme != "https" or parsed.fragment or parsed.netloc.lower() not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        raise ValueError("Use a direct HTTPS YouTube video URL without encoded content or a fragment")
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    parameters = {}
    for key, item in query:
        if key in parameters:
            raise ValueError("Duplicate YouTube URL parameters are not allowed")
        if key == "v" and parsed.path == "/watch" and parsed.netloc.lower() != "youtu.be":
            parameters[key] = _video_id(item)
        elif key in {"si", "feature"} and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item, re.ASCII):
            parameters[key] = item
        else:
            raise ValueError("Remove playlist or other URL parameters and specify reviewed start/end times separately")
    if parsed.netloc.lower() == "youtu.be":
        if not re.fullmatch(r"/[A-Za-z0-9_-]{11}", parsed.path, re.ASCII):
            raise ValueError("Use one direct YouTube video URL; specify reviewed start/end times separately")
        video_id = parsed.path[1:]
    elif parsed.path == "/watch":
        if "v" not in parameters:
            raise ValueError("A watch URL must contain exactly one video ID and no playlist or other parameters")
        video_id = parameters["v"]
    else:
        matched = re.fullmatch(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})", parsed.path, re.ASCII)
        if matched is None:
            raise ValueError("Use a direct watch, shorts, embed or youtu.be video URL")
        video_id = matched.group(1)
    return {"video_id": video_id, "canonical_url": "https://www.youtube.com/watch?v=" + video_id}


def _plain(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        raise ValueError("Invalid YouTube metadata text")
    return value.strip()


def _author_url(value):
    parsed = _url(value)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"youtube.com", "www.youtube.com"} or parsed.query or parsed.fragment:
        raise ValueError("Invalid YouTube channel URL")
    # This URL is attribution only, never a fetch target. Unicode/percent-encoded
    # handles are legitimate; reject encoded separators and traversal ambiguity.
    if not re.fullmatch(r"/(?:@[^/]+|(?:channel|c|user)/[^/]+)", parsed.path) or any(marker in parsed.path.lower() for marker in ("%2f", "%5c", "%00")) or parsed.path.rsplit("/", 1)[-1] in {".", ".."}:
        raise ValueError("Invalid YouTube channel URL")
    return "https://www.youtube.com" + parsed.path


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate metadata field")
        result[key] = value
    return result


def _reject_number(_value):
    raise ValueError("Non-finite metadata number")


def _metadata(value, video_id):
    if not isinstance(value, dict) or value.get("type") != "video" or value.get("provider_name") != "YouTube":
        raise ValueError("Not YouTube video metadata")
    dimensions = {}
    for key in ("width", "height"):
        number = value.get(key)
        if type(number) is not int or not 1 <= number <= 8192:
            raise ValueError("Invalid YouTube video dimensions")
        dimensions[key] = number
    result = {"video_id": video_id, "canonical_url": "https://www.youtube.com/watch?v=" + video_id, "type": "video", "provider_name": "YouTube", "title": _plain(value.get("title"), 500), "author_name": _plain(value.get("author_name"), 200), "author_url": _author_url(value.get("author_url")), **dimensions}
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    result["metadata_sha256"] = sha256(encoded).hexdigest()
    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return result


def fetch_youtube_metadata(video_id):
    """One fixed public oEmbed request, pinned DNS/TLS and no returned HTML.

    A total elapsed deadline is checked after resolution, headers and each body
    chunk; individual network operations are capped by its remaining duration.
    No redirects, proxies, cookies, credentials, retry or arbitrary fetch path.
    """
    video_id = _video_id(video_id)
    started = time.monotonic()
    try:
        answers = socket.getaddrinfo(_HOST, 443, type=socket.SOCK_STREAM)
        addresses = sorted({entry[4][0] for entry in answers})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("YouTube DNS returned a nonpublic address")
        remaining = _DEADLINE_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("YouTube metadata deadline exceeded")
        url = httpx.URL("https://www.youtube.com/oembed").copy_with(params={"url": "https://www.youtube.com/watch?v=" + video_id, "format": "json"}, host=addresses[0])
        timeout = httpx.Timeout(remaining, connect=min(3, remaining), pool=min(1, remaining))
        with httpx.Client(cookies=CookieJar(policy=_NoCookies()), follow_redirects=False, trust_env=False, timeout=timeout, transport=httpx.HTTPTransport(retries=0, trust_env=False)) as client:
            with client.stream("GET", url, headers={"Host": _HOST, "Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "NCAcademy-ExternalReview/1.0"}, extensions={"sni_hostname": _HOST}) as response:
                if response.status_code != 200 or time.monotonic() - started >= _DEADLINE_SECONDS:
                    raise ValueError("YouTube metadata unavailable")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                length = response.headers.get("content-length")
                if media_type != "application/json" or response.headers.get("content-encoding", "identity").lower() not in {"", "identity"} or (length is not None and (not length.isascii() or not length.isdigit() or int(length) > _MAX_BYTES)):
                    raise ValueError("Unsupported YouTube metadata response")
                raw = bytearray()
                for chunk in response.iter_raw(chunk_size=4096):
                    if time.monotonic() - started >= _DEADLINE_SECONDS or len(raw) + len(chunk) > _MAX_BYTES:
                        raise ValueError("YouTube metadata response exceeded bounds")
                    raw.extend(chunk)
                if time.monotonic() - started >= _DEADLINE_SECONDS:
                    raise ValueError("YouTube metadata deadline exceeded")
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_number)
                return _metadata(value, video_id)
    except (httpx.HTTPError, OSError, ValueError, TypeError, RecursionError):
        raise YouTubeFailure("YouTube metadata unavailable or invalid; review remains pending") from None


def _origin(app_url):
    if not isinstance(app_url, str) or not app_url or app_url != app_url.strip() or "%" in app_url or "\\" in app_url or any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in app_url):
        raise ValueError("A clean application origin is required")
    try:
        parsed = urlsplit(app_url)
        _ = parsed.port
    except ValueError:
        raise ValueError("A clean application origin is required") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.hostname.isascii() or parsed.netloc.endswith(":") or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Only the application origin may be sent to YouTube")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if len(parsed.hostname) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part, re.ASCII) for part in parsed.hostname.split(".")):
            raise ValueError("A valid application hostname is required") from None
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("An HTTPS application origin is required outside loopback development")
    return parsed.scheme + "://" + parsed.netloc.lower()


def youtube_embed_url(video_id, app_url, start_seconds=0, end_seconds=None):
    video_id = _video_id(video_id)
    if type(start_seconds) is not int or not 0 <= start_seconds <= 86400 or (end_seconds is not None and (type(end_seconds) is not int or not start_seconds < end_seconds <= 86400)):
        raise ValueError("YouTube segment must use bounded whole seconds with end after start")
    parameters = {"controls": 1, "autoplay": 0, "rel": 0, "playsinline": 1, "origin": _origin(app_url)}
    if start_seconds:
        parameters["start"] = start_seconds
    if end_seconds is not None:
        parameters["end"] = end_seconds
    return "https://www.youtube-nocookie.com/embed/" + video_id + "?" + urlencode(parameters)
