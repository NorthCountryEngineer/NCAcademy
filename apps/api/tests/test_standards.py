"""Reviewed standards, exact corpus approvals, evidence and adversarial scopes."""
import json
from copy import deepcopy

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text

from academy.config import Settings
from academy.db import Audit, Base, Database, Record, audit, canonical
from academy.permissions import bind_scope, membership_id
from academy.standards import (approve_corpus, build_course_corpus, corpus_descriptor,
                               current_corpus, load_catalogs, retrieve_corpus,
                               package_for, catalog_descriptor, digest)
from academy.standards_api import register_standards_api
from academy.standards_schemas import CurriculumInput
from conftest import login


CURRICULUM = {"state": "US-NY", "subject": "environmental_science", "grades": [10], "academic_year": "2026-2027", "standards_version": None}


@pytest.fixture
def corpus_env(tmp_path):
    settings = Settings("sqlite:///" + str(tmp_path / "corpus.db"), "local", "http://localhost:8090", "synthetic-corpus-bootstrap-secret", False, False)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    identity = {"id": "admin", "tenant_id": settings.tenant_id, "role": "admin", "principal_kind": "human"}
    def audit_actor(db, actor, event_type, payload):
        audit(db, actor["tenant_id"], actor["id"], event_type, {**payload, "principal_kind": actor.get("principal_kind", "human")})
    with database.write() as db:
        db.add(Record(id="school", tenant_id=settings.tenant_id, kind="school", data=canonical({"name": "Synthetic school"})))
        db.add(Record(id="other-school", tenant_id=settings.tenant_id, kind="school", data=canonical({"name": "Other school"})))
        course = Record(id="class", tenant_id=settings.tenant_id, kind="course", owner_id="teacher", data=canonical({"title": "Environmental Science", "description": "Synthetic", "curriculum": CURRICULUM}))
        db.add(course)
        db.flush()
        bind_scope(db, course, "school")
        for principal, role, sid in (("teacher", "teacher", "school"), ("student", "student", "school"), ("manager", "school", "school"), ("foreign-teacher", "teacher", "other-school")):
            db.add(Record(id=membership_id(sid, principal), tenant_id=settings.tenant_id, kind="school_membership", parent_id=sid, owner_id=principal, data=canonical({"role": role, "active": True})))
    yield database, identity, audit_actor, settings
    database.engine.dispose()


def add_material(db, tenant, resource_id="lesson", school_id="school", course_id="class", content="Carbon cycling connects the atmosphere and biosphere. Compare measured evidence."):
    row = Record(id=resource_id, tenant_id=tenant, kind="resource", parent_id=school_id, owner_id="teacher", data=canonical({"kind": "text", "name": "Reviewed lesson", "course_id": course_id, "audience": "teachers", "content": content}))
    db.add(row)
    db.flush()
    bind_scope(db, row, school_id)
    return row


def test_reviewed_catalog_has_real_source_hashes_and_hs_band():
    catalog = load_catalogs()[0]
    assert catalog["grades"] == [9, 10, 11, 12]
    assert catalog["academic_years"] == ["2026-2027"]
    assert catalog["standards_version"] == "nyssls-2016"
    assert len(catalog["standards"]) == 13
    assert catalog["sources"][0]["content_sha256"] == "0f3d02ec4a52051ed6e92c3c7ce98991615f29d84e8fdc4b349c7fd8a9f74e55"
    assert "not a NYSED Grade 10" in catalog["mapping_note"]
    assert len(catalog["teaching_documents"]) == 4 and len(catalog["sources"]) == 6
    assert {source["publisher"] for source in catalog["sources"]} >= {"NASA Earth Observatory", "NOAA Education", "U.S. Environmental Protection Agency"}


def test_curriculum_validation_does_not_accept_broken_school_year_or_grades():
    for changed in ({"academic_year": "2026-2028"}, {"grades": [10, 10]}, {"grades": [13]}, {"grades": [True]}, {"grades": [False]}, {"grades": [10.0]}, {"grades": ["10"]}, {"state": "NY"}, {"subject": "arbitrary url"}):
        with pytest.raises(ValidationError):
            CurriculumInput.model_validate({**CURRICULUM, **changed})


