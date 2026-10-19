"""Reviewed, state-extensible standards and local instructional corpus snapshots.

Catalogs are reviewed source packages shipped with the application. Refresh imports
the current reviewed package and local class materials; request handling never
fetches a URL, follows document instructions, embeds content, or invokes a model.
Adding a jurisdiction means adding a validated catalog and registering its path.
"""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re

from fastapi import HTTPException
from sqlalchemy import select

from .db import Record, canonical, now
from .permissions import bind_scope, scope_id
from .standards_schemas import CurriculumInput
from .curriculum_review import load_reviews, review_for, review_catalog_descriptor


CATALOG_PATHS = tuple(Path(__file__).with_name("standards_data") / name for name in (
    "ny_environmental_science_2026.json",
    "ny_earth_space_science_2026.json",
))
MAX_SUPPLEMENTS = 25
MAX_SUPPLEMENT_CHARS = 250000


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load_catalogs():
    """Accept only explicit on-disk packages, not administrator-selected paths/URLs."""
    result = []
    for path in CATALOG_PATHS:
        content = path.read_bytes()
        if len(content) > 1000000:
            raise ValueError("Standards catalog exceeds reviewed package limit")
        value = json.loads(content)
        required = {"state", "subject", "grades", "academic_years", "standards_version", "mapping_version", "sources", "standards", "reviewed_at", "review_valid_through"}
        if value.get("schema_version") != 1 or not required <= value.keys() or len(value["standards"]) > 500:
            raise ValueError("Invalid reviewed standards package")
        if type(value.get("mapping_revision", 0)) is not int or value.get("mapping_revision", 0) < 0:
            raise ValueError("Mapping revision must be an explicit nonnegative integer")
        codes = [item["code"] for item in value["standards"]]
        if len(codes) != len(set(codes)) or not codes:
            raise ValueError("Standards codes must be nonempty and unique")
        for source in value["sources"]:
            if not re.fullmatch(r"[a-f0-9]{64}", source["content_sha256"]):
                raise ValueError("Source digest is required")
        source_ids = {source["id"] for source in value["sources"]}
        teaching_documents = value.get("teaching_documents", [])
        if len(teaching_documents) > 30 or len({item["id"] for item in teaching_documents}) != len(teaching_documents):
            raise ValueError("Teaching document identifiers must be bounded and unique")
        for item in teaching_documents:
            if item["source_id"] not in source_ids or not set(item["standard_codes"]) <= set(codes) or not 1 <= len(item["content"]) <= 10000:
                raise ValueError("Teaching documents need reviewed source and standards mappings")
        value["catalog_sha256"] = hashlib.sha256(content).hexdigest()
        result.append(value)
    return result


def _review_rank(catalog):
    """Opaque mapping names are not versions that can safely be lexically ranked."""
    try:
        reviewed = datetime.strptime(catalog["reviewed_at"], "%Y-%m-%d").date()
        expires = datetime.strptime(catalog["review_valid_through"], "%Y-%m-%d").date()
        revision = catalog.get("mapping_revision", 0)
        if type(revision) is not int or revision < 0 or not reviewed <= date.today() <= expires:
            return None
        return reviewed.toordinal(), revision
    except (KeyError, ValueError, TypeError):
        return None


def _latest_reviewed(catalogs):
    available = [(rank, catalog) for catalog in catalogs if (rank := _review_rank(catalog)) is not None]
    if not available:
        return None
    newest = max(rank for rank, _catalog in available)
    finalists = {digest(catalog): catalog for rank, catalog in available if rank == newest}
    return next(iter(finalists.values())) if len(finalists) == 1 else None


