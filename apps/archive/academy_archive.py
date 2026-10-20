"""Separate, read-only database exporter for a private S3-compatible audit archive.

No API imports, provider calls, records-table reads or ambient AWS credentials.
Objects are encrypted; signed, conditional-create commit records form the cursor.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, unquote

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ZERO = "0" * 64
MAX_OBJECT = 32 * 1024 * 1024
EVENT_KEYS = {"id", "tenant_id", "sequence", "session_id", "actor_id", "event_type", "payload", "previous_hash", "created_at", "hash"}
FORBIDDEN_KEYS = {"images_cipher", "audio_cipher", "audio_base64", "key_cipher", "token_cipher", "api_key", "client_secret", "access_token", "refresh_token", "id_token", "data_base64", "data_url", "core_workload_token", "session_secret"}


class ArchiveError(ValueError):
    """Integrity, configuration or admission failure; never print student data."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def safe_payload(value):
    """Fail closed rather than silently redacting and breaking the original chain."""
    if isinstance(value, dict):
        if any(str(key).lower() in FORBIDDEN_KEYS for key in value):
            raise ArchiveError("Audit payload contains prohibited credential or binary fields")
        for item in value.values():
            safe_payload(item)
    elif isinstance(value, list):
        for item in value:
            safe_payload(item)


def check_events(events, tenant, sequence=0, digest=ZERO):
    for event in events:
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise ArchiveError("Unexpected audit event schema")
        sequence += 1
        if event["tenant_id"] != tenant or type(event["sequence"]) is not int or event["sequence"] != sequence or event["previous_hash"] != digest:
            raise ArchiveError("Audit tenant, sequence or predecessor mismatch")
        safe_payload(event["payload"])
        digest = sha(canonical({key: value for key, value in event.items() if key != "hash"}))
        if event["hash"] != digest:
            raise ArchiveError("Audit event content digest mismatch")
    return sequence, digest


def private_bytes(path, *, size=4096):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > size:
        raise ArchiveError("Missing or oversized archive configuration file")
    return path.read_bytes()


def atomic_private(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ArchiveConfig:
    tenant_id: str
    endpoint: str
    allowed_origins: tuple[str, ...]
    bucket: str
    prefix: str
    ca_bundle: str
    retention_days: int = 365
    batch_size: int = 100

    def __post_init__(self):
        origin = urlsplit(self.endpoint)
        if origin.scheme != "https" or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password or self.endpoint not in self.allowed_origins:
            raise ArchiveError("Archive endpoint must be an explicitly allowed exact HTTPS origin")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.tenant_id) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise ArchiveError("Invalid tenant or private bucket")
        expected = "ncacademy/" + sha(self.tenant_id.encode())
        if self.prefix != expected or not 1 <= self.retention_days <= 36500 or not 1 <= self.batch_size <= 100:
            raise ArchiveError("Archive prefix or bounded retention/batch configuration is invalid")
        if not Path(self.ca_bundle).is_absolute() or not Path(self.ca_bundle).is_file():
            raise ArchiveError("A mounted TLS CA bundle is required")

    @classmethod
    def load(cls, path):
        value = json.loads(private_bytes(path))
        value["allowed_origins"] = tuple(value["allowed_origins"])
        return cls(**value)


