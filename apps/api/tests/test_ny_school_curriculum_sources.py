"""Reviewed NY school source applicability and automatic Earth-science corpus boundaries."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from academy import standards
from academy.config import Settings
from academy.db import Base, Database, Record, audit, canonical
from academy.permissions import bind_scope


CURRICULUM = {"state": "US-NY", "subject": "earth_space_science", "grades": [9],
              "academic_year": "2026-2027", "standards_version": "nyssls-2016"}


def earth_package():
    return next(item for item in standards.load_catalogs() if item["subject"] == "earth_space_science")


def test_earth_package_is_separately_selectable_without_replacing_environmental_sources():
    catalogs = standards.load_catalogs()
    assert catalogs[0]["subject"] == "environmental_science"
    assert {item["subject"] for item in catalogs} >= {"environmental_science", "earth_space_science"}
    subject = next(item for item in standards.catalog_descriptor()["states"][0]["subjects"]
                   if item["code"] == "earth_space_science")
    assert subject["bundled_instructional_documents"] == 5
    assert subject["coverage"] == "partial"
    assert subject["default_selection_requires_curriculum"] is True


@pytest.mark.parametrize("grade", [9, 10, 11, 12])
def test_school_may_place_earth_course_in_each_reviewed_high_school_grade(grade):
    package, gaps = standards.package_for({**CURRICULUM, "grades": [grade]}, standards.load_catalogs())
    assert package["subject"] == "earth_space_science" and not gaps
    assert package["grades"] == [9, 10, 11, 12]
    assert "not a prescribed grade-specific" in package["mapping_note"]


@pytest.mark.parametrize("change,gap", [
    ({"grades": [8]}, "grade_unreviewed"),
    ({"academic_year": "2027-2028"}, "year_unreviewed"),
    ({"state": "US-CA"}, "mapping_unavailable"),
    ({"standards_version": "nyssls-2026"}, "version_unreviewed"),
])
def test_school_source_selection_does_not_invent_unreviewed_years_states_or_grade_placement(change, gap):
    package, gaps = standards.package_for({**CURRICULUM, **change}, standards.load_catalogs())
    assert package is None and gap in {item["code"] for item in gaps}


def test_new_york_earth_provenance_pins_actual_reviewed_editions_and_bytes():
    package = earth_package()
    sources = {item["id"]: item for item in package["sources"]}
    assert package["standards_version"] == "nyssls-2016"
    assert package["academic_years"] == ["2026-2027"]
    assert package["review_valid_through"] == "2027-06-30"
    assert sources["nysed-hs-standards"]["content_sha256"] == "0f3d02ec4a52051ed6e92c3c7ce98991615f29d84e8fdc4b349c7fd8a9f74e55"
    assert sources["nysed-hs-standards"]["byte_length"] == 749309
    assert sources["nysed-earth-course-map"]["content_sha256"] == "d90f5a178a93edde41c8abae625939c97332e1d5bd83a807833c08ff69ad4a62"
    assert sources["nysed-earth-reference-tables-2026"]["content_sha256"] == "0ede34a27075ff4888353971e4c7da559708eaa897fadb6c0fc0d516e395507e"
    table_evidence = next(item for item in package["applicability_references"]
                          if item["source_id"] == "nysed-reference-tables-index")
    assert "January 2027" in table_evidence["finding"]
    assert "2024 edition" in table_evidence["finding"]
    # NYSED material remains alignment evidence, never a copied lesson document.
    assert all(item["source_id"] not in {key for key in sources if key.startswith("nysed-")}
               for item in package["teaching_documents"])
    path = Path(standards.__file__).with_name("standards_data") / "ny_earth_space_science_2026.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == package["catalog_sha256"]


def test_each_public_earth_teaching_document_has_exact_source_and_selected_standard_mapping():
    package = earth_package()
    codes = {item["code"] for item in package["standards"]}
    sources = {item["id"]: item for item in package["sources"]}
    assert len(codes) == 14
    assert {item["topic"] for item in package["standards"]} >= {"tectonics", "space_systems", "geologic_history", "weather"}
    for item in package["teaching_documents"]:
        source = sources[item["source_id"]]
        assert item["standard_codes"] and set(item["standard_codes"]) <= codes
        assert item["source_sections"]
        assert source["publisher"] in {"NASA Science", "U.S. Geological Survey", "NOAA Education"}
        assert len(source["content_sha256"]) == 64 and source["byte_length"] > 10000
        assert len(item["content"].split()) < 120
    assert next(item for item in package["standards"] if item["code"] == "HS-ESS1-1")["pdf_page"] == 20
    assert next(item for item in package["standards"] if item["code"] == "HS-ESS2-8")["source_page"] == 79


@pytest.fixture
def earth_course(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "earth.db"), "local", "http://localhost:8090",
                        "synthetic-earth-source-bootstrap", False, False)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    identity = {"id": "synthetic-admin", "tenant_id": settings.tenant_id, "role": "admin", "principal_kind": "human"}
    def audit_actor(db, actor, event, value):
        audit(db, actor["tenant_id"], actor["id"], event, value)
    with database.write() as db:
        db.add(Record(id="synthetic-school", tenant_id=settings.tenant_id, kind="school", data=canonical({"name": "Synthetic school"})))
        course = Record(id="synthetic-earth", tenant_id=settings.tenant_id, kind="course", owner_id=identity["id"],
                        data=canonical({"title": "Earth and space sciences", "description": "Synthetic school placement", "curriculum": CURRICULUM}))
        db.add(course)
        db.flush()
        bind_scope(db, course, "synthetic-school")
    yield database, identity, audit_actor
    database.engine.dispose()


def test_earth_class_builds_referential_corpus_and_requires_separate_human_approval(earth_course):
    database, identity, audit_actor = earth_course
    with database.write() as db:
        course = db.get(Record, "synthetic-earth")
        first = standards.build_course_corpus(db, identity, course, "synthetic-school", audit_actor)
        again = standards.build_course_corpus(db, identity, course, "synthetic-school", audit_actor)
        assert first["id"] == again["id"]
        assert first["coverage"] == "partial" and first["status"] == "aligned"
        assert first["source_reference_count"] == 5
        assert len(first["documents"]) == 19
        assert first["ready_for_teaching"] is False and first["approval"]["status"] == "pending"
        coverage = next(item["message"] for item in first["gaps"] if item["code"] == "partial_instructional_coverage")
        assert "radiometric-time" in coverage and "reef" not in coverage
        with pytest.raises(HTTPException) as denied:
            standards.retrieve_corpus(db, identity, course, "synthetic-school", "fusion", 3, audit_actor)
        assert denied.value.status_code == 409
        approved = standards.approve_corpus(db, identity, course, "synthetic-school", first["id"], first["snapshot_sha256"], audit_actor)
        assert approved["ready_for_teaching"] is True
        retrieved = standards.retrieve_corpus(db, identity, course, "synthetic-school", "photosphere fusion", 3, audit_actor)
        assert retrieved["answer"] is None
        assert any(item["source_id"] == "nasa-sun-facts" and item["kind"] == "source_reference" for item in retrieved["matches"])


def test_expired_earth_source_review_blocks_automatic_corpus_readiness(earth_course, monkeypatch):
    database, identity, audit_actor = earth_course
    expired = deepcopy(earth_package())
    expired["review_valid_through"] = "2026-10-17"
    monkeypatch.setattr(standards, "load_catalogs", lambda: [expired])
    with database.write() as db:
        course = db.get(Record, "synthetic-earth")
        corpus = standards.build_course_corpus(db, identity, course, "synthetic-school", audit_actor)
        assert corpus["status"] == "unverified" and corpus["ready_for_teaching"] is False
        assert "source_review_expired" in {item["code"] for item in corpus["gaps"]}
        assert corpus["instructional_material_count"] == 0
