"""Reviewed planning scope stays distinct from mapped, approved teaching data."""
from copy import deepcopy
from datetime import date
import hashlib
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from academy import curriculum_review as review_module
from academy import school_curriculum as planner
from academy import standards
from academy.curriculum_review import load_reviews, review_catalog_descriptor, review_for
from academy.db import Record, canonical
from academy.school_curriculum_schemas import CurriculumPublishInput, SchoolCurriculumInput
from academy.standards_schemas import CurriculumInput
from conftest import login
from test_standards import CURRICULUM, add_material, corpus_env


SCOPE = {"state": "US-NY", "subject": "mathematics", "grades": [-1], "academic_year": "2026-2027", "standards_version": None}


def _raw_review():
    return json.loads(review_module.REVIEW_PATHS[0].read_bytes())


def _install_fixture(tmp_path, monkeypatch, value):
    path = tmp_path / "synthetic-source-review.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(review_module, "REVIEW_PATHS", (path,))
    return path


def test_explicit_review_inventory_covers_all_grades_without_teaching_or_fabricated_codes():
    reviews = load_reviews()
    assert len(reviews) == 4
    subjects = [s for bundle in reviews for s in bundle["subjects"]]
    assert len(subjects) == len({s["code"] for s in subjects}) == 67
    assert sum(len(s["grades"]) for s in subjects) == 470
    assert set().union(*(set(s["grades"]) for s in subjects)) == set(range(-1, 13))
    assert {b["academic_year"] for b in reviews} == {"2026-2027"}
    assert {b["review_valid_through"] for b in reviews} == {"2027-06-30"}
    for path, bundle in zip(review_module.REVIEW_PATHS, reviews):
        assert bundle["review_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert not {"standards", "teaching_documents", "catalog_sha256"} & bundle.keys()
        for source in bundle["sources"]:
            assert source["publisher"] == "NYSED"
            if source["verification"] == "exact_bytes_review":
                assert len(source["content_sha256"]) == 64 and source["byte_length"] > 0


@pytest.mark.parametrize("change,code", [
    ({"state": "US-VT"}, "mapping_unavailable"),
    ({"subject": "unreviewed_subject"}, "mapping_unavailable"),
    ({"academic_year": "2027-2028"}, "year_unreviewed"),
    ({"grades": [9]}, "grade_unreviewed"),
    ({"standards_version": "invented-2026"}, "version_unreviewed"),
    ({"grades": [-2]}, "curriculum_invalid"),
    ({"grades": [True]}, "curriculum_invalid"),
])
def test_source_review_requires_exact_applicability_and_never_verifies_version_pins(change, code):
    selected, gaps = review_for({**SCOPE, **change})
    assert selected is None and gaps[0]["code"] == code


def test_source_review_has_framework_versions_and_original_topics_without_alignment_claim():
    selected, gaps = review_for(SCOPE)
    assert not gaps and selected["selected_grades"] == [-1]
    assert selected["frameworks"] and selected["grade_focus"][0]["grades"] == [-1]
    assert selected["standards_version"] is None and selected["mapping_version"] is None
    assert selected["coverage"] == "framework_review" and not selected["alignment_mapping_available"]
    assert selected["sources"] and selected["review_sha256"] and selected["subject_review_sha256"]
    assert not {"standards", "teaching_documents", "documents"} & selected.keys()
    assert review_for({**SCOPE, "subject": "ela", "grades": [12]})[0]
    assert review_for({**SCOPE, "subject": "health_education", "grades": [-1]})[0]


def test_scope_version_pin_cannot_replace_the_exact_package_authority():
    pinned = {**CURRICULUM, "standards_version": "nyssls-2016"}
    assert review_for(pinned)[0] is None
    assert standards.package_for(pinned, standards.load_catalogs())[0]["standards_version"] == "nyssls-2016"


@pytest.mark.parametrize("url", [
    "http://www.nysed.gov/standards", "https://not-nysed.gov/standards",
    "https://www.nysed.gov.evil.test/standards", "https://user:secret@www.nysed.gov/standards",
    "https://www.nysed.gov:444/standards", "https://www.nysed.gov/a b",
    "https://www.nysed.gov\\@evil.test/standards",
])
def test_loader_rejects_noncanonical_or_foreign_source_urls(tmp_path, monkeypatch, url):
    value = _raw_review()
    value["sources"][0]["url"] = url
    _install_fixture(tmp_path, monkeypatch, value)
    with pytest.raises(ValueError):
        load_reviews()


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(schema_version=True),
    lambda v: v.update(jurisdiction="US-CA"),
    lambda v: v.update(academic_year="2027-2028"),
    lambda v: v.update(reviewed_at="2027-07-01"),
    lambda v: v.update(standards=[{"code": "fabricated"}]),
    lambda v: v["sources"].append(deepcopy(v["sources"][0])),
    lambda v: v["sources"][0].update(publisher="Unreviewed third party"),
    lambda v: v["sources"][0].update(content_sha256="not-an-observed-sha256"),
    lambda v: v["sources"][0].pop("content_sha256"),
    lambda v: v["sources"][0].update(byte_length=True),
    lambda v: v["sources"][0].update(source_type=[]),
    lambda v: v["subjects"].append(deepcopy(v["subjects"][0])),
    lambda v: v["subjects"][0].update(grades=[False]),
    lambda v: v["subjects"][0].update(placement=[]),
    lambda v: v["subjects"][0].update(source_ids=["unknown"]),
    lambda v: v["subjects"][0]["grade_focus"][0].update(source_ids=["unknown"]),
    lambda v: v["subjects"][0]["frameworks"][0].update(source_id="unknown"),
    lambda v: v["subjects"][0]["grade_focus"].pop(),
])
def test_loader_rejects_bad_metadata_unobserved_hashes_and_broken_source_references(tmp_path, monkeypatch, mutation):
    value = _raw_review()
    mutation(value)
    _install_fixture(tmp_path, monkeypatch, value)
    with pytest.raises(ValueError):
        load_reviews()