def synthetic_package(year="2026-2027", version="synthetic-v1", reviewed="2026-10-17", grades=None, revision=0):
    """Selection fixtures only; never assertions about future state standards."""
    value = deepcopy(load_catalogs()[0])
    value.update(academic_years=[year], standards_version=version,
                 mapping_version="synthetic-map-" + version, mapping_revision=revision,
                 reviewed_at=reviewed, review_valid_through="2029-06-30",
                 grades=grades or [9, 10, 11, 12],
                 mapping_note="Synthetic package for catalog-selection tests; no state applicability claim.")
    for source in value["sources"]:
        source.update(url="https://education.example.test/" + source["id"], publisher="Synthetic fixture", title="Synthetic source", content_sha256="c" * 64)
    value["catalog_sha256"] = digest({key: item for key, item in value.items() if key != "catalog_sha256"})
    return value


def test_package_selection_matches_exact_year_and_optional_version_even_when_old_package_is_first():
    old = synthetic_package()
    new = synthetic_package("2027-2028", "synthetic-v2", "2026-10-18")
    selected, gaps = package_for({**CURRICULUM, "academic_year": "2027-2028"}, [old, new])
    assert selected is new and gaps == []
    assert package_for(CURRICULUM, [old, new])[0] is old
    assert package_for({**CURRICULUM, "academic_year": "2027-2028", "standards_version": "synthetic-v2"}, [old, new])[0] is new
    unsupported, gaps = package_for({**CURRICULUM, "academic_year": "2027-2028", "standards_version": "synthetic-v1"}, [old, new])
    assert unsupported is None and gaps[0]["code"] == "curriculum_combination_unreviewed"


def test_unpinned_selection_uses_latest_valid_review_but_pinned_version_remains_exact():
    old = synthetic_package()
    new = synthetic_package(version="synthetic-v2", reviewed="2026-10-18")
    for catalogs in ([old, new], [new, old]):
        assert package_for(CURRICULUM, catalogs)[0] is new
        assert package_for({**CURRICULUM, "standards_version": "synthetic-v1"}, catalogs)[0] is old


def test_equally_recent_conflicting_packages_fail_closed_until_version_or_revision_resolves_tie():
    old = synthetic_package(reviewed="2026-10-18")
    new = synthetic_package(version="synthetic-v2", reviewed="2026-10-18")
    for catalogs in ([old, new], [new, old]):
        selected, gaps = package_for(CURRICULUM, catalogs)
        assert selected is None and gaps[0]["code"] == "mapping_ambiguous"
        assert package_for({**CURRICULUM, "standards_version": "synthetic-v1"}, catalogs)[0] is old
    revised = synthetic_package(version="synthetic-v2", reviewed="2026-10-18", revision=1)
    assert package_for(CURRICULUM, [old, revised])[0] is revised


def test_catalog_grade_and_year_unions_do_not_verify_unsupported_cross_product():
    old = synthetic_package(grades=[9])
    new = synthetic_package("2027-2028", "synthetic-v2", "2026-10-18", grades=[10])
    selected, gaps = package_for({**CURRICULUM, "grades": [10]}, [old, new])
    assert selected is None and gaps[0]["code"] == "curriculum_combination_unreviewed"
    assert package_for({**CURRICULUM, "grades": [9, 10]}, [old, new])[0] is None


def test_catalog_descriptor_groups_subjects_and_preserves_exact_package_applicability(monkeypatch):
    from academy import standards
    old = synthetic_package(grades=[9])
    new = synthetic_package("2027-2028", "synthetic-v2", "2026-10-18", grades=[10])
    monkeypatch.setattr(standards, "load_catalogs", lambda: [old, new, deepcopy(old)])
    descriptor = catalog_descriptor()
    assert len(descriptor["states"]) == 1 and len(descriptor["states"][0]["subjects"]) == 1
    subject = descriptor["states"][0]["subjects"][0]
    assert subject["grades"] == [9, 10]
    assert subject["academic_years"] == ["2027-2028", "2026-2027"]
    assert subject["standards_version"] == "synthetic-v2"
    assert len(subject["versions"]) == 2
    assert all(version["available"] for version in subject["versions"])
    assert {tuple(version["grades"]): tuple(version["academic_years"]) for version in subject["versions"]} == {(9,): ("2026-2027",), (10,): ("2027-2028",)}


