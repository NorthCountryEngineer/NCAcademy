"""Bounded, reviewed curriculum scope; never a teaching or standards package.

Only explicitly installed NYSED source reviews are loaded. A scope review can
support a school planning draft, but cannot assert indicator-level alignment,
provide teaching documents or satisfy corpus approval. No request fetches URLs.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .db import canonical
from .standards_schemas import CurriculumInput


REVIEW_PATHS = tuple(Path(__file__).with_name("curriculum_data") / name for name in (
    "ny_mathematics_2026_2027.json",
    "ny_humanities_2026_2027.json",
    "ny_applied_arts_2026_2027.json",
    "ny_stem_2026_2027.json",
))
ACADEMIC_YEAR = "2026-2027"
REVIEW_VALID_THROUGH = "2027-06-30"
MAX_REVIEW_BYTES = 1000000
SOURCE_TYPES = {"learning_standards", "framework", "guidance", "assessment", "implementation"}
PLACEMENTS = {"grade_specific", "grade_band", "proficiency", "local_elective", "cross_curricular"}


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate curriculum review object key")
        result[key] = value
    return result


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Invalid curriculum review object fields")


def _text(value, limit=2000, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()) or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("Invalid curriculum review text")
    return value


def _identifier(value, *, subject=False):
    pattern = r"[a-z][a-z0-9_]{0,79}" if subject else r"[A-Za-z][A-Za-z0-9_-]{0,159}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError("Invalid curriculum review identifier")


def _grades(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 14 or any(type(g) is not int or not -1 <= g <= 12 for g in value) or value != sorted(set(value)):
        raise ValueError("Curriculum review grades must be unique, ordered PK (-1) through 12")


def _list(value, maximum, *, empty=False):
    if not isinstance(value, list) or len(value) > maximum or (not empty and not value):
        raise ValueError("Curriculum review list exceeds its bounded schema")


def _refs(value, source_ids):
    _list(value, 160)
    if any(not isinstance(item, str) for item in value) or len(set(value)) != len(value) or not set(value) <= source_ids:
        raise ValueError("Curriculum review has unknown or duplicate source references")


def _texts(value, maximum=40):
    _list(value, maximum)
    for item in value:
        _text(item, 4000)


def _official_url(value):
    _text(value, 2048)
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise ValueError("Curriculum sources require a canonical NYSED HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not (host == "nysed.gov" or host.endswith(".nysed.gov")):
            raise ValueError("Curriculum sources require a canonical NYSED HTTPS URL")
    except ValueError as exc:
        raise ValueError("Curriculum sources require a canonical NYSED HTTPS URL") from exc


def _validate_review(value):
    _keys(value, {"schema_version", "jurisdiction", "academic_year", "reviewed_at", "sources", "subjects", "transitions", "review_notes"})
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["jurisdiction"] != "US-NY" or value["academic_year"] != ACADEMIC_YEAR:
        raise ValueError("Only the explicitly reviewed New York 2026-2027 scope is available")
    try:
        reviewed = date.fromisoformat(value["reviewed_at"])
        if reviewed.isoformat() != value["reviewed_at"] or not date(2026, 1, 1) <= reviewed <= date.fromisoformat(REVIEW_VALID_THROUGH):
            raise ValueError("Invalid curriculum review date")
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid curriculum review date") from exc
    _list(value["sources"], 160)
    source_ids = set()
    for source in value["sources"]:
        _keys(source, {"id", "title", "url", "publisher", "source_type", "edition", "notes", "verification"}, {"content_sha256", "byte_length"})
        _identifier(source["id"])
        if source["id"] in source_ids:
            raise ValueError("Curriculum review source IDs must be unique")
        source_ids.add(source["id"])
        for field in ("title", "edition", "notes"):
            _text(source[field], 4000, empty=field == "notes")
        _official_url(source["url"])
        if source["publisher"] != "NYSED" or not isinstance(source["source_type"], str) or source["source_type"] not in SOURCE_TYPES or not isinstance(source["verification"], str) or source["verification"] not in {"official_page_review", "exact_bytes_review"}:
            raise ValueError("Invalid curriculum source provenance")
        has_hash, has_length = "content_sha256" in source, "byte_length" in source
        if has_hash != has_length or source["verification"] == "exact_bytes_review" and not has_hash:
            raise ValueError("An exact-byte source review requires its observed digest and length")
        if has_hash and (not isinstance(source["content_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", source["content_sha256"]) or type(source["byte_length"]) is not int or not 1 <= source["byte_length"] <= 50000000):
            raise ValueError("Invalid observed curriculum source digest or length")
    _list(value["subjects"], 80)
    subject_codes = set()
    for subject in value["subjects"]:
        _keys(subject, {"code", "name", "category", "grades", "placement", "frameworks", "grade_focus", "tutoring_scope", "limitations", "source_ids"})
        _identifier(subject["code"], subject=True)
        _identifier(subject["category"], subject=True)
        _text(subject["name"], 200)
        if subject["code"] in subject_codes or not isinstance(subject["placement"], str) or subject["placement"] not in PLACEMENTS:
            raise ValueError("Curriculum subject codes must be unique with an explicit placement")
        subject_codes.add(subject["code"])
        _grades(subject["grades"])
        grades = set(subject["grades"])
        _refs(subject["source_ids"], source_ids)
        _texts(subject["tutoring_scope"])
        _texts(subject["limitations"])
        _list(subject["frameworks"], 30)
        for framework in subject["frameworks"]:
            _keys(framework, {"source_id", "version", "grades", "notes"})
            _identifier(framework["source_id"])
            _grades(framework["grades"])
            _text(framework["version"], 500)
            _text(framework["notes"], 4000)
            if framework["source_id"] not in source_ids or not set(framework["grades"]) <= grades:
                raise ValueError("Framework references must match the reviewed subject grades")
        _list(subject["grade_focus"], 30)
        focused_grades = set()
        for focus in subject["grade_focus"]:
            _keys(focus, {"grades", "topics", "source_ids"})
            _grades(focus["grades"])
            _texts(focus["topics"])
            _refs(focus["source_ids"], source_ids)
            if not set(focus["grades"]) <= grades:
                raise ValueError("Grade focus exceeds the reviewed subject scope")
            focused_grades.update(focus["grades"])
        if focused_grades != grades:
            raise ValueError("Each listed subject grade requires reviewed planning topics")
    for collection, fields, maximum in (("transitions", {"id", "title", "applies_from", "grades", "source_ids", "finding"}, 80), ("review_notes", {"id", "source_ids", "finding"}, 80)):
        _list(value[collection], maximum, empty=True)
        identifiers = set()
        for item in value[collection]:
            _keys(item, fields)
            _identifier(item["id"])
            if item["id"] in identifiers:
                raise ValueError("Curriculum review note and transition IDs must be unique")
            identifiers.add(item["id"])
            _refs(item["source_ids"], source_ids)
            _text(item["finding"], 8000)
            if collection == "transitions":
                _text(item["title"], 500)
                _text(item["applies_from"], 80)
                _grades(item["grades"])


def load_reviews():
    """Read the four explicit reviewed files, never a caller-supplied path or URL."""
    result, source_ids, subject_codes = [], set(), set()
    for path in REVIEW_PATHS:
        with path.open("rb") as stream:
            content = stream.read(MAX_REVIEW_BYTES + 1)
        if len(content) > MAX_REVIEW_BYTES:
            raise ValueError("Curriculum source review exceeds its package limit")
        value = json.loads(content, object_pairs_hook=_object)
        _validate_review(value)
        current_ids = {item["id"] for item in value["sources"]}
        current_subjects = {item["code"] for item in value["subjects"]}
        if source_ids & current_ids or subject_codes & current_subjects:
            raise ValueError("Curriculum review sources and subjects must be unambiguous across installed files")
        source_ids.update(current_ids)
        subject_codes.update(current_subjects)
        value["review_sha256"] = hashlib.sha256(content).hexdigest()
        value["review_valid_through"] = REVIEW_VALID_THROUGH
        result.append(value)
    return result


def _current(review):
    try:
        return date.fromisoformat(review["reviewed_at"]) <= date.today() <= date.fromisoformat(review["review_valid_through"])
    except (ValueError, KeyError, TypeError):
        return False


def _subject_review(bundle, subject):
    source_ids = set(subject["source_ids"]) | {item["source_id"] for item in subject["frameworks"]}
    for focus in subject["grade_focus"]:
        source_ids.update(focus["source_ids"])
    return {
        **deepcopy(subject), "state": bundle["jurisdiction"], "state_name": "New York",
        "academic_year": bundle["academic_year"], "reviewed_at": bundle["reviewed_at"],
        "review_valid_through": bundle["review_valid_through"], "review_sha256": bundle["review_sha256"],
        "subject_review_sha256": _digest({"review_sha256": bundle["review_sha256"], "subject": subject}),
        "coverage": "framework_review", "alignment_mapping_available": False,
        "standards_version": None, "mapping_version": None,
        "source_ids": sorted(source_ids),
        "sources": [deepcopy(item) for item in bundle["sources"] if item["id"] in source_ids],
    }


def review_for(curriculum, reviews=None):
    """Return (scope review, gaps) for an exact state/year/grade combination.

    A scope review never verifies a standards-version pin. Real package selection
    remains the responsibility of standards.package_for.
    """
    if not curriculum:
        return None, [{"code": "curriculum_missing", "message": "Configure the class curriculum before selecting a source review."}]
    try:
        selected = CurriculumInput.model_validate(curriculum).model_dump()
    except ValueError:
        return None, [{"code": "curriculum_invalid", "message": "Saved curriculum metadata requires administrator review."}]
    reviews = load_reviews() if reviews is None else reviews
    candidates = [(bundle, subject) for bundle in reviews if bundle.get("jurisdiction") == selected["state"]
                  for subject in bundle.get("subjects", []) if subject.get("code") == selected["subject"]]
    if not candidates:
        return None, [{"code": "mapping_unavailable", "message": "No reviewed curriculum scope is installed for this state and subject."}]
    if selected["academic_year"] != ACADEMIC_YEAR or not any(bundle.get("academic_year") == selected["academic_year"] for bundle, _subject in candidates):
        return None, [{"code": "year_unreviewed", "message": "This source-scope review applies only to the 2026-2027 academic year."}]
    if selected["standards_version"]:
        return None, [{"code": "version_unreviewed", "message": "A source-scope review cannot verify a pinned standards version; an exact alignment package is required."}]
    exact = [(bundle, subject) for bundle, subject in candidates if bundle.get("academic_year") == selected["academic_year"] and set(selected["grades"]) <= set(subject["grades"])]
    if not exact:
        return None, [{"code": "grade_unreviewed", "message": "The selected grades fall outside this subject's reviewed source scope."}]
    available = [(bundle, subject) for bundle, subject in exact if _current(bundle)]
    if not available:
        expired = all(bundle.get("review_valid_through", "") < date.today().isoformat() for bundle, _subject in exact)
        return None, [{"code": "source_review_expired" if expired else "source_review_unavailable", "message": "A current source-scope review is required for this planning draft."}]
    matches = {_digest({"bundle": bundle, "subject": subject}): (bundle, subject) for bundle, subject in available}
    if len(matches) != 1:
        return None, [{"code": "mapping_ambiguous", "message": "Conflicting source-scope reviews require administrator resolution."}]
    bundle, subject = next(iter(matches.values()))
    result = _subject_review(bundle, subject)
    result["selected_grades"] = selected["grades"]
    result["frameworks"] = [item for item in result["frameworks"] if set(item["grades"]) & set(selected["grades"])]
    result["grade_focus"] = [item for item in result["grade_focus"] if set(item["grades"]) & set(selected["grades"])]
    return result, []


def review_catalog_descriptor(reviews=None):
    """Describe planning coverage without creating standards or teaching content."""
    reviews = load_reviews() if reviews is None else reviews
    subjects = []
    for bundle in reviews:
        if bundle.get("jurisdiction") != "US-NY" or bundle.get("academic_year") != ACADEMIC_YEAR or not _current(bundle):
            continue
        for subject in bundle["subjects"]:
            review = _subject_review(bundle, subject)
            subjects.append({
                **review, "academic_years": [ACADEMIC_YEAR], "versions": [],
                "default_selection_requires_curriculum": True, "bundled_instructional_documents": 0,
                "teaching_pack_version": None,
                "mapping_note": "Reviewed subject and source scope only. Exact standards alignment and school-approved teaching materials are still required.",
            })
    subjects.sort(key=lambda item: item["code"])
    return {"states": [{"code": "US-NY", "name": "New York", "subjects": subjects}] if subjects else [],
            "academic_year": ACADEMIC_YEAR, "review_valid_through": REVIEW_VALID_THROUGH,
            "network_fetch": False, "coverage": "framework_review", "alignment_mapping_available": False}
