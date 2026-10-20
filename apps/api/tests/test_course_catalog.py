"""Whole NYSED course coverage is independent of instructional source readiness."""
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

from fastapi import HTTPException
import pytest

from academy import course_catalog
from academy.course_catalog import assessment_source_descriptor, catalog_selection, load_course_catalog, regents_course_codes


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("import_ny_course_catalog", ROOT / "scripts/import_ny_course_catalog.py")
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


def selection(code="08001", **changes):
    catalog = load_course_catalog()
    return {"state": "US-NY", "academic_year": "2026-2027", "course_code": code,
            "catalog_sha256": catalog["catalog_sha256"], **changes}


def test_all_2030_official_workbook_rows_are_retained_exactly_with_source_receipt():
    raw = IMPORTER.SOURCE.read_bytes()
    catalog = load_course_catalog()
    assert len(raw) == 269951
    assert hashlib.sha256(raw).hexdigest() == "ba10897d5193eca4c9740f717c5665ed7bcd6ab956aa4d14d1426b875ac2f438"
    assert IMPORTER.render_catalog(raw) == course_catalog.CATALOG_PATH.read_text()
    assert catalog["courses"] == IMPORTER.workbook_courses(raw)
    assert len(catalog["courses"]) == len({item["course_code"] for item in catalog["courses"]}) == 2030
    assert Counter(item["level"] for item in catalog["courses"]) == {"secondary": 1605, "prior_to_secondary": 416, "all_grades": 9}
    assert len({item["subject_area"] for item in catalog["courses"]}) == 25
    assert catalog["catalog_sha256"] == hashlib.sha256(course_catalog.CATALOG_PATH.read_bytes()).hexdigest()
    assert catalog["sources"][0]["course_rows"] == 2030


@pytest.mark.parametrize("code,title", [
    ("01003", "ELA III (11th grade)"), ("02050", "Algebra I"), ("02056", "Algebra II"),
    ("02072", "Geometry"), ("03008", "Earth and Space Sciences"), ("03050", "Life Science/Biology"),
    ("03120", "Physical Science: Chemistry"), ("03170", "Physical Science: Physics"),
    ("04052", "Global History and Geography"), ("04101", "U.S. History and Government"),
    ("08001", "Physical Education"), ("05101", "General Band"), ("05102", "Concert Band"),
    ("05103", "Marching Band"), ("05104", "Orchestra"), ("05110", "Chorus"),
    ("05154", "Creative Art-Comprehensive"), ("05001", "Dance Technique"),
    ("05052", "Theatre Arts"), ("17001", "Construction Careers Explore"),
    ("58029", "Phys Ed (pre-k)"), ("03008L", "Earth and Space Sciences Lab"),
    ("03170L", "Physical Science: Physics Lab"),
])
def test_regents_practical_arts_early_years_and_lab_courses_can_all_be_selected(code, title):
    result = catalog_selection(selection(code))
    assert result["course_code"] == code and result["title"] == title
    assert result["description"] and result["source_url"].endswith("sced-course-codes-2026-27.xlsx")
    # A course option does not falsely create grade, standards or teaching approval.
    assert not {"grades", "standards", "curriculum", "corpus", "approved", "teacher_certification"} & result.keys()


def test_catalog_selection_does_not_mutate_the_catalog_and_pins_server_metadata():
    original = load_course_catalog()
    result = catalog_selection(selection())
    result["title"] = "A caller cannot mutate the installed catalog"
    assert load_course_catalog() == original
    assert result["source_sha256"] == original["sources"][0]["content_sha256"]


@pytest.mark.parametrize("changes,status", [
    ({"state": "US-CA"}, 422), ({"academic_year": "2027-2028"}, 422),
    ({"course_code": "not_an_official_code"}, 422), ({"catalog_sha256": "0" * 64}, 409),
    ({"catalog_sha256": None}, 409), ({"title": "Forged official title"}, 422),
])
def test_catalog_selection_requires_exact_installed_source_and_code(changes, status):
    with pytest.raises(HTTPException) as caught:
        catalog_selection(selection(**changes))
    assert caught.value.status_code == status