def test_matching_expired_or_future_review_does_not_override_valid_package():
    valid = synthetic_package()
    expired = synthetic_package(version="synthetic-expired", reviewed="2026-10-18")
    expired["review_valid_through"] = "2026-10-16"
    future = synthetic_package(version="synthetic-future", reviewed="2028-01-01")
    assert package_for(CURRICULUM, [expired, future, valid])[0] is valid
    assert package_for({**CURRICULUM, "standards_version": "synthetic-future"}, [valid, future])[0] is None


def test_snapshot_pins_one_matching_package_and_is_stable_when_registry_order_or_unrelated_state_changes(corpus_env, monkeypatch):
    from academy import standards
    old = synthetic_package()
    new = synthetic_package(version="synthetic-v2", reviewed="2026-10-18")
    unrelated = synthetic_package("2027-2028", "synthetic-other-state", "2026-10-18")
    unrelated.update(state="US-VT", state_name="Synthetic other state")
    unrelated["catalog_sha256"] = digest({key: item for key, item in unrelated.items() if key != "catalog_sha256"})
    catalogs = [old, new]
    monkeypatch.setattr(standards, "load_catalogs", lambda: catalogs)
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        first = build_course_corpus(db, identity, course, "school", audit_actor)
        assert first["standards_version"] == "synthetic-v2" and first["catalog_sha256"] == new["catalog_sha256"]
        assert first["catalog_set_sha256"] == digest([new["catalog_sha256"]])
        catalogs[:] = [unrelated, new, old]
        second = build_course_corpus(db, identity, course, "school", audit_actor)
        assert second["id"] == first["id"] and second["snapshot_sha256"] == first["snapshot_sha256"]


def test_snapshot_automatically_includes_source_teaching_documents_and_needs_human_approval(corpus_env):
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        first = build_course_corpus(db, identity, course, "school", audit_actor)
        second = build_course_corpus(db, identity, course, "school", audit_actor)
        assert first["id"] == second["id"]
        assert first["status"] == "aligned" and first["coverage"] == "partial"
        assert first["ready_for_teaching"] is False
        assert len(first["documents"]) == 17
        assert first["source_reference_count"] == first["instructional_material_count"] == 4
        assert first["local_material_count"] == 0
        assert {gap["code"] for gap in first["gaps"]} == {"partial_standards_mapping", "partial_instructional_coverage"}
        source_docs = [doc for doc in first["documents"] if doc["kind"] == "source_reference"]
        assert all(doc["standard_codes"] and doc["source_content_sha256"] and doc["source_url"].startswith("https://") for doc in source_docs)
        assert all(doc["audience"] == "all" for doc in source_docs)
        assert any("infiltration" in doc["content"].lower() for doc in source_docs)
        approved = approve_corpus(db, identity, course, "school", first["id"], first["snapshot_sha256"], audit_actor)
        assert approved["ready_for_teaching"] is True
        for query, source_id in (("photosynthesis", "nasa-carbon-cycle"), ("infiltration", "noaa-water-cycle"), ("shorelines", "noaa-reef-ecosystems"), ("manufacturing", "epa-recycling-resources")):
            evidence = retrieve_corpus(db, identity, course, "school", query, 8, audit_actor)
            matched = [match for match in evidence["matches"] if match["source_id"] == source_id]
            assert matched and matched[0]["kind"] == "source_reference"
            assert matched[0]["source_content_sha256"] and matched[0]["standard_codes"]
            assert query in matched[0]["excerpt"].lower()
        assert len(list(db.scalars(select(Audit).where(Audit.event_type == "corpus.snapshot_created")))) == 1


