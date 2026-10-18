"""Synthetic external publication: no live models, YouTube, student data or keys."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from academy import external_review
from academy.db import Audit, Record, canonical
from academy.external_review import document_fingerprint, external_documents, filter_reviewed_documents
from academy.permissions import bind_scope, default_school_id
from conftest import login


KEY = "synthetic-external-review-key-never-live"
VIDEO = "AbCdEfGhIj0"


def verdict(value="approve", risks=None):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps({"verdict": value, "risk_codes": risks or [], "coverage": "supplied_text"})}}], "usage": {"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}}


def metadata(title="Synthetic carbon lesson"):
    return {"video_id": VIDEO, "canonical_url": f"https://www.youtube.com/watch?v={VIDEO}", "title": title, "author_name": "Synthetic classroom", "author_url": "https://www.youtube.com/@synthetic", "width": 1280, "height": 720, "provider_name": "YouTube", "type": "video", "metadata_sha256": external_review.digest({"title": title}), "fetched_at": "2026-10-18T12:00:00+00:00"}


def external_environment(client, app, monkeypatch, assign=True, **policy):
    admin = login(client, "demo-admin")
    school = default_school_id(app.state.settings.tenant_id)
    response = client.post("/api/admin/connections", json={"school_id": school, "name": "Synthetic material auditor", "endpoint": "https://models.example.test/v1", "models": ["review-only"], "enabled": True, "api_key": KEY}, headers=admin)
    assert response.status_code == 201, response.text
    connection = response.json()
    config = {"name": "School public-material auditor", "connection_id": connection["id"], "model": "review-only", **policy}
    agent = client.put(f"/api/schools/{school}/audit-agent", json=config, headers=admin).json() if assign else None
    calls, fetches = [], []
    def classify(*args):
        calls.append(args)
        return verdict()
    def fetch(video):
        fetches.append(video)
        return metadata()
    monkeypatch.setattr(app.state, "classify", classify)
    monkeypatch.setattr(app.state, "fetch_youtube_metadata", fetch)
    app.state.settings.allow_live_models = True
    return {"school": school, "admin": admin, "connection": connection, "agent": agent, "config": config, "course": "demo-course", "calls": calls, "fetches": fetches}


def submit(client, env, kind="external_text", **fields):
    payload = {"kind": kind, "source_url": f"https://www.youtube.com/watch?v={VIDEO}" if kind == "youtube" else "https://science.example.test/carbon", "title": "Synthetic carbon evidence", "teaching_notes": "Compare carbon transfers through plants and soil.", "rights_confirmed": True, **({"transcript": "Carbon moves from air to plants during photosynthesis. Plants supply carbon to food webs."} if kind == "youtube" else {"content": "Carbon moves from air to plants during photosynthesis. Decomposition returns carbon to soil and air."}), **fields}
    response = client.post(f"/api/courses/{env['course']}/external-resources", json=payload, headers=env["admin"])
    assert response.status_code == 201, response.text
    return response.json()


def review(client, env, resource, key="synthetic-material-review-12345"):
    response = client.post(f"/api/external-resources/{resource['id']}/review", json={"expected_version": resource["version"], "idempotency_key": key}, headers=env["admin"])
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, env, resource, job, **fields):
    return client.post(f"/api/external-resources/{resource['id']}/approve", json={"expected_version": resource["version"], "review_id": job["review_id"], "snapshot_sha256": resource["snapshot_sha256"], **fields}, headers=env["admin"])


def test_admin_assignment_creates_configuration_not_identity_or_permissions(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch, assign=False)
    with app.state.database.factory() as db:
        identities = [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind.in_(["user", "school_membership", "agent_placement", "enrollment"])))]
    response = client.put(f"/api/schools/{env['school']}/audit-agent", json=env["config"], headers=env["admin"])
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["external_audit_required"] and value["agent"]["version"] == 1
    assert KEY not in response.text and env["calls"] == []
    with app.state.database.factory() as db:
        assert identities == [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind.in_(["user", "school_membership", "agent_placement", "enrollment"])))]
        assert db.get(Record, value["agent"]["revision_id"]).kind == "external_audit_agent_revision"
    assert client.put(f"/api/schools/{env['school']}/audit-agent", json=env["config"], headers=env["admin"]).status_code == 409
    assert client.put(f"/api/schools/{env['school']}/audit-agent", json={**env["config"], "expected_version": 1, "require_human_approval": False}, headers=env["admin"]).status_code == 200


@pytest.mark.parametrize("user", ["demo-instructor", "demo-student", "demo-student2"])
def test_audit_agent_configuration_and_school_queue_require_human_manager(client, app, monkeypatch, user):
    env = external_environment(client, app, monkeypatch)
    headers = login(client, user)
    assert client.get(f"/api/schools/{env['school']}/audit-agent").status_code == 403
    assert client.put(f"/api/schools/{env['school']}/audit-agent", json={**env["config"], "expected_version": 1}, headers=headers).status_code == 403
    assert client.get(f"/api/schools/{env['school']}/external-review").status_code == 403


def test_new_material_is_private_until_current_clean_agent_and_human_approval(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    assert resource["status"] == "pending" and resource["review_only"]
    student = login(client)
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 404
    assert client.get("/api/courses/demo-course/external-resources").json()["resources"] == []
    env["admin"] = login(client, "demo-admin")
    job = review(client, env, resource)
    assert job["status"] == "completed" and job["send_marker"]
    assert len(env["calls"]) == 1
    queued = client.get(f"/api/external-resources/{resource['id']}").json()
    assert queued["status"] == "reviewed" and queued["can_approve"]
    assert queued["review"]["coverage"] == "supplied_text"
    response = approve(client, env, resource, job)
    assert response.status_code == 200, response.text
    assert response.json()["approved"]
    login(client)
    public = client.get(f"/api/external-resources/{resource['id']}").json()
    assert public["approved"] and "Carbon moves" in public["content"]
    assert not {"review", "approval", "transcript", "metadata", "origin_document", "policy_snapshot", "prompt"} & public.keys()
    with app.state.database.factory() as db:
        docs = external_documents(db, db.get(Record, env["course"]), env["school"])
        assert len(docs) == 1 and docs[0]["external_review"]["approval_id"]
        job_value = db.get(Record, job["id"]).value
        assert job_value["send_marker"] and job_value["reservation"] > 100
        assert KEY not in canonical(job_value)
        evidence = job_value["projection"]
        assert set(evidence) == {"material", "course", "policy_notes"}
        assert "student" not in canonical(evidence).lower()
        assert all(KEY not in row.payload for row in db.scalars(select(Audit)))


def test_youtube_requires_human_watched_complete_attestation_and_click_fetch(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env, "youtube", start_seconds=10, end_seconds=70)
    assert env["fetches"] == [VIDEO]
    job = review(client, env, resource)
    assert approve(client, env, resource, job).status_code == 409
    assert approve(client, env, resource, job, watched_video=True).status_code == 409
    response = approve(client, env, resource, job, watched_video=True, transcript_complete=True)
    assert response.status_code == 200, response.text
    login(client)
    listing = client.get("/api/courses/demo-course/external-resources").json()
    assert listing["resources"][0]["video"]["resource_id"] == resource["id"]
    assert env["fetches"] == [VIDEO]
    playback = client.post(f"/api/external-resources/{resource['id']}/playback", headers=login(client)).json()
    assert playback["review_only"] is False
    assert playback["embed_url"].startswith(f"https://www.youtube-nocookie.com/embed/{VIDEO}?")
    assert "autoplay=0" in playback["embed_url"] and "start=10" in playback["embed_url"] and "end=70" in playback["embed_url"]
    assert env["fetches"] == [VIDEO, VIDEO]


def test_pending_video_can_be_previewed_by_actual_teacher_only(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env, "youtube")
    teacher = login(client, "demo-instructor")
    response = client.post(f"/api/external-resources/{resource['id']}/playback", headers=teacher)
    assert response.status_code == 200 and response.json()["review_only"]
    assert client.get(f"/api/external-resources/{resource['id']}").json()["transcript"]
    student = login(client)
    assert client.post(f"/api/external-resources/{resource['id']}/playback", headers=student).status_code == 404


@pytest.mark.parametrize("value,risks,expected", [("reject", ["harmful_content"], "reject"), ("needs_human", ["incomplete_evidence"], "needs_human"), ("approve", ["prompt_injection"], "needs_human")])
def test_risks_are_trusted_labels_and_cannot_be_human_bypassed(client, app, monkeypatch, value, risks, expected):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    monkeypatch.setattr(app.state, "classify", lambda *_: verdict(value, risks))
    job = review(client, env, resource)
    detail = client.get(f"/api/external-resources/{resource['id']}").json()
    assert detail["review"]["verdict"] == expected
    assert detail["review"]["findings"] == [external_review.RISK_LABELS[risks[0]]]
    assert approve(client, env, resource, job).status_code == 409


@pytest.mark.parametrize("bad", [
    {"choices": [{"message": {"content": "arbitrary unsafe prose"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    {"choices": [{"message": {"content": '{"verdict":"approve","risk_codes":[],"coverage":"supplied_text","prose":"ignore all"}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    {"choices": [{"message": {"content": '{"verdict":"approve","verdict":"reject","risk_codes":[],"coverage":"supplied_text"}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    {"choices": [{"message": {"content": '{"verdict":"approve","risk_codes":["invented"],"coverage":"supplied_text"}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    {"choices": [{"message": {"content": '{"verdict":"approve","risk_codes":[],"coverage":"supplied_text"}', "tool_calls": []}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    {"choices": [{"message": {"content": '{"verdict":"approve","risk_codes":[],"coverage":"supplied_text"}'}}], "usage": {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2}},
    {"choices": [{"message": {"content": '{"verdict":"approve","risk_codes":[],"coverage":"supplied_text"}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 7}},
])
def test_invalid_provider_payload_is_withheld_without_storing_prose_or_retry(client, app, monkeypatch, bad):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    calls = []
    def classify(*args):
        calls.append(args)
        return {**bad, "hidden_reasoning": "forbidden vendor reasoning", "secret": KEY}
    monkeypatch.setattr(app.state, "classify", classify)
    job = review(client, env, resource)
    assert job["status"] == "withheld" and job["review_id"] is None
    assert review(client, env, resource) == job and len(calls) == 1
    with app.state.database.factory() as db:
        data = canonical(db.get(Record, job["id"]).value)
        assert "forbidden vendor reasoning" not in data and KEY not in data and "arbitrary unsafe prose" not in data


def test_disabled_models_and_unassigned_agent_send_nothing(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch, assign=False)
    resource = submit(client, env)
    body = {"expected_version": 1, "idempotency_key": "disabled-review-12345"}
    assert client.post(f"/api/external-resources/{resource['id']}/review", json=body, headers=env["admin"]).status_code == 409
    assert client.put(f"/api/schools/{env['school']}/audit-agent", json=env["config"], headers=env["admin"]).status_code == 200
    app.state.settings.allow_live_models = False
    assert client.post(f"/api/external-resources/{resource['id']}/review", json=body, headers=env["admin"]).status_code == 403
    assert env["calls"] == []


def test_idempotency_is_schoolwide_exact_and_pending_survives_day_change(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource, another = submit(client, env), submit(client, env)
    with app.state.database.write() as db:
        pending = Record(id="synthetic-pending-review", tenant_id=app.state.settings.tenant_id, kind="external_review_job", parent_id=resource["id"], data=canonical({"school_id": env["school"], "resource_id": resource["id"], "version": 1, "idempotency_key": "uncertain-review-12345", "status": "pending", "created_at": "2026-10-17T00:00:00+00:00", "budget_day": "2026-10-17"}))
        db.add(pending)
        db.flush()
        bind_scope(db, pending, env["school"])
    assert review(client, env, resource, "uncertain-review-12345")["status"] == "pending"
    response = client.post(f"/api/external-resources/{another['id']}/review", json={"expected_version": 1, "idempotency_key": "uncertain-review-12345"}, headers=env["admin"])
    assert response.status_code == 409
    response = client.post(f"/api/external-resources/{another['id']}/review", json={"expected_version": 1, "idempotency_key": "new-review-different-day-12345"}, headers=env["admin"])
    assert response.status_code == 409 and env["calls"] == []


def test_daily_school_budget_cannot_be_reset_by_agent_edit(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch, max_daily_reviews=1)
    first, second = submit(client, env), submit(client, env)
    review(client, env, first)
    updated = client.put(f"/api/schools/{env['school']}/audit-agent", json={**env["config"], "expected_version": 1, "max_daily_reviews": 100}, headers=env["admin"])
    assert updated.status_code == 200
    response = client.post(f"/api/external-resources/{second['id']}/review", json={"expected_version": 1, "idempotency_key": "budget-review-second-12345"}, headers=env["admin"])
    assert response.status_code == 403 and len(env["calls"]) == 1


def test_resource_edit_and_agent_revision_revoke_prior_publication(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    job = review(client, env, resource)
    assert approve(client, env, resource, job).status_code == 200
    response = client.put(f"/api/schools/{env['school']}/audit-agent", json={**env["config"], "expected_version": 1, "policy_notes": "Review the grade-band suitability carefully."}, headers=env["admin"])
    assert response.status_code == 200
    login(client)
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 404
    env["admin"] = login(client, "demo-admin")
    assert approve(client, env, resource, job).status_code == 409
    with app.state.database.factory() as db:
        assert db.get(Record, job["review_id"]).kind == "external_review_result"
        original = db.get(Record, resource["snapshot_id"]).data
    response = client.put(f"/api/external-resources/{resource['id']}", json={"kind": "external_text", "source_url": resource["source_url"], "title": "Edited carbon evidence", "content": "Updated source contents", "teaching_notes": "", "rights_confirmed": True, "expected_version": 1}, headers=env["admin"])
    assert response.status_code == 200 and response.json()["version"] == 2 and response.json()["status"] == "pending"
    assert approve(client, env, resource, job).status_code == 409
    with app.state.database.factory() as db:
        assert db.get(Record, resource["snapshot_id"]).data == original


def test_disabling_agent_keeps_broad_gate_and_school_edit_preserves_it(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    assert approve(client, env, resource, review(client, env, resource)).status_code == 200
    with app.state.database.write() as db:
        connection = db.get(Record, env["connection"]["id"])
        connection.set({**connection.value, "enabled": False})
    response = client.put(f"/api/schools/{env['school']}/audit-agent", json={**env["config"], "expected_version": 1, "enabled": False}, headers=env["admin"])
    assert response.status_code == 200 and response.json()["external_audit_required"]
    response = client.put(f"/api/schools/{env['school']}", json={"name": "Renamed school"}, headers=env["admin"])
    assert response.status_code == 200
    with app.state.database.factory() as db:
        school = db.get(Record, env["school"])
        assert school.value["external_audit_required"] is True
        documents = [{"id": "public", "kind": "instructional_supplement", "audience": "all", "title": "Public", "content": "Teaching material"}, {"id": "private", "kind": "instructional_supplement", "audience": "teachers"}]
        assert filter_reviewed_documents(db, db.get(Record, env["course"]), env["school"], documents) == [documents[1]]
        assert external_documents(db, db.get(Record, env["course"]), env["school"]) == []


def test_import_corpus_is_idempotent_exact_and_cannot_duplicate_document(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    document = {"id": "resource:synthetic", "kind": "instructional_supplement", "audience": "all", "title": "Synthetic teaching material", "content": "Carbon evidence for a class", "source_record_id": "synthetic", "source_record_sha256": "a" * 64}
    documents = [document, {"id": "private", "kind": "instructional_supplement", "audience": "teachers", "title": "Private teacher notes", "content": "Unpublished notes"}]
    monkeypatch.setattr(external_review, "_raw_documents", lambda *_: documents)
    response = client.post("/api/courses/demo-course/external-resources/import-corpus", headers=env["admin"])
    assert response.status_code == 200, response.text
    resources = response.json()["resources"]
    assert len(resources) == 1 and resources[0]["kind"] == "corpus_document"
    assert client.post("/api/courses/demo-course/external-resources/import-corpus", headers=env["admin"]).json()["resources"][0]["id"] == resources[0]["id"]
    job = review(client, env, resources[0])
    assert approve(client, env, resources[0], job).status_code == 200
    with app.state.database.factory() as db:
        course = db.get(Record, env["course"])
        filtered = filter_reviewed_documents(db, course, env["school"], documents)
        assert len(filtered) == 2 and filtered[0]["external_review"]["approval_id"]
        assert external_documents(db, course, env["school"]) == []
    documents[0] = {**document, "content": "Changed material; old approval cannot transfer"}
    with app.state.database.factory() as db:
        filtered = filter_reviewed_documents(db, db.get(Record, env["course"]), env["school"], documents)
        assert len(filtered) == 1 and filtered[0]["audience"] == "teachers"
    assert client.get(f"/api/external-resources/{resources[0]['id']}").json()["status"] == "stale"


def test_review_transport_revalidation_withholds_after_midflight_resource_change(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    def classify(*_args):
        with app.state.database.write() as db:
            row = db.get(Record, resource["id"])
            row.set({**row.value, "revoked": True})
        return verdict()
    monkeypatch.setattr(app.state, "classify", classify)
    job = review(client, env, resource)
    assert job["status"] == "withheld" and job["review_id"] is None
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind == "external_review_result")))


def test_model_projection_never_includes_student_work_or_course_description(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    with app.state.database.write() as db:
        course = db.get(Record, env["course"])
        course.set({**course.value, "description": "PRIVATE-STUDENT-RECORD-SHOULD-NOT-SEND"})
        db.add(Record(id="private-student-draft", tenant_id=app.state.settings.tenant_id, kind="draft", parent_id="demo-assignment", owner_id="demo-student", data=canonical({"content": "PRIVATE-STUDENT-WORK-SHOULD-NOT-SEND"})))
    resource = submit(client, env)
    review(client, env, resource)
    prompt = canonical(env["calls"][0][3])
    assert "PRIVATE-STUDENT" not in prompt and KEY not in prompt and "public" in prompt.lower()


@pytest.mark.parametrize("field,value", [("rights_confirmed", False), ("rights_confirmed", 1), ("source_url", "javascript:alert(1)"), ("source_url", "https://user:pass@example.test/path"), ("kind", "raw_html"), ("expected_version", 1)])
def test_unbounded_or_unrecognized_resource_inputs_fail_closed(client, app, monkeypatch, field, value):
    env = external_environment(client, app, monkeypatch)
    payload = {"kind": "external_text", "source_url": "https://science.example.test", "title": "Source", "content": "Teaching material", "rights_confirmed": True, field: value}
    response = client.post("/api/courses/demo-course/external-resources", json=payload, headers=env["admin"])
    assert response.status_code == 422 and env["calls"] == [] and env["fetches"] == []


def test_document_fingerprint_ignores_release_state_but_pins_content_and_provenance():
    source = {"id": "reference:test", "kind": "source_reference", "audience": "all", "title": "Source", "content": "Exact words", "source_url": "https://example.test", "source_content_sha256": "a" * 64}
    assert document_fingerprint(source) == document_fingerprint({**source, "external_review": {"approval_id": "changing"}, "video": {"resource_id": "changing"}})
    for key in ("content", "title", "source_url", "source_content_sha256", "audience"):
        assert document_fingerprint({**source, key: "changed"}) != document_fingerprint(source)


@pytest.mark.parametrize("field,value", [("title", "A different class"), ("curriculum", {"state": "US-NY", "subject": "environmental_science", "grades": [12], "academic_year": "2026-2027"})])
def test_course_suitability_context_is_pinned_before_release_and_later_reads(client, app, monkeypatch, field, value):
    env = external_environment(client, app, monkeypatch, require_human_approval=False)
    resource = submit(client, env)
    job = review(client, env, resource)
    assert job["status"] == "completed"
    login(client)
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 200
    env["admin"] = login(client, "demo-admin")
    with app.state.database.write() as db:
        course = db.get(Record, env["course"])
        course.set({**course.value, field: value})
    assert client.get(f"/api/external-resources/{resource['id']}").json()["status"] == "stale"
    assert approve(client, env, resource, job).status_code == 409
    login(client)
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 404


@pytest.mark.parametrize("change", ["course", "policy", "connection", "gate", "membership"])
def test_midflight_runtime_changes_withhold_agent_decisions(client, app, monkeypatch, change):
    env = external_environment(client, app, monkeypatch, require_human_approval=False)
    resource = submit(client, env)
    env["admin"] = login(client, "demo-instructor")
    def classify(*_args):
        with app.state.database.write() as db:
            if change == "course":
                row = db.get(Record, env["course"])
                row.set({**row.value, "title": "Changed suitability context"})
            elif change == "policy":
                row = db.get(Record, env["agent"]["agent"]["id"])
                row.set({**row.value, "enabled": False})
            elif change == "connection":
                row = db.get(Record, env["connection"]["id"])
                row.set({**row.value, "enabled": False})
            elif change == "gate":
                app.state.settings.allow_live_models = False
            else:
                enrollment = db.scalar(select(Record).where(Record.kind == "enrollment", Record.parent_id == env["course"], Record.owner_id == "demo-instructor"))
                enrollment.set({**enrollment.value, "role": "student"})
        return verdict()
    monkeypatch.setattr(app.state, "classify", classify)
    job = review(client, env, resource)
    assert job["status"] == "withheld" and job["review_id"] is None
    assert job["send_marker"] and "billable" in job["uncertainty"]
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind.in_(["external_review_result", "external_publication"]))))


def test_metadata_change_durably_withdraws_video_from_later_reads_and_corpus(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env, "youtube")
    job = review(client, env, resource)
    assert approve(client, env, resource, job, watched_video=True, transcript_complete=True).status_code == 200
    student = login(client)
    monkeypatch.setattr(app.state, "fetch_youtube_metadata", lambda *_: metadata("Changed public title"))
    assert client.post(f"/api/external-resources/{resource['id']}/playback", headers=student).status_code == 409
    assert client.get(f"/api/external-resources/{resource['id']}").status_code == 404
    assert client.get("/api/courses/demo-course/external-resources").json()["resources"] == []
    with app.state.database.factory() as db:
        assert db.get(Record, resource["id"]).value["metadata_stale"] is True
        assert external_documents(db, db.get(Record, env["course"]), env["school"]) == []
        assert list(db.scalars(select(Audit).where(Audit.event_type == "external.metadata_changed")))
    env["admin"] = login(client, "demo-admin")
    detail = client.get(f"/api/external-resources/{resource['id']}").json()
    assert detail["status"] == "stale" and detail["can_review"] is False
    assert approve(client, env, resource, job, watched_video=True, transcript_complete=True).status_code == 409
    response = client.post(f"/api/external-resources/{resource['id']}/review", json={"expected_version": 1, "idempotency_key": "stale-video-re-review-12345"}, headers=env["admin"])
    assert response.status_code == 409


def test_staff_job_projection_preserves_durable_failure_without_provider_payload(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch)
    resource = submit(client, env)
    monkeypatch.setattr(app.state, "classify", lambda *_: {"choices": [{"message": {"content": "No untrusted prose should persist"}}]})
    job = review(client, env, resource)
    detail = client.get(f"/api/external-resources/{resource['id']}").json()
    assert detail["latest_review_job"]["id"] == job["id"]
    assert detail["latest_review_job"]["status"] == "withheld"
    assert detail["latest_review_job"]["error"] and "No untrusted prose" not in canonical(detail)
    assert set(detail["latest_review_job"]) == {"id", "status", "error", "uncertainty", "usage", "created_at"}


def test_text_automatic_publication_is_explicit_policy_and_has_exact_immutable_proofs(client, app, monkeypatch):
    env = external_environment(client, app, monkeypatch, require_human_approval=False)
    resource = submit(client, env)
    job = review(client, env, resource)
    detail = client.get(f"/api/external-resources/{resource['id']}").json()
    assert detail["status"] == "approved" and detail["approval"]["approval_method"] == "automatic_policy"
    with app.state.database.factory() as db:
        publication = db.get(Record, detail["approval"]["id"])
        assert publication.value["review_id"] == job["review_id"]
        assert publication.value["course_context_snapshot"]["title"] == "Evidence and reasoning"
        assert publication.value["agent_revision_id"] == env["agent"]["agent"]["revision_id"]
        assert publication.value["expires_at"] == db.get(Record, job["review_id"]).value["expires_at"]
