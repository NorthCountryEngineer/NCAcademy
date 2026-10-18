"""Independent integration review of school onboarding scope and atomicity."""
from fastapi import HTTPException
from sqlalchemy import select

from academy.db import Audit, Record
from academy.permissions import default_school_id
from conftest import login
from test_school_curriculum import CONFIG, publish, run, school


def test_review_actual_earth_school_plan_to_class_preserves_grade_year_and_source_provenance(client, app):
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={**CONFIG, "subjects": ["earth_space_science"], "grades": [9, 12]})
    planned = run(client, headers, value)
    assert planned["current"] and planned["agent"]["network_fetch"] is False
    published = publish(client, headers, value, planned)
    assert published.status_code == 201, published.text
    assert len(published.json()["courses"]) == 2
    with app.state.database.factory() as db:
        for item in published.json()["courses"]:
            course = db.get(Record, item["course_id"])
            assert course.value["curriculum"] == {"state": "US-NY", "subject": "earth_space_science", "grades": [item["grade"]], "academic_year": "2026-2027", "standards_version": "nyssls-2016"}
            assert course.value["school_curriculum"]["curriculum_revision"] == value["curriculum_revision"]
            corpus = db.get(Record, item["corpus"]["id"])
            assert corpus.value["course_id"] == course.id and corpus.value["school_id"] == value["id"]
            assert len(corpus.value["documents"]) == 19
            sun = next(document for document in corpus.value["documents"] if document.get("source_id") == "nasa-sun-facts")
            assert sun["source_content_sha256"] == "dd31bbd8675c59deaa371689985765fb18c4806657333076616e0a85c94dcc8a"
            assert sun["source_url"] == "https://science.nasa.gov/sun/facts/"
            assert sun["standard_codes"] == ["HS-ESS1-1"]
            assert not item["corpus"]["ready_for_teaching"] and item["corpus"]["approval"]["status"] == "pending"


def test_review_failed_teacher_reassignment_rolls_back_links_version_corpus_and_run_evidence(client, app, monkeypatch):
    import academy.standards as source
    headers = login(client, "demo-admin")
    value = school(client, headers, curriculum={**CONFIG, "grades": [9, 10, 11]})
    planned = run(client, headers, value)
    classes = publish(client, headers, value, planned).json()["courses"]
    teacher_url = f"/api/schools/{value['id']}/teachers"
    teacher = client.post(teacher_url, json={"kind": "ai", "name": "Synthetic atomicity teacher"}, headers=headers).json()
    teacher_url += "/" + teacher["id"]
    assigned = client.put(teacher_url + "/courses", json={"expected_version": teacher["version"], "course_ids": [classes[0]["course_id"]]}, headers=headers)
    assert assigned.status_code == 200, assigned.text
    assigned = assigned.json()
    def snapshot():
        with app.state.database.factory() as db:
            kinds = ("teacher_profile", "teacher_profile_course", "course_corpus", "knowledge_workflow_run")
            return ([(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind.in_(kinds)).order_by(Record.id))],
                    [(row.id, row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))])
    before = snapshot()
    original = source.build_course_corpus
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(args[2].id)
        if len(calls) == 2:
            raise HTTPException(503, "Synthetic second-class corpus failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(source, "build_course_corpus", fail_second)
    response = client.put(teacher_url + "/courses", json={"expected_version": assigned["version"], "course_ids": [item["course_id"] for item in classes[1:]]}, headers=headers)
    assert response.status_code == 503
    assert len(calls) == 2 and snapshot() == before
    current = client.get(teacher_url).json()
    assert current["version"] == assigned["version"] and current["course_ids"] == assigned["course_ids"]
    assert current["latest_onboarding_run"]["id"] == assigned["latest_onboarding_run"]["id"]
    monkeypatch.setattr(source, "build_course_corpus", original)
    retried = client.put(teacher_url + "/courses", json={"expected_version": assigned["version"], "course_ids": [item["course_id"] for item in classes[1:]]}, headers=headers)
    assert retried.status_code == 200 and len(retried.json()["course_ids"]) == 2


def test_review_revoked_school_manager_cannot_replay_saved_plan_or_publication_receipt(client, app):
    admin = login(client, "demo-admin")
    school_id = default_school_id(app.state.settings.tenant_id)
    value = client.put(f"/api/schools/{school_id}", json={"name": "Synthetic managed school", "curriculum": CONFIG}, headers=admin).json()
    manager = login(client, "demo-school")
    planned = run(client, manager, value)
    published = publish(client, manager, value, planned)
    assert published.status_code == 201
    admin = login(client, "demo-admin")
    assert client.delete(f"/api/schools/{school_id}/members/demo-school", headers=admin).status_code == 200
    manager = login(client, "demo-school")
    # Exact saved command bodies must still authorize the actor before receipt lookup.
    response = client.post(f"/api/schools/{school_id}/curriculum/runs", json={"idempotency_key": "planner-key-001", "expected_school_version": value["version"]}, headers=manager)
    assert response.status_code == 403
    assert publish(client, manager, value, planned).status_code == 403
    assert client.get(f"/api/schools/{school_id}/curriculum").status_code == 403
    assert client.get(f"/api/schools/{school_id}/onboarding/runs").status_code == 403