def test_standards_only_package_still_cannot_be_approved_as_subject_knowledge(corpus_env, monkeypatch):
    from academy import standards
    catalog = deepcopy(load_catalogs()[0])
    catalog["teaching_documents"] = []
    monkeypatch.setattr(standards, "load_catalogs", lambda: [catalog])
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        first = build_course_corpus(db, identity, course, "school", audit_actor)
        assert first["instructional_material_count"] == 0 and first["ready_for_teaching"] is False
        with pytest.raises(HTTPException) as denied:
            approve_corpus(db, identity, course, "school", first["id"], first["snapshot_sha256"], audit_actor)
        assert denied.value.status_code == 409


@pytest.mark.parametrize("change,code", [({"state": "US-VT"}, "mapping_unavailable"), ({"subject": "mathematics"}, "mapping_unavailable"), ({"grades": [8]}, "grade_unreviewed"), ({"academic_year": "2027-2028"}, "year_unreviewed"), ({"standards_version": "fictional-2026"}, "version_unreviewed")])
def test_unsupported_mappings_fail_closed_with_no_invented_standards(corpus_env, change, code):
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        course.set({**course.value, "curriculum": {**CURRICULUM, **change}})
        result = build_course_corpus(db, identity, course, "school", audit_actor)
        assert result["status"] == "unverified" and result["ready_for_teaching"] is False
        assert result["documents"] == [] and result["sources"] == []
        assert code in {gap["code"] for gap in result["gaps"]}


def test_review_expiry_fails_closed_even_for_pinned_school_year(corpus_env, monkeypatch):
    from academy import standards
    class ExpiredDate:
        @staticmethod
        def today():
            from datetime import date
            return date(2027, 7, 1)
    monkeypatch.setattr(standards, "date", ExpiredDate)
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        result = build_course_corpus(db, identity, db.get(Record, "class"), "school", audit_actor)
        assert result["status"] == "unverified"
        assert "source_review_expired" in {gap["code"] for gap in result["gaps"]}


def test_human_review_binds_local_material_hashes_and_retrieval_audits_actual_evidence(corpus_env):
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        add_material(db, identity["tenant_id"])
        add_material(db, identity["tenant_id"], "foreign", "other-school", content="Foreign private secret carbon text")
        first = build_course_corpus(db, identity, course, "school", audit_actor)
        assert first["instructional_material_count"] == 5 and first["local_material_count"] == 1 and "Foreign private" not in canonical(first)
        assert next(doc for doc in first["documents"] if doc["id"] == "resource:lesson")["audience"] == "teachers"
        with pytest.raises(HTTPException):
            retrieve_corpus(db, identity, course, "school", "carbon", 5, audit_actor)
        approved = approve_corpus(db, identity, course, "school", first["id"], first["snapshot_sha256"], audit_actor)
        assert approved["ready_for_teaching"] is True
        result = retrieve_corpus(db, identity, course, "school", "carbon", 8, audit_actor)
        assert result["answer"] is None and result["status"] == "matched"
        assert "resource:lesson" in {match["id"] for match in result["matches"]}
        assert next(match for match in result["matches"] if match["id"] == "resource:lesson")["audience"] == "teachers"
        event = db.scalar(select(Audit).where(Audit.event_type == "corpus.retrieved"))
        evidence = json.loads(event.payload)
        assert evidence["matches"] == result["matches"]
        assert evidence["snapshot_sha256"] == approved["snapshot_sha256"]
        empty = retrieve_corpus(db, identity, course, "school", "zzyzxunexpected", 5, audit_actor)
        assert empty["status"] == "no_evidence" and empty["matches"] == []


