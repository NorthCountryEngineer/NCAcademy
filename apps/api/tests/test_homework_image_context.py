"""Synthetic retained worksheets keep their ownership, consent and image bytes."""

import base64
from copy import deepcopy

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from academy.curated_lesson_api import _image_metadata
from academy.curated_media import normalize_assignment_image
from academy.db import Record, canonical, now, uid
from academy.homework_image_context import resolve_image_context, revalidate_image_context
from academy.learning_preferences import NEUTRAL
from academy.permissions import bind_scope, default_school_id, membership_id
from conftest import login
from test_curated_lessons import photo
from test_family_accounts import password_login, student
from test_homework_ai import ask, environment


@pytest.fixture
def worksheet(client, app):
    headers = login(client, "demo-admin")
    school_id = default_school_id(app.state.settings.tenant_id)
    child = student(client, app, headers, course_ids=["demo-course"])
    enabled = client.put("/api/homework-help/private-policy", params={"school_id": school_id},
                         json={"enabled": True, "expected_version": 0}, headers=headers)
    assert enabled.status_code == 200
    headers = password_login(client, "sample.student")
    intake = client.put("/api/me/learning-preferences", json={"answers": NEUTRAL, "expected_version": 0}, headers=headers)
    assert intake.status_code == 200
    identity = client.get("/api/auth/me").json()
    session = client.post("/api/homework-help/ai/sessions", json={"school_id": school_id,
                          "idempotency_key": "synthetic-retained-worksheet"}, headers=headers)
    assert session.status_code == 200
    session_id = session.json()["id"]
    normalized = {"id": "image-1", "name": "Private worksheet filename sentinel.png",
                  **normalize_assignment_image(photo())}
    snapshot = {"revision_id": "synthetic-config-revision", "approval_id": "synthetic-image-approval",
                "configuration_sha256": "a" * 64, "connections": {"triage": {"id": "synthetic-approved-vision"}}}

    def add_source(*, scope=school_id, owner=child["id"], parent=session_id, **changes):
        data = {"session_id": parent, "school_id": school_id, "course_id": None,
                "created_at": now(), "status": "released", "private_mode": True,
                "message": "Synthetic original worksheet", "answer": "A reviewed study hint",
                "images": _image_metadata([normalized]), "images_cipher": app.state.auth.seal({"images": [normalized]}),
                "image_use_confirmed": True, **changes}
        with app.state.database.write() as db:
            row = Record(id=uid(), tenant_id=identity["tenant_id"], kind="homework_ai_message",
                         parent_id=parent, owner_id=owner, data=canonical(data))
            db.add(row)
            db.flush()
            bind_scope(db, row, scope)
            return row.id

    source_id = add_source()
    return {"identity": identity, "settings": app.state.settings, "auth": app.state.auth,
            "session_id": session_id, "school_id": school_id, "course_id": None,
            "normalized_images": [], "previous_image_message_id": source_id, "image_use_confirmed": True,
            "configuration_snapshot": snapshot, "normalized": normalized, "source_id": source_id,
            "add_source": add_source, "app": app}


def resolve(db, worksheet, **changes):
    arguments = {key: worksheet[key] for key in ("identity", "settings", "auth", "session_id", "school_id", "course_id",
                 "normalized_images", "previous_image_message_id", "image_use_confirmed", "configuration_snapshot")}
    return resolve_image_context(db, **{**arguments, **changes})


def turn(worksheet, context, **changes):
    return {"session_id": worksheet["session_id"], "school_id": worksheet["school_id"],
            "configuration_snapshot": worksheet["configuration_snapshot"], "image_use_confirmed": True,
            "images": _image_metadata([worksheet["normalized"]]),
            "image_context": context, "message": "Question 7. Look at the sheet.", **changes}


def test_follow_up_reuses_exact_encrypted_worksheet_with_new_photo_confirmation(worksheet):
    with worksheet["app"].state.database.factory() as db:
        original = db.get(Record, worksheet["source_id"])
        assert "__academy_pii__" in original.data
        with pytest.raises(HTTPException) as rejected:
            resolve(db, worksheet, image_use_confirmed=False)
        assert rejected.value.status_code == 403
        images, context = resolve(db, worksheet)
        assert len(images) == 1
        assert images[0]["data_base64"] == worksheet["normalized"]["data_base64"]
        assert images[0]["sha256"] == worksheet["normalized"]["sha256"]
        assert images[0]["name"] == "Homework picture 1"
        assert context["source_message_id"] == worksheet["source_id"] and context["source_private_mode"]
        assert "filename sentinel" not in str(context) and "data_base64" not in str(context)
        assert context["policy_binding"]["revision_id"] == worksheet["configuration_snapshot"]["revision_id"]
        revalidate_image_context(db, worksheet["identity"], worksheet["settings"], turn(worksheet, context))