def test_loader_rejects_duplicate_json_keys_oversize_and_cross_file_ambiguity(tmp_path, monkeypatch):
    value = _raw_review()
    path = _install_fixture(tmp_path, monkeypatch, value)
    path.write_text(json.dumps(value).replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1))
    with pytest.raises(ValueError, match="Duplicate"):
        load_reviews()
    path.write_text(" " * (review_module.MAX_REVIEW_BYTES + 1))
    with pytest.raises(ValueError, match="limit"):
        load_reviews()
    path.write_text(json.dumps(value))
    monkeypatch.setattr(review_module, "REVIEW_PATHS", (path, path))
    with pytest.raises(ValueError, match="unambiguous"):
        load_reviews()


def test_expired_future_and_conflicting_reviews_fail_closed(monkeypatch):
    reviews = load_reviews()
    class ExpiredDate(date):
        @classmethod
        def today(cls):
            return cls(2027, 7, 1)
    monkeypatch.setattr(review_module, "date", ExpiredDate)
    assert review_for(SCOPE, reviews)[1][0]["code"] == "source_review_expired"
    assert review_catalog_descriptor(reviews)["states"] == []
    monkeypatch.setattr(review_module, "date", date)
    future = deepcopy(reviews)
    for item in future:
        item["reviewed_at"] = "2027-01-01"
    assert review_for(SCOPE, future)[1][0]["code"] == "source_review_unavailable"
    conflict = deepcopy(reviews[0])
    conflict["subjects"][0]["name"] = "Synthetic conflicting fixture"
    assert review_for(SCOPE, [*reviews, conflict])[1][0]["code"] == "mapping_ambiguous"