def test_long_material_returns_a_bounded_matching_excerpt_with_precise_hash(corpus_env):
    import hashlib
    database, identity, audit_actor, _ = corpus_env
    content = "preface " * 700 + "distinctivebiosphere evidence " + "tail " * 1000
    with database.write() as db:
        course = db.get(Record, "class")
        add_material(db, identity["tenant_id"], content=content)
        first = build_course_corpus(db, identity, course, "school", audit_actor)
        approve_corpus(db, identity, course, "school", first["id"], first["snapshot_sha256"], audit_actor)
        result = retrieve_corpus(db, identity, course, "school", "distinctivebiosphere", 1, audit_actor)
        match = result["matches"][0]
        assert "distinctivebiosphere" in match["excerpt"] and len(match["excerpt"]) <= 3000
        assert match["excerpt_start"] > 0 and match["truncated"]
        assert content[match["excerpt_start"]:match["excerpt_start"] + len(match["excerpt"])] == match["excerpt"]
        assert hashlib.sha256(match["excerpt"].encode()).hexdigest() == match["excerpt_sha256"]


@pytest.mark.parametrize("target", ["course", "resource"])
def test_metadata_or_resource_change_invalidates_approval_without_rewriting_old_snapshot(corpus_env, target):
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        course = db.get(Record, "class")
        material = add_material(db, identity["tenant_id"])
        first = build_course_corpus(db, identity, course, "school", audit_actor)
        approve_corpus(db, identity, course, "school", first["id"], first["snapshot_sha256"], audit_actor)
        before = db.get(Record, first["id"]).data
        if target == "course":
            course.set({**course.value, "description": "Changed class purpose"})
        else:
            material.set({**material.value, "content": "Changed carbon lesson"})
        stale = corpus_descriptor(db, course, "school")
        assert stale["status"] == "stale" and stale["ready_for_teaching"] is False
        with pytest.raises(HTTPException):
            retrieve_corpus(db, identity, course, "school", "carbon", 5, audit_actor)
        second = build_course_corpus(db, identity, course, "school", audit_actor)
        assert second["id"] != first["id"] and second["approval"]["status"] == "pending"
        assert db.get(Record, first["id"]).data == before


def test_snapshot_mutation_and_integrity_corruption_fail_closed(corpus_env):
    database, identity, audit_actor, _ = corpus_env
    with database.write() as db:
        first = build_course_corpus(db, identity, db.get(Record, "class"), "school", audit_actor)
    with pytest.raises(ValueError, match="immutable"):
        with database.write() as db:
            db.get(Record, first["id"]).set({"forged": True})
    # This isolated test database intentionally has no production triggers;
    # digest validation still refuses a privileged raw-SQL rewrite.
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE records SET data = :data WHERE id = :id"), {"id": first["id"], "data": canonical({"forged": True})})
    with database.factory() as db:
        with pytest.raises(HTTPException) as denied:
            current_corpus(db, db.get(Record, "class"), "school")
        assert denied.value.status_code == 409


def test_corpus_api_permissions_refresh_review_and_scope_revocation(corpus_env):
    database, admin, audit_actor, settings = corpus_env
    app = FastAPI()
    def actor(request: Request):
        principal = request.headers.get("X-Synthetic-Principal", "admin")
        return {**admin, "id": principal, "role": "admin" if principal == "admin" else "teacher" if principal in {"teacher", "foreign-teacher"} else principal}
    register_standards_api(app, database, settings, actor, audit_actor)
    with database.write() as db:
        add_material(db, admin["tenant_id"])
    with TestClient(app) as client:
        assert client.get("/api/standards/catalog").json()["network_fetch"] is False
        assert client.get("/api/standards/catalog", headers={"X-Synthetic-Principal": "student"}).status_code == 403
        assert client.get("/api/courses/class/corpus", headers={"X-Synthetic-Principal": "foreign-teacher"}).status_code == 403
        assert client.post("/api/courses/class/corpus/refresh", json={}, headers={"X-Synthetic-Principal": "teacher"}).status_code == 403
        snapshot = client.post("/api/courses/class/corpus/refresh", json={}).json()
        body = {"corpus_id": snapshot["id"], "snapshot_sha256": snapshot["snapshot_sha256"]}
        assert client.post("/api/courses/class/corpus/approve", json=body, headers={"X-Synthetic-Principal": "teacher"}).status_code == 403
        approved = client.post("/api/courses/class/corpus/approve", json=body, headers={"X-Synthetic-Principal": "manager"})
        assert approved.status_code == 200 and approved.json()["ready_for_teaching"] is True
        assert client.post("/api/courses/class/corpus/retrieve", json={"query": "carbon"}, headers={"X-Synthetic-Principal": "teacher"}).status_code == 200
        assert client.post("/api/courses/class/corpus/retrieve", json={"query": "carbon"}, headers={"X-Synthetic-Principal": "student"}).status_code == 403
        with database.write() as db:
            member = db.get(Record, membership_id("school", "teacher"))
            member.set({**member.value, "active": False})
        assert client.post("/api/courses/class/corpus/retrieve", json={"query": "carbon"}, headers={"X-Synthetic-Principal": "teacher"}).status_code == 403