def catalog_descriptor():
    grouped = {}
    for catalog in load_catalogs():
        grouped.setdefault((catalog["state"], catalog["subject"]), []).append(catalog)
    states = {}
    for (state_code, subject_code), catalogs in sorted(grouped.items()):
        catalogs = list({digest(catalog): catalog for catalog in catalogs}.values())
        # Default fields keep the single-package interface compatible. Exact
        # year/grade/version applicability is in versions and package_for.
        ordered = sorted(catalogs, key=lambda item: (item["reviewed_at"], item.get("mapping_revision", 0), item["standards_version"], item["mapping_version"], item["catalog_sha256"]), reverse=True)
        default = _latest_reviewed(ordered) or ordered[0]
        state = states.setdefault(state_code, {"code": state_code, "name": default["state_name"], "subjects": []})
        versions = [{**{key: catalog[key] for key in ("standards_version", "mapping_version", "academic_years", "grades", "coverage", "reviewed_at", "review_valid_through", "catalog_sha256")}, "mapping_revision": catalog.get("mapping_revision", 0), "available": _review_rank(catalog) is not None} for catalog in ordered]
        state["subjects"].append({"code": subject_code, "name": default["subject_name"], "grades": sorted({grade for catalog in catalogs for grade in catalog["grades"]}), "academic_years": sorted({year for catalog in catalogs for year in catalog["academic_years"]}, reverse=True), "versions": versions, "default_selection_requires_curriculum": True, "bundled_instructional_documents": len(default.get("teaching_documents", [])), "teaching_pack_version": default.get("teaching_pack_version"), **{key: default[key] for key in ("standards_version", "mapping_version", "coverage", "reviewed_at", "review_valid_through", "mapping_note")}})
    for reviewed_state in review_catalog_descriptor()["states"]:
        state = states.setdefault(reviewed_state["code"], {"code": reviewed_state["code"], "name": reviewed_state["name"], "subjects": []})
        existing = {item["code"]: item for item in state["subjects"]}
        for reviewed in reviewed_state["subjects"]:
            if reviewed["code"] in existing:
                subject = existing[reviewed["code"]]
                subject["curriculum_review"] = deepcopy(reviewed)
                subject["alignment_mapping_available"] = any(version["available"] for version in subject["versions"])
                for field in ("category", "placement", "frameworks", "grade_focus", "tutoring_scope", "limitations", "source_ids"):
                    subject[field] = deepcopy(reviewed[field])
            else:
                state["subjects"].append(deepcopy(reviewed))
        state["subjects"].sort(key=lambda item: item["code"])
    return {"states": list(states.values()), "refresh_mode": "reviewed_packages_and_scope", "network_fetch": False, "instructional_resources_required": False, "human_corpus_approval_required": True,
            "scope_review_is_teaching_package": False}


def configuration_material(db, course, school_id):
    """Hash the actual course configuration and all eligible resource versions."""
    resources = []
    query = select(Record).where(Record.tenant_id == course.tenant_id, Record.kind == "resource").order_by(Record.id)
    for row in db.scalars(query):
        value = row.value
        if value.get("kind") == "text" and value.get("course_id") == course.id and value.get("audience") in {"all", "teachers"} and scope_id(db, row) == school_id:
            resources.append({"record_id": row.id, "record_sha256": digest({"id": row.id, "owner_id": row.owner_id, "data": value}), "data": value, "owner_id": row.owner_id})
    return {"course_id": course.id, "school_id": school_id, "course": {key: course.value.get(key) for key in ("title", "description", "curriculum")}, "resources": resources}