def test_public_catalog_preserves_real_partial_packages_and_labels_scope_only_subjects():
    descriptor = standards.catalog_descriptor()
    subjects = {item["code"]: item for item in descriptor["states"][0]["subjects"]}
    assert len(subjects) == 67
    assert not descriptor["network_fetch"] and descriptor["human_corpus_approval_required"]
    for code in ("environmental_science", "earth_space_science"):
        item = subjects[code]
        assert item["coverage"] == "partial" and item["standards_version"] == "nyssls-2016"
        assert item["alignment_mapping_available"] and item["versions"]
        assert item["curriculum_review"]["coverage"] == "framework_review"
    for code in ("mathematics", "ela", "health_education", "world_languages_modern"):
        item = subjects[code]
        assert item["coverage"] == "framework_review" and not item["alignment_mapping_available"]
        assert item["standards_version"] is None and item["versions"] == []
        assert item["frameworks"] and item["grade_focus"] and item["limitations"] and item["sources"]


def test_all_reviewed_grade_subject_combinations_plan_without_fabricated_standards():
    subjects = [s for b in load_reviews() for s in b["subjects"]]
    expected = {(g, s["code"]) for s in subjects for g in s["grades"]}
    plan = planner.plan_curriculum({"mode": "state", "state": "US-NY", "academic_year": "2026-2027", "grades": list(range(-1, 13)), "subjects": [s["code"] for s in subjects]})
    assert plan["status"] == "ready" and not plan["ingestion_supported"]
    assert {(c["grade"], c["subject"]) for c in plan["courses"]} == expected
    assert len(plan["courses"]) == len({c["key"] for c in plan["courses"]}) == 470
    mapped = [c for c in plan["courses"] if c["alignment_mapping_available"]]
    assert len(mapped) == 8
    for course in plan["courses"]:
        assert course["curriculum_review_sha256"]
        if course["coverage"] == "framework_review":
            assert course["curriculum"]["standards_version"] is None
            assert course["standards_version"] is None and course["mapping_version"] is None
            assert course["planning_topics"] and course["frameworks"]
            assert all(not u["standard_codes"] and not u["objectives"] and u["planning_topics"] for u in course["units"])
            assert {g["code"] for g in course["gaps"]} >= {"alignment_mapping_required", "instructional_materials_missing"}
            if course["grade"] == -1:
                assert "Pre-K" in course["title"]


def test_pre_k_and_school_selection_bounds_do_not_expand_publication_limit():
    assert CurriculumInput.model_validate(SCOPE).grades == [-1]
    config = {"mode": "state", "grades": list(range(-1, 13)), "subjects": ["synthetic_" + str(i) for i in range(80)]}
    assert len(SchoolCurriculumInput.model_validate(config).subjects) == 80
    with pytest.raises(ValidationError):
        SchoolCurriculumInput.model_validate({**config, "subjects": config["subjects"] + ["overflow"]})
    for grades in ([-2], [13], [False], [-1.0], [-1, -1]):
        with pytest.raises(ValidationError):
            SchoolCurriculumInput.model_validate({**config, "grades": grades})
    with pytest.raises(ValidationError):
        CurriculumPublishInput.model_validate({"idempotency_key": "synthetic-key", "expected_school_version": 1, "run_id": "synthetic-run", "plan_sha256": "a" * 64, "course_keys": [f"{i:032x}" for i in range(101)]})


def test_scope_only_corpus_stays_unverified_even_after_local_teaching_material_is_added(corpus_env):
    database, identity, audit_actor, _settings = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        course.data = canonical({**course.value, "curriculum": SCOPE})
        descriptor = standards.build_course_corpus(db, identity, course, "school", audit_actor)
        assert descriptor["status"] == "unverified" and descriptor["coverage"] == "framework_review"
        assert descriptor["sources"] and descriptor["curriculum_review"]["frameworks"]
        assert descriptor["documents"] == [] and not descriptor["ready_for_teaching"]
        assert {g["code"] for g in descriptor["gaps"]} >= {"alignment_mapping_required", "instructional_materials_missing"}
        add_material(db, identity["tenant_id"], content="Synthetic human-authored conceptual lesson for local review.")
        updated = standards.build_course_corpus(db, identity, course, "school", audit_actor)
        assert updated["instructional_material_count"] == 1 and updated["status"] == "unverified"
        with pytest.raises(HTTPException) as blocked:
            standards.approve_corpus(db, identity, course, "school", updated["id"], updated["snapshot_sha256"], audit_actor)
        assert blocked.value.status_code == 409
        with pytest.raises(HTTPException):
            standards.retrieve_corpus(db, identity, course, "school", "conceptual", 3, audit_actor)


