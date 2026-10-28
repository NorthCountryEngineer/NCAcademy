"""Deterministic reviewed-source curriculum planning and visible workflow evidence.

This adapter never visits URLs or asks a model to invent standards. It derives
partial, grade-specific planning drafts from currently reviewed local packages.
Adding state packages reuses the same planner. Custom ingestion is a reserved
configuration mode, not an implemented import or corpus.
"""
from collections import defaultdict
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from .db import Record, canonical, now, uid
from .permissions import bind_scope, scope_id
from .school_curriculum_schemas import SchoolCurriculumInput
from .standards import digest, load_catalogs, package_for
from .curriculum_review import load_reviews, review_for


WORKFLOW_KINDS = ("school_curriculum_run", "school_curriculum_publication", "knowledge_workflow_run")
PLANNER_VERSION = "reviewed-school-planner-v2"


def _grade_name(grade):
    return "Pre-K" if grade == -1 else "Kindergarten" if grade == 0 else "Grade " + str(grade)


def _scope_course(config, curriculum, grade, subject, review):
    """Plan subject scope without inventing official objectives or teaching text."""
    focus = [item for item in review["grade_focus"] if grade in item["grades"]]
    topics = list(dict.fromkeys(topic for item in focus for topic in item["topics"]))
    gaps = [
        {"code": "alignment_mapping_required", "message": "The official subject scope and sources have been reviewed, but an exact standards alignment package is not installed. Planning topics are not official standard statements or identifiers."},
        {"code": "instructional_materials_missing", "message": "This source review contains no approved teaching documents. Add reviewed class materials and an exact alignment package before teaching approval."},
        {"code": "course_sequence_requires_review", "message": "A human must choose the local syllabus, sequence, grade/proficiency placement, accessibility supports and assessments. Catalog coverage does not make this a required course in every listed grade."},
    ]
    return {
        "key": digest({"grade": grade, "subject": subject, "state": config["state"], "academic_year": config["academic_year"]})[:32],
        "title": f"{review['name']} · {_grade_name(grade)} · {config['academic_year']}",
        "grade": grade, "academic_year": config["academic_year"], "subject": subject,
        "curriculum": curriculum, "description": "Reviewed curriculum scope planning draft. Exact standards alignment, local syllabus and approved teaching materials remain required.",
        "units": [{"title": "Reviewed planning topics", "standard_codes": [], "objectives": [], "planning_topics": topics,
                   "source_ids": sorted({source_id for item in focus for source_id in item["source_ids"]})}],
        "planning_topics": topics, "frameworks": deepcopy(review["frameworks"]),
        "source_ids": deepcopy(review["source_ids"]), "sources": deepcopy(review["sources"]),
        "catalog_sha256": review["review_sha256"], "curriculum_review_sha256": review["subject_review_sha256"],
        "standards_version": None, "mapping_version": None, "coverage": "framework_review",
        "alignment_mapping_available": False, "planning_only": True,
        "placement": review["placement"], "category": review["category"],
        "limitations": deepcopy(review["limitations"]), "tutoring_scope": deepcopy(review["tutoring_scope"]),
        "curriculum_review": deepcopy(review), "gaps": gaps,
    }


def school_configuration(school):
    return SchoolCurriculumInput.model_validate(school.value.get("curriculum", {})).model_dump()