def package_for(curriculum, catalogs):
    if not curriculum:
        return None, [{"code": "curriculum_missing", "message": "Configure the class state, subject, grades and school year to select standards."}]
    try:
        selected = CurriculumInput.model_validate(curriculum).model_dump()
    except ValueError:
        return None, [{"code": "curriculum_invalid", "message": "The saved curriculum metadata requires administrator review."}]
    candidates = [catalog for catalog in catalogs if catalog["state"] == selected["state"] and catalog["subject"] == selected["subject"]]
    if not candidates:
        return None, [{"code": "mapping_unavailable", "message": "No reviewed state and subject adapter is installed for this class."}]
    gaps = []
    if not any(set(selected["grades"]) <= set(catalog["grades"]) for catalog in candidates):
        gaps.append({"code": "grade_unreviewed", "message": "One or more class grades fall outside the reviewed standards band."})
    if not any(selected["academic_year"] in catalog["academic_years"] for catalog in candidates):
        gaps.append({"code": "year_unreviewed", "message": "Source applicability has not been reviewed for this school year. A new year is not a new standards version."})
    if selected["standards_version"] and not any(selected["standards_version"] == catalog["standards_version"] for catalog in candidates):
        gaps.append({"code": "version_unreviewed", "message": "The requested standards version does not match the reviewed package."})
    if gaps:
        return None, gaps
    exact = [catalog for catalog in candidates if set(selected["grades"]) <= set(catalog["grades"])
             and selected["academic_year"] in catalog["academic_years"]
             and (not selected["standards_version"] or selected["standards_version"] == catalog["standards_version"])]
    if not exact:
        return None, [{"code": "curriculum_combination_unreviewed", "message": "No single reviewed package covers this exact combination of school year, grades and standards version."}]
    available = [catalog for catalog in exact if _review_rank(catalog) is not None]
    if not available:
        expired = all(isinstance(catalog.get("review_valid_through"), str) and catalog["review_valid_through"] < date.today().isoformat() for catalog in exact)
        return None, [{"code": "source_review_expired" if expired else "source_review_unavailable", "message": "The applicable package requires a current, valid source review before use."}]
    match = _latest_reviewed(available)
    if not match:
        return None, [{"code": "mapping_ambiguous", "message": "Applicable packages have conflicting equally recent reviews. Pin a unique standards version or publish an explicit newer mapping revision."}]
    return match, []