def test_scope_review_changes_invalidate_exact_corpus_approval_and_reference_plans(corpus_env, monkeypatch):
    database, identity, audit_actor, _settings = corpus_env
    config = {"mode": "state", "grades": [10], "subjects": ["environmental_science", "ela"]}
    original_plan = planner.plan_curriculum(config)
    with database.write() as db:
        course = db.get(Record, "class")
        first = standards.build_course_corpus(db, identity, course, "school", audit_actor)
        approved = standards.approve_corpus(db, identity, course, "school", first["id"], first["snapshot_sha256"], audit_actor)
        assert approved["ready_for_teaching"] and approved["coverage"] == "partial"
        reviews = deepcopy(load_reviews())
        for bundle in reviews:
            bundle["review_sha256"] = standards.digest({"synthetic_revision": 2, "previous_hash": bundle["review_sha256"]})
        monkeypatch.setattr(standards, "load_reviews", lambda: deepcopy(reviews))
        monkeypatch.setattr(planner, "load_reviews", lambda: deepcopy(reviews))
        stale = standards.corpus_descriptor(db, course, "school")
        assert stale["status"] == "stale" and not stale["ready_for_teaching"]
        assert stale["approval"]["status"] == "stale"
        assert standards.digest(planner.plan_curriculum(config)) != standards.digest(original_plan)


def test_scope_catalog_is_protected_and_pre_k_class_publication_cannot_approve_corpus(client):
    assert client.get("/api/school-curriculum/catalog").status_code == 401
    student = login(client, "demo-student")
    assert client.get("/api/school-curriculum/catalog").status_code == 403
    assert client.get("/api/standards/catalog").status_code == 403
    admin = login(client, "demo-admin")
    catalog = client.get("/api/school-curriculum/catalog")
    assert catalog.status_code == 200 and len(catalog.json()["states"][0]["subjects"]) == 67
    response = client.post("/api/schools", json={"name": "Synthetic reviewed scope school", "curriculum": {"mode": "state", "state": "US-NY", "academic_year": "2026-2027", "grades": [-1], "subjects": ["ela", "mathematics"]}}, headers=admin)
    assert response.status_code == 201, response.text
    school = response.json()
    planned = client.post(f"/api/schools/{school['id']}/curriculum/runs", json={"idempotency_key": "synthetic-scope-plan", "expected_school_version": school["version"]}, headers=admin)
    assert planned.status_code == 201, planned.text
    run = planned.json()
    assert run["current"] and run["status"] == "ready"
    assert all(item["coverage"] == "framework_review" for item in run["plan"]["courses"])
    published = client.post(f"/api/schools/{school['id']}/curriculum/publish", json={"idempotency_key": "synthetic-scope-publish", "expected_school_version": school["version"], "run_id": run["id"], "plan_sha256": run["plan_sha256"], "course_keys": [item["key"] for item in run["plan"]["courses"]]}, headers=admin)
    assert published.status_code == 201, published.text
    for item in published.json()["courses"]:
        corpus = item["corpus"]
        assert corpus["status"] == "unverified" and not corpus["ready_for_teaching"]
        attempted = client.post(f"/api/courses/{item['course_id']}/corpus/approve", json={"corpus_id": corpus["id"], "snapshot_sha256": corpus["snapshot_sha256"]}, headers=admin)
        assert attempted.status_code == 409