def plan_curriculum(configuration):
    config = SchoolCurriculumInput.model_validate(configuration).model_dump()
    courses, gaps = [], []
    if config["mode"] == "none":
        return {"adapter_version": PLANNER_VERSION, "configuration": config, "courses": [], "gaps": [{"code": "no_curriculum", "message": "No school curriculum selected. Create independent classes or base teacher profiles when needed."}], "ingestion_supported": False, "status": "needs_configuration"}
    if config["mode"] == "custom":
        return {"adapter_version": PLANNER_VERSION, "configuration": config, "courses": [], "gaps": [{"code": "custom_ingestion_deferred", "message": "Custom curriculum intent is saved. Document ingestion and curriculum extraction are not available in this release."}], "ingestion_supported": False, "status": "unavailable"}
    catalogs = load_catalogs()
    reviews = load_reviews()
    for grade in config["grades"]:
        for subject in config["subjects"]:
            curriculum = {"state": config["state"], "subject": subject, "grades": [grade], "academic_year": config["academic_year"], "standards_version": None}
            catalog, selection_gaps = package_for(curriculum, catalogs)
            review, review_gaps = review_for(curriculum, reviews)
            if catalog is None:
                # An unavailable or narrower adapter may have a separate scope
                # review. Expired, future, ambiguous or version-mismatched exact
                # packages never become an approved mapping through fallback.
                if review and {gap["code"] for gap in selection_gaps} <= {"mapping_unavailable", "grade_unreviewed"}:
                    courses.append(_scope_course(config, curriculum, grade, subject, review))
                    continue
                effective_gaps = review_gaps if selection_gaps[0]["code"] == "mapping_unavailable" and review_gaps and review_gaps[0]["code"] != "mapping_unavailable" else selection_gaps
                gaps.extend({**gap, "grade": grade, "subject": subject} for gap in effective_gaps)
                continue
            curriculum["standards_version"] = catalog["standards_version"]
            grouped = defaultdict(list)
            for standard in catalog["standards"]:
                grouped[standard["topic"]].append({"code": standard["code"], "title": standard["title"], "summary": standard["summary"], "source_id": catalog["sources"][0]["id"], "source_page": standard["source_page"], "pdf_page": standard["pdf_page"]})
            key = digest({"grade": grade, "subject": subject, "state": config["state"], "academic_year": config["academic_year"]})[:32]
            complete = catalog.get("coverage") == "complete_standards"
            partial = [{"code": "standards_mapping_review" if complete else "partial_standards_mapping", "message": catalog["mapping_note"]}, {"code": "course_sequence_requires_review", "message": "The local sequence and teaching references require human suitability review, practice tasks, accessibility and assessments. Standards coverage does not make this a state-authored curriculum." if complete else "Topic groups are a planning draft derived from mapped standards, not a complete state-prescribed grade-year course. Review sequencing, investigations, accessibility and assessments before use."}]
            title = f"{catalog['subject_name']} · {_grade_name(grade)} · {config['academic_year']}"
            course_plan = {"key": key, "title": title, "grade": grade, "academic_year": config["academic_year"], "subject": subject, "curriculum": curriculum, "description": "Partial reviewed-source curriculum plan. " + catalog["mapping_note"], "units": [{"title": topic.replace("_", " ").title(), "standard_codes": [item["code"] for item in standards], "objectives": standards} for topic, standards in sorted(grouped.items())], "source_ids": [source["id"] for source in catalog["sources"]], "sources": deepcopy(catalog["sources"]), "catalog_sha256": catalog["catalog_sha256"], "standards_version": catalog["standards_version"], "mapping_version": catalog["mapping_version"], "coverage": "partial", "alignment_mapping_available": True, "gaps": partial}
            if review:
                course_plan.update(curriculum_review=deepcopy(review), curriculum_review_sha256=review["subject_review_sha256"])
                for field in ("placement", "category", "limitations", "tutoring_scope", "frameworks"):
                    course_plan[field] = deepcopy(review[field])
            if complete:
                course_plan.update(coverage="complete_standards", description="Locally authored full-grade standards alignment and teaching references. " + catalog["mapping_note"], local_sequence=deepcopy(catalog.get("local_sequence", [])), coverage_counts=deepcopy(catalog.get("coverage_counts", {})))
            courses.append(course_plan)
    return {"adapter_version": PLANNER_VERSION, "configuration": config, "courses": courses, "gaps": gaps, "ingestion_supported": False, "status": "ready" if courses else "unavailable"}


def workflow_record(db, identity, school_id, kind, value, record_id=None):
    row = Record(id=record_id or uid(), tenant_id=identity["tenant_id"], kind=kind, parent_id=school_id, owner_id=identity["id"], data=canonical(value))
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    return row


def workflow_rows(db, tenant_id, school_id, kinds=WORKFLOW_KINDS):
    return list(db.scalars(select(Record).where(Record.tenant_id == tenant_id, Record.kind.in_(kinds), Record.parent_id == school_id).order_by(Record.created_at.desc(), Record.id.desc())))


