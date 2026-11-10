"""End-to-end synthetic daily practice through real class and password routes."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from academy import daily_practice_api as practice
from academy.db import Audit, Record
from academy.grade3_practice_content import grade_practice_item
from academy.permissions import bind_scope
from conftest import login
from test_enrollment_candidates import math_class
from test_family_accounts import password_login, student
from test_school_permissions import grant, new_school, put_user

TODAY = "2026-11-10"
TOMORROW = "2026-11-11"
NAME = "Synthetic Practice Learner"


def ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def approve(browser, headers, course_id):
    base = f"/api/courses/{course_id}/corpus"
    reviewed = ok(browser.get(base))
    return ok(browser.post(base + "/approve", json={"corpus_id": reviewed["id"],
        "snapshot_sha256": reviewed["snapshot_sha256"]}, headers=headers))


@pytest.fixture
def classroom(client, app, monkeypatch):
    monkeypatch.setattr(practice, "_today", lambda: TODAY)
    staff = login(client, "demo-admin")
    course = math_class(client, staff)
    learner = student(client, app, staff, username="practice.synthetic", name=NAME,
                      grade="3", course_ids=[course["id"]])
    base = f"/api/courses/{course['id']}/daily-practice"
    with TestClient(app, base_url=app.state.settings.app_url) as browser:
        learner_headers = password_login(browser, learner["username"])
        yield client, staff, course, learner, base, browser, learner_headers


def draft_body(learner, workspace, **overrides):
    topic = next(topic for topic in workspace["topics"] if topic["unit"] == 1 and
                 "NY-3.OA.4" in topic["standards"] and not topic["id"].startswith("unit-"))
    return {"request_id": "synthetic-draft-1", "student_id": learner["id"], "date": TODAY,
            "topic_id": topic["id"], "count": 6, **overrides}


def draft_published(client, staff, course, learner, base, **overrides):
    approve(client, staff, course["id"])
    body = draft_body(learner, ok(client.get(base)), **overrides)
    made = ok(client.post(base + "/drafts", headers=staff, json=body), 201)
    return ok(client.post(base + f"/{made['id']}/publish", headers=staff, json={}))


def stored_items(app, assignment_id):
    with app.state.database.factory() as db:
        row = db.get(Record, assignment_id)
        assert row.kind == practice.ASSIGNMENT
        return practice._value(app.state.auth, row)["items"]


def answers_for(items, wrong_index=None):
    answers = {}
    for i, item in enumerate(items):
        should_be_correct = i != wrong_index
        candidates = [option["id"] for option in item["options"]] if item["answer_type"] == "choice" else (
            [str(n) for n in range(1001)] if should_be_correct else ["synthetic-private-answer-sentinel"])
        answers[item["id"]] = next(candidate for candidate in candidates
                                  if grade_practice_item(item, candidate) is should_be_correct)
    return answers


def assert_redacted(response):
    data = json.dumps(response)
    for private in ("answer_key", "seed", "request_sha256", practice.CIPHER, NAME):
        assert private not in data


def test_workflow_publish_submit_review_copy_and_ciphertext(classroom, app):
    client, staff, course, learner, base, browser, learner_headers = classroom
    before = ok(client.get(base))
    assert before["can_manage"] and not before["ready"] and before["eligible_student_ids"] == [learner["id"]]
    body = draft_body(learner, before)
    assert client.post(base + "/drafts", headers=staff, json=body).status_code == 409
    approved = approve(client, staff, course["id"])
    docs = {row["id"]: row for row in approved["documents"]}
    workspace = ok(client.get(base))
    assert workspace["ready"] and workspace["today"] == TODAY
    assert {topic["unit"] for topic in workspace["topics"]} == set(range(1, 10))
    draft = ok(client.post(base + "/drafts", headers=staff, json=body), 201)
    assignment_url = base + "/" + draft["id"]
    assert draft["status"] == "draft" and draft["can_publish"] and not draft["can_submit"]
    assert draft["item_count"] == 6 and draft["student_id"] == learner["id"]
    assert_redacted(draft)
    assert ok(client.post(base + "/drafts", headers=staff, json=body), 201) == draft
    assert client.post(base + "/drafts", headers=staff, json={**body, "count": 5}).status_code == 409
    assert ok(browser.get(base))["assignments"] == []
    assert browser.get(assignment_url).status_code == 404
    assert browser.post(assignment_url + "/publish", headers=learner_headers, json={}).status_code == 403
    assert client.post(assignment_url + "/publish", json={}).status_code == 403
    published = ok(client.post(assignment_url + "/publish", headers=staff, json={}))
    assert published["status"] == "published" and not published["can_publish"]
    assert ok(client.post(assignment_url + "/publish", headers=staff, json={})) == published
    assert [row["id"] for row in ok(browser.get(base))["assignments"]] == [draft["id"]]
    today_only = ok(browser.get(base + "?offset=99&limit=1"))
    assert today_only["assignments"] == [] and [item["id"] for item in today_only["today_assignments"]] == [draft["id"]]
    learner_view = ok(browser.get(assignment_url))
    assert learner_view["items"] == draft["items"] and learner_view["can_submit"]
    assert not {"student_id", "source_assignment_id", "result"} & learner_view.keys()
    assert_redacted(learner_view)
    items = stored_items(app, draft["id"])
    wrong = next((i for i, item in enumerate(items) if item["answer_type"] == "text"), 0)
    answers = answers_for(items, wrong)
    assert client.post(assignment_url + "/submit", headers=staff, json={"answers": answers}).status_code == 403
    assert browser.post(assignment_url + "/submit", headers=learner_headers, json={"answers": {}}).status_code == 422
    incomplete = dict(answers)
    del incomplete[items[0]["id"]]
    assert browser.post(assignment_url + "/submit", headers=learner_headers, json={"answers": incomplete}).status_code == 422
    submitted = ok(browser.post(assignment_url + "/submit", headers=learner_headers, json={"answers": answers}))
    assert submitted["status"] == "submitted" and not submitted["can_submit"]
    result = submitted["result"]
    assert (result["correct"], result["total"], result["lessons_withheld"]) == (5, 6, False)
    assert [index for index, item in enumerate(result["items"]) if not item["correct"]] == [wrong]
    assert {item["id"]: item["answer"] for item in result["items"]} == answers
    lesson = result["lessons"][0]
    assert len(result["lessons"]) == 1 and lesson["id"] == items[wrong]["lesson_id"]
    assert lesson["text"] == docs[lesson["id"]]["content"] and lesson["standards"] == docs[lesson["id"]]["standard_codes"]
    assert set(items[wrong]["standards"]) <= set(lesson["standards"])
    assert lesson["source_url"].startswith("https://www.nysed.gov/")
    assert_redacted(submitted)
    assert ok(browser.post(assignment_url + "/submit", headers=learner_headers, json={"answers": answers})) == submitted
    changed = {**answers, **answers_for(items)}
    assert browser.post(assignment_url + "/submit", headers=learner_headers, json={"answers": changed}).status_code == 409
    assert ok(browser.get(assignment_url)) == submitted
    assert ok(client.get(assignment_url))["result"] == submitted["result"]
    assert ok(browser.get(base))["assignments"][0]["result"] == {"correct": 5, "total": 6}
    copy_body = {"request_id": "copy-saved-synthetic", "student_id": learner["id"],
                 "date": TOMORROW, "source_assignment_id": draft["id"]}
    copied = ok(client.post(base + "/drafts", headers=staff, json=copy_body), 201)
    assert copied["id"] != draft["id"] and copied["items"] == draft["items"]
    assert copied["source_assignment_id"] == draft["id"] and copied["date"] == TOMORROW
    assert copied["status"] == "draft" and "result" not in copied
    assert stored_items(app, copied["id"]) == items
    assert ok(client.get(base + "?limit=1&offset=1"))["assignments"][0]["id"] == draft["id"]
    assert ok(client.get(base + "?limit=1&offset=1"))["total"] == 2
    assert ok(client.get("/api/audit/verify"))["valid"]
    with app.state.database.factory() as db:
        all_records = list(db.scalars(select(Record).where(Record.kind.in_({practice.ASSIGNMENT, practice.PUBLICATION, practice.RESULT}))))
        assert sorted(row.kind for row in all_records) == sorted([practice.ASSIGNMENT] * 2 + [practice.PUBLICATION, practice.RESULT])
        for row in all_records:
            payload = json.loads(row.data)
            assert set(payload) == {practice.CIPHER} and payload[practice.CIPHER]["alg"] == "AES-256-GCM"
            assert "synthetic-private-answer-sentinel" not in row.data and "answer_key" not in row.data
        events = list(db.scalars(select(Audit).where(Audit.event_type.like("daily_practice.%"))))
        assert [event.event_type for event in events].count("daily_practice.published") == 1
        assert [event.event_type for event in events].count("daily_practice.submitted") == 1
        assert all("__academy_pii__" in event.payload for event in events)
        assert all("synthetic-private-answer-sentinel" not in event.payload for event in events)
        assert all("answer_key" not in event.payload and NAME not in event.payload for event in events)
    for kind in (practice.ASSIGNMENT, practice.PUBLICATION, practice.RESULT):
        with pytest.raises(ValueError, match="immutable"):
            with app.state.database.write() as db:
                row = db.scalar(select(Record).where(Record.kind == kind))
                row.set({"unauthorized_mutation": True})


def test_future_release_and_distinct_runtime_variants(classroom, app, monkeypatch):
    client, staff, course, learner, base, browser, learner_headers = classroom
    seeds = iter(("0" * 64, "a" * 64))
    monkeypatch.setattr(practice, "token_hex", lambda _: next(seeds))
    assignment = draft_published(client, staff, course, learner, base, date=TOMORROW)
    url = base + "/" + assignment["id"]
    assert browser.get(url).status_code == 404
    assert ok(browser.get(base))["total"] == 0
    data = stored_items(app, assignment["id"])
    assert browser.post(url + "/submit", headers=learner_headers, json={"answers": answers_for(data)}).status_code == 404
    first = draft_body(learner, ok(client.get(base)), request_id="fresh-variant-synthetic", date=TOMORROW)
    other = ok(client.post(base + "/drafts", headers=staff, json=first), 201)
    assert [row["prompt"] for row in other["items"]] != [row["prompt"] for row in assignment["items"]]
    monkeypatch.setattr(practice, "_today", lambda: TOMORROW)
    assert ok(browser.get(url))["can_submit"]
    assert ok(browser.get(base))["total"] == 1


def test_wrong_school_student_class_and_revoked_enrollment_fail_closed(classroom, app):
    client, staff, course, learner, base, browser, learner_headers = classroom
    assignment = draft_published(client, staff, course, learner, base)
    url = base + "/" + assignment["id"]
    preview = ok(client.post(base + "/drafts", headers=staff,
                             json=draft_body(learner, ok(client.get(base)), request_id="enrollment-revoked")), 201)
    other = student(client, app, staff, username="other.practice", grade="3", course_ids=[course["id"]])
    other_class = math_class(client, staff)
    assert client.get(f"/api/courses/{other_class['id']}/daily-practice/{assignment['id']}").status_code == 404
    foreign_school = new_school(client, staff, "Synthetic unrelated practice school")
    put_user(app, "other-practice-school-manager", "school")
    grant(client, staff, foreign_school, "other-practice-school-manager", "school")
    with TestClient(app, base_url=app.state.settings.app_url) as manager:
        login(manager, "other-practice-school-manager")
        assert manager.get(base).status_code == 403 and manager.get(url).status_code == 403
    with TestClient(app, base_url=app.state.settings.app_url) as outsider:
        outsider_headers = password_login(outsider, other["username"])
        assert ok(outsider.get(base))["total"] == 0
        assert outsider.get(url).status_code == 404
        assert outsider.post(url + "/submit", headers=outsider_headers, json={"answers": answers_for(stored_items(app, assignment["id"]))}).status_code == 404
    ok(client.delete(f"/api/courses/{course['id']}/enrollments/{learner['id']}", headers=staff))
    assert browser.get(base).status_code == 403
    assert browser.get(url).status_code == 403
    assert browser.post(url + "/submit", headers=learner_headers, json={"answers": answers_for(stored_items(app, assignment["id"]))}).status_code == 403
    assert learner["id"] not in ok(client.get(base))["eligible_student_ids"]
    preview_url = base + "/" + preview["id"]
    assert not ok(client.get(preview_url))["can_publish"]
    assert client.post(preview_url + "/publish", headers=staff, json={}).status_code == 409
    assert client.post(base + "/drafts", headers=staff, json=draft_body(learner, ok(client.get(base)), request_id="unassigned-student")).status_code == 422
    assert ok(client.get(url))["status"] == "published"


def test_corpus_change_withholds_lesson_and_blocks_stale_preview_and_submission(classroom, app):
    client, staff, course, learner, base, browser, learner_headers = classroom
    assignment = draft_published(client, staff, course, learner, base)
    url = base + "/" + assignment["id"]
    submitted = ok(browser.post(url + "/submit", headers=learner_headers, json={
        "answers": answers_for(stored_items(app, assignment["id"]), 0)}))
    assert submitted["result"]["lessons"]
    body = draft_body(learner, ok(client.get(base)), request_id="stale-preview-synthetic")
    preview = ok(client.post(base + "/drafts", headers=staff, json=body), 201)
    other = draft_published(client, staff, course, learner, base, request_id="stale-submit-synthetic")
    with app.state.database.write() as db:
        school = db.get(Record, course["school_id"])
        school.set({**school.value, "external_audit_required": True})
    assert not ok(client.get(base))["ready"]
    stale = ok(browser.get(url))
    assert stale["result"]["items"] == submitted["result"]["items"]
    assert stale["result"]["lessons"] == [] and stale["result"]["lessons_withheld"]
    assert not ok(client.get(base + "/" + preview["id"]))["can_publish"]
    assert client.post(base + "/" + preview["id"] + "/publish", headers=staff, json={}).status_code == 409
    assert not ok(browser.get(base + "/" + other["id"]))["can_submit"]
    assert browser.post(base + "/" + other["id"] + "/submit", headers=learner_headers,
        json={"answers": answers_for(stored_items(app, other["id"]))}).status_code == 409


def test_staff_role_topic_date_request_and_answer_bounds(classroom, app):
    client, staff, course, learner, base, browser, learner_headers = classroom
    approve(client, staff, course["id"])
    body = draft_body(learner, ok(client.get(base)))
    for changes in ({"date": "2026-11-09"}, {"date": "2027-07-01"}, {"date": "2026-02-30"},
                    {"count": 0}, {"count": 21}, {"count": True}, {"topic_id": "unknown"},
                    {"source_assignment_id": "other-snapshot"}, {"instruction": "malicious"},
                    {"request_id": "bad/id"}, {"student_id": "demo-student"}):
        assert client.post(base + "/drafts", headers=staff, json={**body, **changes}).status_code == 422
    assert client.get(base + "?offset=-1").status_code == 422
    assert client.get(base + "?limit=51").status_code == 422
    assert browser.post(base + "/drafts", headers=learner_headers, json=body).status_code == 403
    with TestClient(app, base_url=app.state.settings.app_url) as teacher:
        teacher_headers = login(teacher, "demo-instructor")
        assert teacher.get(base).status_code == 403
        assert teacher.post(base + "/drafts", headers=teacher_headers, json=body).status_code == 403
        ok(client.post(f"/api/courses/{course['id']}/enrollments", headers=staff,
                       json={"user_id": "demo-instructor", "role": "teacher"}))
        assert ok(teacher.get(base))["can_manage"]
        assert ok(teacher.post(base + "/drafts", headers=teacher_headers, json=body), 201)["can_publish"]
    grade4 = math_class(client, staff, grades=(4,))
    assert client.get(f"/api/courses/{grade4['id']}/daily-practice").status_code == 404


def test_tampered_cipher_and_snapshot_identity_do_not_release_content(classroom, app):
    client, staff, course, learner, base, browser, _ = classroom
    assignment = draft_published(client, staff, course, learner, base)
    # SQL blocks mutation independently of the ORM guard.
    with pytest.raises(IntegrityError, match="immutable"):
        with app.state.database.engine.begin() as connection:
            connection.execute(Record.__table__.update().where(Record.id == assignment["id"]).values(data="{}"))
    # Even valid ciphertext copied into a different record ID is unavailable:
    # authenticated encryption binds the tenant, class, student, kind and ID.
    forged_id = "synthetic-corrupt-copy"
    with app.state.database.write() as db:
        exact = db.get(Record, assignment["id"])
        forged = Record(id=forged_id, tenant_id=exact.tenant_id, kind=exact.kind, owner_id=exact.owner_id,
                        parent_id=exact.parent_id, data=exact.data)
        db.add(forged)
        db.flush()
        bind_scope(db, forged, course["school_id"])
    url = base + "/" + assignment["id"]
    assert ok(browser.get(url))["status"] == "published"
    assert client.get(base + "/" + forged_id).status_code == 503
    assert browser.get(base + "/" + forged_id).status_code == 404
