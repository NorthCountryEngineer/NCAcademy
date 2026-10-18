"""Fixed-origin, bounded ElevenLabs transport. No clone or account voice APIs."""
import ipaddress
import json
import socket
import time
from datetime import date, datetime, timezone

import httpx

from .provider import ProviderFailure


HOST = "api.elevenlabs.io"
# Independently documented premade examples. Never populated from an account list.
STOCK_VOICES = {
    "JBFqnCBsd6RMkjVDRZzb": "George",
    "EXAVITQu4vr4xnSDxMaL": "Sarah",
}
STOCK_RETIREMENT = date(2026, 12, 31)


def stock_available():
    return datetime.now(timezone.utc).date() < STOCK_RETIREMENT


def request(method, path, *, key=None, params=None, payload=None, files=None, data=None, maximum=65536):
    started = time.monotonic()
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ProviderFailure("Voice provider is not publicly routable")
        target = httpx.URL("https://" + HOST + path).copy_with(host=addresses[0])
        headers = {"Host": HOST}
        if key is not None:
            headers["xi-api-key"] = key
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(25, connect=5), transport=httpx.HTTPTransport(retries=0, trust_env=False)) as client:
            with client.stream(method, target, headers=headers, params=params, json=payload, files=files, data=data, extensions={"sni_hostname": HOST}) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > maximum or time.monotonic() - started > 35:
                        raise ProviderFailure("Voice provider response exceeded its bound; no retry")
                if response.status_code != 200:
                    raise ProviderFailure("Voice provider did not return a successful result; no retry")
                return bytes(body)
    except (httpx.HTTPError, OSError, ValueError):
        raise ProviderFailure("Voice provider outcome unavailable; no retry") from None


def verify_stock(key, voice_id):
    if voice_id not in STOCK_VOICES or not stock_available():
        raise ProviderFailure("Only approved stock voices may be used")
    try:
        # Only the independent exact stock ID is looked up, never an account list.
        # The live endpoint currently requires authorization despite the optional
        # header in its docs. No student audio/text is sent during this check.
        value = json.loads(request("GET", "/v1/voices/" + voice_id, key=key))
        if not isinstance(value, dict) or value.get("voice_id") != voice_id or value.get("category") != "premade" or value.get("is_owner") is True or value.get("is_mixed") is True:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise ProviderFailure("The selected stock voice could not be verified") from None
    return {"id": voice_id, "name": STOCK_VOICES[voice_id], "category": "premade", "verification": "documented-stock-exact-id-v1"}


def transcribe(key, recording, zero_retention=False):
    try:
        value = json.loads(request("POST", "/v1/speech-to-text", key=key, params={"enable_logging": "false" if zero_retention else "true"}, files={"file": ("question.wav", recording, "audio/wav")}, data={"model_id": "scribe_v2", "diarize": "false", "tag_audio_events": "false", "timestamps_granularity": "none"}))
        text = value.get("text") if isinstance(value, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 4000 or key in text:
            raise ValueError()
        return text.strip()
    except (ValueError, TypeError, RecursionError):
        raise ProviderFailure("Voice provider returned an invalid transcript; no retry") from None


def synthesize(key, voice_id, text, zero_retention=False):
    audio = request("POST", "/v1/text-to-speech/" + voice_id, key=key, params={"enable_logging": "false" if zero_retention else "true", "output_format": "mp3_44100_128"}, payload={"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0, "use_speaker_boost": False}}, maximum=2 * 1024 * 1024)
    if not audio or not (audio.startswith(b"ID3") or len(audio) >= 2 and audio[0] == 255 and audio[1] & 224 == 224):
        raise ProviderFailure("Voice provider returned invalid audio; no retry")
    return audio