def snapshot_material(db, course, school_id, *, external=True):
    configuration = configuration_material(db, course, school_id)
    catalogs = load_catalogs()
    catalog, gaps = package_for(configuration["course"]["curriculum"], catalogs)
    curriculum = configuration["course"]["curriculum"]
    # An exact teaching package keeps its own version authority. The separate
    # source-scope context can accompany it without verifying another version.
    review_curriculum = {**curriculum, "standards_version": None} if curriculum and catalog else curriculum
    curriculum_review, review_gaps = review_for(review_curriculum, load_reviews())
    documents, sources = [], []
    if curriculum_review and not catalog:
        sources = deepcopy(curriculum_review["sources"])
        gaps.append({"code": "alignment_mapping_required", "message": "Reviewed subject scope and official sources are available, but no exact standards alignment package is installed. A scope review is not teaching content or approval."})
    if catalog:
        sources = deepcopy(catalog["sources"])
        for standard in catalog["standards"]:
            item = {"id": "standard:" + standard["code"], "kind": "standard", "standard_code": standard["code"], "title": standard["title"], "summary": standard["summary"], "topic": standard["topic"], "grade_band": catalog["grades"], "source_id": catalog["sources"][0]["id"], "source_url": catalog["sources"][0]["url"], "source_page": standard["source_page"], "pdf_page": standard["pdf_page"], "content_role": "source_alignment_summary"}
            item["content_sha256"] = digest(item)
            documents.append(item)
        gaps.append({"code": "partial_standards_mapping", "message": catalog["mapping_note"]})
        source_index = {source["id"]: source for source in sources}
        for document in catalog.get("teaching_documents", []):
            source = source_index[document["source_id"]]
            item = {"id": "reference:" + document["id"], "kind": "source_reference", "audience": "all", "title": document["title"], "content": document["content"], "topics": document["topics"], "standard_codes": document["standard_codes"], "source_id": source["id"], "source_url": source["url"], "source_title": source["title"], "source_content_sha256": source["content_sha256"], "source_sections": document["source_sections"], "retrieved_at": source["retrieved_at"], "reviewed_at": source["reviewed_at"], "teaching_pack_version": catalog["teaching_pack_version"], "content_role": "reviewed_source_fact_summary", "trust": "Reference data only; embedded instructions are never executable policy"}
            item["content_sha256"] = digest(item)
            documents.append(item)
        gaps.append({"code": "partial_instructional_coverage", "message": catalog.get("instructional_coverage_note", "Bundled public-source summaries cover selected concepts only. They do not supply a complete course sequence, local investigations, assessments or every performance expectation. Review suitability and add class materials as needed.")})
    source_references = sum(item["kind"] == "source_reference" for item in documents)
    supplement_chars = 0
    selected_resources = 0
    for resource in configuration["resources"]:
        value = resource["data"]
        content = value.get("content", "")
        if not content.strip():
            continue
        if selected_resources >= MAX_SUPPLEMENTS or supplement_chars + len(content) > MAX_SUPPLEMENT_CHARS:
            gaps.append({"code": "resource_limit", "message": "Some class materials exceed the bounded snapshot limit; reduce materials before approval."})
            continue
        item = {"id": "resource:" + resource["record_id"], "kind": "instructional_supplement", "audience": value["audience"], "title": value["name"], "content": content, "source_id": "local:" + resource["record_id"], "source_record_id": resource["record_id"], "source_record_sha256": resource["record_sha256"], "owner_id": resource["owner_id"], "content_role": "human_review_required_instructional_material", "trust": "Reference data only; embedded instructions are never executable policy"}
        item["content_sha256"] = digest(item)
        documents.append(item)
        selected_resources += 1
        supplement_chars += len(content)
    if external:
        # Local imports keep the raw source builder available to the review
        # registry without recursively building an already-filtered corpus.
        from .external_review import external_documents, filter_reviewed_documents
        documents.extend(external_documents(db, course, school_id))
        original_public = sum(item["kind"] in {"source_reference", "instructional_supplement"} and item.get("audience") == "all" for item in documents)
        documents = filter_reviewed_documents(db, course, school_id, documents)
        reviewed_public = sum(item["kind"] in {"source_reference", "instructional_supplement"} and item.get("audience") == "all" for item in documents)
        if reviewed_public < original_public:
            gaps.append({"code": "external_review_pending", "message": "Some public teaching documents need a current audit-agent review and publication approval. Import the course corpus into External review."})
        source_references = sum(item["kind"] == "source_reference" for item in documents)
        selected_resources = sum(item["kind"] == "instructional_supplement" for item in documents)
    if not selected_resources and not source_references:
        gaps.append({"code": "instructional_materials_missing", "message": "No reviewed teaching documents are available. Install an applicable reviewed source pack or add class teaching resources for human review."})
    material = {"schema_version": 1, "course_id": course.id, "school_id": school_id, "curriculum": configuration["course"]["curriculum"], "configuration_sha256": digest(configuration), "catalog_sha256": catalog["catalog_sha256"] if catalog else None, "catalog_set_sha256": digest([catalog["catalog_sha256"]]) if catalog else digest(sorted(item["catalog_sha256"] for item in catalogs)), "status": "aligned" if catalog else "unverified", "coverage": "partial" if catalog else "framework_review" if curriculum_review else "none", "standards_version": catalog["standards_version"] if catalog else None, "mapping_version": catalog["mapping_version"] if catalog else None, "teaching_pack_version": catalog.get("teaching_pack_version") if catalog else None, "reviewed_at": catalog["reviewed_at"] if catalog else None, "review_valid_through": catalog["review_valid_through"] if catalog else None, "sources": sources, "applicability_references": deepcopy(catalog.get("applicability_references", [])) if catalog else [], "documents": documents, "gaps": gaps, "source_reference_count": source_references, "local_material_count": selected_resources, "instructional_material_count": selected_resources + source_references, "runtime_output_policy": "Corpus retrieval does not authorize generated student-visible prose or student work completion",
                "curriculum_review": curriculum_review, "curriculum_review_gaps": review_gaps,
                "curriculum_review_sha256": curriculum_review["subject_review_sha256"] if curriculum_review else None,
                "alignment_mapping_available": catalog is not None}
    return material


def validate_snapshot(row):
    value = row.value
    material = {key: item for key, item in value.items() if key not in {"snapshot_sha256", "captured_at"}}
    if row.kind != "course_corpus" or digest(material) != value.get("snapshot_sha256"):
        raise HTTPException(409, "Corpus snapshot integrity check failed")
    return value


