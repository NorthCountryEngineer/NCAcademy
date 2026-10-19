"""Render the auditable New York subject/grade matrix and source register.

Uses only installed review bundles; never fetches sources or generates lessons.
Run with the API dependencies installed. --check refuses stale checked-in reports.
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def grade_name(grade):
    return "Pre-K" if grade == -1 else "K" if grade == 0 else str(grade)


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def reports():
    from academy.curriculum_review import load_reviews

    reviews = load_reviews()
    subjects = sorted((subject for review in reviews for subject in review["subjects"]), key=lambda item: item["code"])
    source_count = sum(len(review["sources"]) for review in reviews)
    grade_count = sum(len(subject["grades"]) for subject in subjects)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("academic_year", "grade", "subject_code", "subject", "category", "placement", "framework_editions", "planning_topics", "limitations", "source_urls", "teaching_coverage"))
    for review in reviews:
        sources = {source["id"]: source for source in review["sources"]}
        for subject in sorted(review["subjects"], key=lambda item: item["code"]):
            for grade in subject["grades"]:
                frameworks = [item for item in subject["frameworks"] if grade in item["grades"]]
                focus = [item for item in subject["grade_focus"] if grade in item["grades"]]
                topics = [topic for item in focus for topic in item["topics"]]
                source_ids = set(subject["source_ids"]) | {item["source_id"] for item in frameworks} | {key for item in focus for key in item["source_ids"]}
                writer.writerow((review["academic_year"], grade_name(grade), subject["code"], subject["name"], subject["category"], subject["placement"], " / ".join(dict.fromkeys(item["version"] for item in frameworks)), " / ".join(dict.fromkeys(topics)), " / ".join(subject["limitations"]), " ".join(sources[key]["url"] for key in sorted(source_ids)), "partial mapping and limited teaching summaries" if subject["code"] in {"earth_space_science", "environmental_science"} else "framework review only; mapping and materials required"))
    lines = ["# New York curriculum subject and source register", "", "Academic year **2026–2027**. Reviewed **October 18, 2026**. Planning applicability ends **June 30, 2027**.", "", f"The catalog contains **{len(subjects)} subject pathways**, **{grade_count} grade/subject combinations** and **{source_count} source records** across four domain bundles. Source records can refer to the same official resource in different domains; this count is not a count of unique publications. A grade listing is planning availability, not a requirement to take every subject in that grade.", "", "Generated from the installed review bundles by `scripts/review_ny_curriculum.py`. The [review](ny-curriculum-2026-review.md) explains the scope and teaching limits; the [CSV](ny-curriculum-2026-coverage.csv) contains the full grade/topic matrix.", "", "## Subject pathways", "", "| Code | Subject | Grades | Placement |", "| --- | --- | --- | --- |"]
    for subject in subjects:
        lines.append("| " + " | ".join(cell(value) for value in (subject["code"], subject["name"], ", ".join(grade_name(grade) for grade in subject["grades"]), subject["placement"].replace("_", " "))) + " |")
    lines += ["", "## Implementation transitions", "", "These are dated source findings. Later or proposed requirements are not treated as current mandates.", ""]
    for review in reviews:
        sources = {source["id"]: source for source in review["sources"]}
        for transition in review["transitions"]:
            links = ", ".join(f"[{sources[key]['title']}]({sources[key]['url']})" for key in transition["source_ids"])
            lines += [f"### {transition['title']}", "", f"Applies from: **{transition['applies_from']}**. {transition['finding']} {links}.", ""]
    lines += ["## Review notes", ""]
    for review in reviews:
        sources = {source["id"]: source for source in review["sources"]}
        for note in review["review_notes"]:
            links = ", ".join(f"[{sources[key]['title']}]({sources[key]['url']})" for key in note["source_ids"])
            lines += [f"- **{note['id']}**: {note['finding']} {links}.", ""]
    lines += ["## Source evidence", "", "**Exact bytes** means the reviewed downloaded resource has an actual SHA-256 and byte length. **Official page review** means the official page, document or index was reviewed without a retained exact-byte receipt. A topic plan or a page review does not establish complete instructional coverage, reproduction rights or permission to publish teaching material.", "", "| Source ID | Official publication | Edition | Review evidence |", "| --- | --- | --- | --- |"]
    for review in reviews:
        for source in sorted(review["sources"], key=lambda item: item["id"]):
            evidence = f"Exact bytes: {source['byte_length']:,}; SHA-256 `{source['content_sha256']}`" if source["verification"] == "exact_bytes_review" else "Official page review; no byte hash asserted"
            lines.append("| " + " | ".join(cell(value) for value in (source["id"], f"[{source['title']}]({source['url']})", source["edition"], evidence)) + " |")
    return {ROOT / "docs/ny-curriculum-2026-coverage.csv": output.getvalue(), ROOT / "docs/ny-curriculum-2026-register.md": "\n".join(lines) + "\n"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in reports().items():
        if args.check:
            if not path.is_file() or path.read_text() != content:
                raise SystemExit(f"Curriculum report needs regeneration: {path.relative_to(ROOT)}")
        else:
            path.write_text(content)
        print(("Checked " if args.check else "Wrote ") + str(path.relative_to(ROOT)))


if __name__ == "__main__":
    main()
