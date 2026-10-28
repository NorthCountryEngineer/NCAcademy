"""Full New York grade3 scope, honest local teaching coverage and review gates."""
from copy import deepcopy
from pathlib import Path
import hashlib

import pytest
from fastapi import HTTPException

from academy import standards
from academy.db import Record, canonical
from academy.school_curriculum import plan_curriculum
from conftest import login
from test_standards import corpus_env  # isolated synthetic fixture, no provider I/O

CURRICULUM = {"state": "US-NY", "subject": "mathematics", "grades": [3],
              "academic_year": "2026-2027", "standards_version": None}
# Independently enumerated from printed/PDF pp.46-54, not generated from package.
CONTENT = {
    "OA": "1 2 3 4 5 6 7a 7b 8 8a 8b 9",
    "NBT": "1 2 3 4a 4b",
    "NF": "1 2 2a 2b 3 3a 3b 3c 3d",
    "MD": "1 2a 2b 3 4 5 5a 5b 6 7 7a 7b 7c 7d 8a 8b",
    "G": "1 2",
}
CODES = {f"NY-3.{domain}.{part}" for domain, parts in CONTENT.items() for part in parts.split()}
PRACTICES = {f"MP.{i}" for i in range(1, 9)}


def package():
    return next(item for item in standards.load_catalogs() if item["subject"] == "mathematics")


def test_exact_new_york_inventory_has_every_component_and_no_invented_parent():
    value = package()
    assert len(CODES) == 44 and len(value["standards"]) == 52
    assert {item["code"] for item in value["standards"]} == CODES | PRACTICES
    assert len(value["standards"]) == len({item["code"] for item in value["standards"]})
    by_code = {item["code"]: item for item in value["standards"]}
    # The NY-specific four-digit expectations and split fact goals must survive.
    assert by_code["NY-3.NBT.4b"]["source_page"] == 49
    assert "four-digit" in by_code["NY-3.NBT.4b"]["summary"]
    assert "recall" in by_code["NY-3.OA.7b"]["summary"]
    for code in CODES:
        assert 46 <= by_code[code]["pdf_page"] <= 54
        assert by_code[code]["source_page"] == by_code[code]["pdf_page"]
    for code in PRACTICES:
        assert by_code[code]["applies_across_grades"] is True
        assert by_code[code]["source_page"] in {7, 8}
    assert not {"NY-3.OA.7", "NY-3.NBT.4", "NY-3.MD.2", "NY-3.MD.8"} & CODES


def test_exact_source_bytes_and_current_applicability_remain_distinct_from_edition():
    value = package()
    assert value["grades"] == [3] and value["academic_years"] == ["2026-2027"]
    assert value["standards_version"] == "nys-next-generation-mathematics-2017-updated-2019"
    source = value["sources"][0]
    assert source["content_sha256"] == "1bc23fd7e8793c22ba2d398be43411f570b043e4a4da930662c22b81cd5def95"
    assert source["byte_length"] == 3736579
    assert source["retrieved_at"] == source["reviewed_at"] == "2026-10-28"
    assert value["review_valid_through"] == "2027-06-30"
    assert all(item["url"].startswith("https://www.nysed.gov/") for item in value["sources"])
    path = Path(standards.__file__).with_name("standards_data") / "ny_grade3_mathematics_2026.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == value["catalog_sha256"]
    assert "not a state-authored curriculum" in value["mapping_note"]
    assert "assessments" in value["instructional_coverage_note"]


def test_every_standard_has_original_public_teaching_reference_and_local_sequence():
    value = package()
    documents = value["teaching_documents"]
    assert len(documents) == 27 <= 30
    assert {code for item in documents for code in item["standard_codes"]} == CODES | PRACTICES
    for item in documents:
        assert item["standard_codes"] and item["source_sections"]
        assert item["content_role"] == "original_local_teaching_summary"
        assert 250 <= len(item["content"]) <= 10000
        assert item["source_id"] in {source["id"] for source in value["sources"]}
    units = value["local_sequence"]
    assert len(units) == 9 and [u["order"] for u in units] == list(range(1, 10))
    assert {code for unit in units for code in unit["standard_codes"]} == CODES
    indexed = {d["id"]: d for d in documents}
    for unit in units:
        assert set(unit["ongoing_practices"]) == PRACTICES
        assert set(unit["standard_codes"]) == {code for key in unit["document_ids"] for code in indexed[key]["standard_codes"]}
    assert value["coverage_counts"] == {"content_expectations_and_components": 44, "mathematical_practices": 8, "teaching_documents": 27, "local_units": 9}
    assert value["scope_limits"]["fraction_denominators"] == [2, 3, 4, 6, 8]
    assert value["scope_limits"]["whole_number_fraction_denominator_one"] is True
    assert value["scope_limits"]["compound_area_max_unknown_sides"] == 1


