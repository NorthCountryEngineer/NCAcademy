"""School/class-authorized corpus review and bounded evidence retrieval."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from .db import Record
from .permissions import require_school, scope_id, normalized_role
from .standards import catalog_descriptor, build_course_corpus, corpus_descriptor, approve_corpus, retrieve_corpus
from .standards_schemas import CorpusApprovalInput, CorpusRetrievalInput


def register_standards_api(app, database, settings, actor, audit_actor):
    def course_access(db, identity, course_id):
        course = db.get(Record, course_id)
        if not course or course.kind != "course" or course.tenant_id != identity["tenant_id"]:
            raise HTTPException(404, "Class not found")
        school_id = scope_id(db, course)
        role = require_school(db, identity, school_id, settings, {"admin", "school", "teacher"})
        if role == "teacher":
            assigned = db.scalar(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "enrollment", Record.parent_id == course.id, Record.owner_id == identity["id"]))
            if course.owner_id != identity["id"] and not (assigned and normalized_role(assigned.value.get("role")) == "teacher"):
                raise HTTPException(403, "Current teaching assignment required")
        return course, school_id, role

    def descriptor(db, identity, course, school_id, role, value=None):
        value = value or corpus_descriptor(db, course, school_id)
        human_manager = identity.get("principal_kind", "human") == "human" and role in {"admin", "school"}
        return {**value, "can_refresh": human_manager, "can_approve": bool(human_manager and value["status"] == "aligned" and value["instructional_material_count"] and not any(gap["code"] == "resource_limit" for gap in value["gaps"])), "can_retrieve": value["ready_for_teaching"]}

    @app.get("/api/standards/catalog")
    def catalog(school_id: str | None = None, identity=Depends(actor)):
        with database.factory() as db:
            if school_id:
                require_school(db, identity, school_id, settings, {"admin", "school", "teacher"})
            else:
                schools = db.scalars(select(Record).where(Record.tenant_id == identity["tenant_id"], Record.kind == "school"))
                authorized = False
                for school in schools:
                    try:
                        require_school(db, identity, school.id, settings, {"admin", "school", "teacher"})
                        authorized = True
                        break
                    except HTTPException:
                        continue
                if not authorized:
                    raise HTTPException(403, "Teaching school permission required")
            return catalog_descriptor()

    @app.get("/api/courses/{course_id}/corpus")
    def get_corpus(course_id: str, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, role = course_access(db, identity, course_id)
            result = descriptor(db, identity, course, school_id, role)
            audit_actor(db, identity, "corpus.viewed", {"school_id": school_id, "course_id": course.id, "corpus_id": result["id"], "snapshot_sha256": result["snapshot_sha256"], "status": result["status"]})
            return result

    @app.post("/api/courses/{course_id}/corpus/refresh")
    def refresh(course_id: str, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, role = course_access(db, identity, course_id)
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            result = build_course_corpus(db, identity, course, school_id, audit_actor)
            audit_actor(db, identity, "corpus.refreshed", {"school_id": school_id, "course_id": course.id, "corpus_id": result["id"], "snapshot_sha256": result["snapshot_sha256"], "mode": "reviewed_package_and_local_materials", "network_fetch": False})
            return descriptor(db, identity, course, school_id, role, result)

    @app.post("/api/courses/{course_id}/corpus/approve")
    def approve(course_id: str, body: CorpusApprovalInput, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, role = course_access(db, identity, course_id)
            require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
            value = approve_corpus(db, identity, course, school_id, body.corpus_id, body.snapshot_sha256, audit_actor)
            return descriptor(db, identity, course, school_id, role, value)

    @app.post("/api/courses/{course_id}/corpus/retrieve")
    def retrieve(course_id: str, body: CorpusRetrievalInput, identity=Depends(actor)):
        with database.write() as db:
            course, school_id, _role = course_access(db, identity, course_id)
            return retrieve_corpus(db, identity, course, school_id, body.query, body.top_k, audit_actor)