class S3Objects:
    """Bounded single-object calls; never list, delete, overwrite or follow origin changes."""
    def __init__(self, config, credentials_file, *, writable=True):
        import boto3
        from botocore.config import Config
        self.config, self.writable = config, writable
        self._read_versions = {}
        credentials = json.loads(private_bytes(credentials_file))
        if set(credentials) != {"access_key_id", "secret_access_key"} or not all(isinstance(value, str) and value for value in credentials.values()):
            raise ArchiveError("Explicit archive-only credentials are required")
        session = boto3.Session(aws_access_key_id=credentials["access_key_id"], aws_secret_access_key=credentials["secret_access_key"], region_name="us-east-1")
        self.client = session.client("s3", endpoint_url=config.endpoint, verify=config.ca_bundle,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, proxies={},
                          retries={"total_max_attempts": 1, "mode": "standard"}, connect_timeout=5, read_timeout=20,
                          request_checksum_calculation="when_required", response_checksum_validation="when_required"))
        self.client.meta.events.register("before-send.s3", self._guard_request)

    def _guard_request(self, request, **_):
        target = urlsplit(request.url)
        origin = target.scheme + "://" + target.netloc
        expected_path = "/" + self.config.bucket + "/" + self.config.prefix + "/"
        decoded = unquote(target.path)
        if origin != self.config.endpoint or not decoded.startswith(expected_path) or any(part in {".", ".."} for part in decoded.split("/")) or request.method not in ({"GET", "PUT"} if self.writable else {"GET"}):
            raise ArchiveError("Archive request origin, scope or operation changed")

    def _key(self, key):
        if not re.fullmatch(r"[A-Za-z0-9/_.-]+", key) or not key.startswith(self.config.prefix + "/") or any(part in {"", ".", ".."} for part in key.split("/")):
            raise ArchiveError("Object key escaped the configured tenant prefix")
        return key

    def get(self, key):
        try:
            response = self.client.get_object(Bucket=self.config.bucket, Key=self._key(key))
        except self.client.exceptions.NoSuchKey:
            return None
        with response["Body"] as body:
            if response.get("ContentLength", MAX_OBJECT + 1) > MAX_OBJECT:
                raise ArchiveError("Archive object exceeds bounded size")
            result = body.read(MAX_OBJECT + 1)
        if len(result) > MAX_OBJECT:
            raise ArchiveError("Archive object exceeds bounded size")
        self._read_versions[key] = {name: response.get(name) for name in ("VersionId", "LastModified")}
        return result

    def create(self, key, body):
        if not self.writable or len(body) > MAX_OBJECT:
            raise ArchiveError("Archive write is unavailable or oversized")
        # Conditional create is deliberately a single PutObject, not managed multipart.
        # 412/409/timeout may mean another writer won or this request committed;
        # reconcile by a verified GET, never an unconditional replacement.
        try:
            self.client.put_object(Bucket=self.config.bucket, Key=self._key(key), Body=body,
                ContentType="application/octet-stream", IfNoneMatch="*", ContentMD5=base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode())
        except Exception:
            existing = self.get(key)
            if existing is None:
                raise
            self._check_initial_retention(key)
            return existing
        result = self.get(key)
        if result is None:
            raise ArchiveError("Archive write was not durable on read-back")
        self._check_initial_retention(key)
        return result

    def _check_initial_retention(self, key):
        from datetime import timedelta
        # Owner configures bucket-default COMPLIANCE retention. The worker can
        # read the result, but cannot set/shorten retention or change the bucket.
        observed = self._read_versions.get(key, {})
        version, created = observed.get("VersionId"), observed.get("LastModified")
        if not isinstance(version, str) or not version or version == "null" or not isinstance(created, datetime) or created.tzinfo is None:
            raise ArchiveError("Archive object lacks a versioned creation timestamp")
        retention = self.client.get_object_retention(Bucket=self.config.bucket, Key=self._key(key), VersionId=version).get("Retention", {})
        until = retention.get("RetainUntilDate")
        if retention.get("Mode") != "COMPLIANCE" or not isinstance(until, datetime) or until.tzinfo is None or until <= datetime.now(timezone.utc) or until < created + timedelta(days=self.config.retention_days, minutes=-1):
            raise ArchiveError("New archive object lacks the required default COMPLIANCE retention")


