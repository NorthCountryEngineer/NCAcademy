"""Public, data-only Game Studio reads. No user credentials cross services."""
from http.cookiejar import CookieJar, DefaultCookiePolicy
import ipaddress
import json
import re
import socket
import time
from urllib.parse import parse_qsl, urlsplit

import httpx


MAX_BYTES = 1048576
DEADLINE_SECONDS = 12
GAME_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class GameStudioFailure(Exception):
    """A generic failure; upstream text is never an application error."""


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def validate_origin(value):
    """Accept a public HTTPS origin only, with no path or URL credentials."""
    if not isinstance(value, str) or not 1 <= len(value) <= 300 or not value.isascii() or re.search(r"[\s\\%?#<>\x00-\x20\x7f]", value):
        raise ValueError("Use an exact public HTTPS Game Studio origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Use an exact public HTTPS Game Studio origin") from None
    if parsed.scheme != "https" or not parsed.hostname or parsed.netloc.endswith(":") or parsed.username is not None or parsed.password is not None or port is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Use an exact public HTTPS Game Studio origin without ports or a path")
    host = parsed.hostname
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal")) or len(host) > 253:
        raise ValueError("A public Game Studio hostname is required")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in host.split(".")) or "." not in host or host.rsplit(".", 1)[-1].isdigit():
            raise ValueError("A public Game Studio hostname is required") from None
    else:
        if not address.is_global:
            raise ValueError("A public Game Studio hostname is required")
    return "https://" + ("[" + host + "]" if ":" in host else host)


def _path(value):
    if not isinstance(value, str) or len(value) > 6000 or not value.isascii() or re.search(r"[\s\\#<>\x00-\x20\x7f]", value):
        raise ValueError("Invalid public Game Studio route")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path not in {"/integration", "/games/published"} and not re.fullmatch("/games/published/" + GAME_ID, parsed.path):
        raise ValueError("Only public Game Studio catalog routes are allowed")
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if parsed.path != "/games/published" or len(pairs) != 1 or pairs[0][0] != "cursor" or not 1 <= len(pairs[0][1]) <= 4096 or re.search(r"[\x00-\x20\x7f]", pairs[0][1]):
            raise ValueError("Invalid public Game Studio pagination")
    elif "?" in value:
        raise ValueError("Invalid public Game Studio pagination")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate Game Studio field")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("Invalid Game Studio number")


def fetch_public(origin, path):
    """One bounded GET with pinned public DNS/TLS and no cookies or redirects.

    Callers supply only registered public paths and opaque game/cursor IDs. No
    Academy token, identity, course, question, photograph or browser data is sent.
    """
    started = time.monotonic()
    try:
        origin, path = validate_origin(origin), _path(path)
        host = urlsplit(origin).hostname
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = sorted({entry[4][0] for entry in answers})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("Game Studio did not resolve exclusively to public addresses")
        remaining = DEADLINE_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("Game Studio deadline exceeded")
        target = httpx.URL(origin + path).copy_with(host=addresses[0])
        timeout = httpx.Timeout(remaining, connect=min(3, remaining), pool=min(1, remaining))
        with httpx.Client(cookies=CookieJar(policy=_NoCookies()), follow_redirects=False, trust_env=False, timeout=timeout, transport=httpx.HTTPTransport(retries=0, trust_env=False)) as client:
            with client.stream("GET", target, headers={"Host": host, "Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "NCAcademy-GameStudio/1.0"}, extensions={"sni_hostname": host}) as response:
                if response.status_code != 200:
                    raise ValueError("Game Studio publication unavailable")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                length = response.headers.get("content-length")
                if media_type != "application/json" or response.headers.get("content-encoding", "identity").lower() not in {"", "identity"} or length is not None and (not length.isascii() or not length.isdigit() or int(length) > MAX_BYTES):
                    raise ValueError("Unsupported Game Studio response")
                raw = bytearray()
                for chunk in response.iter_raw(chunk_size=4096):
                    if time.monotonic() - started >= DEADLINE_SECONDS or len(raw) + len(chunk) > MAX_BYTES:
                        raise ValueError("Game Studio response exceeded bounds")
                    raw.extend(chunk)
                if time.monotonic() - started >= DEADLINE_SECONDS:
                    raise ValueError("Game Studio deadline exceeded")
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
                if not isinstance(value, dict):
                    raise ValueError("Game Studio must return a public JSON object")
                return value
    except (httpx.HTTPError, OSError, ValueError, TypeError, RecursionError):
        raise GameStudioFailure("Game Studio is unavailable or returned unsupported public data") from None
