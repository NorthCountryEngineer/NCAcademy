"""Synthetic teacher onboarding, exact bindings and approval/context boundaries."""
import base64
import hashlib
import json
import struct
import zlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from academy.db import Audit, Record, canonical
from academy.permissions import bind_scope, default_school_id, membership_id
from conftest import login


CURRICULUM = {"state": "US-NY", "subject": "environmental_science", "grades": [10], "academic_year": "2026-2027"}


def path(school, profile=None, action=None):
    return f"/api/schools/{school}/teachers" + (f"/{profile}" if profile else "") + (f"/{action}" if action else "")


def create(client, headers, school, **fields):
    response = client.post(path(school), json={"kind": "human", "name": "Synthetic Teacher", **fields}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def school_id(app):
    return default_school_id(app.state.settings.tenant_id)


def new_school(client, headers):
    response = client.post("/api/schools", json={"name": "Other synthetic school"}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def png(width=1, height=1, extra=b"", pixels=None):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    data = pixels if pixels is not None else (b"\0" + b"\x12\x34\x56\xff" * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + (chunk(b"tEXt", extra) if extra else b"") + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b"")


def picture(raw):
    return {"media_type": "image/png", "data_base64": base64.b64encode(raw).decode()}


def placement(client, headers, school, active=True):
    assert client.put(f"/api/schools/{school}", json={"name": "Synthetic school", "core_resource_id": "registered-school"}, headers=headers).status_code == 200
    body = {"name": "Existing synthetic agent", "core_agent_id": str(uuid4()), "core_binding_id": str(uuid4()), "core_consumer_service_id": str(uuid4()), "environment": "production", "task_id": str(uuid4()), "core_resource_id": "registered-school", "active": active}
    response = client.post(f"/api/schools/{school}/agents", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json(), body


def course(client, headers, school, teacher="demo-instructor", title="Synthetic environmental class"):
    response = client.post("/api/courses", json={"school_id": school, "teacher_id": teacher, "title": title, "curriculum": CURRICULUM}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def approved_corpus(client, headers, school, course_id):
    response = client.post(f"/api/schools/{school}/resources", json={"name": "Human-reviewed synthetic ecology lesson", "course_id": course_id, "audience": "teachers", "content": "Matter cycles through ecosystems. Students collect evidence about biodiversity and compare the effects of human activity."}, headers=headers)
    assert response.status_code == 201, response.text
    resource = response.json()
    descriptor = client.post(f"/api/courses/{course_id}/corpus/refresh", headers=headers).json()
    response = client.post(f"/api/courses/{course_id}/corpus/approve", json={"corpus_id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"]}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["ready_for_teaching"]
    return resource


def assign(client, headers, school, profile, courses):
    response = client.put(path(school, profile["id"], "courses"), json={"expected_version": profile["version"], "course_ids": courses}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, headers, school, profile):
    response = client.post(path(school, profile["id"], "approve"), json={"expected_version": profile["version"]}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_human_profile_pending_metadata_never_adopts_email_or_grants_authority(client, app):
    headers = login(client, "demo-school")
    school = school_id(app)
    with app.state.database.factory() as db:
        original_memberships = [(r.id, r.data) for r in db.scalars(select(Record).where(Record.kind.in_(["school_membership", "enrollment", "user", "agent_placement"])))]
    profile = create(client, headers, school, name="Demo teacher", contact_email="same@example.test", pronouns="they/them", expertise=["Ecology", "Ecology"])
    duplicate = create(client, headers, school, name="Demo teacher", contact_email="same@example.test")
    assert profile["id"] != duplicate["id"]
    assert profile["principal_id"] is None and profile["status"] == "pending"
    assert profile["expertise"] == ["Ecology"]
    assert profile["activation"]["identity_linked"] is False
    assert profile["activation"]["runtime_active"] is False and profile["authority_granted"] is False
    updated = client.put(path(school, profile["id"]), json={"expected_version": 1, "biography": "A reusable, pending school teacher profile."}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == profile["name"] and updated.json()["version"] == 2
    assert client.put(path(school, profile["id"]), json={"expected_version": 1, "name": "Lost update"}, headers=headers).status_code == 409
    with app.state.database.factory() as db:
        assert [(r.id, r.data) for r in db.scalars(select(Record).where(Record.kind.in_(["school_membership", "enrollment", "user", "agent_placement"])))] == original_memberships


@pytest.mark.parametrize("user", ["demo-instructor", "demo-student", "demo-student2"])
def test_only_human_school_managers_can_onboard_or_read_private_profiles(client, app, user):
    admin = login(client, "demo-admin")
    school = school_id(app)
    profile = create(client, admin, school, contact_email="private@example.test")
    headers = login(client, user)
    assert client.get(path(school)).status_code == 403
    assert client.get(path(school, profile["id"])).status_code == 403
    assert client.post(path(school), json={"kind": "human", "name": "No"}, headers=headers).status_code == 403
    assert client.put(path(school, profile["id"]), json={"expected_version": 1, "name": "No"}, headers=headers).status_code == 403
    assert client.post(path(school, profile["id"], "bind"), json={"expected_version": 1, "principal_id": "demo-instructor"}, headers=headers).status_code == 403
    assert client.post(path(school, profile["id"], "approve"), json={"expected_version": 1}, headers=headers).status_code == 403
    assert client.put(path(school, profile["id"], "courses"), json={"expected_version": 1, "course_ids": []}, headers=headers).status_code == 403
    assert client.delete(path(school, profile["id"]), headers=headers).status_code == 403


def test_even_agent_spoofing_admin_summary_cannot_manage_profiles(client, app, monkeypatch):
    headers = login(client, "demo-admin")
    school = school_id(app)
    profile = create(client, headers, school)
    identity = {"id": "agent:" + str(uuid4()), "tenant_id": app.state.settings.tenant_id, "role": "admin", "principal_kind": "agent", "school_id": school, "placement_id": "none", "permissions": ["academy-use", "academy-teach"], "csrf_token": ""}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    assert client.get(path(school)).status_code == 403
    assert client.post(path(school), json={"kind": "ai", "name": "No"}).status_code == 403
    assert client.delete(path(school, profile["id"])).status_code == 403
    assert client.post(path(school, profile["id"], "approve"), json={"expected_version": 1}).status_code == 403


def test_school_and_tenant_scopes_are_enforced_for_profiles_bindings_and_courses(client, app):
    admin = login(client, "demo-admin")
    own, other = school_id(app), new_school(client, admin)
    foreign = create(client, admin, other)
    own_profile = create(client, admin, own)
    other_course = client.post("/api/courses", json={"school_id": other, "title": "Foreign class"}, headers=admin).json()
    assert client.get(path(own, foreign["id"])).status_code == 404
    assert client.put(path(own, own_profile["id"], "courses"), json={"expected_version": 1, "course_ids": [other_course["id"]]}, headers=admin).status_code == 404
    assert client.post(path(other, foreign["id"], "bind"), json={"expected_version": 1, "principal_id": "demo-instructor"}, headers=admin).status_code == 422
    with app.state.database.write() as db:
        record = Record(id="foreign-tenant-profile", tenant_id="another-tenant", kind="teacher_profile", parent_id=own, data=canonical({"kind": "human", "name": "Hidden", "version": 1}))
        db.add(record)
        db.flush()
        bind_scope(db, record, own)
    assert client.get(path(own, "foreign-tenant-profile")).status_code == 404
    manager = login(client, "demo-school")
    assert client.get(path(other)).status_code == 403
    assert client.put(path(other, foreign["id"]), json={"expected_version": 1, "name": "No"}, headers=manager).status_code == 403


def test_explicit_human_binding_is_eligible_exact_and_permanent(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    profile = create(client, headers, school, contact_email="demo-instructor@example.test")
    assert client.post(path(school, profile["id"], "bind"), json={"expected_version": 1, "principal_id": "demo-student"}, headers=headers).status_code == 422
    assert client.post(path(school, profile["id"], "bind"), json={"expected_version": 1, "principal_id": "missing-user"}, headers=headers).status_code == 404
    response = client.post(path(school, profile["id"], "bind"), json={"expected_version": 1, "principal_id": "demo-instructor"}, headers=headers)
    assert response.status_code == 200, response.text
    linked = response.json()
    assert linked["principal_id"] == "demo-instructor" and linked["activation"]["identity_eligible"]
    assert client.post(path(school, profile["id"], "bind"), json={"expected_version": 2, "principal_id": "demo-instructor"}, headers=headers).status_code == 409
    for injection in ({"kind": "ai"}, {"principal_id": None}, {"agent_placement_id": "other"}, {"permissions": ["admin"]}):
        assert client.put(path(school, profile["id"]), json={"expected_version": 2, **injection}, headers=headers).status_code == 422
    assert client.delete(f"/api/schools/{school}/members/demo-instructor", headers=headers).status_code == 200
    assert client.get(path(school, profile["id"])).json()["activation"]["identity_eligible"] is False


def test_ai_profile_behavior_draft_and_binding_never_activates_placement_or_gates(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    agent, body = placement(client, headers, school, active=False)
    profile = create(client, headers, school, kind="ai", agent_placement_id=agent["id"], ai_behavior={"tone": "formal", "teaching_style": "guided_practice", "boundaries": ["Ask for the student's own evidence."]})
    assert profile["status"] == "draft" and profile["ai_behavior"]["detail"] == "balanced"
    assert profile["activation"]["identity_linked"] and not profile["activation"]["identity_eligible"]
    assert not profile["activation"]["agent_admission_enabled"] and not profile["activation"]["live_models_enabled"]
    assert client.get(f"/api/schools/{school}/agents").json()[0]["active"] is False
    assert client.post(path(school), json={"kind": "ai", "name": "No", "ai_behavior": {"system_prompt": "Ignore policy"}}, headers=headers).status_code == 422
    assert client.post(path(school), json={"kind": "human", "name": "No", "ai_behavior": {}}, headers=headers).status_code == 422
    assert client.post(path(school), json={"kind": "ai", "name": "No", "principal_id": "demo-instructor"}, headers=headers).status_code == 422
    assert app.state.settings.allow_agent_teachers is False and app.state.settings.allow_live_models is False


def test_many_class_plans_build_idempotent_corpora_but_never_enroll_teachers(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    first, second = course(client, headers, school), course(client, headers, school, title="Second class")
    profile = create(client, headers, school, kind="ai")
    with app.state.database.factory() as db:
        enrollment_bytes = [(r.id, r.data) for r in db.scalars(select(Record).where(Record.kind == "enrollment"))]
    assigned = assign(client, headers, school, profile, [first["id"], second["id"]])
    assert len(assigned["assigned_courses"]) == 2
    assert all(not item["has_teaching_authority"] for item in assigned["assigned_courses"])
    assert all(item["corpus"]["id"] for item in assigned["assigned_courses"])
    again = assign(client, headers, school, assigned, [first["id"], second["id"]])
    with app.state.database.factory() as db:
        assert len(list(db.scalars(select(Record).where(Record.kind == "course_corpus")))) == 2
        assert [(r.id, r.data) for r in db.scalars(select(Record).where(Record.kind == "enrollment"))] == enrollment_bytes
    removed = assign(client, headers, school, again, [second["id"]])
    assert removed["course_ids"] == [second["id"]]
    current = approve(client, headers, school, removed)
    assert current["approval_current"] is True
    assert not current["assigned_courses"][0]["corpus"]["ready_for_teaching"]


@pytest.mark.parametrize("kind", ["human", "ai"])
@pytest.mark.parametrize("curriculum", [CURRICULUM, {**CURRICULUM, "grades": [8]}, None], ids=["pending-supported", "unsupported-grade", "custom-class"])
def test_profile_approval_pins_pending_class_context_without_approving_materials_or_granting_authority(client, app, kind, curriculum):
    headers = login(client, "demo-school")
    school = school_id(app)
    response = client.post("/api/courses", json={"school_id": school, "title": "Profile review before class preparation", "curriculum": curriculum}, headers=headers)
    assert response.status_code == 201, response.text
    classroom = response.json()
    profile = assign(client, headers, school, create(client, headers, school, kind=kind), [classroom["id"]])
    before = client.get(f"/api/courses/{classroom['id']}/corpus").json()
    assert not before["ready_for_teaching"] and before["approval"]["status"] == "pending"
    with app.state.database.factory() as db:
        authority = [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind.in_(["school_membership", "enrollment", "user", "agent_placement"])).order_by(Record.id))]
        approvals = [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind == "corpus_approval").order_by(Record.id))]
    current = approve(client, headers, school, profile)
    assert current["approval_current"] is True and current["approved_version"] == current["version"]
    assert current["activation"]["managed_lesson_configured"] is False
    assert current["activation"]["runtime_active"] is False and current["authority_granted"] is False
    assert client.get(f"/api/courses/{classroom['id']}/corpus").json() == before
    with app.state.database.factory() as db:
        revision = db.get(Record, current["approved_revision_id"])
        assert revision.value["approved_by"] == "demo-school"
        assert revision.value["snapshot"]["courses"][0]["corpus"]["approval"]["status"] == "pending"
        assert [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind == "corpus_approval").order_by(Record.id))] == approvals
        assert [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind.in_(["school_membership", "enrollment", "user", "agent_placement"])).order_by(Record.id))] == authority
        events = list(db.scalars(select(Audit).where(Audit.event_type == "teacher_profile.approved")))
        assert len(events) == 1 and current["approved_revision_id"] in events[0].payload


def test_approving_pending_corpus_requires_a_fresh_profile_review_before_managed_teaching(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    classroom = course(client, headers, school)
    connection = client.post("/api/admin/connections", json={"name": "Pending review model", "school_id": school, "endpoint": "https://models.example.test/v1", "models": ["bounded"], "enabled": False}, headers=headers).json()
    profile = create(client, headers, school, kind="ai", tutor_model={"connection_id": connection["id"], "model": "bounded"})
    current = approve(client, headers, school, assign(client, headers, school, profile, [classroom["id"]]))
    assert current["approval_current"] is True and not current["activation"]["managed_lesson_configured"]
    corpus = client.get(f"/api/courses/{classroom['id']}/corpus").json()
    reviewed = client.post(f"/api/courses/{classroom['id']}/corpus/approve", json={"corpus_id": corpus["id"], "snapshot_sha256": corpus["snapshot_sha256"]}, headers=headers)
    assert reviewed.status_code == 200 and reviewed.json()["ready_for_teaching"], reviewed.text
    stale = client.get(path(school, current["id"])).json()
    assert stale["approved_version"] == stale["version"] and stale["approval_current"] is False
    assert not stale["activation"]["managed_lesson_configured"]
    refreshed = approve(client, headers, school, stale)
    assert refreshed["approval_current"] is True and refreshed["activation"]["managed_lesson_configured"] is True
    assert refreshed["approved_revision_id"] != current["approved_revision_id"]


def test_picture_actual_raster_validation_strips_metadata_and_avoids_audit_bytes(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    raw = png(extra=b"Comment\0https://example.test/secret-metadata")
    profile = create(client, headers, school, picture=picture(raw), contact_email="private@example.test")
    image = profile["picture"]
    assert (image["width"], image["height"], image["media_type"]) == (1, 1, "image/png")
    normalized = base64.b64decode(image["data_base64"])
    assert b"secret-metadata" not in normalized and b"tEXt" not in normalized
    assert image["sha256"] == hashlib.sha256(normalized).hexdigest()
    response = client.put(path(school, profile["id"]), json={"expected_version": 1, "title": "Ecology Teacher"}, headers=headers)
    assert response.status_code == 200 and response.json()["picture"] == image
    cleared = client.put(path(school, profile["id"]), json={"expected_version": 2, "picture": None}, headers=headers)
    assert cleared.status_code == 200 and cleared.json()["picture"] is None
    with app.state.database.factory() as db:
        events = list(db.scalars(select(Audit).where(Audit.event_type.like("teacher_profile.%"))))
        assert image["sha256"] in events[0].payload
        assert all(image["data_base64"] not in event.payload and "private@example.test" not in event.payload for event in events)


@pytest.mark.parametrize("image", [
    {"media_type": "image/svg+xml", "data_base64": base64.b64encode(b"<svg/>").decode()},
    {"media_type": "image/png", "data_base64": "https://example.test/picture.png"},
    picture(b"<html>not raster</html>"),
    picture(png(width=513)),
    picture(png(pixels=b"\x05\0\0\0\0")),
    picture(png(pixels=b"\0" * 1000000)),
    picture(png() + b"<script>extra</script>"),
    picture(png()[:-5]),
    picture(b"\x89PNG\r\n\x1a\n" + b"x" * 65536),
])
def test_invalid_oversized_or_forged_pictures_are_rejected(client, app, image):
    headers = login(client, "demo-admin")
    response = client.post(path(school_id(app)), json={"kind": "human", "name": "Invalid picture", "picture": image}, headers=headers)
    assert response.status_code == 422, response.text


def test_approval_versions_are_immutable_and_edits_invalidate_current_approval(client, app):
    headers = login(client, "demo-school")
    school = school_id(app)
    profile = create(client, headers, school, kind="ai", ai_behavior={"tone": "neutral"})
    approved = approve(client, headers, school, profile)
    assert approved["status"] == "ready" and approved["approved_version"] == approved["version"] == 2
    with app.state.database.factory() as db:
        original = db.get(Record, approved["approved_revision_id"]).data
        snapshot = json.loads(original)["snapshot"]
        assert snapshot["ai_behavior"]["tone"] == "neutral" and "contact_email" not in snapshot
    response = client.put(path(school, profile["id"]), json={"expected_version": 2, "ai_behavior": {"tone": "warm"}}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["approved_revision_id"] is None
    assert client.post(path(school, profile["id"], "approve"), json={"expected_version": 2}, headers=headers).status_code == 409
    with app.state.database.factory() as db:
        assert db.get(Record, approved["approved_revision_id"]).data == original
    with pytest.raises(ValueError, match="immutable|append-only"):
        with app.state.database.write() as db:
            db.get(Record, approved["approved_revision_id"]).set({"tampered": True})
    archived = client.delete(path(school, profile["id"]), headers=headers)
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert client.post(path(school, profile["id"], "approve"), json={"expected_version": archived.json()["version"]}, headers=headers).status_code == 409


@pytest.fixture
def ready_teacher(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    agent, body = placement(client, headers, school)
    planned_course = course(client, headers, school, teacher=agent["principal_id"])
    # Plan first: corpus may still be pending. Approval later refreshes its ref.
    profile = create(client, headers, school, kind="ai", agent_placement_id=agent["id"], name="Evidence Coach", biography="A bounded synthetic teaching persona", contact_email="never-to-students@example.test", picture=picture(png()))
    profile = assign(client, headers, school, profile, [planned_course["id"]])
    assert not profile["assigned_courses"][0]["corpus"]["ready_for_teaching"]
    resource = approved_corpus(client, headers, school, planned_course["id"])
    profile = approve(client, headers, school, profile)
    assert client.post(f"/api/courses/{planned_course['id']}/enrollments", json={"user_id": "demo-student"}, headers=headers).status_code == 200
    return {"headers": headers, "school": school, "profile": profile, "course": planned_course, "placement": agent, "placement_body": body, "resource": resource}


def context_url(data):
    return path(data["school"], data["profile"]["id"], "context") + "?course_id=" + data["course"]["id"]


def test_safe_context_and_student_cards_require_real_class_authority_not_planning(client, app, ready_teacher):
    data = ready_teacher
    response = client.get(context_url(data))
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["approved_hash"] == data["profile"]["approved_hash"]
    assert "contact_email" not in context and "data_base64" not in response.text and "core_binding_id" not in response.text and "documents" not in context["corpus"]
    login(client, "demo-student")
    assert client.get(context_url(data)).status_code == 200
    card = client.get(f"/api/courses/{data['course']['id']}").json()["teachers"][0]
    assert card["name"] == "Evidence Coach" and card["available_for_tutoring"] is False
    assert not {"contact_email", "principal_id", "agent_placement_id", "binding_context"} & card.keys()
    login(client, "demo-student2")
    assert client.get(context_url(data)).status_code == 403
    headers = login(client, "demo-admin")
    # Removing actual enrollment/ownership is independent of the profile plan.
    assert client.put(f"/api/courses/{data['course']['id']}", json={"title": data["course"]["title"], "teacher_id": "demo-instructor"}, headers=headers).status_code == 200
    assert client.delete(f"/api/courses/{data['course']['id']}/enrollments/{data['placement']['principal_id']}", headers=headers).status_code == 200
    assert client.get(context_url(data)).status_code == 403
    assert client.get(path(data["school"], data["profile"]["id"])).json()["course_ids"] == [data["course"]["id"]]


def test_current_corpus_resource_edit_and_new_approval_invalidate_teacher_context(client, ready_teacher):
    data = ready_teacher
    assert client.get(context_url(data)).status_code == 200
    resource = data["resource"]
    response = client.put(f"/api/resources/{resource['id']}", json={"name": resource["name"], "content": "Revised, human-authored ecology materials and new evidence.", "course_id": data["course"]["id"], "audience": "teachers"}, headers=data["headers"])
    assert response.status_code == 200, response.text
    assert client.get(context_url(data)).status_code == 409
    descriptor = client.post(f"/api/courses/{data['course']['id']}/corpus/refresh", headers=data["headers"]).json()
    assert client.post(f"/api/courses/{data['course']['id']}/corpus/approve", json={"corpus_id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"]}, headers=data["headers"]).status_code == 200
    # A new corpus approval cannot silently adopt a previous teacher approval.
    assert client.get(context_url(data)).status_code == 409
    updated = approve(client, data["headers"], data["school"], data["profile"])
    assert updated["approved_revision_id"] != data["profile"]["approved_revision_id"]
    assert client.get(context_url(data)).status_code == 200


def test_placement_revocation_or_boundary_repointing_invalidates_approved_context(client, ready_teacher):
    data = ready_teacher
    assert client.get(context_url(data)).status_code == 200
    body = {**data["placement_body"], "active": False}
    endpoint = f"/api/schools/{data['school']}/agents/{data['placement']['id']}"
    assert client.put(endpoint, json=body, headers=data["headers"]).status_code == 200
    assert client.get(context_url(data)).status_code == 403
    body["active"] = True
    assert client.put(endpoint, json=body, headers=data["headers"]).status_code == 200
    assert client.get(context_url(data)).status_code == 200
    body["core_binding_id"] = str(uuid4())
    assert client.put(endpoint, json=body, headers=data["headers"]).status_code == 200
    assert client.get(context_url(data)).status_code == 403


def test_human_teacher_reads_only_exact_linked_context_with_live_local_membership(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    planned_course = course(client, headers, school)
    approved_corpus(client, headers, school, planned_course["id"])
    own = create(client, headers, school, principal_id="demo-instructor", contact_email="private@example.test")
    own = approve(client, headers, school, assign(client, headers, school, own, [planned_course["id"]]))
    other_agent, _ = placement(client, headers, school)
    assert client.post(f"/api/courses/{planned_course['id']}/enrollments", json={"user_id": other_agent["principal_id"], "role": "teacher"}, headers=headers).status_code == 200
    other = create(client, headers, school, kind="ai", agent_placement_id=other_agent["id"])
    other = approve(client, headers, school, assign(client, headers, school, other, [planned_course["id"]]))
    context = path(school, own["id"], "context") + "?course_id=" + planned_course["id"]
    other_context = path(school, other["id"], "context") + "?course_id=" + planned_course["id"]
    login(client, "demo-instructor")
    response = client.get(context)
    assert response.status_code == 200, response.text
    assert response.json()["ai_behavior"] is None and "private@example.test" not in response.text
    assert client.get(other_context).status_code == 403
    # Separate human Core capability ceilings are checked on the next request.
    with app.state.database.write() as db:
        member = db.get(Record, membership_id(school, "demo-instructor"))
        member.set({**member.value, "active": False})
    assert client.get(context).status_code == 403


def test_exact_linked_agent_has_read_context_parity_but_no_profile_admin(client, app, ready_teacher, monkeypatch):
    data = ready_teacher
    body = data["placement_body"]
    identity = {"id": data["placement"]["principal_id"], "name": "Synthetic Teacher", "tenant_id": app.state.settings.tenant_id, "role": "teacher", "principal_kind": "agent", "school_id": data["school"], "placement_id": data["placement"]["id"], "account_id": body["core_agent_id"], "binding_id": body["core_binding_id"], "consumer_service_id": body["core_consumer_service_id"], "environment": body["environment"], "task_id": body["task_id"], "permissions": ["academy-use", "academy-teach"], "csrf_token": ""}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    assert client.get(context_url(data)).status_code == 200
    assert client.get(path(data["school"], data["profile"]["id"])).status_code == 403
    assert client.post(path(data["school"], data["profile"]["id"], "approve"), json={"expected_version": data["profile"]["version"]}).status_code == 403
    identity["permissions"] = ["academy-use"]
    assert client.get(context_url(data)).status_code == 403


def test_school_capability_revocation_blocks_onboarding_next_request(client, app, monkeypatch):
    headers = login(client, "demo-school")
    school = school_id(app)
    profile = create(client, headers, school)
    identity = {"id": "demo-school", "tenant_id": app.state.settings.tenant_id, "role": "school", "principal_kind": "human", "permissions": ["academy-use"], "csrf_token": headers["X-CSRF-Token"]}
    monkeypatch.setattr(app.state.auth, "actor", lambda request: dict(identity))
    app.state.settings.auth_mode = "core"
    assert client.get(path(school)).status_code == 403
    assert client.put(path(school, profile["id"]), json={"expected_version": 1, "name": "No"}, headers=headers).status_code == 403


def reapprove_corpus(client, headers, course_id):
    descriptor = client.post(f"/api/courses/{course_id}/corpus/refresh", headers=headers).json()
    response = client.post(f"/api/courses/{course_id}/corpus/approve", json={"corpus_id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"]}, headers=headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("changed", ["course", "source"])
def test_approval_current_requires_teacher_reapproval_after_course_or_source_review(client, ready_teacher, changed):
    data = ready_teacher
    endpoint = path(data["school"], data["profile"]["id"])
    current = client.get(endpoint).json()
    assert current["approval_current"] is True
    if changed == "course":
        response = client.put(f"/api/courses/{data['course']['id']}", json={"title": "Revised course title", "curriculum": {**CURRICULUM, "grades": [11]}}, headers=data["headers"])
    else:
        response = client.put(f"/api/resources/{data['resource']['id']}", json={"name": "Revised lesson", "content": "New, human-authored ecosystem evidence for review.", "course_id": data["course"]["id"], "audience": "teachers"}, headers=data["headers"])
    assert response.status_code == 200, response.text
    stale = client.get(endpoint).json()
    assert stale["approved_version"] == stale["version"] and stale["approval_current"] is False
    reapprove_corpus(client, data["headers"], data["course"]["id"])
    assert client.get(endpoint).json()["approval_current"] is False
    refreshed = approve(client, data["headers"], data["school"], stale)
    assert refreshed["approval_current"] is True
    assert refreshed["version"] == stale["version"] + 1


def test_approval_current_checks_every_planned_course_when_only_one_changes(client, ready_teacher):
    data = ready_teacher
    other = course(client, data["headers"], data["school"], title="Second planned class")
    resource = approved_corpus(client, data["headers"], data["school"], other["id"])
    profile = assign(client, data["headers"], data["school"], data["profile"], [data["course"]["id"], other["id"]])
    profile = approve(client, data["headers"], data["school"], profile)
    assert profile["approval_current"] is True
    response = client.put(f"/api/resources/{resource['id']}", json={"name": resource["name"], "content": "A changed second-class resource requiring review.", "course_id": other["id"], "audience": "teachers"}, headers=data["headers"])
    assert response.status_code == 200, response.text
    stale = client.get(path(data["school"], profile["id"])).json()
    readiness = {item["id"]: item["corpus"]["ready_for_teaching"] for item in stale["assigned_courses"]}
    assert readiness == {data["course"]["id"]: True, other["id"]: False}
    assert stale["approval_current"] is False
    reapprove_corpus(client, data["headers"], other["id"])
    assert client.get(path(data["school"], profile["id"])).json()["approval_current"] is False
    assert approve(client, data["headers"], data["school"], stale)["approval_current"] is True


def test_approval_current_requires_exact_approved_course_set(client, app, ready_teacher):
    data = ready_teacher
    other = course(client, data["headers"], data["school"], title="Second planned class")
    approved_corpus(client, data["headers"], data["school"], other["id"])
    profile = approve(client, data["headers"], data["school"], assign(client, data["headers"], data["school"], data["profile"], [data["course"]["id"], other["id"]]))
    assert profile["approval_current"] is True
    # A missing planning link cannot leave the remaining approved subset current,
    # even if metadata/revision numbers were accidentally left unchanged.
    with app.state.database.write() as db:
        link = next(row for row in db.scalars(select(Record).where(Record.kind == "teacher_profile_course", Record.parent_id == profile["id"])) if row.value["course_id"] == other["id"])
        link.set({**link.value, "active": False})
    current = client.get(path(data["school"], profile["id"])).json()
    assert current["approved_version"] == current["version"]
    assert current["course_ids"] == [data["course"]["id"]] and current["approval_current"] is False


def test_no_course_metadata_approval_is_current_without_runtime_admission(client, app):
    headers = login(client, "demo-school")
    school = school_id(app)
    profile = create(client, headers, school, kind="ai")
    assert profile["approval_current"] is False
    current = approve(client, headers, school, profile)
    assert current["approval_current"] is True
    assert app.state.settings.allow_live_models is False and app.state.settings.allow_agent_teachers is False
    assert current["activation"]["managed_lesson_configured"] is False
    archived = client.delete(path(school, profile["id"]), headers=headers).json()
    assert archived["approval_current"] is False


def test_managed_lesson_configuration_requires_current_approval_for_all_course_materials(client, app):
    headers = login(client, "demo-admin")
    school = school_id(app)
    classroom = course(client, headers, school)
    resource = approved_corpus(client, headers, school, classroom["id"])
    connection = client.post("/api/admin/connections", json={"name": "Managed approval fixture", "school_id": school, "endpoint": "https://models.example.test/v1", "models": ["bounded"], "enabled": False}, headers=headers).json()
    profile = create(client, headers, school, kind="ai", tutor_model={"connection_id": connection["id"], "model": "bounded"})
    profile = approve(client, headers, school, assign(client, headers, school, profile, [classroom["id"]]))
    assert profile["approval_current"] is True and profile["activation"]["managed_lesson_configured"] is True
    response = client.put(f"/api/resources/{resource['id']}", json={"name": resource["name"], "content": "Changed managed lesson evidence.", "course_id": classroom["id"], "audience": "teachers"}, headers=headers)
    assert response.status_code == 200, response.text
    current = client.get(path(school, profile["id"])).json()
    assert current["approval_current"] is False and current["activation"]["managed_lesson_configured"] is False
