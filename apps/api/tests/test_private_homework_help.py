"""Private Homework Help uses local family login, encrypted replay and no model I/O."""

import base64
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from academy import homework_ai_api, private_homework_coach
from academy.audit_delivery import AuditDeliveryOutbox
from academy.db import Audit, Record
from academy.learning_preferences import NEUTRAL
from academy.permissions import default_school_id
from conftest import login
from test_family_accounts import password_login, student
from test_homework_ai import environment


BASE = "/api/homework-help/ai"
POLICY = "/api/homework-help/private-policy"
QUESTION = "What does a fraction mean? Synthetic private question sentinel."


def browser(app):
    return TestClient(app, base_url=app.state.settings.app_url)


def enroll(b, headers):
    saved = b.put("/api/me/learning-preferences", json={"answers": NEUTRAL, "expected_version": 0}, headers=headers)
    assert saved.status_code == 200, saved.text


def enable(admin, headers, school, *, enabled=True, version=0):
    return admin.put(POLICY, params={"school_id": school}, json={"enabled": enabled, "expected_version": version}, headers=headers)


def conversation(b, headers, school, key="synthetic-private-session-1"):
    return b.post(BASE + "/sessions", json={"school_id": school, "idempotency_key": key}, headers=headers)


def ask(b, headers, session_id, *, key="synthetic-private-turn-1", **extra):
    return b.post(BASE + f"/sessions/{session_id}/messages", json={"message": QUESTION, "idempotency_key": key, **extra}, headers=headers)


