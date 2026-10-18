"""Synthetic raw-store confidentiality, authority and archive-chain acceptance."""
import base64
import hashlib
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from academy.db import Audit, Database, Record, audit, audit_material, canonical, family_schema_id
from academy.permissions import default_school_id
from academy.pii_storage import ENVELOPE, audit_payload
from conftest import login


PASSWORD = "Synthetic learning password 2026!"
NAME = "Synthetic Private Learner"
WORK = "Private student evidence: my own observation of the school garden."


def create_student(client, app):
    headers = login(client, "demo-admin")
    response = client.post("/api/admin/family-accounts", headers=headers, json={
        "kind": "student", "username": "private.learner", "password": PASSWORD,
        "name": NAME, "date_of_birth": "2011-02-03", "notes": "Synthetic private support note",
        "school_id": default_school_id(app.state.settings.tenant_id),
        "academic_year": "2026–2027", "grade": "10", "course_ids": ["demo-course"],
        "must_change_password": False,
    })
    assert response.status_code == 201, response.text
    return response.json()


def student_login(client):
    response = client.post("/api/auth/password", json={"username": "private.learner", "password": PASSWORD}, headers={"Origin": "http://localhost:8090"})
    assert response.status_code == 200, response.text
    return {"Origin": "http://localhost:8090", "X-CSRF-Token": response.json()["csrf_token"]}


def test_local_account_and_educational_pii_encrypted_with_authorized_replay(client, app, monkeypatch):
    student = create_student(client, app)
    monkeypatch.setattr(app.state.auth, "core", lambda *a, **kw: pytest.fail("Local student contacted Core"))
    headers = student_login(client)
    assert client.get("/api/auth/me").json()["name"] == NAME
    assert client.put("/api/assignments/demo-assignment/draft", json={"content": WORK}, headers=headers).status_code == 200
    assert client.post("/api/assignments/demo-assignment/submit", json={"content": WORK}, headers=headers).status_code == 201
    assert client.get("/api/assignments/demo-assignment").json()["draft"]["content"] == WORK
    # Quotas, class links and optional providers cannot bypass the local boundary.
    for path in ("/api/chat/groups", "/api/courses/demo-course/classroom", "/api/me/google-classroom/connections"):
        assert client.get(path).status_code == 403
    with app.state.database.factory() as db:
        raw = " ".join(db.scalars(text("SELECT data FROM records"))) + " ".join(db.scalars(text("SELECT payload FROM audit_events")))
        for secret in (NAME, PASSWORD, "private.learner", "2011-02-03", "Synthetic private support note", WORK):
            assert secret not in raw
        owned = list(db.scalars(select(Record).where(Record.owner_id == student["id"], Record.kind.in_(["draft", "submission"]))))
        assert len(owned) == 2 and all(ENVELOPE in json.loads(row.data) for row in owned)
        assert all(row.value["content"] == WORK for row in owned)
        events = list(db.scalars(select(Audit).order_by(Audit.sequence)))
        encrypted = [row for row in events if row.actor_id == student["id"]]
        assert encrypted and all(ENVELOPE in json.loads(row.payload) for row in encrypted)
        assert any(audit_payload(row).get("content") == WORK for row in encrypted)
        previous = "0" * 64
        for row in events:
            assert row.previous_hash == previous
            assert row.hash == hashlib.sha256(canonical(audit_material(row)).encode()).hexdigest()
            previous = row.hash
    admin = login(client, "demo-admin")
    assert client.get("/api/audit/verify").json()["valid"] is True
    sessions = client.get("/api/audit/sessions").json()
    session = next(row for row in sessions if row["student_id"] == student["id"])
    replay = client.get("/api/audit/sessions/" + session["session_id"])
    assert replay.status_code == 200
    assert any(event.get("private_payload", {}).get("content") == WORK for event in replay.json()["events"])
    # Full chain exports preserve ciphertext hashes; they are not silently
    # rewritten as plaintext with invalid original evidence hashes.
    exported = client.get("/api/admin/audit/export").json()
    assert WORK not in canonical(exported)
    assert client.put("/api/admin/family-accounts/" + student["id"] + "/password", json={"new_password": "Another synthetic safe password!", "expected_version": student["version"], "must_change_password": True}, headers=admin).status_code == 200


