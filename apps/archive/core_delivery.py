"""Deliver immutable, metadata-only Academy outbox events to Core Data.

Uses its own database and Core workload credentials. No API imports, PII keys,
provider calls, student credentials, ambient proxies or automatic inference retry.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import ssl
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


MAX_RESPONSE = 16384
HEX = re.compile(r"[a-f0-9]{64}\Z")
REFERENCES = {"tenant_ref", "event_ref", "session_ref", "school_ref", "course_ref", "teacher_ref", "teacher_profile_ref", "lesson_turn_ref", "homework_session_ref", "homework_message_ref", "operation_ref"}
SNAPSHOTS = {"model_snapshot", "policy_snapshot", "integration_snapshot", "teacher_snapshot", "corpus_snapshot", "routing_snapshot", "configuration_snapshot", "grounding_snapshot", "policy", "model", "triage"}
STATES = {"admitted", "pending", "sent", "completed", "released", "refused", "blocked", "failed", "withheld", "uncertain", "not_sent", "clarification", "ready", "unavailable", "cancelled", "approved", "revoked"}
USAGE = {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens"}


class DeliveryError(ValueError):
    pass


class TransportError(DeliveryError):
    def __init__(self, kind, http_status=0):
        self.kind, self.http_status = kind, http_status
        super().__init__("Core metadata transport did not confirm delivery")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def private_file(path, limit=8192):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > limit:
        raise DeliveryError("Missing or oversized mounted worker input")
    return path.read_text()


@dataclass(frozen=True)
class DeliveryConfig:
    tenant_id: str
    core_origin: str
    allowed_origins: tuple[str, ...]
    pipeline_slug: str
    service_id: str
    environment: str
    ca_bundle: str
    batch_size: int = 50
    lease_seconds: int = 120

    def __post_init__(self):
        origin = urlsplit(self.core_origin)
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment or self.core_origin not in self.allowed_origins:
            raise DeliveryError("Core must be an explicitly allowed exact HTTPS origin")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.tenant_id) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.service_id) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.pipeline_slug):
            raise DeliveryError("Invalid worker organization, service or pipeline binding")
        if self.environment not in {"production", "development", "staging", "test"} or type(self.batch_size) is not int or not 1 <= self.batch_size <= 100 or type(self.lease_seconds) is not int or not 60 <= self.lease_seconds <= 300:
            raise DeliveryError("Invalid bounded worker configuration")
        if not Path(self.ca_bundle).is_absolute() or not Path(self.ca_bundle).is_file():
            raise DeliveryError("A mounted trusted TLS CA bundle is required")

    @property
    def destination(self):
        return sha(canonical({"organization": self.tenant_id, "origin": self.core_origin, "pipeline": self.pipeline_slug, "service": self.service_id, "environment": self.environment}))

    @classmethod
    def load(cls, path):
        value = json.loads(private_file(path))
        value["allowed_origins"] = tuple(value["allowed_origins"])
        return cls(**value)


def validate_metadata(row):
    encoded = row["metadata_json"]
    if not isinstance(encoded, str) or len(encoded.encode()) > 8192 or sha(encoded) != row["metadata_sha256"]:
        raise DeliveryError("Outbox checksum or size is invalid")
    value = json.loads(encoded)
    required = {"schema_version", "audit_sequence", "audit_sha256", "previous_audit_sha256", "event_type", "occurred_at", "tenant_ref", "event_ref"}
    allowed = required | REFERENCES | {"state", "phase", "usage", "snapshot_sha256"}
    if canonical(value) != encoded or not isinstance(value, dict) or not required <= set(value) or set(value) - allowed or type(value["schema_version"]) is not int or value["schema_version"] != 1 or type(value["audit_sequence"]) is not int or value["audit_sequence"] != row["audit_sequence"] or value["audit_sequence"] < 1:
        raise DeliveryError("Unexpected Core metadata schema")
    if not isinstance(value["event_type"], str) or not re.fullmatch(r"[a-z][a-z0-9_.]{0,59}", value["event_type"]):
        raise DeliveryError("Invalid audit event type")
    if datetime.fromisoformat(value["occurred_at"]).tzinfo is None:
        raise DeliveryError("Invalid audit timestamp")
    for key in (REFERENCES | {"audit_sha256", "previous_audit_sha256"}) & set(value):
        if not isinstance(value[key], str) or not HEX.fullmatch(value[key]):
            raise DeliveryError("Nonopaque Core audit reference")
    expected_event = "academy-audit-" + sha(canonical([row["tenant_id"], row["audit_id"]]))
    if row["id"] != expected_event or value["tenant_ref"] != sha(canonical([row["tenant_id"], "tenant"])) or value["event_ref"] != sha(canonical([row["tenant_id"], "audit", row["audit_id"]])) or value.get("school_ref", "") != row["school_ref"]:
        raise DeliveryError("Outbox identity or scope changed")
    if "state" in value and (not isinstance(value["state"], str) or value["state"] not in STATES):
        raise DeliveryError("Invalid audit state")
    if "phase" in value and (not isinstance(value["phase"], str) or value["phase"] not in {"triage", "tutor", "safety"}):
        raise DeliveryError("Invalid provider phase")
    for field, allowed_fields in (("usage", USAGE), ("snapshot_sha256", SNAPSHOTS)):
        if field in value:
            entries = value[field]
            if not isinstance(entries, dict) or not entries or set(entries) - allowed_fields:
                raise DeliveryError("Invalid Core metadata nested schema")
            for item in entries.values():
                if field == "usage" and (type(item) is not int or not 0 <= item <= 10**12):
                    raise DeliveryError("Invalid usage scalar")
                if field == "snapshot_sha256" and (not isinstance(item, str) or not HEX.fullmatch(item)):
                    raise DeliveryError("Invalid snapshot checksum")
    return value


class CorePipeline:
    """Fixed endpoints, verified TLS, bounded responses, zero redirect/retry logic."""
    def __init__(self, config, credentials_file, *, connection_factory=None):
        self.config = config
        credentials = json.loads(private_file(credentials_file))
        if not isinstance(credentials, dict) or set(credentials) != {"token"} or not isinstance(credentials["token"], str) or not 32 <= len(credentials["token"]) <= 256 or not re.fullmatch(r"[A-Za-z0-9_.~-]+", credentials["token"]) or credentials["token"].startswith("ca_"):
            raise DeliveryError("A separate Core workload credential is required")
        self.token = credentials["token"]
        self.context = ssl.create_default_context(cafile=config.ca_bundle)
        self.connection_factory = connection_factory or http.client.HTTPSConnection

    def _request(self, method, path, body=None):
        paths = {("GET", "/api/platform/v1/integration/"), ("POST", "/api/platform/v1/pipelines/" + self.config.pipeline_slug + "/events/")}
        if (method, path) not in paths:
            raise DeliveryError("Core request escaped its exact allowed operation")
        origin = urlsplit(self.config.core_origin)
        connection = self.connection_factory(origin.hostname, port=origin.port or 443, timeout=5, context=self.context)
        try:
            connection.request(method, path, body=canonical(body).encode() if body is not None else None,
                headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", "Accept": "application/json", "X-Core-Purpose": "academy-audit-metadata"})
            if connection.sock is not None:
                connection.sock.settimeout(15)
            response = connection.getresponse()
            status = response.status
            if status in {401, 403, 404}:
                raise TransportError("authority_denied", status)
            if status == 409:
                raise TransportError("conflict", status)
            if status not in {200, 201}:
                raise TransportError("retryable" if status in {408, 425, 429} or status >= 500 else "rejected", status)
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE):
                raise TransportError("invalid_response", status)
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise TransportError("invalid_response", status)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise TransportError("invalid_response", status) from None
            if not isinstance(value, dict):
                raise TransportError("invalid_response", status)
            return status, value
        except (OSError, http.client.HTTPException):
            # The remote commit may have happened. Only this metadata event's
            # stable idempotency key can be retried; never retry an inference.
            raise TransportError("uncertain") from None
        finally:
            connection.close()

    def verify_authority(self):
        _, value = self._request("GET", "/api/platform/v1/integration/")
        if value.get("organization_id") != self.config.tenant_id or value.get("service_id") != self.config.service_id or value.get("environment") != self.config.environment or not isinstance(value.get("capabilities"), list) or "data.write" not in value["capabilities"]:
            raise TransportError("authority_denied")

    def publish(self, row):
        metadata = validate_metadata(row)
        status, value = self._request("POST", "/api/platform/v1/pipelines/" + self.config.pipeline_slug + "/events/", {"event_id": row["id"], "data": metadata})
        if set(value) != {"record_id", "created"} or type(value["record_id"]) is not int or not 1 <= value["record_id"] <= 2**63 - 1 or type(value["created"]) is not bool or value["created"] != (status == 201):
            raise TransportError("invalid_response", status)
        return {"outcome": "created" if value["created"] else "duplicate", "http_status": status, "core_record_id": value["record_id"]}


class DeliveryStore:
    def __init__(self, database_url, config, *, local_test=False):
        url = make_url(database_url)
        if url.get_backend_name() != "postgresql" or url.query.get("sslmode") != "verify-full" or not url.query.get("sslrootcert"):
            if not (local_test and url.get_backend_name() == "sqlite"):
                raise DeliveryError("Dispatcher database requires separate PostgreSQL credentials with verify-full TLS")
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args={"timeout": 30} if url.get_backend_name() == "sqlite" else {})
        self.config = config

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            try:
                if self.engine.dialect.name == "sqlite":
                    connection.execute(text("BEGIN IMMEDIATE"))
                else:
                    connection.execute(text("SET LOCAL search_path = public"))
                    connection.execute(text("SET LOCAL statement_timeout = '15s'"))
                    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def verify_privileges(self):
        if self.engine.dialect.name == "sqlite":
            return
        expected = {"audit_delivery_outbox": {"SELECT"}, "audit_delivery_state": {"SELECT", "UPDATE"}, "audit_delivery_receipts": {"SELECT", "INSERT"}}
        with self.transaction() as connection:
            bad = connection.execute(text("SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid) OR EXISTS(SELECT 1 FROM pg_class WHERE relowner=pg_roles.oid) OR has_database_privilege(current_user,current_database(),'CREATE,TEMPORARY') OR has_schema_privilege(current_user,'public','CREATE') FROM pg_roles WHERE rolname=current_user")).scalar_one()
            if bad:
                raise DeliveryError("Dispatcher database identity has elevated or ownership authority")
            tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars().all()
            for table in tables:
                granted = {privilege for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER") if connection.execute(text("SELECT has_table_privilege(current_user,:table,:privilege)"), {"table": "public." + table, "privilege": privilege}).scalar_one()}
                if granted != expected.get(table, set()):
                    raise DeliveryError("Dispatcher database permissions exceed the exact metadata boundary")
            if not set(expected) <= set(tables):
                raise DeliveryError("Dispatcher schema migration is missing")

    def claim(self, *, clock=None):
        epoch = int(time.time() if clock is None else clock)
        with self.transaction() as connection:
            suffix = " FOR UPDATE OF s SKIP LOCKED" if self.engine.dialect.name == "postgresql" else ""
            row = connection.execute(text("SELECT o.*,s.attempts,s.destination_sha256,s.status AS delivery_status FROM audit_delivery_outbox o JOIN audit_delivery_state s ON s.event_id=o.id AND s.tenant_id=o.tenant_id WHERE o.tenant_id=:tenant AND ((s.status IN ('pending','retry') AND s.next_attempt_at<=:epoch) OR (s.status='leased' AND s.lease_until<=:epoch)) ORDER BY o.audit_sequence LIMIT 1" + suffix), {"tenant": self.config.tenant_id, "epoch": epoch}).mappings().first()
            if row is None:
                return None
            row = dict(row)
            if row["destination_sha256"] not in {"", self.config.destination}:
                raise DeliveryError("Outbox delivery is pinned to another destination")
            if type(row["attempts"]) is not int or not 0 <= row["attempts"] < 10**9:
                raise DeliveryError("Outbox attempt counter is invalid")
            validate_metadata(row)
            if row["delivery_status"] == "leased":
                # Retain the abandoned attempt before replacing its lease.
                # It may have reached Core; the new owner reuses the same ID.
                connection.execute(text("INSERT INTO audit_delivery_receipts (id,tenant_id,event_id,attempt,outcome,http_status,core_record_id,destination_sha256,created_at) VALUES (:id,:tenant,:event,:attempt,'lease_expired',0,0,:destination,:created)"), {"id": uuid4().hex, "tenant": self.config.tenant_id, "event": row["id"], "attempt": row["attempts"], "destination": self.config.destination, "created": utcnow()})
            lease = uuid4().hex
            changed = connection.execute(text("UPDATE audit_delivery_state SET status='leased',attempts=attempts+1,lease_token=:lease,lease_until=:until,destination_sha256=:destination,updated_at=:updated WHERE event_id=:event AND tenant_id=:tenant"), {"lease": lease, "until": epoch + self.config.lease_seconds, "destination": self.config.destination, "updated": utcnow(), "event": row["id"], "tenant": self.config.tenant_id})
            if changed.rowcount != 1:
                raise DeliveryError("Metadata lease admission failed")
            return {**row, "attempts": row["attempts"] + 1, "lease_token": lease}

    def finish(self, row, outcome, *, clock=None):
        epoch = int(time.time() if clock is None else clock)
        allowed = {"created", "duplicate", "uncertain", "retryable", "authority_denied", "conflict", "rejected", "invalid_response"}
        if outcome["outcome"] not in allowed:
            raise DeliveryError("Unknown bounded delivery outcome")
        successful = outcome["outcome"] in {"created", "duplicate"}
        blocked = outcome["outcome"] in {"authority_denied", "conflict", "rejected"}
        status = "delivered" if successful else "blocked" if blocked else "retry"
        with self.transaction() as connection:
            changed = connection.execute(text("UPDATE audit_delivery_state SET status=:status,next_attempt_at=:next,lease_token='',lease_until=0,updated_at=:updated WHERE event_id=:event AND tenant_id=:tenant AND status='leased' AND lease_token=:lease AND destination_sha256=:destination"), {"status": status, "next": 0 if successful or blocked else epoch + min(3600, 2 ** min(row["attempts"], 10) * 5), "updated": utcnow(), "event": row["id"], "tenant": self.config.tenant_id, "lease": row["lease_token"], "destination": self.config.destination})
            if changed.rowcount != 1:
                # A stale process may have returned after another lease owner.
                # It cannot overwrite that owner's state or invent a receipt.
                raise DeliveryError("Metadata lease was replaced before its outcome")
            connection.execute(text("INSERT INTO audit_delivery_receipts (id,tenant_id,event_id,attempt,outcome,http_status,core_record_id,destination_sha256,created_at) VALUES (:id,:tenant,:event,:attempt,:outcome,:http,:record,:destination,:created)"), {"id": uuid4().hex, "tenant": self.config.tenant_id, "event": row["id"], "attempt": row["attempts"], "outcome": outcome["outcome"], "http": outcome.get("http_status", 0), "record": outcome.get("core_record_id", 0), "destination": self.config.destination, "created": utcnow()})

    def resume_blocked(self):
        with self.transaction() as connection:
            changed = connection.execute(text("UPDATE audit_delivery_state SET status='retry',next_attempt_at=0,updated_at=:updated WHERE tenant_id=:tenant AND status='blocked' AND destination_sha256=:destination"), {"updated": utcnow(), "tenant": self.config.tenant_id, "destination": self.config.destination})
            return changed.rowcount

    def health(self, *, clock=None):
        epoch = int(time.time() if clock is None else clock)
        with self.transaction() as connection:
            counts = dict(connection.execute(text("SELECT status,COUNT(*) FROM audit_delivery_state WHERE tenant_id=:tenant GROUP BY status"), {"tenant": self.config.tenant_id}).all())
            oldest = connection.execute(text("SELECT MIN(o.created_at) FROM audit_delivery_outbox o JOIN audit_delivery_state s ON s.event_id=o.id WHERE o.tenant_id=:tenant AND s.status<>'delivered'"), {"tenant": self.config.tenant_id}).scalar_one()
            latest = connection.execute(text("SELECT MAX(created_at) FROM audit_delivery_receipts WHERE tenant_id=:tenant AND outcome IN ('created','duplicate')"), {"tenant": self.config.tenant_id}).scalar_one()
        age = max(0, epoch - int(datetime.fromisoformat(oldest).timestamp())) if oldest else 0
        return {"status": "metadata_delivery_blocked" if counts.get("blocked") else "metadata_delivery_pending" if oldest else "metadata_delivery_current", "counts": counts, "oldest_pending_seconds": age, "last_delivery_at": latest, "checked_at": utcnow()}


def deliver_once(store, pipeline, *, resume_blocked=False):
    store.verify_privileges()
    pipeline.verify_authority()
    if resume_blocked:
        store.resume_blocked()
    delivered = 0
    for _ in range(store.config.batch_size):
        row = store.claim()
        if row is None:
            break
        try:
            result = pipeline.publish(row)
        except TransportError as error:
            result = {"outcome": error.kind, "http_status": error.http_status}
        except Exception:
            # No remote body, SQL exception or student text enters a receipt.
            result = {"outcome": "uncertain", "http_status": 0}
        store.finish(row, result)
        if result["outcome"] not in {"created", "duplicate"}:
            break
        delivered += 1
    return {**store.health(), "delivered_this_run": delivered}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("deliver", "health"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--resume-blocked", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = DeliveryConfig.load(args.config)
        store = DeliveryStore(private_file(args.database_url_file).strip(), config)
        if args.command == "health":
            store.verify_privileges()
            result = store.health()
        else:
            if args.credentials is None:
                raise DeliveryError("Delivery requires a mounted Core workload credential")
            result = deliver_once(store, CorePipeline(config, args.credentials), resume_blocked=args.resume_blocked)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["counts"].get("blocked") or result["oldest_pending_seconds"] > 180 else 0
    except Exception:
        print(json.dumps({"status": "metadata_delivery_failed", "checked_at": utcnow(), "action": "Investigate private worker authority, configuration and retained delivery evidence"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
