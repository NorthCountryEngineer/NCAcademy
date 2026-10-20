"""Extract every row of the pinned official NYSED 2026-27 course workbook.

Offline only: no URL fetching, formulas, macros or external relationships are
executed. Source titles, descriptions, codes and reporting classifications are
preserved. The source workbook is not a standards mapping or teaching corpus.
Run --check in verification to compare the installed JSON against its source.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "apps/api/academy/course_catalog_data/sced-course-codes-2026-27.xlsx"
DESTINATION = SOURCE.with_name("ny_sced_2026_2027.json")
SOURCE_SHA256 = "ba10897d5193eca4c9740f717c5665ed7bcd6ab956aa4d14d1426b875ac2f438"
SOURCE_BYTES = 269951
ASSESSMENT_SOURCE = SOURCE.with_name("codes-and-descriptions-for-courses-ending-in-state-assessments-2026-27.xlsx")
ASSESSMENT_SHA256 = "87e00f4d56af4e3d0abf2440d59640eb10fd73e8031868f6bfeb47b519f442d3"
ASSESSMENT_BYTES = 22322
SOURCE_URL = "https://www.nysed.gov/sites/default/files/programs/information-reporting-services/sced-course-codes-2026-27.xlsx"
INDEX_URL = "https://www.nysed.gov/information-reporting-services/new-york-state-comprehensive-course-catalog"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
HEADERS = ["Course Code (Course ID)", "Course Code Description", "Course Description", "Course Level", "Course Subject Area", "CTE Indicator", "AP Indicator", "IB Indicator", "Growth (Core Ind)"]
LEVELS = {"SECONDARY": "secondary", "PRIOR TO SECONDARY": "prior_to_secondary", "ALL GRADES": "all_grades"}


def _xml(archive, member):
    info = archive.getinfo(member)
    if info.file_size > 20000000:
        raise ValueError("Source XML exceeds the bounded workbook import limit")
    content = archive.read(member)
    if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        raise ValueError("Workbook declarations are not accepted")
    return ET.fromstring(content)


def workbook_courses(content):
    """Extract the single exact reviewed workbook; do not import arbitrary data."""
    if len(content) != SOURCE_BYTES or hashlib.sha256(content).hexdigest() != SOURCE_SHA256:
        raise ValueError("Workbook differs from the reviewed NYSED source receipt")
    with ZipFile(io.BytesIO(content)) as archive:
        if len(archive.infolist()) > 200 or sum(item.file_size for item in archive.infolist()) > 30000000:
            raise ValueError("Workbook exceeds the bounded import limit")
        workbook = _xml(archive, "xl/workbook.xml")
        first = workbook.find("m:sheets/m:sheet", NS)
        if first is None or first.attrib.get("name") != "All courses" or first.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") != "rId1":
            raise ValueError("The reviewed All courses sheet was not found")
        strings = ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in _xml(archive, "xl/sharedStrings.xml").findall("m:si", NS)]
        sheet = _xml(archive, "xl/worksheets/sheet1.xml")
        rows = []
        for row in sheet.findall("m:sheetData/m:row", NS):
            cells = {}
            for cell in row.findall("m:c", NS):
                if cell.find("m:f", NS) is not None:
                    raise ValueError("Course source cells must not contain executable formulas")
                address = cell.attrib.get("r", "")
                column = re.sub(r"[0-9]+$", "", address)
                if column not in "ABCDEFGHI" or len(column) != 1 or column in cells:
                    raise ValueError("Unexpected course source columns")
                value = cell.findtext("m:v", "", NS)
                if cell.attrib.get("t") == "s":
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall("m:is//m:t", NS))
                cells[column] = value
            if any(cells.values()):
                rows.append((int(row.attrib["r"]), [cells.get(column, "") for column in "ABCDEFGHI"]))
    if not rows or rows[0] != (1, HEADERS):
        raise ValueError("NYSED workbook columns changed; review before importing")
    courses = []
    for row_number, row in rows[1:]:
        code, title, description, level, area, cte, ap, ib, growth = row
        if level not in LEVELS or any(value not in {"Yes", "No"} for value in (cte, ap, ib)) or growth not in {"", "Yes", "No"}:
            raise ValueError("Unexpected NYSED reporting classifications")
        courses.append({"course_code": code, "title": title, "description": description,
                        "level": LEVELS[level], "subject_area": area,
                        "cte": cte == "Yes", "ap": ap == "Yes", "ib": ib == "Yes",
                        "growth_core_indicator": None if not growth else growth == "Yes", "source_row": row_number})
    if len(courses) != 2030 or len({course["course_code"] for course in courses}) != len(courses):
        raise ValueError("The reviewed source must contain every one of its 2,030 unique course rows")
    return courses


def catalog_document(content):
    courses = workbook_courses(content)
    return {"schema_version": 1, "state": "US-NY", "state_name": "New York", "academic_year": "2026-2027",
            "title": "2026-27 NYS Comprehensive Course Catalog", "reviewed_at": "2026-10-20",
            "sources": [{"id": "nysed-sced-2026-27", "title": "2026-27 NYS Comprehensive Course Catalog Workbook",
                         "publisher": "NYSED", "url": SOURCE_URL, "index_url": INDEX_URL,
                         "content_sha256": SOURCE_SHA256, "byte_length": SOURCE_BYTES,
                         "sheet": "All courses", "course_rows": len(courses)}], "courses": courses}


def assessment_courses(content):
    """Validate every state-assessment course against a separate official receipt.

    NYSED stores numeric course IDs without leading zeroes in this workbook. The
    documented five-digit course identifier is restored for catalog comparison;
    the authoritative comprehensive workbook retains its original text codes.
    """
    if len(content) != ASSESSMENT_BYTES or hashlib.sha256(content).hexdigest() != ASSESSMENT_SHA256:
        raise ValueError("Assessment workbook differs from its reviewed source receipt")
    with ZipFile(io.BytesIO(content)) as archive:
        strings = ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in _xml(archive, "xl/sharedStrings.xml").findall("m:si", NS)]
        rows = []
        for row in _xml(archive, "xl/worksheets/sheet1.xml").findall("m:sheetData/m:row", NS)[1:]:
            cells = {}
            for cell in row.findall("m:c", NS):
                if cell.find("m:f", NS) is not None:
                    raise ValueError("Assessment source cells must not contain formulas")
                column = re.sub(r"[0-9]+$", "", cell.attrib["r"])
                text = cell.findtext("m:v", "", NS)
                cells[column] = strings[int(text)] if cell.attrib.get("t") == "s" else text
            if cells:
                code = cells.get("C", "")
                if not re.fullmatch(r"[0-9]{4,5}", code):
                    raise ValueError("Unexpected state-assessment course identifier")
                rows.append({"assessment_name": cells["A"], "assessment_code": cells["B"],
                             "course_code": code.zfill(5), "course_title": cells["D"], "source_row": int(row.attrib["r"])})
    if len(rows) != 48:
        raise ValueError("The reviewed state-assessment workbook must retain all 48 source rows")
    return rows


def render_catalog(content):
    return json.dumps(catalog_document(content), ensure_ascii=False, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render_catalog(SOURCE.read_bytes())
    if args.check:
        if not DESTINATION.is_file() or DESTINATION.read_text() != expected:
            raise SystemExit("The installed course catalog differs from the pinned official workbook")
    else:
        DESTINATION.parent.mkdir(parents=True, exist_ok=True)
        DESTINATION.write_text(expected)
    sys.path.insert(0, str(ROOT / "apps/api"))
    from academy.course_catalog import assessment_source_descriptor, load_course_catalog, regents_course_codes
    catalog = load_course_catalog()
    assessed = assessment_courses(ASSESSMENT_SOURCE.read_bytes())
    codes = {course["course_code"] for course in catalog["courses"]}
    if not {course["course_code"] for course in assessed} <= codes:
        raise SystemExit("A state-assessment course is missing from the comprehensive catalog")
    if {course["course_code"] for course in assessed if course["assessment_name"].startswith("Regents ")} != regents_course_codes():
        raise SystemExit("The Regents discovery mapping differs from the official assessment workbook")
    receipt = assessment_source_descriptor()
    if receipt["content_sha256"] != ASSESSMENT_SHA256 or receipt["byte_length"] != ASSESSMENT_BYTES:
        raise SystemExit("The published assessment source receipt differs from its reviewed workbook")
    levels = Counter(course["level"] for course in catalog["courses"])
    print(f"{'Checked' if args.check else 'Imported'} {len(catalog['courses']):,} NYSED course codes across {len({course['subject_area'] for course in catalog['courses']})} subject areas: {dict(levels)}; all {len(assessed)} state-assessment mappings retained")


if __name__ == "__main__":
    main()