@pytest.mark.parametrize("change,gap", [
    ({"grades": [2]}, "grade_unreviewed"), ({"grades": [3, 4]}, "grade_unreviewed"),
    ({"academic_year": "2027-2028"}, "year_unreviewed"),
    ({"standards_version": "invented-2026-standards"}, "version_unreviewed"),
    ({"state": "US-CA"}, "mapping_unavailable"),
])
def test_exact_mapping_does_not_admit_unreviewed_grade_year_state_or_edition(change, gap):
    selected, gaps = standards.package_for({**CURRICULUM, **change}, standards.load_catalogs())
    assert selected is None and gap in {item["code"] for item in gaps}


def test_catalog_preserves_other_math_grades_without_claiming_they_are_mapped():
    subject = next(s for state in standards.catalog_descriptor()["states"] for s in state["subjects"] if s["code"] == "mathematics")
    assert subject["grades"] == list(range(-1, 9))
    assert subject["coverage"] == "complete_standards"
    assert subject["versions"][0]["grades"] == [3]
    assert subject["curriculum_review"]["coverage"] == "framework_review"
    plan = plan_curriculum({"mode": "state", "state": "US-NY", "academic_year": "2026-2027", "grades": [2, 3, 4], "subjects": ["mathematics"]})
    courses = {c["grade"]: c for c in plan["courses"]}
    assert courses[3]["coverage"] == "complete_standards"
    assert len(courses[3]["local_sequence"]) == 9
    assert {o["code"] for unit in courses[3]["units"] for o in unit["objectives"]} == CODES | PRACTICES
    assert not {"partial_standards_mapping", "alignment_mapping_required"} & {g["code"] for g in courses[3]["gaps"]}
    for grade in (2, 4):
        assert courses[grade]["coverage"] == "framework_review"
        assert not courses[grade]["alignment_mapping_available"]
        assert all(not u["standard_codes"] for u in courses[grade]["units"])


def test_grade3_build_still_needs_exact_human_approval_and_rejects_stale_context(corpus_env):
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        course.data = canonical({**course.value, "title": "Synthetic grade3 mathematics", "curriculum": CURRICULUM})
        corpus = standards.build_course_corpus(db, identity, course, "school", audit_actor)
        assert corpus["status"] == "aligned" and corpus["coverage"] == "complete_standards"
        assert corpus["approval"]["status"] == "pending" and not corpus["ready_for_teaching"]
        assert corpus["instructional_material_count"] == 27 and len(corpus["documents"]) == 79
        assert len(corpus["local_sequence"]) == 9
        assert {d["content_role"] for d in corpus["documents"] if d["kind"] == "source_reference"} == {"original_local_teaching_summary"}
        with pytest.raises(HTTPException):
            standards.approve_corpus(db, identity, course, "school", corpus["id"], "0" * 64, audit_actor)
        approved = standards.approve_corpus(db, identity, course, "school", corpus["id"], corpus["snapshot_sha256"], audit_actor)
        assert approved["ready_for_teaching"]
        found = standards.retrieve_corpus(db, identity, course, "school", "four-digit thousands", 3, audit_actor)
        assert found["status"] == "matched" and found["answer"] is None
        course.data = canonical({**course.value, "curriculum": {**CURRICULUM, "grades": [4]}})
        current = standards.corpus_descriptor(db, course, "school")
        assert current["status"] == "stale" and not current["ready_for_teaching"]
        with pytest.raises(HTTPException):
            standards.approve_corpus(db, identity, course, "school", corpus["id"], corpus["snapshot_sha256"], audit_actor)


def test_school_grade3_plan_publication_prepares_course_without_approving_content(client):
    headers = login(client, "demo-admin")
    made = client.post('/api/schools', json={"name": "Synthetic grade3 school", "curriculum": {"mode": "state", **{k: v for k, v in CURRICULUM.items() if k not in {'subject', 'standards_version'}}, "subjects": ["mathematics"]}}, headers=headers)
    assert made.status_code == 201, made.text
    school = made.json()
    response = client.post(f"/api/schools/{school['id']}/curriculum/runs", json={"idempotency_key": "grade3-plan-synthetic", "expected_school_version": school['version']}, headers=headers)
    assert response.status_code == 201, response.text
    run = response.json()
    course = run['plan']['courses'][0]
    assert course['coverage'] == 'complete_standards' and len(course['local_sequence']) == 9
    response = client.post(f"/api/schools/{school['id']}/curriculum/publish", json={"idempotency_key": "grade3-publish-synthetic", "expected_school_version": school['version'], "run_id": run['id'], "plan_sha256": run['plan_sha256'], "course_keys": [course['key']]}, headers=headers)
    assert response.status_code == 201, response.text
    corpus = response.json()['courses'][0]['corpus']
    assert corpus['coverage'] == 'complete_standards' and not corpus['ready_for_teaching']
    assert corpus['approval']['status'] == 'pending'
