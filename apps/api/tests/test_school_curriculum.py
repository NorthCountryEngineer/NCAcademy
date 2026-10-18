"""Synthetic onboarding plans, scoped publication and real corpus-run receipts."""
import base64
from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from academy.db import Audit, Record
from academy.permissions import default_school_id
from academy.standards import digest
from conftest import login
from test_teacher_onboarding import png, picture


CONFIG = {"mode": "state", "state": "US-NY", "academic_year": "2026-2027", "grades": [10], "subjects": ["environmental_science"]}


def school(client, headers, **fields):
    response = client.post("/api/schools", json={"name": "Synthetic school", "curriculum": CONFIG, **fields}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def run(client, headers, value, key="planner-key-001"):
    response = client.post(f"/api/schools/{value['id']}/curriculum/runs", json={"idempotency_key": key, "expected_school_version": value["version"]}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, headers, value, planned, key="publish-key-001", **fields):
    body = {"idempotency_key": key, "expected_school_version": value["version"], "run_id": planned["id"], "plan_sha256": planned["plan_sha256"], "course_keys": [item["key"] for item in planned["plan"]["courses"]], **fields}
    return client.post(f"/api/schools/{value['id']}/curriculum/publish", json=body, headers=headers)


def counts(app):
    with app.state.database.factory() as db:
        return {kind: len(list(db.scalars(select(Record).where(Record.kind == kind)))) for kind in ("course", "course_corpus", "enrollment", "teacher_profile_course", "school_curriculum_publication")}


def test_branding_metadata_and_curriculum_preserve_legacy_put_and_safe_audit(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers, logo=picture(png(extra=b"Comment\0private-logo-metadata")), custom_metadata=[{"key": "Campus", "value": "North"}])
    logo = value["logo"]
    assert logo["width"] == 1 and b"private-logo-metadata" not in base64.b64decode(logo["data_base64"])
    assert value["version"] == 1 and value["curriculum_revision"] == 1
    saved = client.put(f"/api/schools/{value['id']}", json={"name": "Renamed"}, headers=headers).json()
    assert saved["version"] == 2 and saved["curriculum_revision"] == 1
    assert saved["logo"] == logo and saved["curriculum"] == value["curriculum"] and saved["custom_metadata"] == value["custom_metadata"]
    assert client.put(f"/api/schools/{value['id']}", json={"name": "Lost update", "expected_version": 1}, headers=headers).status_code == 409
    changed = client.put(f"/api/schools/{value['id']}", json={"name": "Renamed", "expected_version": 2, "curriculum": {"mode": "none"}, "logo": None, "custom_metadata": []}, headers=headers).json()
    assert changed["version"] == 3 and changed["curriculum_revision"] == 2 and changed["logo"] is None and changed["custom_metadata"] == []
    with app.state.database.factory() as db:
        events = list(db.scalars(select(Audit).where(Audit.event_type.in_(["school.created", "school.updated"]))))
        assert any(logo["sha256"] in event.payload for event in events)
        assert all(logo["data_base64"] not in event.payload and "private-logo-metadata" not in event.payload and '"North"' not in event.payload for event in events)


@pytest.mark.parametrize("field,value", [
    ("custom_metadata", [{"key": "Campus", "value": "A"}, {"key": " campus ", "value": "B"}]),
    ("custom_metadata", [{"key": "   ", "value": "A"}]),
    ("custom_metadata", [{"key": "X", "value": "x" * 1001}]),
    ("logo", {"media_type": "image/svg+xml", "data_base64": "PHN2Zy8+"}),
    ("logo", {"media_type": "image/png", "data_base64": "YWJj"}),
    ("curriculum", {**CONFIG, "academic_year": "2026-2028"}),
    ("curriculum", {**CONFIG, "grades": [True]}),
    ("curriculum", {**CONFIG, "grades": [10, 10]}),
    ("curriculum", {**CONFIG, "subjects": ["environmental_science", "environmental_science"]}),
    ("curriculum", {**CONFIG, "subjects": ["https://example.test"]}),
    ("curriculum", {"mode": "state"}),
    ("curriculum", {"mode": "custom"}),
    ("curriculum", {**CONFIG, "system_prompt": "Invent standards"}),
    ("expected_version", 1),
])
def test_school_configuration_rejects_invalid_or_executable_inputs(client, field, value):
    headers = login(client, "demo-admin")
    assert client.post("/api/schools", json={"name": "No", field: value}, headers=headers).status_code == 422


def test_catalog_is_available_to_human_admin_before_any_school_exists(client, app):
    headers = login(client, "demo-admin")
    with app.state.database.write() as db:
        for row in list(db.scalars(select(Record).where(Record.kind == "school"))):
            db.delete(row)
    response = client.get("/api/school-curriculum/catalog")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["modes"] == ["state", "custom", "none"] and result["network_fetch"] is False
    assert result["custom_ingestion_supported"] is False and result["human_corpus_approval_required"]
    assert any(state["code"] == "US-NY" for state in result["states"])


def test_planner_creates_cited_grade_year_draft_without_creating_class_or_approval(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={**CONFIG, "grades": [9, 10]})
    before = counts(app)
    planned = run(client, headers, value)
    assert counts(app) == before
    assert planned["current"] and planned["status"] == "ready" and planned["agent"]["live_model_used"] is False
    assert planned["plan_sha256"] == digest(planned["plan"])
    assert {item["grade"] for item in planned["plan"]["courses"]} == {9, 10}
    for item in planned["plan"]["courses"]:
        assert item["coverage"] == "partial" and item["curriculum"]["grades"] == [item["grade"]]
        assert item["academic_year"] == "2026-2027" and item["source_ids"] and item["catalog_sha256"]
        assert item["units"] and all(unit["standard_codes"] and unit["objectives"] for unit in item["units"])
        assert all(objective["source_id"] in item["source_ids"] for unit in item["units"] for objective in unit["objectives"])
    again = run(client, headers, value)
    assert again["id"] == planned["id"]
    saved = client.get(f"/api/schools/{value['id']}/curriculum").json()
    assert len(saved["runs"]) == 1 and saved["runs"][0]["id"] == planned["id"] and saved["publications"] == []


@pytest.mark.parametrize("configuration,code", [
    ({"mode": "none"}, "no_curriculum"),
    ({"mode": "custom", "custom_name": "Local studies", "custom_description": "Pending custom upload"}, "custom_ingestion_deferred"),
    ({**CONFIG, "state": "US-CA"}, "mapping_unavailable"),
    ({**CONFIG, "grades": [3]}, "grade_unreviewed"),
    ({**CONFIG, "academic_year": "2027-2028"}, "year_unreviewed"),
])
def test_no_custom_and_unavailable_curricula_remain_truthful_and_create_no_corpus(client, app, configuration, code):
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum=configuration)
    before = counts(app)
    planned = run(client, headers, value)
    assert planned["status"] in {"needs_configuration", "unavailable"} and planned["plan"]["courses"] == []
    assert code in {gap["code"] for gap in planned["plan"]["gaps"]}
    assert planned["plan"]["ingestion_supported"] is False and counts(app) == before
    response = publish(client, headers, value, planned, course_keys=["0" * 32])
    assert response.status_code == 409 and counts(app) == before