def test_ciphertext_context_wrong_key_and_terminal_protection(client, app):
    student = create_student(client, app)
    headers = student_login(client)
    assert client.post("/api/assignments/demo-assignment/submit", json={"content": WORK}, headers=headers).status_code == 201
    with app.state.database.factory() as db:
        row = db.scalar(select(Record).where(Record.kind == "submission", Record.owner_id == student["id"]))
        identifier = row.id
        envelope = json.loads(row.data)[ENVELOPE]
        from academy.pii_storage import _purpose
        with pytest.raises(HTTPException):
            app.state.auth.family.decrypt(envelope, row.tenant_id, "different-record", _purpose(row))
        original = app.state.settings.pii_encryption_key
        app.state.settings.pii_encryption_key = base64.urlsafe_b64encode(b"X" * 32).decode()
        try:
            with pytest.raises(HTTPException):
                _ = row.value
        finally:
            app.state.settings.pii_encryption_key = original
        assert row.value["content"] == WORK
    with pytest.raises(Exception):
        with app.state.database.write() as db:
            db.execute(text("UPDATE records SET data = :data WHERE id = :id"), {"data": "{}", "id": identifier})


def test_schema10_requires_additive_owner_migration(client, app):
    from academy.migrate import migrate
    assert client.get("/api/ready").status_code == 200
    with app.state.database.write() as db:
        db.delete(db.get(Record, family_schema_id(app.state.settings.tenant_id)))
    assert client.get("/api/ready").status_code == 503
    migrate(app.state.settings)
    assert client.get("/api/ready").status_code == 200


def test_secondary_writer_cannot_store_local_work_without_crypto(client, app):
    student = create_student(client, app)
    secondary = Database(app.state.settings)
    try:
        with pytest.raises(HTTPException):
            with secondary.write() as db:
                db.add(Record(id="unencrypted-attempt", tenant_id=app.state.settings.tenant_id, kind="draft", parent_id="demo-assignment", owner_id=student["id"], data=canonical({"content": WORK})))
        with pytest.raises(HTTPException):
            with secondary.write() as db:
                audit(db, app.state.settings.tenant_id, student["id"], "student.test", {"content": WORK})
    finally:
        secondary.engine.dispose()


def test_encrypted_turn_preserves_pending_barrier_and_cold_lookup(client, app):
    student = create_student(client, app)
    identity = {"id": student["id"], "tenant_id": app.state.settings.tenant_id, "auth_provider": "academy"}
    key = "synthetic-private-recovery-key"
    with app.state.database.write() as db:
        row = Record(id="protected-turn", tenant_id=identity["tenant_id"], kind="lesson_turn", parent_id="demo-course", owner_id=student["id"], data=canonical({"status": "pending", "idempotency_key": key, "message": WORK}))
        db.add(row)
    with app.state.database.write() as db:
        from academy.homework_api import _linked_turn
        recovered = _linked_turn(db, identity, {"triage": {"course_id": "demo-course"}, "lesson_key": key})
        assert recovered.id == "protected-turn"
        assert key not in recovered.data and WORK not in recovered.data
        recovered.set({**recovered.value, "status": "released"})
    with pytest.raises(Exception):
        with app.state.database.write() as db:
            row = db.get(Record, "protected-turn")
            row.set({**row.value, "status": "pending"})
    with app.state.database.write() as db:
        row = Record(id="protected-proposal", tenant_id=identity["tenant_id"], kind="change_request", parent_id="demo-course", owner_id="synthetic-agent", data=canonical({"target_id": "protected-turn", "status": "pending", "payload": {"feedback": WORK}}))
        db.add(row)
        db.flush()
        audit(db, identity["tenant_id"], "synthetic-agent", "change.proposed", {"change_id": row.id, "payload": {"feedback": WORK}})
    with app.state.database.factory() as db:
        assert WORK not in db.get(Record, "protected-proposal").data
        event = db.scalar(select(Audit).where(Audit.event_type == "change.proposed"))
        assert WORK not in event.payload
        assert audit_payload(event)["payload"]["feedback"] == WORK
