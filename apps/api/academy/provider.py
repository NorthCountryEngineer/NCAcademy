"""Bounded, single-send classifier transport. Provider prose is never rendered."""
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit

import httpx


class ProviderFailure(Exception):
    pass


# Exact, reviewed OpenAI model IDs only. A compatible endpoint or an unknown
# model name must keep its own existing request contract. See the dated review
# in docs/openai-chat-compatibility.md before extending this list.
OPENAI_REASONING_EFFORT = {
    "gpt-6-luna": "none",
    "gpt-6-sol": "none",
    "gpt-6.1-terra": "none",
    "gpt-6.1-sol": "low",
    "gpt-6-astra": "low",
}


def _chat_payload(parsed, model, prompt, max_output_tokens):
    payload = {"model": model, "messages": prompt, "temperature": 0,
               "max_tokens": max_output_tokens, "stream": False, "store": False}
    effort = OPENAI_REASONING_EFFORT.get(model)
    if parsed.hostname == "api.openai.com" and parsed.path == "/v1" and effort is not None:
        # This is the same approved cap, including any reasoning tokens. Never
        # add a separate reasoning allowance or retry a truncated selection.
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        payload["reasoning_effort"] = effort
        if effort != "none":
            payload.pop("temperature")
    return payload


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.isascii() or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or len(parsed.path) > 200:
        raise ValueError("Connection endpoint must be an HTTPS URL on port 443 without credentials, query or fragment")
    if parsed.hostname in {"localhost", "metadata.google.internal"}:
        raise ValueError("Public provider hostname required")
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global:
            raise ValueError("Public provider hostname required")
    except ValueError as error:
        if "Public provider" in str(error):
            raise
    return value.rstrip("/")


def classify(connection, api_key, model, prompt, max_output_tokens):
    """Resolve once, reject every private answer, pin chosen IP and TLS SNI.

    httpcore uses sni_hostname for both hostname verification and SNI when the
    connection URL contains the pinned address. No redirects, environment proxy,
    model discovery, cookies, tools, retries or streaming response to the learner.
    """
    parsed = urlsplit(endpoint(connection["endpoint"]))
    started = time.monotonic()
    try:
        answers = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        addresses = sorted({entry[4][0] for entry in answers})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ProviderFailure("Provider endpoint is not publicly routable")
        target = httpx.URL(connection["endpoint"] + "/chat/completions").copy_with(host=addresses[0])
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(20, connect=5), transport=httpx.HTTPTransport(retries=0, trust_env=False)) as client:
            with client.stream("POST", target, headers={"Host": parsed.hostname, "Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, json=_chat_payload(parsed, model, prompt, max_output_tokens), extensions={"sni_hostname": parsed.hostname}) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > 30:
                        raise ProviderFailure("Provider response deadline exceeded; outcome may be billable")
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ProviderFailure("Provider response exceeded bound; outcome may be billable")
                if response.status_code != 200:
                    raise ProviderFailure("Provider returned an unsuccessful response; no retry")
                try:
                    result = json.loads(raw)
                except (ValueError, RecursionError):
                    raise ProviderFailure("Provider returned malformed JSON") from None
                return result
    except (httpx.HTTPError, OSError, ValueError):
        raise ProviderFailure("Provider request failed; outcome may be billable; no retry") from None
