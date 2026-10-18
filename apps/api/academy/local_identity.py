"""Platform-only family credentials and purpose-bound PII encryption.

This module never calls Core or a model/provider. Production family data uses
an independent operator-supplied key, never the session/bootstrap secret.
"""
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import unicodedata
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException
from sqlalchemy import func, select

from .db import Record, canonical

SCRYPT_N, SCRYPT_R, SCRYPT_P = 1 << 17, 8, 1
SCRYPT_MAXMEM = 256 * 1024 * 1024
LOGIN_WINDOW = 900
LOGIN_USERNAME_LIMIT = 5
LOGIN_IP_LIMIT = 30
LOGIN_COUNTER_LIMIT = 20000
MAX_ENCRYPTED_PLAINTEXT = 8 * 1024 * 1024
_KDF_SLOTS = threading.BoundedSemaphore(2)
COMMON_PASSWORDS = frozenset({
    "passwordpassword", "passwordpasswordpassword", "123456789012345",
    "1234567890123456", "12345678901234567890", "qwertyuiopasdfgh",
    "iloveyouiloveyou", "letmeinletmeinletmein",
})


def _scrypt(password, salt):
    # FastAPI's thread pool can otherwise allocate 128 MiB per simultaneous
    # password attempt. Never queue an unbounded set of expensive verifications.
    if not _KDF_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Credential service busy; try again shortly", headers={"Retry-After": "2"})
    try:
        return hashlib.scrypt(password, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32)
    finally:
        _KDF_SLOTS.release()


def decode_pii_key(value):
    """Canonical URL-safe base64, exactly 32 bytes; never include input errors."""
    try:
        if not isinstance(value, str) or len(value) != 44:
            raise ValueError()
        raw = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
        if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode() != value:
            raise ValueError()
        return raw
    except (ValueError, UnicodeError, binascii.Error):
        raise ValueError("PII_ENCRYPTION_KEY must be a canonical URL-safe base64 32-byte key") from None


