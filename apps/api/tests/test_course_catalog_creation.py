"""Full NY reporting-catalog discovery and independent custom class creation."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from academy.db import Record
from academy.permissions import default_school_id
from conftest import login
from test_school_permissions import teacher_agent


def catalog(client, **query):
    response = client.get("/api/course-catalog", params={"limit": 250, **query})
    assert response.status_code == 200, response.text
    return response.json()


def selection(result, item):
    return {
        "state": result["state"], "academic_year": result["academic_year"],
        "catalog_sha256": result["catalog_sha256"], "course_code": item["course_code"],
    }


def test_complete_catalog_is_browsable_without_tutoring_or_grade_filters(client):
    login(client, "demo-admin")
    first = catalog(client)
    assert first["total"] == 2030
    assert len(first["subject_areas"]) == 25
    assert set(first["levels"]) == {"secondary", "prior_to_secondary", "all_grades"}
    assert first["custom_courses_supported"]
    assert not first["curriculum_alignment_required"]
    assert not first["teaching_corpus_required_for_class_creation"]
    assert not first["network_fetch"] and first["sources"]
    items = list(first["items"])
    for offset in range(first["limit"], first["total"], first["limit"]):
        page = catalog(client, offset=offset)
        assert page["total"] == first["total"] and page["catalog_sha256"] == first["catalog_sha256"]
        items.extend(page["items"])
    assert len(items) == len({item["course_code"] for item in items}) == first["total"]
    assert sum(item["level"] == "all_grades" for item in items) == 9
    assert any("band" in item["title"].casefold() for item in items)
    assert any("physical education" in item["title"].casefold() for item in items)
    assert any("art" in item["title"].casefold() for item in items)


@pytest.mark.parametrize("user", ["demo-admin", "demo-school", "demo-instructor"])
def test_every_staff_role_can_discover_course_choices(client, app, user):
    login(client, user)
    assert catalog(client)["items"]
    assert catalog(client, school_id=default_school_id(app.state.settings.tenant_id))["items"]


def test_catalog_discovery_requires_current_staff_school_permission(client):
    assert client.get("/api/course-catalog").status_code == 401
    admin = login(client, "demo-admin")
    foreign = client.post("/api/schools", json={"name": "Another school"}, headers=admin).json()["id"]
    login(client, "demo-instructor")
    assert client.get("/api/course-catalog", params={"school_id": foreign}).status_code == 403
    login(client, "demo-student")
    assert client.get("/api/course-catalog").status_code == 403


@pytest.mark.parametrize("query", [
    {"offset": -1}, {"limit": 0}, {"limit": 251}, {"level": "tutorable"},
    {"q": "x" * 201}, {"state": "../NY"},
])
def test_catalog_query_is_bounded(client, query):
    login(client, "demo-admin")
    assert client.get("/api/course-catalog", params=query).status_code == 422


def test_catalog_search_filters_and_pagination_preserve_exact_source_rows(client):
    login(client, "demo-admin")
    result = catalog(client, q="physical education", limit=1)
    assert result["total"] > 1 and len(result["items"]) == 1
    item = result["items"][0]
    assert catalog(client, q="gym", limit=1)["items"] == result["items"]
    exact = catalog(client, q=item["course_code"])
    assert item in exact["items"]
    filtered = catalog(client, subject_area=item["subject_area"], level=item["level"])
    assert item in filtered["items"]
    assert all(row["subject_area"] == item["subject_area"] and row["level"] == item["level"] for row in filtered["items"])
    assert catalog(client, offset=2030)["items"] == []
    assert client.get("/api/course-catalog", params={"state": "US-CA"}).status_code == 404
    assert client.get("/api/course-catalog", params={"academic_year": "2027-2028"}).status_code == 404


def test_regents_discovery_uses_official_assessment_codes_without_restricting_other_classes(client):
    from academy.course_catalog import regents_course_codes
    login(client, "demo-admin")
    full = catalog(client)
    selected = catalog(client, regents_only=True)
    named = catalog(client, q="Regents")
    expected = regents_course_codes("US-NY", "2026-2027")
    assert len(expected) == selected["total"] == named["total"] == 12
    assert {item["course_code"] for item in selected["items"]} == expected
    assert named["items"] == selected["items"]
    assert set(full["regents_course_codes"]) == expected
    assert full["total"] == 2030
    source = selected["state_assessment_source"]
    assert source["regents_course_count"] == 12 and source["publisher"] == "NYSED"
    assert source["content_sha256"] and source["url"].startswith("https://")
    # These mapped courses include the mathematics, ELA, history and science
    # courses, rather than only the lab titles containing the word Regents.
    assert any("algebra" in item["title"].casefold() for item in selected["items"])
    assert any("history" in item["title"].casefold() for item in selected["items"])
    assert all("algebra" in (item["title"] + " " + item["description"]).casefold()
               for item in catalog(client, regents_only=True, q="algebra")["items"])


@pytest.mark.parametrize("query", ["physical education", "band", "art", "music"])
def test_official_nonacademic_courses_are_creatable_without_standards_or_corpus(client, app, query):
    headers = login(client, "demo-admin")
    school = client.post("/api/schools", json={
        "name": "NY curriculum school", "curriculum": {
            "mode": "state", "state": "US-NY", "academic_year": "2026-2027",
            "grades": [10], "subjects": ["environmental_science"],
        },
    }, headers=headers).json()
    result = catalog(client, q=query)
    item = result["items"][0]
    response = client.post("/api/courses", json={
        "title": item["title"], "school_id": school["id"], "curriculum": None,
        "course_catalog": selection(result, item),
    }, headers=headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["curriculum"] is None
    assert created["course_catalog"]["course_code"] == item["course_code"]
    assert created["course_catalog"]["title"] == item["title"]
    assert created["course_catalog"]["description"] == item["description"]
    assert created["course_catalog"]["catalog_sha256"] == result["catalog_sha256"]
    assert created["course_catalog"]["source_url"].startswith("https://")
    with app.state.database.factory() as db:
        assert not list(db.scalars(select(Record).where(Record.kind == "corpus_approval", Record.parent_id == created["id"])))


def test_custom_course_does_not_inherit_or_require_school_state_curriculum(client):
    headers = login(client, "demo-admin")
    school = client.post("/api/schools", json={"name": "State school", "curriculum": {
        "mode": "state", "state": "US-NY", "academic_year": "2026-2027",
        "grades": [10], "subjects": ["environmental_science"],
    }}, headers=headers).json()
    response = client.post("/api/courses", json={
        "school_id": school["id"], "title": "Axolotl husbandry", "description": "Our own local course",
        "curriculum": None, "course_catalog": None,
    }, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()["curriculum"] is None and response.json()["course_catalog"] is None
    saved_school = next(row for row in client.get("/api/schools").json() if row["id"] == school["id"])
    assert saved_school["curriculum"] == school["curriculum"]


def test_catalog_source_snapshot_is_server_owned_and_stale_choices_are_rejected(client, app):
    headers = login(client, "demo-admin")
    result = catalog(client)
    item = result["items"][0]
    chosen = selection(result, item)
    for changes, status in [({"catalog_sha256": "0" * 64}, 409), ({"course_code": "00000"}, 422), ({"title": "Forged official course"}, 422)]:
        response = client.post("/api/courses", json={"title": "No", "course_catalog": {**chosen, **changes}}, headers=headers)
        assert response.status_code == status, response.text
    assert not any(course["title"] == "No" for course in client.get("/api/courses").json())


def test_saved_catalog_metadata_survives_legacy_edits_and_can_be_explicitly_removed(client):
    headers = login(client, "demo-admin")
    result = catalog(client, q="band")
    created = client.post("/api/courses", json={"title": "Local band section", "course_catalog": selection(result, result["items"][0])}, headers=headers).json()
    updated = client.put(f"/api/courses/{created['id']}", json={"title": "Band renamed"}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["course_catalog"] == created["course_catalog"]
    cleared = client.put(f"/api/courses/{created['id']}", json={"title": "Independent ensemble", "course_catalog": None, "curriculum": None}, headers=headers)
    assert cleared.status_code == 200 and cleared.json()["course_catalog"] is None and cleared.json()["curriculum"] is None


def test_agent_catalog_proposals_require_human_publication_and_revalidate_source(client, teacher_agent, monkeypatch):
    from academy import course_catalog as source
    data = teacher_agent
    assert client.get("/api/course-catalog", headers=data["headers"]).status_code == 200
    result = catalog(client, q="band")
    proposed = client.post("/api/courses", json={
        "title": "Agent proposed band", "school_id": data["school"],
        "course_catalog": selection(result, result["items"][0]),
    }, headers=data["headers"])
    assert proposed.status_code == 202, proposed.text
    change_id = proposed.json()["change_request"]["id"]
    original = source.load_course_catalog
    def changed_catalog(*args, **kwargs):
        value = deepcopy(original(*args, **kwargs))
        value["catalog_sha256"] = "a" * 64
        return value
    monkeypatch.setattr(source, "load_course_catalog", changed_catalog)
    rejected = client.post(f"/api/changes/{change_id}/approve", headers=data["admin"])
    assert rejected.status_code == 409, rejected.text
    monkeypatch.setattr(source, "load_course_catalog", original)
    approved = client.post(f"/api/changes/{change_id}/approve", headers=data["admin"])
    assert approved.status_code == 200, approved.text
    created = next(row for row in client.get("/api/courses", headers=data["headers"]).json() if row["title"] == "Agent proposed band")
    assert created["course_catalog"]["course_code"] == result["items"][0]["course_code"]
    assert created["curriculum"] is None


def test_agent_catalog_edit_preserves_absent_selection_and_allows_custom_conversion(client, teacher_agent):
    data = teacher_agent
    result = catalog(client, q="band")
    created = client.post("/api/courses", json={
        "title": "Assigned band", "teacher_id": data["identity"]["id"],
        "course_catalog": selection(result, result["items"][0]),
    }, headers=data["admin"]).json()
    for payload, expected in [
        ({"title": "Band edited by agent"}, created["course_catalog"]),
        ({"title": "Custom ensemble", "course_catalog": None}, None),
    ]:
        proposed = client.put(f"/api/courses/{created['id']}", json=payload, headers=data["headers"])
        assert proposed.status_code == 202, proposed.text
        approved = client.post(f"/api/changes/{proposed.json()['change_request']['id']}/approve", headers=data["admin"])
        assert approved.status_code == 200, approved.text
        assert client.get(f"/api/courses/{created['id']}", headers=data["headers"]).json()["course_catalog"] == expected