def build_course_corpus(db, identity, course, school_id, audit_actor):
    """Append an idempotent snapshot in the caller's authorized transaction.

Callers must first authorize course creation/update or teacher planning assignment.
The helper still checks tenant and stored school scope against the target course.
"""
    if course.kind != "course" or course.tenant_id != identity["tenant_id"] or scope_id(db, course) != school_id:
        raise HTTPException(403, "Class corpus scope mismatch")
    material = snapshot_material(db, course, school_id)
    snapshot_hash = digest(material)
    record_id = "corpus:" + digest({"tenant_id": course.tenant_id, "course_id": course.id, "snapshot_sha256": snapshot_hash})[:48]
    existing = db.get(Record, record_id)
    if existing:
        if existing.tenant_id != identity["tenant_id"] or existing.parent_id != course.id or scope_id(db, existing) != school_id:
            raise HTTPException(409, "Corpus identity conflicts with class scope")
        validate_snapshot(existing)
        return corpus_descriptor(db, course, school_id, existing)
    row = Record(id=record_id, tenant_id=course.tenant_id, kind="course_corpus", parent_id=course.id, owner_id=identity["id"], data=canonical({**material, "snapshot_sha256": snapshot_hash, "captured_at": now()}))
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    audit_actor(db, identity, "corpus.snapshot_created", {"school_id": school_id, "course_id": course.id, "corpus_id": row.id, "snapshot_sha256": snapshot_hash, "configuration_sha256": material["configuration_sha256"], "catalog_set_sha256": material["catalog_set_sha256"], "status": material["status"], "coverage": material["coverage"], "documents": [{"id": item["id"], "content_sha256": item["content_sha256"]} for item in material["documents"]]})
    return corpus_descriptor(db, course, school_id, row)


def current_corpus(db, course, school_id):
    material_hash = digest(snapshot_material(db, course, school_id))
    snapshots = list(db.scalars(select(Record).where(Record.tenant_id == course.tenant_id, Record.kind == "course_corpus", Record.parent_id == course.id).order_by(Record.created_at.desc(), Record.id.desc())))
    for row in snapshots:
        value = validate_snapshot(row)
        if scope_id(db, row) != school_id:
            raise HTTPException(409, "Corpus school scope integrity check failed")
        if value["snapshot_sha256"] == material_hash:
            return row
    return snapshots[0] if snapshots else None


def corpus_descriptor(db, course, school_id, row=None):
    row = row or current_corpus(db, course, school_id)
    expected = snapshot_material(db, course, school_id)
    if row is None:
        return {"id": None, **expected, "status": "unverified", "snapshot_sha256": None, "created_at": None, "approval": {"status": "pending"}, "ready_for_teaching": False, "gaps": [*expected["gaps"], {"code": "snapshot_missing", "message": "Create a reviewed corpus snapshot before teaching retrieval."}]}
    value = validate_snapshot(row)
    stale = value["snapshot_sha256"] != digest(expected)
    approval = db.scalar(select(Record).where(Record.tenant_id == course.tenant_id, Record.kind == "corpus_approval", Record.parent_id == row.id).order_by(Record.created_at.desc(), Record.id.desc()))
    approved = bool(approval and approval.value.get("snapshot_sha256") == value["snapshot_sha256"] and approval.value.get("configuration_sha256") == value["configuration_sha256"] and scope_id(db, approval) == school_id)
    approval_status = "stale" if stale else "approved" if approved else "pending"
    gaps = deepcopy(value["gaps"])
    if stale:
        gaps.append({"code": "snapshot_stale", "message": "Class metadata, resources, or reviewed catalog changed. Refresh and approve the new snapshot."})
    result = {"id": row.id, **value, "status": "stale" if stale else value["status"], "created_at": row.created_at, "gaps": gaps, "approval": {"status": approval_status}, "ready_for_teaching": bool(approved and not stale and value["status"] == "aligned" and value["instructional_material_count"] and not any(gap["code"] == "resource_limit" for gap in gaps))}
    if approval:
        result["approval"].update({key: approval.value[key] for key in ("approved_by", "approved_at")})
        result["approval"].update(id=approval.id, approval_sha256=hashlib.sha256(approval.data.encode()).hexdigest())
    return result


