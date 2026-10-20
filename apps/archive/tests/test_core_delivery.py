"""Synthetic Core Data contract, leases, outage recovery and metadata boundary."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
from core_delivery import CorePipeline, DeliveryConfig, DeliveryError, DeliveryStore, TransportError, canonical, deliver_once, validate_metadata
from academy.audit_delivery import enqueue_audit_metadata
from academy.db import Base, Database, audit


@pytest.fixture
def worker(tmp_path):
    # Trust system CA bytes solely to construct the SSLContext; network mocked.
    import certifi
    config = DeliveryConfig("synthetic-tenant", "https://core.example.test", ("https://core.example.test",), "academy-audit-metadata", "synthetic-service", "test", certifi.where(), batch_size=5)
    url = "sqlite:///" + str(tmp_path / "worker.db")
    database = Database(SimpleNamespace(database_url=url))
    Base.metadata.create_all(database.engine)
    store = DeliveryStore(url, config, local_test=True)
    with database.write() as db:
        row = audit(db, config.tenant_id, "private-student", "homework.response_recorded", {"question": "private synthetic text", "school_id": "school-a", "status": "released"}, "session-private")
        entry = enqueue_audit_metadata(db, row, {"question": "private synthetic text", "school_id": "school-a", "status": "released"})
        event_id = entry.id
    return store, database, event_id


class Pipeline:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.fail_after_commit = False
        self.authorized = True

    def verify_authority(self):
        if not self.authorized:
            raise TransportError("authority_denied", 403)

    def publish(self, row):
        self.calls.append(row["id"])
        metadata = validate_metadata(row)
        created = row["id"] not in self.values
        self.values.setdefault(row["id"], metadata)
        assert self.values[row["id"]] == metadata
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise TransportError("uncertain")
        return {"outcome": "created" if created else "duplicate", "http_status": 201 if created else 200, "core_record_id": 17}


def test_delivery_is_durable_without_transcript_and_is_not_resent_after_success(worker):
    store, _, event_id = worker
    pipeline = Pipeline()
    result = deliver_once(store, pipeline)
    assert result["status"] == "metadata_delivery_current"
    assert result["counts"] == {"delivered": 1}
    assert "private synthetic text" not in canonical(pipeline.values)
    assert "session-private" not in canonical(pipeline.values)
    assert pipeline.calls == [event_id]
    assert deliver_once(store, pipeline)["delivered_this_run"] == 0
    assert pipeline.calls == [event_id]
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT outcome FROM audit_delivery_receipts")).scalar_one() == "created"


def test_timeout_after_core_commit_retries_same_idempotency_key_only(worker):
    store, _, event_id = worker
    pipeline = Pipeline()
    pipeline.fail_after_commit = True
    assert deliver_once(store, pipeline)["counts"] == {"retry": 1}
    # Scheduled run before backoff does not contact Core again.
    assert deliver_once(store, pipeline)["delivered_this_run"] == 0
    with store.transaction() as connection:
        connection.execute(text("UPDATE audit_delivery_state SET next_attempt_at=0"))
    assert deliver_once(store, pipeline)["counts"] == {"delivered": 1}
    assert pipeline.calls == [event_id, event_id]
    assert len(pipeline.values) == 1
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT outcome FROM audit_delivery_receipts ORDER BY attempt")).scalars().all() == ["uncertain", "duplicate"]


def test_lost_process_lease_recovers_and_stale_owner_cannot_overwrite(worker):
    store, _, event_id = worker
    first = store.claim(clock=100)
    second = DeliveryStore(str(store.engine.url), store.config, local_test=True)
    assert second.claim(clock=101) is None
    recovered = second.claim(clock=221)
    assert first["id"] == recovered["id"] == event_id
    assert first["lease_token"] != recovered["lease_token"]
    with pytest.raises(DeliveryError, match="replaced"):
        store.finish(first, {"outcome": "created", "http_status": 201, "core_record_id": 17}, clock=222)
    second.finish(recovered, {"outcome": "duplicate", "http_status": 200, "core_record_id": 17}, clock=223)
    assert store.health()["counts"] == {"delivered": 1}
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT outcome FROM audit_delivery_receipts ORDER BY attempt")).scalars().all() == ["lease_expired", "duplicate"]


def test_revocation_and_conflict_block_without_following_other_events(worker):
    store, _, _ = worker
    pipeline = Pipeline()
    pipeline.authorized = False
    with pytest.raises(TransportError):
        deliver_once(store, pipeline)
    assert pipeline.calls == []
    assert store.health()["counts"] == {"pending": 1}
    pipeline.authorized = True
    pipeline.publish = lambda row: (_ for _ in ()).throw(TransportError("conflict", 409))
    assert deliver_once(store, pipeline)["counts"] == {"blocked": 1}
    assert store.claim() is None
    assert store.resume_blocked() == 1


def test_destination_is_pinned_before_send_and_cannot_change_on_recovery(worker):
    store, _, _ = worker
    store.claim(clock=1)
    changed = DeliveryStore(str(store.engine.url), replace(store.config, pipeline_slug="another-pipeline"), local_test=True)
    with pytest.raises(DeliveryError, match="another destination"):
        changed.claim(clock=122)


def test_metadata_checksum_and_schema_are_rechecked_in_worker(worker):
    store, _, _ = worker
    row = store.claim()
    changed = {**row, "metadata_json": row["metadata_json"].replace("homework.response_recorded", "homework.other_event")}
    with pytest.raises(DeliveryError, match="checksum"):
        validate_metadata(changed)
    value = json.loads(row["metadata_json"])
    value["question"] = "private synthetic text"
    encoded = canonical(value)
    changed = {**row, "metadata_json": encoded, "metadata_sha256": hashlib.sha256(encoded.encode()).hexdigest()}
    with pytest.raises(DeliveryError, match="schema"):
        validate_metadata(changed)


class Reply:
    def __init__(self, status, value=None, raw=None, length=None):
        self.status = status
        self.raw = raw if raw is not None else json.dumps(value).encode()
        self.length = str(len(self.raw)) if length is None else length

    def getheader(self, name):
        return self.length if name == "Content-Length" else None

    def read(self, count):
        return self.raw[:count]


class Connection:
    sock = None
    def __init__(self, reply):
        self.reply, self.requests, self.closed = reply, [], False

    def request(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.reply

    def close(self):
        self.closed = True


def transport(store, tmp_path, reply):
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"token": "synthetic-workload-token-never-live-000000"}))
    connection = Connection(reply)
    calls = []
    def factory(host, **kwargs):
        calls.append((host, kwargs))
        return connection
    pipeline = CorePipeline(store.config, credentials, connection_factory=factory)
    return pipeline, connection, calls


def test_core_wire_contract_is_exact_and_uses_verified_tls_without_proxies(worker, tmp_path, monkeypatch):
    store, _, event_id = worker
    monkeypatch.setenv("HTTPS_PROXY", "https://evil.example.test")
    pipeline, connection, calls = transport(store, tmp_path, Reply(201, {"record_id": 17, "created": True}))
    assert pipeline.publish(store.claim())["outcome"] == "created"
    assert calls[0][0] == "core.example.test"
    assert calls[0][1]["context"].verify_mode == ssl.CERT_REQUIRED
    assert calls[0][1]["context"].check_hostname
    method, path, body, headers = connection.requests[0]
    assert method == "POST"
    assert path == "/api/platform/v1/pipelines/academy-audit-metadata/events/"
    assert set(json.loads(body)) == {"event_id", "data"}
    assert json.loads(body)["event_id"] == event_id
    assert "private synthetic text" not in body.decode()
    assert headers["X-Core-Purpose"] == "academy-audit-metadata"
    assert connection.closed
    with pytest.raises(DeliveryError, match="escaped"):
        pipeline._request("POST", "https://evil.example.test/events/")


@pytest.mark.parametrize("reply,kind", [(Reply(302), "rejected"), (Reply(401), "authority_denied"), (Reply(403), "authority_denied"), (Reply(404), "authority_denied"), (Reply(409), "conflict"), (Reply(429), "retryable"), (Reply(503), "retryable"), (Reply(201, raw=b"x" * 16385), "invalid_response"), (Reply(201, {"record_id": True, "created": True}), "invalid_response"), (Reply(201, {"record_id": 3, "created": False}), "invalid_response"), (Reply(201, {"record_id": 3, "created": True, "question": "private"}), "invalid_response")])
def test_core_errors_redirects_and_unexpected_receipts_fail_closed(worker, tmp_path, reply, kind):
    store, _, _ = worker
    pipeline, connection, _ = transport(store, tmp_path, reply)
    with pytest.raises(TransportError) as error:
        pipeline.publish(store.claim())
    assert error.value.kind == kind
    assert len(connection.requests) == 1
    assert connection.closed


def test_preflight_checks_exact_tenant_service_environment_and_capability(worker, tmp_path):
    store, _, _ = worker
    valid = {"organization_id": store.config.tenant_id, "service_id": store.config.service_id, "environment": "test", "capabilities": ["data.write"]}
    pipeline, _, _ = transport(store, tmp_path, Reply(200, valid))
    pipeline.verify_authority()
    for field, value in (("organization_id", "foreign-tenant"), ("service_id", "foreign-service"), ("environment", "production"), ("capabilities", [])):
        pipeline, _, _ = transport(store, tmp_path, Reply(200, {**valid, field: value}))
        with pytest.raises(TransportError, match="did not confirm"):
            pipeline.verify_authority()


def test_production_database_refuses_sqlite_and_unverified_postgres(worker):
    store, _, _ = worker
    for url in ("sqlite:///:memory:", "postgresql://user:synthetic@db.example.test/academy", "postgresql://user:synthetic@db.example.test/academy?sslmode=require"):
        with pytest.raises(DeliveryError, match="verify-full"):
            DeliveryStore(url, store.config)


@pytest.mark.parametrize("origin", ["http://core.example.test", "https://core.example.test/", "https://user:pass@core.example.test", "https://core.example.test?key=secret", "https://other.example.test"])
def test_origin_allowlist_rejects_unsafe_configuration(worker, origin):
    store, _, _ = worker
    with pytest.raises(DeliveryError, match="exact HTTPS"):
        replace(store.config, core_origin=origin)