def test_supported_subset_retains_missing_grade_and_subject_gaps(client):
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={**CONFIG, "grades": [3, 10], "subjects": ["environmental_science", "custom_uninstalled"]})
    planned = run(client, headers, value)
    assert len(planned["plan"]["courses"]) == 1
    assert {gap["code"] for gap in planned["plan"]["gaps"]} == {"grade_unreviewed", "mapping_unavailable"}


def test_confirmed_selected_classes_inherit_curriculum_build_corpora_and_are_idempotent(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={**CONFIG, "grades": [9, 10]})
    planned = run(client, headers, value)
    before = counts(app)
    selected = [planned["plan"]["courses"][1]["key"]]
    response = publish(client, headers, value, planned, course_keys=selected)
    assert response.status_code == 201, response.text
    created = response.json()
    assert len(created["courses"]) == 1 and created["courses"][0]["created"]
    result = created["courses"][0]
    assert result["corpus"]["id"] and result["corpus"]["approval"]["status"] == "pending" and not result["corpus"]["ready_for_teaching"]
    course_value = client.get(f"/api/courses/{result['course_id']}").json()
    assert course_value["curriculum"]["grades"] == [10]
    with app.state.database.factory() as db:
        lineage = db.get(Record, result["course_id"]).value["school_curriculum"]
        assert lineage["curriculum_revision"] == value["curriculum_revision"] and lineage["course_key"] == selected[0]
        assert not list(db.scalars(select(Record).where(Record.kind.in_(["corpus_approval", "teacher_profile_revision"]))))
    same = publish(client, headers, value, planned, course_keys=selected).json()
    assert same["id"] == created["id"] and same["courses"] == created["courses"]
    reused = publish(client, headers, value, planned, key="new-publication-key", course_keys=selected)
    assert reused.status_code == 201 and reused.json()["courses"][0]["course_id"] == result["course_id"] and not reused.json()["courses"][0]["created"]
    after = counts(app)
    assert after["course"] == before["course"] + 1 and after["course_corpus"] == before["course_corpus"] + 1