def approve_corpus(db, identity, course, school_id, corpus_id, snapshot_hash, audit_actor):
    row = current_corpus(db, course, school_id)
    descriptor = corpus_descriptor(db, course, school_id, row)
    if not row or row.id != corpus_id or descriptor["snapshot_sha256"] != snapshot_hash or descriptor["status"] != "aligned":
        raise HTTPException(409, "Review the current supported corpus snapshot before approval")
    if not descriptor["instructional_material_count"] or any(gap["code"] == "resource_limit" for gap in descriptor["gaps"]):
        raise HTTPException(409, "Reviewed teaching documents within snapshot limits are required before corpus approval")
    record_id = "corpus-ok:" + digest({"tenant_id": course.tenant_id, "corpus_id": row.id, "approved_by": identity["id"]})[:48]
    if not db.get(Record, record_id):
        value = {"course_id": course.id, "school_id": school_id, "corpus_id": row.id, "snapshot_sha256": snapshot_hash, "configuration_sha256": descriptor["configuration_sha256"], "catalog_set_sha256": descriptor["catalog_set_sha256"], "approved_by": identity["id"], "approved_at": now(), "review_scope": "Exact standards alignment and attached instructional materials; partial coverage acknowledged"}
        approval = Record(id=record_id, tenant_id=course.tenant_id, kind="corpus_approval", parent_id=row.id, owner_id=identity["id"], data=canonical(value))
        db.add(approval)
        db.flush()
        bind_scope(db, approval, school_id)
        audit_actor(db, identity, "corpus.approved", value)
    return corpus_descriptor(db, course, school_id, row)


def retrieve_corpus(db, identity, course, school_id, query, top_k, audit_actor):
    descriptor = corpus_descriptor(db, course, school_id)
    if not descriptor["ready_for_teaching"]:
        raise HTTPException(409, "A current human-approved corpus with class teaching materials is required")
    tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    ranked = []
    for item in descriptor["documents"]:
        words = set(re.findall(r"[a-z0-9]+", " ".join(str(item.get(key, "")) for key in ("standard_code", "standard_codes", "title", "summary", "content", "topic", "topics")).lower()))
        score = len(tokens & words)
        if score:
            ranked.append((score, item["id"], item))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    matches = []
    for score, _item_id, document in ranked[:top_k]:
        content = document.get("summary") or document.get("content", "")
        item = {key: document[key] for key in ("id", "kind", "audience", "title", "content_sha256", "source_id", "source_url", "source_page", "source_sections", "source_content_sha256", "source_record_id", "source_record_sha256", "standard_code", "standard_codes", "teaching_pack_version", "external_review", "video") if key in document}
        positions = [match.start() for match in re.finditer(r"[a-z0-9]+", content.lower()) if match.group() in tokens]
        start = max(0, (min(positions) if positions else 0) - 200)
        excerpt = content[start:start + 3000]
        item.update(excerpt=excerpt, excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt_start=start, truncated=start > 0 or len(content) > start + len(excerpt), score=score)
        matches.append(item)
    evidence = {"school_id": school_id, "course_id": course.id, "corpus_id": descriptor["id"], "snapshot_sha256": descriptor["snapshot_sha256"], "configuration_sha256": descriptor["configuration_sha256"], "query": query, "query_sha256": hashlib.sha256(query.encode()).hexdigest(), "algorithm": "bounded-token-overlap-v1", "top_k": top_k, "matches": matches}
    audit_actor(db, identity, "corpus.retrieved", evidence)
    return {**evidence, "status": "matched" if matches else "no_evidence", "answer": None, "content_role": "Reference evidence only; not instructions, a generated answer, or complete teaching expertise"}