@pytest.mark.parametrize("source_changes", [
    {"owner": "demo-student"}, {"parent": "another-synthetic-session"}, {"scope": "another-synthetic-school"},
    {"school_id": "another-synthetic-school"},
])
def test_import_rejects_another_owner_conversation_or_school(worksheet, source_changes):
    source_id = worksheet["add_source"](**source_changes)
    with worksheet["app"].state.database.factory() as db, pytest.raises(HTTPException) as rejected:
        resolve(db, worksheet, previous_image_message_id=source_id)
    assert rejected.value.status_code == 404


def test_new_and_retained_images_share_two_picture_limit_and_unique_ids(worksheet):
    current = {"id": "image-1", "name": "new.png", **normalize_assignment_image(photo("yellow"))}
    with worksheet["app"].state.database.factory() as db:
        images, _ = resolve(db, worksheet, normalized_images=[current])
        assert [image["id"] for image in images] == ["image-1", "image-2"]
        assert images[1]["sha256"] == current["sha256"]
        with pytest.raises(HTTPException) as rejected:
            resolve(db, worksheet, normalized_images=[current, current])
        assert rejected.value.status_code == 422
        with pytest.raises(HTTPException) as rejected:
            resolve(db, worksheet, normalized_images=[current] * 3, previous_image_message_id=None)
        assert rejected.value.status_code == 422


def test_carried_picture_metadata_must_match_the_selected_source(worksheet):
    with worksheet["app"].state.database.factory() as db:
        _, context = resolve(db, worksheet)
        changed = turn(worksheet, context)
        changed["images"][0]["sha256"] = "0" * 64
        with pytest.raises(HTTPException) as rejected:
            revalidate_image_context(db, worksheet["identity"], worksheet["settings"], changed)
        assert rejected.value.status_code == 409


def test_pending_and_corrupted_source_photos_cannot_be_imported(worksheet):
    pending = worksheet["add_source"](status="pending")
    corrupted = deepcopy(worksheet["normalized"])
    corrupted["data_base64"] = base64.b64encode(b"not the retained JPEG").decode()
    invalid = worksheet["add_source"](images_cipher=worksheet["auth"].seal({"images": [corrupted]}))
    with worksheet["app"].state.database.factory() as db:
        for source_id in (pending, invalid):
            with pytest.raises(HTTPException) as rejected:
                resolve(db, worksheet, previous_image_message_id=source_id)
            assert rejected.value.status_code == 409


def test_changed_destination_policy_and_lost_school_membership_withhold_carried_photo(worksheet):
    with worksheet["app"].state.database.factory() as db:
        _, context = resolve(db, worksheet)
        changed = deepcopy(worksheet["configuration_snapshot"])
        changed["connections"]["triage"]["id"] = "different-synthetic-vision-provider"
        with pytest.raises(HTTPException) as rejected:
            revalidate_image_context(db, worksheet["identity"], worksheet["settings"], turn(worksheet, context,
                                     configuration_snapshot=changed))
        assert rejected.value.status_code == 409
    with worksheet["app"].state.database.write() as db:
        member = db.get(Record, membership_id(worksheet["school_id"], worksheet["identity"]["id"]))
        member.set({**member.value, "active": False})
    with worksheet["app"].state.database.factory() as db, pytest.raises(HTTPException) as rejected:
        revalidate_image_context(db, worksheet["identity"], worksheet["settings"], turn(worksheet, context))
    assert rejected.value.status_code == 403


def test_current_class_enrollment_is_required_for_retained_class_picture(worksheet):
    source_id = worksheet["add_source"](course_id="demo-course")
    with worksheet["app"].state.database.factory() as db:
        _, context = resolve(db, worksheet, previous_image_message_id=source_id)
    with worksheet["app"].state.database.write() as db:
        enrollment = db.scalar(select(Record).where(Record.kind == "enrollment", Record.parent_id == "demo-course",
                                                   Record.owner_id == worksheet["identity"]["id"]))
        db.delete(enrollment)
    with worksheet["app"].state.database.factory() as db, pytest.raises(HTTPException) as rejected:
        revalidate_image_context(db, worksheet["identity"], worksheet["settings"], turn(worksheet, context))
    assert rejected.value.status_code == 403


