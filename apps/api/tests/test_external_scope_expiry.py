"""Exact expiry and foreign-school/tenant boundaries for external resources."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from academy import external_review
from academy.db import Audit, Record, canonical, uid
from academy.external_review import external_documents
from academy.permissions import bind_scope
from conftest import login
from test_curated_lessons import curate, selection as curated_selection
from test_external_lessons import setup as reviewed_course
from test_external_review import external_environment, submit
from test_lesson_tutoring import ask, selection as qa_selection


def freeze(monkeypatch, at):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    monkeypatch.setattr(external_review, "datetime", FrozenDatetime)


@pytest.mark.parametrize("activity", ["qa", "curated"])
def test_exact_expiry_withdraws_source_playback_corpus_and_history_without_rewriting_evidence(client, app, monkeypatch, activity):
    env, auditor, resources = reviewed_course(client, app, monkeypatch, video=True)
    resource = next(item for item in resources if item["kind"] == "youtube")
    document_id = "external:" + resource["id"]
    monkeypatch.setattr(app.state, "classify", lambda *_: qa_selection(document_id) if activity == "qa" else curated_selection(document_id))
    send = ask if activity == "qa" else curate
    released = send(client, env).json()
    assert released["status"] == "released", released
    with app.state.database.factory() as db:
        original_turn = db.get(Record, released["id"]).data
        original_snapshot = db.get(Record, resource["snapshot_id"]).data
        publication = db.scalar(select(Record).where(Record.kind == "external_publication", Record.parent_id == resource["id"]))
        original_publication = publication.data
        publication_id = publication.id
        expires = datetime.fromisoformat(publication.value["expires_at"])
        original_audit = [(row.id, row.payload, row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]

    # Expiry is exclusive: the last microsecond remains authorized.
    freeze(monkeypatch, expires - timedelta(microseconds=1))
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 200
    assert client.post(f"/api/external-resources/{resource['id']}/playback", headers=env["headers"]).status_code == 200
    assert send(client, env).json()["status"] == "released"

    # At exactly the stored expiry, no new protected access may expose it.
    freeze(monkeypatch, expires)
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 404
    assert client.post(f"/api/external-resources/{resource['id']}/playback", headers=env["headers"]).status_code == 404
    assert all(item["id"] != resource["id"] for item in client.get(f"/api/courses/{env['course']['id']}/external-resources").json()["resources"])
    with app.state.database.factory() as db:
        course = db.get(Record, env["course"]["id"])
        assert all(item["id"] != document_id for item in external_documents(db, course, env["school"]))
        from academy.standards import snapshot_material
        documents = snapshot_material(db, course, env["school"])["documents"]
        assert all(item["id"] != document_id for item in documents)

    withheld = send(client, env).json()
    assert withheld["status"] == "withheld" and withheld["approved_text"] is None and withheld["citations"] == []
    if activity == "curated":
        assert withheld["lesson"] is None
        assert client.post(f"/api/courses/{env['course']['id']}/curated-lessons/{released['id']}/progress", json={"section_id": "section-1", "event": "viewed"}, headers=env["headers"]).status_code == 409
    route = "teacher-tutor" if activity == "qa" else "curated-lessons"
    assert client.get(f"/api/courses/{env['course']['id']}/{route}").json() == [withheld]
    with app.state.database.factory() as db:
        assert db.get(Record, released["id"]).data == original_turn
        assert db.get(Record, resource["snapshot_id"]).data == original_snapshot
        assert db.get(Record, publication_id).data == original_publication
        current_audit = [(row.id, row.payload, row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert current_audit[:len(original_audit)] == original_audit
    login(client, "demo-admin")
    replay = client.get(f"/api/audit/sessions/{released['session_id']}")
    assert replay.status_code == 200 and document_id in replay.text


def test_foreign_school_manager_cannot_read_assign_or_review_other_school_material(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    response = client.post("/api/schools", json={"name": "Separate synthetic school"}, headers=env["admin"])
    assert response.status_code == 201
    other_school = response.json()["id"]
    course = client.post("/api/courses", json={"school_id": other_school, "title": "Other school class"}, headers=env["admin"])
    assert course.status_code == 201, course.text
    foreign = submit(client, {**env, "course": course.json()["id"]})
    headers = login(client, "demo-school")
    assert client.get(f"/api/schools/{other_school}/audit-agent").status_code == 403
    assert client.put(f"/api/schools/{other_school}/audit-agent", json=env["config"], headers=headers).status_code == 403
    assert client.get(f"/api/schools/{other_school}/external-review").status_code == 403
    assert client.get(f"/api/courses/{course.json()['id']}/external-resources").status_code == 403
    assert client.get(f"/api/external-resources/{foreign['id']}").status_code == 403
    assert client.post(f"/api/external-resources/{foreign['id']}/review", json={"expected_version": 1, "idempotency_key": "foreign-school-review-12345"}, headers=headers).status_code == 403
    assert client.post(f"/api/external-resources/{foreign['id']}/revoke", json={"expected_version": 1, "reason": "Unauthorized request"}, headers=headers).status_code == 403
    assert env["calls"] == []


def test_same_tenant_manager_cannot_assign_other_schools_model_connection(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    response = client.post("/api/schools", json={"name": "Separate synthetic school"}, headers=env["admin"])
    other_school = response.json()["id"]
    response = client.put(f"/api/schools/{other_school}/audit-agent", json=env["config"], headers=env["admin"])
    assert response.status_code == 403
    with app.state.database.factory() as db:
        assert not db.get(Record, other_school).value.get("external_audit_required")
        assert not list(db.scalars(select(Record).where(Record.kind == "external_audit_agent", Record.parent_id == other_school)))


def test_foreign_tenant_school_course_resource_and_agent_ids_never_confer_access(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    foreign_school, foreign_course, foreign_resource = uid(), uid(), uid()
    with app.state.database.write() as db:
        rows = [
            Record(id=foreign_school, tenant_id="foreign-tenant", kind="school", data=canonical({"name": "Private foreign school"})),
            Record(id=foreign_course, tenant_id="foreign-tenant", kind="course", data=canonical({"title": "Private foreign class"})),
            Record(id=foreign_resource, tenant_id="foreign-tenant", kind="external_resource", parent_id=foreign_course, data=canonical({"version": 1, "snapshot_id": resource["snapshot_id"]})),
        ]
        for row in rows:
            db.add(row)
            db.flush()
        bind_scope(db, rows[1], foreign_school)
        bind_scope(db, rows[2], foreign_school)
    assert client.get(f"/api/schools/{foreign_school}/audit-agent").status_code == 403
    assert client.put(f"/api/schools/{foreign_school}/audit-agent", json=env["config"], headers=env["admin"]).status_code == 403
    assert client.get(f"/api/schools/{foreign_school}/external-review").status_code == 403
    assert client.get(f"/api/courses/{foreign_course}/external-resources").status_code == 404
    assert client.get(f"/api/external-resources/{foreign_resource}").status_code == 404
    assert client.post(f"/api/external-resources/{foreign_resource}/review", json={"expected_version": 1, "idempotency_key": "foreign-tenant-review-12345"}, headers=env["admin"]).status_code == 404
    assert foreign_resource not in canonical(client.get(f"/api/schools/{env['school']}/external-review").json())
    assert env["calls"] == []