class DatabaseSource:
    def __init__(self, database_url, tenant, *, local_test=False):
        url = make_url(database_url)
        if url.get_backend_name() != "postgresql" or url.query.get("sslmode") != "verify-full" or not url.query.get("sslrootcert"):
            if not (local_test and url.get_backend_name() == "sqlite"):
                raise ArchiveError("Archive database requires separate read-only PostgreSQL credentials and verify-full TLS")
        self.engine, self.tenant = create_engine(database_url, pool_pre_ping=True), tenant

    @contextmanager
    def snapshot(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "postgresql":
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                elevated = connection.execute(text("SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one()
                if elevated:
                    raise ArchiveError("Archive database login must not have elevated role attributes")
                broad = connection.execute(text("SELECT has_database_privilege(current_user, current_database(), 'CREATE,TEMPORARY') OR has_schema_privilege(current_user, 'public', 'CREATE') OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))")).scalar_one()
                if broad:
                    raise ArchiveError("Archive database login must not have schema/database creation or role membership")
                for table in ("audit_events", "audit_heads"):
                    privileges = connection.execute(text("SELECT has_table_privilege(current_user, :table, 'SELECT') AS readable, has_table_privilege(current_user, :table, 'INSERT,UPDATE,DELETE,TRUNCATE') AS writable"), {"table": table}).mappings().one()
                    if not privileges["readable"] or privileges["writable"]:
                        raise ArchiveError("Archive database login requires SELECT-only audit-table privileges")
                for table in ("records", "logins", "oauth_flows", "core_identity_bindings"):
                    if connection.execute(text("SELECT has_table_privilege(current_user, :table, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"), {"table": table}).scalar_one():
                        raise ArchiveError("Archive database login must not access private application/vault/auth tables")
            else:
                connection.execute(text("PRAGMA query_only = ON"))
            try:
                snapshot = DatabaseSnapshot(connection, self.tenant)
                snapshot.verify()
                yield snapshot
            finally:
                connection.rollback()
                if self.engine.dialect.name == "sqlite":
                    connection.execute(text("PRAGMA query_only = OFF"))
                    connection.rollback()


class DatabaseSnapshot:
    def __init__(self, connection, tenant):
        self.connection, self.tenant = connection, tenant
        row = connection.execute(text("SELECT sequence, digest FROM audit_heads WHERE tenant_id=:tenant"), {"tenant": tenant}).mappings().first()
        self.head = dict(row) if row else {"sequence": 0, "digest": ZERO}

    def events(self, after=0, limit=None):
        query = "SELECT id, tenant_id, sequence, session_id, actor_id, event_type, payload, previous_hash, created_at, hash FROM audit_events WHERE tenant_id=:tenant AND sequence>:after AND sequence<=:head ORDER BY sequence"
        parameters = {"tenant": self.tenant, "after": after, "head": self.head["sequence"]}
        if limit is not None:
            query += " LIMIT :limit"
            parameters["limit"] = limit
        result = self.connection.execution_options(stream_results=True).execute(text(query), parameters)
        try:
            for row in result.mappings():
                value = dict(row)
                value["payload"] = json.loads(value["payload"])
                yield value
        finally:
            result.close()

    def verify(self):
        actual = self.connection.execute(text("SELECT COUNT(*) AS count, COALESCE(MAX(sequence),0) AS last_sequence FROM audit_events WHERE tenant_id=:tenant"), {"tenant": self.tenant}).mappings().one()
        # Do not let the bounded batch query hide records beyond a damaged or
        # missing head. Both are atomic app writes in a legitimate snapshot.
        if actual["count"] != self.head["sequence"] or actual["last_sequence"] != self.head["sequence"]:
            raise ArchiveError("Database audit event inventory differs from its claimed head")
        sequence, digest = check_events(self.events(), self.tenant)
        if self.head != {"sequence": sequence, "digest": digest}:
            raise ArchiveError("Database audit head differs from its complete tenant chain")


class Archive:
    def __init__(self, config, objects, encryption_key, public_key, signing_key=None):
        if len(encryption_key) != 32:
            raise ArchiveError("Archive AES-256 key must contain exactly 32 bytes")
        self.config, self.objects, self.key = config, objects, encryption_key
        self.public = Ed25519PublicKey.from_public_bytes(public_key)
        self.signing = Ed25519PrivateKey.from_private_bytes(signing_key) if signing_key else None
        if self.signing and self.signing.public_key().public_bytes_raw() != public_key:
            raise ArchiveError("Archive signing key does not match independently pinned public key")
        self.archive_id = sha(canonical({"endpoint": config.endpoint, "bucket": config.bucket, "prefix": config.prefix, "signer": sha(public_key), "encryption_key": sha(encryption_key)}))

    def empty(self):
        return {"format": 1, "archive_id": self.archive_id, "tenant_id": self.config.tenant_id, "sequence": 0, "digest": ZERO, "manifest_sha256": ZERO, "manifest_first": 0}

    def commit_key(self, first):
        return self.config.prefix + f"/commits/{first:020d}.json"

    def _open_manifest(self, raw):
        try:
            signed = json.loads(raw)
            if set(signed) != {"body", "signature"}:
                raise ValueError()
            body = signed["body"]
            if set(body) != {"format", "archive_id", "tenant_id", "first", "last", "previous_hash", "last_hash", "previous_manifest_sha256", "events_sha256", "object_key", "created_at"} or body["format"] != 1 or body["archive_id"] != self.archive_id or body["tenant_id"] != self.config.tenant_id:
                raise ValueError()
            self.public.verify(base64.b64decode(signed["signature"], validate=True), canonical(body))
            if canonical(signed) != raw:
                raise ValueError()
            return body
        except Exception:
            raise ArchiveError("Archive manifest signature or format verification failed") from None

    def _open_events(self, body):
        expected = self.config.prefix + "/segments/" + body["events_sha256"] + ".aes256gcm"
        if body["object_key"] != expected:
            raise ArchiveError("Archive manifest object scope is invalid")
        raw = self.objects.get(expected)
        if raw is None:
            raise ArchiveError("Committed archive segment is missing")
        try:
            envelope = json.loads(raw)
            if set(envelope) != {"format", "nonce", "ciphertext"} or envelope["format"] != 1:
                raise ValueError()
            plaintext = AESGCM(self.key).decrypt(base64.b64decode(envelope["nonce"], validate=True), base64.b64decode(envelope["ciphertext"], validate=True), (self.archive_id + expected).encode())
            events = json.loads(plaintext)
            if sha(plaintext) != body["events_sha256"] or canonical(events) != plaintext or not isinstance(events, list) or not events:
                raise ValueError()
            sequence, digest = check_events(events, self.config.tenant_id, body["first"] - 1, body["previous_hash"])
            if sequence != body["last"] or digest != body["last_hash"] or len(events) > 100:
                raise ValueError()
            return events
        except Exception:
            raise ArchiveError("Archive ciphertext, event chain or content hash verification failed") from None

    def read_next(self, cursor):
        raw = self.objects.get(self.commit_key(cursor["sequence"] + 1))
        if raw is None:
            return None
        body = self._open_manifest(raw)
        if body["first"] != cursor["sequence"] + 1 or body["previous_hash"] != cursor["digest"] or body["previous_manifest_sha256"] != cursor["manifest_sha256"]:
            raise ArchiveError("Archive commit sequence or predecessor mismatch")
        events = self._open_events(body)
        return events, {**self.empty(), "sequence": body["last"], "digest": body["last_hash"], "manifest_sha256": sha(raw), "manifest_first": body["first"]}

    def append(self, cursor, events):
        if not self.signing or not events or len(events) > self.config.batch_size:
            raise ArchiveError("Archive append requires a signer and a bounded nonempty batch")
        sequence, digest = check_events(events, self.config.tenant_id, cursor["sequence"], cursor["digest"])
        plaintext = canonical(events)
        object_key = self.config.prefix + "/segments/" + sha(plaintext) + ".aes256gcm"
        nonce = os.urandom(12)
        encrypted = canonical({"format": 1, "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(AESGCM(self.key).encrypt(nonce, plaintext, (self.archive_id + object_key).encode())).decode()})
        self.objects.create(object_key, encrypted)
        body = {"format": 1, "archive_id": self.archive_id, "tenant_id": self.config.tenant_id, "first": cursor["sequence"] + 1, "last": sequence, "previous_hash": cursor["digest"], "last_hash": digest, "previous_manifest_sha256": cursor["manifest_sha256"], "events_sha256": sha(plaintext), "object_key": object_key, "created_at": utcnow()}
        # Check the winning ciphertext before publishing its signed commit.
        self._open_events(body)
        signed = canonical({"body": body, "signature": base64.b64encode(self.signing.sign(canonical(body))).decode()})
        self.objects.create(self.commit_key(body["first"]), signed)
        winner = self.read_next(cursor)
        if winner is None:
            raise ArchiveError("Archive commit missing after conditional write")
        winner_events, advanced = winner
        # A concurrent worker can commit a shorter/longer valid prefix. Never
        # adopt different source evidence, even with the same signing authority.
        if winner_events[:len(events)] != events[:len(winner_events)]:
            raise ArchiveError("Concurrent archive commit disagrees with database evidence")
        return winner_events, advanced

    def verify_cursor(self, cursor):
        if set(cursor) != set(self.empty()) or any(cursor[key] != self.empty()[key] for key in ("format", "archive_id", "tenant_id")) or type(cursor["sequence"]) is not int or cursor["sequence"] < 0:
            raise ArchiveError("Local cursor belongs to another archive or has an invalid format")
        if cursor["sequence"] == 0:
            if cursor != self.empty():
                raise ArchiveError("Invalid genesis cursor")
            return
        raw = self.objects.get(self.commit_key(cursor["manifest_first"]))
        if raw is None or sha(raw) != cursor["manifest_sha256"]:
            raise ArchiveError("Previously checkpointed archive tail was removed or changed")
        body = self._open_manifest(raw)
        self._open_events(body)
        if body["last"] != cursor["sequence"] or body["last_hash"] != cursor["digest"]:
            raise ArchiveError("Local cursor does not match its retained archive commit")

    def export(self, trusted=None):
        cursor, events = self.empty(), []
        # An independently accepted checkpoint defines a stable snapshot. New
        # commits may arrive during owner review; restore only that trusted end.
        while trusted is None or cursor["sequence"] < trusted["sequence"]:
            next_segment = self.read_next(cursor)
            if next_segment is None:
                break
            part, cursor = next_segment
            events.extend(part)
        if trusted is not None and trusted != cursor:
            raise ArchiveError("Independent trusted checkpoint differs: possible missing tail or replacement")
        return {"tenant_id": self.config.tenant_id, "events": events, "head": {"sequence": cursor["sequence"], "digest": cursor["digest"]}}, cursor


def export_once(archive, source, cursor_path, *, trusted_checkpoint=None, health_path=None):
    path = Path(cursor_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "wb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArchiveError("Another exporter holds this persistent cursor") from None
        result = _export_locked(archive, source, cursor_path, trusted_checkpoint=trusted_checkpoint)
        if health_path is not None:
            # This path shares the cursor's lock and survives an interrupted
            # fsync/rename without concealing a later successful export.
            health_path = Path(health_path)
            health_path.with_name(health_path.name + ".pending").unlink(missing_ok=True)
            atomic_private(health_path, canonical(result))
        return result


def _export_locked(archive, source, cursor_path, *, trusted_checkpoint=None):
    path = Path(cursor_path)
    # A leftover fsync temporary file is not a committed cursor. Remote commit
    # records recover a crash between upload, checkpoint and local rename.
    path.with_name(path.name + ".pending").unlink(missing_ok=True)
    cursor = json.loads(path.read_bytes()) if path.exists() else archive.empty()
    archive.verify_cursor(cursor)
    if trusted_checkpoint is not None:
        # A separately retained expected head survives loss of both this PVC
        # and a remote tail. Verify the anchored manifest history in bounded
        # segments; do not accumulate decrypted tenant history in memory.
        archive.verify_cursor(trusted_checkpoint)
        anchored = archive.empty()
        while anchored["sequence"] < trusted_checkpoint["sequence"]:
            found = archive.read_next(anchored)
            if found is None:
                raise ArchiveError("Independently checkpointed archive history is missing")
            _, anchored = found
        if anchored != trusted_checkpoint:
            raise ArchiveError("Independent checkpoint differs from retained manifest history")
    with source.snapshot() as snapshot:
        if trusted_checkpoint is not None and trusted_checkpoint["sequence"]:
            expected = trusted_checkpoint
            boundary = list(snapshot.events(expected["sequence"] - 1, 1))
            if expected["sequence"] > snapshot.head["sequence"] or not boundary or boundary[0]["hash"] != expected["digest"]:
                raise ArchiveError("Database differs from independently retained checkpoint")
        if cursor["sequence"] > snapshot.head["sequence"]:
            raise ArchiveError("Database is behind independently archived evidence")
        if cursor["sequence"]:
            boundary = list(snapshot.events(cursor["sequence"] - 1, 1))
            if not boundary or boundary[0]["hash"] != cursor["digest"]:
                raise ArchiveError("Database differs at independently archived boundary")
        while cursor["sequence"] < snapshot.head["sequence"]:
            existing = archive.read_next(cursor)
            if existing:
                archived_events, advanced = existing
                current = list(snapshot.events(cursor["sequence"], len(archived_events)))
                if current != archived_events:
                    raise ArchiveError("Database differs from retained archive evidence")
            else:
                batch = list(snapshot.events(cursor["sequence"], archive.config.batch_size))
                while len(batch) > 1 and len(canonical(batch)) > MAX_OBJECT // 2:
                    batch = batch[:len(batch) // 2]
                archived_events, advanced = archive.append(cursor, batch)
                if list(snapshot.events(cursor["sequence"], len(archived_events))) != archived_events:
                    raise ArchiveError("Concurrent archive commit is outside the verified database snapshot")
            cursor = advanced
            atomic_private(path, canonical(cursor))
        # A remote commit may have succeeded before a lost cursor rename. An
        # internally consistent database rollback to that older local cursor
        # must not hide the independently committed tail (including genesis).
        # A concurrent newer export can conservatively fail this snapshot and
        # retry against a fresh database snapshot on the next scheduled run.
        if archive.read_next(cursor) is not None:
            raise ArchiveError("Archive contains committed evidence beyond the verified database snapshot")
        return {"status": "archive_current", "checked_at": utcnow(), "database_sequence": snapshot.head["sequence"], "archived_sequence": cursor["sequence"], "lag_events": 0, "checkpoint": cursor}


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("export", "restore"))
    value.add_argument("--config", type=Path, required=True)
    value.add_argument("--credentials", type=Path, required=True)
    value.add_argument("--encryption-key", type=Path, required=True)
    value.add_argument("--public-key", type=Path, required=True)
    value.add_argument("--signing-key", type=Path)
    value.add_argument("--database-url-file", type=Path)
    value.add_argument("--cursor", type=Path)
    value.add_argument("--trusted-checkpoint", type=Path)
    value.add_argument("--health-file", type=Path)
    value.add_argument("--output", type=Path)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = ArchiveConfig.load(args.config)
        archive = Archive(config, S3Objects(config, args.credentials, writable=args.command == "export"), private_bytes(args.encryption_key), private_bytes(args.public_key), private_bytes(args.signing_key) if args.signing_key else None)
        if args.command == "export":
            if not args.database_url_file or not args.cursor or not args.signing_key:
                raise ArchiveError("Export requires a separate database URL, signer and persistent cursor path")
            database_url = private_bytes(args.database_url_file).decode().strip()
            trusted = json.loads(private_bytes(args.trusted_checkpoint)) if args.trusted_checkpoint else None
            result = export_once(archive, DatabaseSource(database_url, config.tenant_id), args.cursor, trusted_checkpoint=trusted, health_path=args.health_file)
        else:
            if not args.output or not args.trusted_checkpoint:
                raise ArchiveError("Owner restore requires a private output path and independently retained trusted checkpoint")
            if args.output.exists():
                raise ArchiveError("Refusing to replace an existing private evidence export")
            export, checkpoint = archive.export(json.loads(private_bytes(args.trusted_checkpoint)))
            atomic_private(args.output, canonical(export))
            result = {"status": "archive_verified_export", "sequence": checkpoint["sequence"], "export_sha256": sha(canonical(export)), "independently_anchored": True, "checked_at": utcnow()}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Exceptions from SDK/database may contain signed headers, connection
        # strings or student values. Operators receive a bounded failure signal.
        print(json.dumps({"status": "archive_failed", "checked_at": utcnow(), "action": "Investigate private archive connectivity, configuration and integrity; cursor was not advanced past verified commits"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