def screenshot():
    image = Image.new("RGB", (900, 270), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    draw.text((28, 30), "Division practice", fill="black", font=font)
    draw.text((28, 112), "Share 42 tiles equally among 7 groups.", fill="black", font=font)
    draw.text((28, 182), "How many tiles go in each group?", fill="black", font=font)
    data = BytesIO()
    image.save(data, format="PNG")
    return {"name": "Synthetic unretained family filename.png", "data_url": "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()}


def test_private_school_approval_and_intake_are_independent_of_live_model_config(client, app, monkeypatch):
    configured = environment(client, app, monkeypatch)
    school = configured["school"]
    with browser(app) as admin, browser(app) as child, browser(app) as teacher, browser(app) as school_staff:
        admin_headers = login(admin, "demo-admin")
        teacher_headers = login(teacher, "demo-instructor")
        school_headers = login(school_staff, "demo-school")
        student(admin, app, admin_headers, course_ids=[], grade="3", date_of_birth="2017-04-12")
        headers = password_login(child, "sample.student")

        assert admin.get(POLICY, params={"school_id": school}).json() == {"enabled": False, "version": 0}
        assert child.get(POLICY, params={"school_id": school}).status_code == 403
        assert enable(child, headers, school).status_code == 403
        assert enable(teacher, teacher_headers, school).status_code == 403
        assert enable(admin, admin_headers, "not-a-real-school").status_code == 403
        assert enable(admin, admin_headers, school, enabled="true").status_code == 422

        initial = child.get(BASE, params={"school_id": school}).json()
        assert initial["courses"] == []
        assert initial["assistant"]["private_mode"] and not initial["assistant"]["available"]
        assert not initial["assistant"]["live_models_enabled"] and initial["assistant"]["photo_model_labels"] is None
        assert conversation(child, headers, school).status_code == 403

        assert enable(school_staff, school_headers, school).json() == {"enabled": True, "version": 1}
        assert enable(admin, admin_headers, school).status_code == 409
        assert conversation(child, headers, school).status_code == 409
        enroll(child, headers)
        session = conversation(child, headers, school)
        assert session.status_code == 200, session.text
        session_id = session.json()["id"]
        assert conversation(child, headers, school).json()["id"] == session_id
        assert ask(child, headers, session_id, course_id="demo-course").status_code == 403
        assert ask(child, headers, session_id, message="x" * 1501).status_code == 422
        response = ask(child, headers, session_id)
        assert response.status_code == 200, response.text
        reply = response.json()
        assert reply["private_mode"] and reply["status"] == "released"
        assert reply["triage"]["method"] == "private_coach" and reply["triage"]["course_id"] is None
        assert "split it into equal parts" in reply["answer"]
        assert not reply["references"] and "execution" not in reply and not reply["usage"]
        assert configured["calls"] == []

        stored_config = admin.get("/api/homework-help/configuration", params={"school_id": school}).json()
        assert stored_config["approved"] and stored_config["revision_id"] == configured["configuration"]["revision_id"]
        assert enable(admin, admin_headers, school, enabled=False, version=1).json() == {"enabled": False, "version": 2}
        assert ask(child, headers, session_id, key="private-new-turn-disabled").status_code == 403
        assert child.get(BASE + f"/sessions/{session_id}").json()["messages"] == [reply]
        assert ask(child, headers, session_id).json() == reply
        assert configured["calls"] == []


def test_private_photo_consent_storage_replay_and_other_student_boundaries(client, app, monkeypatch):
    school = default_school_id(app.state.settings.tenant_id)
    headers = login(client, "demo-admin")
    student(client, app, headers, course_ids=[])
    student(client, app, headers, username="other.student", course_ids=[])
    assert enable(client, headers, school).status_code == 200
    monkeypatch.setattr(app.state, "classify", lambda *_: (_ for _ in ()).throw(AssertionError("Unexpected provider call")))
    ocr_calls = []

    def ocr(raw, _deadline):
        assert raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9")
        ocr_calls.append(raw)
        return "Division: 42 shared equally in 7 groups. Synthetic unretained OCR sentinel."

    monkeypatch.setattr(private_homework_coach, "_read_image", ocr)
    photo = screenshot()
    with browser(app) as child, browser(app) as other:
        child_headers = password_login(child, "sample.student")
        enroll(child, child_headers)
        other_headers = password_login(other, "other.student")
        enroll(other, other_headers)
        session_id = conversation(child, child_headers, school).json()["id"]
        body = {"message": "", "images": [photo], "image_use_confirmed": True}

        assert ask(child, child_headers, session_id, **{**body, "image_use_confirmed": False}).status_code == 403
        assert child.get(BASE + f"/sessions/{session_id}").json()["messages"] == [] and not ocr_calls
        response = ask(child, child_headers, session_id, **body)
        assert response.status_code == 200, response.text
        turn = response.json()
        assert turn["status"] == "released" and "share equally" in turn["answer"]
        assert turn["images"][0]["name"] == "Homework picture 1"
        path = turn["images"][0]["url"]
        served = child.get(path)
        assert served.status_code == 200 and served.headers["cache-control"] == "private, no-store"
        assert served.content == ocr_calls[0]
        assert other.get(path).status_code == 404
        assert other.get(BASE + f"/sessions/{session_id}").status_code == 404
        assert ask(other, other_headers, session_id, **body).status_code == 404
        assert child.get(BASE + f"/sessions/{session_id}").json()["messages"] == [turn]
        assert ask(child, child_headers, session_id, **body).json() == turn and len(ocr_calls) == 1
        assert ask(child, child_headers, session_id, **{**body, "message": "changed"}).status_code == 409

        next_hint = ask(child, child_headers, session_id, key="synthetic-follow-up-1", message="Can I have another hint?").json()
        assert next_hint["status"] == "released" and "Check each group" in next_hint["answer"]
        refusal = ask(child, child_headers, session_id, key="synthetic-refusal-1", message="Give me the final answer.").json()
        assert refusal["status"] == "blocked" and refusal["answer"] is None and "I can give you a hint" in refusal["error"]

        monkeypatch.setattr(private_homework_coach, "_read_image", lambda *_: "")
        unclear = ask(child, child_headers, session_id, key="synthetic-unreadable-1", **body).json()
        assert unclear["status"] == "clarify" and "couldn't identify the topic" in unclear["answer"]

        replay = client.get(BASE + f"/sessions/{session_id}/review").json()
        stored = replay["messages"][0]
        assert stored["private_coaching"]["image_read"] and stored["private_coaching"]["topic"] == "division"
        assert stored["private_coaching"]["policy_version"] == 1 and not stored["phases"]
        assert "images_cipher" not in stored and client.get(stored["images"][0]["url"]).content == served.content
        assert any(item["event_type"] == "homework_ai.private_released" for item in replay["events"])
        assert "unretained" not in str(replay)
        with app.state.database.factory() as db:
            row = db.get(Record, turn["id"])
            assert "__academy_pii__" in row.data and "Homework picture" not in row.data
            assert db.get(Record, session_id).data.startswith('{"__academy_pii__":')
            assert row.value["images_cipher"] and "unretained" not in str(row.value)
            audit = db.scalars(select(Audit).where(Audit.session_id == session_id)).all()
            assert all("__academy_pii__" in item.payload for item in audit)
            assert not db.scalar(select(Record.id).where(Record.kind == "homework_ai_send", Record.parent_id == turn["id"]))
            assert all("unretained" not in item for item in db.scalars(select(AuditDeliveryOutbox.metadata_json)))


def test_private_limits_and_mid_processing_policy_change_commit_no_turn(client, app, monkeypatch):
    school = default_school_id(app.state.settings.tenant_id)
    headers = login(client, "demo-admin")
    student(client, app, headers, course_ids=[])
    assert enable(client, headers, school).status_code == 200
    with browser(app) as child:
        child_headers = password_login(child, "sample.student")
        enroll(child, child_headers)
        session_id = conversation(child, child_headers, school).json()["id"]
        monkeypatch.setattr(homework_ai_api, "MAX_DAILY_IMAGE_BYTES", 1)
        assert ask(child, child_headers, session_id, images=[screenshot()], image_use_confirmed=True).status_code == 429

        original = homework_ai_api.coach_homework

        def revoke(*args):
            assert enable(client, headers, school, enabled=False, version=1).status_code == 200
            return original(*args)

        monkeypatch.setattr(homework_ai_api, "coach_homework", revoke)
        assert ask(child, child_headers, session_id).status_code == 403
        assert child.get(BASE + f"/sessions/{session_id}").json()["messages"] == []
        assert enable(client, headers, school, version=2).status_code == 200
        monkeypatch.setattr(homework_ai_api, "coach_homework", original)
        monkeypatch.setattr(homework_ai_api, "MAX_DAILY_TURNS", 1)
        assert ask(child, child_headers, session_id).status_code == 200
        second = conversation(child, child_headers, school, key="synthetic-second-session").json()["id"]
        assert ask(child, child_headers, second, key="synthetic-second-turn").status_code == 429