def raw_catalog():
    return json.loads(course_catalog.CATALOG_PATH.read_bytes())


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(schema_version=True),
    lambda value: value.update(state="US-CA"),
    lambda value: value.update(academic_year="2027-2028"),
    lambda value: value.update(reviewed_at="2026-13-20"),
    lambda value: value.update(standards=[{"code": "invented"}]),
    lambda value: value["courses"].append(deepcopy(value["courses"][0])),
    lambda value: value["courses"][0].update(course_code="01001;bad"),
    lambda value: value["courses"][0].update(level="unknown"),
    lambda value: value["courses"][0].update(title=""),
    lambda value: value["courses"][0].update(title="control\0characters"),
    lambda value: value["courses"][0].update(source_row=True),
    lambda value: value["courses"][1].update(source_row=value["courses"][0]["source_row"]),
    lambda value: value["courses"][0].update(cte=1),
    lambda value: value["courses"][0].update(growth_core_indicator="Yes"),
    lambda value: value["sources"][0].update(content_sha256="not_a_digest"),
    lambda value: value["sources"][0].update(byte_length=True),
    lambda value: value["sources"][0].update(course_rows=2000),
    lambda value: value["sources"][0].update(url="http://www.nysed.gov/source"),
    lambda value: value["sources"][0].update(url="https://www.nysed.gov.evil.test/source"),
    lambda value: value["sources"][0].update(url="https://secret@www.nysed.gov/source"),
    lambda value: value["sources"][0].update(url="https://www.nysed.gov:444/source"),
    lambda value: value["sources"][0].update(url="https://www.nysed.gov/source other"),
])
def test_loader_rejects_unreviewed_or_malformed_catalog(tmp_path, monkeypatch, mutation):
    value = raw_catalog()
    mutation(value)
    fixture = tmp_path / "catalog.json"
    fixture.write_text(json.dumps(value))
    monkeypatch.setattr(course_catalog, "CATALOG_PATH", fixture)
    with pytest.raises(ValueError):
        load_course_catalog()


def test_loader_rejects_duplicate_object_keys_and_oversized_json(tmp_path, monkeypatch):
    fixture = tmp_path / "catalog.json"
    fixture.write_text('{"state":"US-NY","state":"US-CA"}')
    monkeypatch.setattr(course_catalog, "CATALOG_PATH", fixture)
    with pytest.raises(ValueError, match="Duplicate"):
        load_course_catalog()
    monkeypatch.setattr(course_catalog, "MAX_CATALOG_BYTES", 5)
    with pytest.raises(ValueError, match="package limit"):
        load_course_catalog()


def test_offline_importer_rejects_any_changed_or_unreviewed_workbook():
    original = IMPORTER.SOURCE.read_bytes()
    for changed in (b"", original + b"extra", b"changed" + original[7:]):
        with pytest.raises(ValueError, match="source receipt"):
            IMPORTER.workbook_courses(changed)


def test_every_official_state_assessment_course_including_all_regents_options_is_available():
    rows = IMPORTER.assessment_courses(IMPORTER.ASSESSMENT_SOURCE.read_bytes())
    catalog = {course["course_code"]: course for course in load_course_catalog()["courses"]}
    assert len(rows) == 48
    assert len({row["course_code"] for row in rows}) == 26
    assert {row["course_code"] for row in rows} <= catalog.keys()
    regents_codes = {row["course_code"] for row in rows if row["assessment_name"].startswith("Regents ")}
    # Preserve both new Physical Science and legacy Chemistry/Physics entries.
    assert regents_codes == {"01003", "02050", "02056", "02072", "03008", "03050", "03101", "03120", "03151", "03170", "04052", "04101"}
    assert regents_codes == regents_course_codes()
    assert all(catalog[code]["level"] == "secondary" for code in regents_codes)
    with pytest.raises(ValueError, match="source receipt"):
        IMPORTER.assessment_courses(IMPORTER.ASSESSMENT_SOURCE.read_bytes() + b"changed")


def test_regents_discovery_and_receipt_are_exact_scoped_and_independent_of_title_keywords():
    codes = regents_course_codes()
    assert isinstance(codes, frozenset) and len(codes) == 12
    source = assessment_source_descriptor()
    source_bytes = IMPORTER.ASSESSMENT_SOURCE.read_bytes()
    assert source["content_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert source["byte_length"] == len(source_bytes) == 22322
    assert source["assessment_rows"] == 48 and source["course_count"] == 26 and source["regents_course_count"] == 12
    # Algebra, ELA and sciences remain discoverable even without Regents in a title.
    indexed = {course["course_code"]: course for course in load_course_catalog()["courses"]}
    assert all("regents" not in indexed[code]["title"].lower() for code in codes)
    source["title"] = "Caller edit"
    assert assessment_source_descriptor()["title"] != "Caller edit"
    for state, year in (("US-CA", "2026-2027"), ("US-NY", "2027-2028")):
        with pytest.raises(HTTPException) as caught:
            regents_course_codes(state, year)
        assert caught.value.status_code == 422