class LocalIdentity:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database

    @property
    def demo_key(self):
        return self.settings.auth_mode == "local" and not self.settings.pii_encryption_key

    def _master_key(self):
        if self.settings.pii_encryption_key:
            try:
                return decode_pii_key(self.settings.pii_encryption_key)
            except ValueError:
                raise HTTPException(503, "Platform account encryption is unavailable") from None
        if self.settings.auth_mode == "local":
            # Repeatable synthetic local previews only. Never selected in Core
            # deployments, even when the session secret happens to match.
            return hashlib.sha256((self.settings.session_secret + ":local-demo-family-pii-only-v1").encode()).digest()
        raise HTTPException(503, "Platform account encryption is not configured")

    def _key(self, purpose):
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"NCAcademy-family-v1", info=purpose.encode()).derive(self._master_key())

    @staticmethod
    def _aad(tenant_id, owner_id, purpose):
        if not all(isinstance(item, str) and 0 < len(item) <= 128 for item in (tenant_id, owner_id, purpose)):
            raise HTTPException(503, "Platform account encryption context is invalid")
        return canonical({"v": 1, "tenant_id": tenant_id, "owner_id": owner_id, "purpose": purpose}).encode()

    def encrypt(self, value, tenant_id, owner_id, purpose):
        aad = self._aad(tenant_id, owner_id, purpose)
        nonce = secrets.token_bytes(12)
        plaintext = canonical(value).encode()
        if len(plaintext) > MAX_ENCRYPTED_PLAINTEXT:
            raise HTTPException(422, "Platform account data is too large")
        ciphertext = AESGCM(self._key("encryption:" + purpose)).encrypt(nonce, plaintext, aad)
        return {"v": 1, "alg": "AES-256-GCM", "nonce": base64.urlsafe_b64encode(nonce).decode(), "ciphertext": base64.urlsafe_b64encode(ciphertext).decode()}

    def decrypt(self, envelope, tenant_id, owner_id, purpose):
        aad = self._aad(tenant_id, owner_id, purpose)
        key = self._key("encryption:" + purpose)
        try:
            if not isinstance(envelope, dict) or set(envelope) != {"v", "alg", "nonce", "ciphertext"} or type(envelope["v"]) is not int or envelope["v"] != 1 or envelope["alg"] != "AES-256-GCM":
                raise ValueError()
            nonce = base64.b64decode(envelope["nonce"].encode("ascii"), altchars=b"-_", validate=True)
            if len(nonce) != 12 or not isinstance(envelope["ciphertext"], str) or len(envelope["ciphertext"]) > 12 * 1024 * 1024:
                raise ValueError()
            ciphertext = base64.b64decode(envelope["ciphertext"].encode("ascii"), altchars=b"-_", validate=True)
            return json.loads(AESGCM(key).decrypt(nonce, ciphertext, aad))
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, InvalidTag, binascii.Error):
            raise HTTPException(503, "Platform account data is unavailable") from None

    @staticmethod
    def normalize_username(username):
        if not isinstance(username, str) or len(username) > 128:
            raise HTTPException(422, "Username must contain 3–64 letters, numbers, dots, underscores or hyphens")
        normalized = unicodedata.normalize("NFKC", username).casefold()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", normalized):
            raise HTTPException(422, "Username must contain 3–64 letters, numbers, dots, underscores or hyphens")
        return normalized

    def username_key(self, tenant_id, username):
        normalized = self.normalize_username(username)
        return hmac.new(self._key("username-index"), canonical([tenant_id, normalized]).encode(), hashlib.sha256).hexdigest()[:48]

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or not 15 <= len(password) <= 128:
            raise HTTPException(422, "Password must contain 15–128 characters")
        try:
            password.encode("utf-8")
        except UnicodeError:
            raise HTTPException(422, "Password contains invalid characters") from None
        if not password.strip() or len(set(password)) == 1 or password.strip().casefold() in COMMON_PASSWORDS:
            raise HTTPException(422, "Choose a less common password")
        return password

    def password_hash(self, password):
        self.validate_password(password)
        salt = secrets.token_bytes(16)
        digest = _scrypt(password.encode(), salt)
        return {"v": 1, "algorithm": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": base64.urlsafe_b64encode(salt).decode(), "digest": base64.urlsafe_b64encode(digest).decode()}

    def password_verify(self, password, value):
        try:
            if not isinstance(password, str) or not 1 <= len(password) <= 128 or not isinstance(value, dict) or set(value) != {"v", "algorithm", "n", "r", "p", "salt", "digest"} or value["v"] != 1 or value["algorithm"] != "scrypt" or (value["n"], value["r"], value["p"]) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
                return False
            salt = base64.b64decode(value["salt"].encode("ascii"), altchars=b"-_", validate=True)
            expected = base64.b64decode(value["digest"].encode("ascii"), altchars=b"-_", validate=True)
            if len(salt) != 16 or len(expected) != 32:
                return False
            digest = _scrypt(password.encode(), salt)
            return hmac.compare_digest(expected, digest)
        except (TypeError, ValueError, UnicodeError, KeyError, AttributeError, binascii.Error):
            return False

    @staticmethod
    def validate_account(db, user):
        if not user or user.kind != "user":
            return False
        value = user.value
        if value.get("auth_provider") != "academy" or value.get("role") not in {"student", "parent"} or value.get("disabled") is not False or type(value.get("credential_version")) is not int or value["credential_version"] < 1:
            return False
        if value["role"] == "parent":
            from .family_accounts import validate_parent_account
            return validate_parent_account(db, user.tenant_id, user.id)
        return True

    def reserve_login(self, username, ip_address):
        """Atomic cross-process limits; only keyed digests reach the database."""
        self._master_key()
        stamp = int(time.time())
        window = stamp // LOGIN_WINDOW
        identifiers = [("ip", ip_address, LOGIN_IP_LIMIT), ("username", username, LOGIN_USERNAME_LIMIT)]
        rows, admitted = [], True
        with self.database.write() as db:
            cutoff = datetime.fromtimestamp(stamp - LOGIN_WINDOW * 2, timezone.utc).isoformat()
            for stale in db.scalars(select(Record).where(Record.tenant_id == self.settings.tenant_id, Record.kind == "local_login_limit", Record.created_at < cutoff).limit(512)):
                db.delete(stale)
            if db.scalar(select(func.count()).select_from(Record).where(Record.tenant_id == self.settings.tenant_id, Record.kind == "local_login_limit")) >= LOGIN_COUNTER_LIMIT:
                admitted = False
            for category, identifier, maximum in identifiers:
                digest = hmac.new(self._key("login-rate-limit"), canonical([self.settings.tenant_id, category, identifier, window]).encode(), hashlib.sha256).hexdigest()[:48]
                row = db.get(Record, "local-limit:" + digest)
                if row is None:
                    if not admitted:
                        continue
                    row = Record(id="local-limit:" + digest, tenant_id=self.settings.tenant_id, kind="local_login_limit", owner_id="", data=canonical({"count": 0, "expires": (window + 1) * LOGIN_WINDOW}))
                    db.add(row)
                count = row.value.get("count", 0)
                if type(count) is not int or count >= maximum:
                    admitted = False
                rows.append((row, min(maximum, count + 1) if type(count) is int else maximum))
            for row, count in rows:
                row.set({"count": count, "expires": (window + 1) * LOGIN_WINDOW})
        if not admitted:
            raise HTTPException(429, "Sign-in temporarily unavailable; try again later", headers={"Retry-After": str(LOGIN_WINDOW)})

    def authenticate(self, username, password, ip_address):
        # Apply the IP limit even to malformed/missing usernames. The opaque
        # username digest is reused as rate-limit input, never a raw login name.
        try:
            username_digest = self.username_key(self.settings.tenant_id, username)
        except HTTPException as error:
            if error.status_code != 422:
                raise
            username_digest = "invalid-username"
        self.reserve_login(username_digest, ip_address)
        snapshot = None
        with self.database.factory() as db:
            claim = db.get(Record, "local-username:" + username_digest)
            user = db.get(Record, claim.owner_id) if claim and claim.kind == "local_username" and claim.tenant_id == self.settings.tenant_id else None
            account = db.get(Record, "local-account:" + user.id) if user else None
            if user and user.tenant_id == self.settings.tenant_id and account and account.kind == "local_account" and account.tenant_id == user.tenant_id and account.owner_id == user.id and self.validate_account(db, user):
                snapshot = (user.id, user.data, account.data)
        if snapshot:
            account_value = json.loads(snapshot[2])
            verifier = self.decrypt(account_value.get("password_cipher"), self.settings.tenant_id, snapshot[0], "password")
            valid = self.password_verify(password, verifier)
        else:
            # Similar cost for absent/disabled accounts; no persistent dummy
            # credential and no account-existence detail is returned.
            candidate = password if isinstance(password, str) and 1 <= len(password) <= 128 else "invalid-input"
            _scrypt(candidate.encode(errors="replace"), b"academy-dummy-v1")
            valid = False
        if not valid:
            raise HTTPException(401, "Invalid username or password")
        return snapshot