def test_changed_configuration_and_catalog_invalidate_old_plans(client, monkeypatch):
    import academy.school_curriculum as planner
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    original_load = planner.load_catalogs
    def changed_catalog():
        result = deepcopy(original_load())
        for item in result:
            item["catalog_sha256"] = "a" * 64
        return result
    monkeypatch.setattr(planner, "load_catalogs", changed_catalog)
    assert not client.get(f"/api/schools/{value['id']}/curriculum").json()["runs"][0]["current"]
    assert publish(client, headers, value, planned).status_code == 409
    monkeypatch.setattr(planner, "load_catalogs", original_load)
    saved = client.put(f"/api/schools/{value['id']}", json={"name": value["name"], "expected_version": value["version"], "curriculum": {**CONFIG, "grades": [11]}}, headers=headers).json()
    assert saved["curriculum_revision"] == 2
    assert publish(client, headers, saved, planned).status_code == 409
    retry = run(client, headers, value)
    assert retry["id"] == planned["id"] and not retry["current"]
    assert client.post(f"/api/schools/{value['id']}/curriculum/runs", json={"expected_school_version": saved["version"], "idempotency_key": "planner-key-001"}, headers=headers).status_code == 409


def test_publication_rejects_wrong_hash_stale_version_unknown_and_duplicate_keys(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    before = counts(app)
    assert publish(client, headers, value, planned, plan_sha256="0" * 64).status_code == 409
    assert publish(client, headers, value, planned, expected_school_version=99).status_code == 409
    assert publish(client, headers, value, planned, course_keys=["0" * 32]).status_code == 422
    key = planned["plan"]["courses"][0]["key"]
    assert publish(client, headers, value, planned, course_keys=[key, key]).status_code == 422
    assert publish(client, headers, value, planned, course_keys=[]).status_code == 422
    assert counts(app) == before


def test_existing_planned_class_edits_never_get_overwritten_or_duplicated(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    created = publish(client, headers, value, planned).json()
    result = created["courses"][0]
    assert client.put(f"/api/courses/{result['course_id']}", json={"title": "Human edited title"}, headers=headers).status_code == 200
    before = counts(app)
    assert publish(client, headers, value, planned, key="different-publication").status_code == 409
    assert counts(app) == before
    assert client.get(f"/api/courses/{result['course_id']}").json()["title"] == "Human edited title"


def test_failed_build_rolls_back_all_classes_snapshots_publication_and_audit(client, app, monkeypatch):
    import academy.school_curriculum_api as api
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={**CONFIG, "grades": [9, 10]})
    planned = run(client, headers, value)
    before = counts(app)
    original_build = api.build_course_corpus
    calls = []
    def fail_second(*args):
        calls.append(True)
        if len(calls) == 2:
            raise HTTPException(503, "Synthetic blocked corpus")
        return original_build(*args)
    monkeypatch.setattr(api, "build_course_corpus", fail_second)
    assert publish(client, headers, value, planned).status_code == 503
    assert counts(app) == before
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Audit).where(Audit.event_type == "school.curriculum_classes_created")))
    monkeypatch.setattr(api, "build_course_corpus", original_build)
    assert publish(client, headers, value, planned).status_code == 201


