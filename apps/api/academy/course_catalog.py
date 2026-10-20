"""Complete, local NYSED course choices, separate from teaching alignment.

This reporting catalog includes practical, performance, academic and other course
codes. Selecting a code creates no standards alignment, teaching approval or
certification claim. Requests never download or parse an administrator's URL.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi import HTTPException


CATALOG_PATH = Path(__file__).with_name("course_catalog_data") / "ny_sced_2026_2027.json"
MAX_CATALOG_BYTES = 5000000
COURSE_LEVELS = {"secondary", "prior_to_secondary", "all_grades"}
COURSE_FIELDS = {"course_code", "title", "description", "level", "subject_area", "cte", "ap", "ib", "growth_core_indicator", "source_row"}
REGENTS_COURSE_CODES = frozenset({"01003", "02050", "02056", "02072", "03008", "03050", "03101", "03120", "03151", "03170", "04052", "04101"})
ASSESSMENT_SOURCE = {
    "id": "nysed-assessment-courses-2026-27", "publisher": "NYSED",
    "title": "2026-27 Codes and Descriptions for Courses Ending in State Assessments",
    "url": "https://www.nysed.gov/sites/default/files/programs/information-reporting-services/codes-and-descriptions-for-courses-ending-in-state-assessments-2026-27.xlsx",
    "index_url": "https://www.nysed.gov/information-reporting-services/vendor-support-student-management-systems-smss",
    "content_sha256": "87e00f4d56af4e3d0abf2440d59640eb10fd73e8031868f6bfeb47b519f442d3",
    "byte_length": 22322, "sheet": "courses end in NYS assessment", "assessment_rows": 48,
    "course_count": 26, "regents_course_count": 12,
}


def regents_course_codes(state="US-NY", academic_year="2026-2027"):
    """Return exact assessed course IDs, including transitional science options.

    The offline import check verifies this allowlist against the separately
    retained official assessment workbook. It is a discovery tag, not a claim
    that every listed assessment is offered in every administration.
    """
    if state != "US-NY" or academic_year != "2026-2027":
        raise HTTPException(422, "No Regents course mapping is installed for this state and academic year")
    return REGENTS_COURSE_CODES


def assessment_source_descriptor(state="US-NY", academic_year="2026-2027"):
    """Return a caller-owned exact-byte receipt for the assessment mapping."""
    regents_course_codes(state, academic_year)
    return deepcopy(ASSESSMENT_SOURCE)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate course catalog object key")
        result[key] = value
    return result


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise ValueError("Invalid course catalog text")


def _official_url(value):
    _text(value, 2048)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not (host == "nysed.gov" or host.endswith(".nysed.gov"))
                or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
            raise ValueError("Invalid official course catalog source")
    except ValueError as exc:
        raise ValueError("Invalid official course catalog source") from exc


def validate_course_catalog(value):
    """Fail closed on malformed packaged data; no teaching source is inferred."""
    required = {"schema_version", "state", "state_name", "academic_year", "title", "reviewed_at", "sources", "courses"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Unexpected course catalog fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["state"] != "US-NY" or value["academic_year"] != "2026-2027":
        raise ValueError("Only the installed New York 2026-2027 course catalog is available")
    for key in ("state_name", "title"):
        _text(value[key], 250)
    try:
        reviewed = date.fromisoformat(value["reviewed_at"])
        if reviewed.isoformat() != value["reviewed_at"] or not date(2026, 1, 1) <= reviewed <= date(2027, 6, 30):
            raise ValueError("Invalid course catalog review date")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid course catalog review date") from exc
    if not isinstance(value["sources"], list) or len(value["sources"]) != 1:
        raise ValueError("A course catalog requires its exact official workbook receipt")
    source = value["sources"][0]
    if not isinstance(source, dict) or set(source) != {"id", "title", "publisher", "url", "index_url", "content_sha256", "byte_length", "sheet", "course_rows"}:
        raise ValueError("Invalid course catalog provenance")
    for field in ("id", "title", "sheet"):
        _text(source[field], 250)
    _official_url(source["url"])
    _official_url(source["index_url"])
    if (source["publisher"] != "NYSED" or not isinstance(source["content_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["content_sha256"])
            or type(source["byte_length"]) is not int or not 1 <= source["byte_length"] <= 10000000):
        raise ValueError("Invalid course catalog source receipt")
    courses = value["courses"]
    if not isinstance(courses, list) or not 1 <= len(courses) <= 10000 or type(source["course_rows"]) is not int or source["course_rows"] != len(courses):
        raise ValueError("Course catalog source row count does not match")
    codes, rows = set(), set()
    for course in courses:
        if not isinstance(course, dict) or set(course) != COURSE_FIELDS:
            raise ValueError("Invalid course catalog entry fields")
        code = course["course_code"]
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{5}[A-Z]?", code) or code in codes:
            raise ValueError("Course catalog codes must be unique source identifiers")
        codes.add(code)
        for field, maximum in (("title", 250), ("description", 10000), ("subject_area", 100)):
            _text(course[field], maximum)
        if not isinstance(course["level"], str) or course["level"] not in COURSE_LEVELS:
            raise ValueError("Invalid course catalog reporting level")
        if any(type(course[field]) is not bool for field in ("cte", "ap", "ib")) or course["growth_core_indicator"] is not None and type(course["growth_core_indicator"]) is not bool:
            raise ValueError("Invalid source course indicators")
        row = course["source_row"]
        if type(row) is not int or row < 2 or row > 10002 or row in rows:
            raise ValueError("Course catalog source rows must be unique")
        rows.add(row)


def load_course_catalog(state="US-NY", academic_year="2026-2027"):
    """Return all published course rows, with the exact installed JSON digest."""
    if state != "US-NY" or academic_year != "2026-2027":
        raise HTTPException(422, "No course catalog is installed for this state and academic year")
    with CATALOG_PATH.open("rb") as stream:
        content = stream.read(MAX_CATALOG_BYTES + 1)
    if len(content) > MAX_CATALOG_BYTES:
        raise ValueError("Course catalog exceeds its package limit")
    value = json.loads(content, object_pairs_hook=_object)
    validate_course_catalog(value)
    return {**value, "catalog_sha256": hashlib.sha256(content).hexdigest()}


def catalog_selection(value):
    """Resolve a browser selection to current server-owned immutable provenance."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict) or set(value) != {"state", "academic_year", "course_code", "catalog_sha256"}:
        raise HTTPException(422, "Choose a course from the installed catalog")
    catalog = load_course_catalog(value["state"], value["academic_year"])
    if value["catalog_sha256"] != catalog["catalog_sha256"]:
        raise HTTPException(409, "The course catalog changed. Refresh the course choices before saving")
    selected = next((course for course in catalog["courses"] if course["course_code"] == value["course_code"]), None)
    if selected is None:
        raise HTTPException(422, "The selected course code is not in the installed catalog")
    source = catalog["sources"][0]
    return deepcopy({**value, **selected, "source_url": source["url"], "source_sha256": source["content_sha256"]})