def test_approved_agent_can_retrieve_own_class_but_cannot_approve_or_cross_school(corpus_env):
    database, admin, audit_actor, settings = corpus_env
    agent = {"id": "agent:account", "tenant_id": settings.tenant_id, "role": "teacher", "principal_kind": "agent", "placement_id": "placement", "school_id": "school", "account_id": "account", "binding_id": "binding", "consumer_service_id": "consumer", "environment": "test", "task_id": "task", "permissions": ["academy-use", "academy-teach"]}
    with database.write() as db:
        school = db.get(Record, "school")
        school.set({**school.value, "core_resource_id": "registered-school"})
        db.add(Record(id="placement", tenant_id=settings.tenant_id, kind="agent_placement", parent_id="school", owner_id=agent["id"], data=canonical({"active": True, "school_id": "school", "core_agent_id": "account", "core_binding_id": "binding", "core_consumer_service_id": "consumer", "environment": "test", "task_id": "task", "core_resource_type": "academy-school", "core_resource_id": "registered-school"})))
        db.add(Record(id="agent-enrollment", tenant_id=settings.tenant_id, kind="enrollment", parent_id="class", owner_id=agent["id"], data=canonical({"role": "teacher"})))
        add_material(db, admin["tenant_id"])
        course = db.get(Record, "class")
        snapshot = build_course_corpus(db, admin, course, "school", audit_actor)
        approve_corpus(db, admin, course, "school", snapshot["id"], snapshot["snapshot_sha256"], audit_actor)
    app = FastAPI()
    register_standards_api(app, database, settings, lambda: agent, audit_actor)
    with TestClient(app) as client:
        assert client.post("/api/courses/class/corpus/retrieve", json={"query": "carbon"}).status_code == 200
        assert client.post("/api/courses/class/corpus/refresh", json={}).status_code == 403
        assert client.post("/api/courses/class/corpus/approve", json={"corpus_id": snapshot["id"], "snapshot_sha256": snapshot["snapshot_sha256"]}).status_code == 403
        agent["school_id"] = "other-school"
        assert client.get("/api/courses/class/corpus").status_code == 403


def test_course_api_auto_materializes_a_corpus_and_preserves_legacy_updates(client, app):
    admin = login(client, "demo-admin")
    created = client.post("/api/courses", json={"title": "Grade 10 environmental science", "curriculum": CURRICULUM}, headers=admin)
    assert created.status_code == 201, created.text
    course_id = created.json()["id"]
    corpus = client.get(f"/api/courses/{course_id}/corpus")
    assert corpus.status_code == 200, corpus.text
    first = corpus.json()
    assert first["status"] == "aligned" and first["id"]
    # Old clients omitting the new field preserve the explicit curriculum.
    updated = client.put(f"/api/courses/{course_id}", json={"title": "Renamed", "description": "New description"}, headers=admin)
    assert updated.status_code == 200, updated.text
    assert updated.json()["curriculum"] == CURRICULUM
    assert client.get(f"/api/courses/{course_id}/corpus").json()["id"] != first["id"]
    cleared = client.put(f"/api/courses/{course_id}", json={"title": "Renamed", "curriculum": None}, headers=admin)
    assert cleared.status_code == 200
    assert client.get(f"/api/courses/{course_id}/corpus").json()["status"] == "unverified"