def test_nested_retained_picture_keeps_original_scope_dependency(worksheet):
    original = worksheet["add_source"](course_id="demo-course")
    with worksheet["app"].state.database.factory() as db:
        images, context = resolve(db, worksheet, previous_image_message_id=original)
    carried = worksheet["add_source"](**turn(worksheet, context, images=_image_metadata(images)),
                                     images_cipher=worksheet["auth"].seal({"images": images}))
    with worksheet["app"].state.database.factory() as db:
        _, follow_up = resolve(db, worksheet, previous_image_message_id=carried)
    with worksheet["app"].state.database.write() as db:
        enrollment = db.scalar(select(Record).where(Record.kind == "enrollment", Record.parent_id == "demo-course",
                                                   Record.owner_id == worksheet["identity"]["id"]))
        db.delete(enrollment)
    with worksheet["app"].state.database.factory() as db, pytest.raises(HTTPException) as rejected:
        revalidate_image_context(db, worksheet["identity"], worksheet["settings"], turn(worksheet, follow_up))
    assert rejected.value.status_code == 403


def test_question_seven_follow_up_sends_the_same_worksheet_to_each_approved_phase(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, allow_images=True, triage_image_capable=True,
                      tutor_image_capable=True, safety_image_capable=True, max_daily_tokens=1000000)
    initial = ask(client, env, message="Help me understand the worksheet", images=[{"name": "synthetic-sheet.png", "data_url": photo()}],
                  image_use_confirmed=True)
    assert initial.status_code == 200 and initial.json()["status"] == "released", initial.text
    original = initial.json()
    before = len(env["calls"])
    old_image = env["calls"][0][3][1]["content"][1]["image_url"]["url"]
    assistant = client.get("/api/homework-help/ai", params={"school_id": env["school"]}).json()["assistant"]
    followup = {"message": "Question 7. Look at the sheet.", "idempotency_key": "synthetic-retained-question-seven",
                "previous_image_message_id": original["id"],
                "expected_image_policy_sha256": assistant["image_policy_sha256"]}
    refused = ask(client, env, **followup, image_use_confirmed=False)
    assert refused.status_code == 403 and len(env["calls"]) == before
    stale = ask(client, env, **{**followup, "expected_image_policy_sha256": "0" * 64},
                image_use_confirmed=True)
    assert stale.status_code == 409 and len(env["calls"]) == before
    response = ask(client, env, **followup, image_use_confirmed=True)
    assert response.status_code == 200 and response.json()["status"] == "released", response.text
    assert response.json()["images"][0]["sha256"] == original["images"][0]["sha256"]
    for call in env["calls"][before:]:
        assert call[3][1]["content"][1]["image_url"]["url"] == old_image
    final_count = len(env["calls"])
    replay = ask(client, env, **followup, image_use_confirmed=True)
    assert replay.json() == response.json() and len(env["calls"]) == final_count
    history = client.get(f"/api/homework-help/ai/sessions/{env['session']['id']}").json()
    assert len(history["messages"]) == 2


def test_credential_rotation_with_same_configuration_revision_requires_fresh_photo_consent(client, app, monkeypatch):
    env = environment(client, app, monkeypatch, allow_images=True, triage_image_capable=True,
                      tutor_image_capable=True, safety_image_capable=True, max_daily_tokens=1000000)
    original = ask(client, env, images=[{"name": "synthetic-sheet.png", "data_url": photo()}], image_use_confirmed=True)
    assert original.status_code == 200 and original.json()["status"] == "released", original.text
    old = client.get("/api/homework-help/ai", params={"school_id": env["school"]}).json()["assistant"]
    before = len(env["calls"])
    admin = login(client, "demo-admin")
    connection = env["connection"]
    rotated = client.put(f"/api/admin/connections/{connection['id']}", json={
        "version": connection["version"], "name": connection["name"], "endpoint": connection["endpoint"],
        "models": connection["models"], "enabled": True, "api_key": "different-synthetic-homework-credential",
    }, headers=admin)
    assert rotated.status_code == 200, rotated.text
    approved = client.post("/api/homework-help/configuration/approve", params={"school_id": env["school"]},
                           json={"revision_id": old["revision_id"]}, headers=admin)
    assert approved.status_code == 200, approved.text
    env["headers"] = login(client)
    current = client.get("/api/homework-help/ai", params={"school_id": env["school"]}).json()["assistant"]
    assert current["revision_id"] == old["revision_id"]
    assert current["image_policy_sha256"] != old["image_policy_sha256"]
    body = {"message": "Please look at question 7 on the sheet", "previous_image_message_id": original.json()["id"],
            "image_use_confirmed": True, "expected_image_policy_sha256": old["image_policy_sha256"]}
    stale = ask(client, env, **body, idempotency_key="synthetic-rotated-photo-stale")
    assert stale.status_code == 409 and len(env["calls"]) == before
    fresh = ask(client, env, **{**body, "expected_image_policy_sha256": current["image_policy_sha256"]},
                idempotency_key="synthetic-rotated-photo-fresh")
    assert fresh.status_code == 200 and fresh.json()["status"] == "released", fresh.text