@pytest.mark.parametrize("user", ["demo-instructor", "demo-student", "demo-student2"])
def test_teachers_and_students_cannot_plan_publish_or_read_onboarding(client, app, user):
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    headers = login(client, user)
    assert client.get("/api/school-curriculum/catalog").status_code == 403
    assert client.get(f"/api/school-curriculum/catalog?school_id={value['id']}").status_code == 403
    assert client.get(f"/api/schools/{value['id']}/curriculum").status_code == 403
    assert client.get(f"/api/schools/{value['id']}/onboarding/runs").status_code == 403
    assert client.post(f"/api/schools/{value['id']}/curriculum/runs", json={"idempotency_key": "no-access-key", "expected_school_version": 1}, headers=headers).status_code == 403
    assert publish(client, headers, value, planned).status_code == 403


def test_school_manager_exact_scope_and_cross_school_runs(client, app):
    admin = login(client, "demo-admin")
    foreign = school(client, admin)
    planned = run(client, admin, foreign)
    own_id = default_school_id(app.state.settings.tenant_id)
    manager = login(client, "demo-school")
    assert client.get(f"/api/school-curriculum/catalog?school_id={own_id}").status_code == 200
    assert client.get(f"/api/schools/{foreign['id']}/curriculum").status_code == 403
    saved = client.put(f"/api/schools/{own_id}", json={"name": "Own school", "curriculum": CONFIG}, headers=manager).json()
    own = {**saved, "id": own_id}
    assert publish(client, manager, own, planned).status_code == 404
    own_plan = run(client, manager, own)
    assert publish(client, manager, own, own_plan).status_code == 201


def test_nonhuman_claims_never_allow_school_mutations_or_onboarding(client, app, monkeypatch):
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    identity = {"id": "agent:" + str(uuid4()), "tenant_id": app.state.settings.tenant_id, "role": "admin", "principal_kind": "agent", "school_id": value["id"], "permissions": ["academy-use", "academy-teach"], "csrf_token": ""}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    assert client.post("/api/schools", json={"name": "No"}).status_code == 403
    assert client.get("/api/school-curriculum/catalog").status_code == 403
    assert client.get(f"/api/schools/{value['id']}/onboarding/runs").status_code == 403
    assert publish(client, {}, value, planned).status_code == 403