def workflow_descriptor(db, row, school):
    if row.tenant_id != school.tenant_id or row.parent_id != school.id or scope_id(db, row) != school.id:
        raise HTTPException(409, "Workflow school scope integrity check failed")
    value = row.value
    current = value.get("curriculum_revision") == school.value.get("curriculum_revision", 1)
    if row.kind == "school_curriculum_run" and current:
        current = value.get("configuration") == school_configuration(school) and digest(value.get("plan")) == value.get("plan_sha256") == digest(plan_curriculum(school_configuration(school)))
    if row.kind == "school_curriculum_publication" and current:
        current = value.get("plan_sha256") == digest(plan_curriculum(school_configuration(school)))
    if row.kind == "knowledge_workflow_run" and current:
        profile = db.get(Record, value.get("teacher_profile_id", ""))
        current = bool(profile and profile.tenant_id == school.tenant_id and profile.kind == "teacher_profile" and scope_id(db, profile) == school.id and profile.value.get("version") == value.get("teacher_version") and profile.value.get("status") != "archived")
        if current:
            links = list(db.scalars(select(Record).where(Record.tenant_id == school.tenant_id, Record.kind == "teacher_profile_course", Record.parent_id == profile.id)))
            active = [item for item in links if item.value.get("active")]
            current = all(scope_id(db, item) == school.id for item in active) and {item.value.get("course_id") for item in active} == {item["course_id"] for item in value.get("courses", [])}
    if row.kind in {"school_curriculum_publication", "knowledge_workflow_run"} and current:
        from .standards import corpus_descriptor
        for item in value.get("courses", []):
            course = db.get(Record, item["course_id"])
            if not course or course.tenant_id != school.tenant_id or course.kind != "course" or scope_id(db, course) != school.id:
                current = False
                break
            corpus = corpus_descriptor(db, course, school.id)
            if any(corpus.get(field) != item.get("corpus", {}).get(field) for field in ("id", "snapshot_sha256")) or corpus.get("status") == "stale":
                current = False
                break
    return {"id": row.id, "kind": row.kind, **value, "created_at": row.created_at, "current": current, "currentness_scope": "teacher_version_assignments_and_corpora" if row.kind == "knowledge_workflow_run" else "school_curriculum_and_reviewed_sources"}


def record_teacher_workflow(db, identity, school_id, profile, course_results, audit_actor, *, reason="course_assignment"):
    """Record actual completed corpus work in the assignment transaction."""
    school = db.get(Record, school_id)
    ready = bool(course_results) and all(item["corpus"].get("ready_for_teaching") for item in course_results)
    status = "completed" if ready else "needs_review" if course_results else "skipped"
    value = {"school_id": school_id, "teacher_profile_id": profile.id, "teacher_name": profile.value["name"], "teacher_kind": profile.value["kind"], "teacher_version": profile.value["version"], "curriculum_revision": school.value.get("curriculum_revision", 1), "status": status, "reason": reason, "agent": {"name": "Teacher knowledge-base builder", "mode": "reviewed_source_adapter", "live_model_used": False, "network_fetch": False}, "steps": [{"code": "assignment", "label": "Resolve assigned classes", "status": "completed", "detail": f"{len(course_results)} explicitly assigned classes."}, {"code": "corpora", "label": "Build and attach class corpora", "status": "completed" if course_results else "skipped", "detail": "Content-addressed snapshots attached to this teacher; existing identical snapshots are reused." if course_results else "Base teacher profile has no class corpus. Personality and instructions are saved without teaching authority."}, {"code": "approval", "label": "Human teaching approval", "status": "pending" if course_results else "skipped", "detail": "Review current corpora and approve the teacher before automatic tutoring. This workflow grants no approval, enrollment or Core authority."}], "courses": course_results, "authority_granted": False, "human_approval_required": True, "completed_at": now()}
    row = workflow_record(db, identity, school_id, "knowledge_workflow_run", value)
    audit_actor(db, identity, "school.knowledge_workflow_completed", {"school_id": school_id, "teacher_profile_id": profile.id, "teacher_version": profile.value["version"], "run_id": row.id, "status": status, "courses": [{"course_id": item["course_id"], "corpus_id": item["corpus"].get("id"), "snapshot_sha256": item["corpus"].get("snapshot_sha256")} for item in course_results], "authority_granted": False, "live_model_used": False})
    return workflow_descriptor(db, row, school)