def test_teacher_assignment_records_actual_corpus_run_and_base_instructions_remain_advisory(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    created = publish(client, headers, value, planned).json()
    url = f"/api/schools/{value['id']}/teachers"
    teacher = client.post(url, json={"kind": "ai", "name": "Synthetic science guide", "ai_behavior": {"tone": "warm", "instructions": "  Ask for observations before selecting an approved excerpt.  "}}, headers=headers).json()
    assert teacher["ai_behavior"]["instructions"] == "Ask for observations before selecting an approved excerpt."
    assert teacher["latest_onboarding_run"]["status"] == "skipped" and not teacher["latest_onboarding_run"]["courses"]
    assert not teacher["authority_granted"] and not teacher["activation"]["runtime_active"]
    before = counts(app)
    assigned_response = client.put(f"{url}/{teacher['id']}/courses", json={"expected_version": teacher["version"], "course_ids": [created["courses"][0]["course_id"]]}, headers=headers)
    assert assigned_response.status_code == 200, assigned_response.text
    assigned = assigned_response.json()
    receipt = assigned["onboarding_run"]
    assert receipt["status"] == "needs_review" and receipt["courses"][0]["corpus"]["id"] == created["courses"][0]["corpus"]["id"]
    assert receipt["agent"]["live_model_used"] is False and not receipt["authority_granted"] and receipt["human_approval_required"]
    assert counts(app)["course_corpus"] == before["course_corpus"] and counts(app)["enrollment"] == before["enrollment"]
    changed = client.put(f"{url}/{teacher['id']}", json={"expected_version": assigned["version"], "ai_behavior": {**assigned["ai_behavior"], "instructions": "Use a patient teaching approach."}}, headers=headers).json()
    assert not changed["approval_current"] and changed["ai_behavior"]["instructions"] == "Use a patient teaching approach."
    assert changed["latest_onboarding_run"]["id"] == receipt["id"] and changed["latest_onboarding_run"]["current"] is False
    assert client.put(f"{url}/{teacher['id']}", json={"expected_version": changed["version"], "ai_behavior": {"instructions": "x" * 4001}}, headers=headers).status_code == 422
    history = client.get(f"/api/schools/{value['id']}/onboarding/runs").json()
    assert {row["kind"] for row in history} == {"school_curriculum_run", "school_curriculum_publication", "knowledge_workflow_run"}
    assert receipt["id"] in {row["id"] for row in history}


def test_expired_review_blocks_plan_publication_and_marks_teacher_receipt_historical(client, monkeypatch):
    import academy.standards as standards
    import academy.school_curriculum as planner
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    published = publish(client, headers, value, planned).json()
    url = f"/api/schools/{value['id']}/teachers"
    teacher = client.post(url, json={"kind": "ai", "name": "Synthetic base"}, headers=headers).json()
    assigned = client.put(f"{url}/{teacher['id']}/courses", json={"expected_version": 1, "course_ids": [published["courses"][0]["course_id"]]}, headers=headers).json()
    assert assigned["latest_onboarding_run"]["current"]
    original = standards.load_catalogs
    def expired():
        values = deepcopy(original())
        for item in values:
            item["review_valid_through"] = "2025-01-01"
        return values
    monkeypatch.setattr(standards, "load_catalogs", expired)
    monkeypatch.setattr(planner, "load_catalogs", expired)
    assert publish(client, headers, value, planned, key="after-expiry-publish").status_code == 409
    history = client.get(f"/api/schools/{value['id']}/onboarding/runs").json()
    assert all(not item["current"] for item in history if item["id"] in {planned["id"], published["id"], assigned["latest_onboarding_run"]["id"]})


def test_archived_base_profile_run_is_historical_and_reassignment_denied(client):
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={"mode": "none"})
    url = f"/api/schools/{value['id']}/teachers"
    profile = client.post(url, json={"kind": "ai", "name": "Synthetic unbound guide", "ai_behavior": {"instructions": "Help students think through observations."}}, headers=headers).json()
    assert profile["latest_onboarding_run"]["current"] and profile["latest_onboarding_run"]["status"] == "skipped"
    archived = client.delete(f"{url}/{profile['id']}", headers=headers).json()
    assert archived["latest_onboarding_run"]["current"] is False and archived["ai_behavior"]["instructions"] == profile["ai_behavior"]["instructions"]
    assert client.put(f"{url}/{profile['id']}/courses", json={"expected_version": archived["version"], "course_ids": []}, headers=headers).status_code == 409


def test_new_workflow_evidence_is_immutable(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers)
    planned = run(client, headers, value)
    for operation in ("update", "delete"):
        with pytest.raises(ValueError):
            with app.state.database.write() as db:
                row = db.get(Record, planned["id"])
                if operation == "delete":
                    db.delete(row)
                else:
                    row.set({**row.value, "status": "tampered"})
