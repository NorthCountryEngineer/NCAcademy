"""Human school onboarding: review a plan, explicitly create classes, inspect runs."""
from fastapi import Depends, HTTPException
from .db import Record, canonical, now
from .permissions import bind_scope, global_admin, require_school, scope_id
from .school_curriculum import plan_curriculum, school_configuration, workflow_descriptor, workflow_record, workflow_rows
from .school_curriculum_schemas import CurriculumPublishInput, CurriculumRunInput
from .standards import build_course_corpus, catalog_descriptor, digest
from .chat_groups import ensure_class_group


def register_school_curriculum_api(app, database, settings, actor, audit_actor):
    def school_access(db, identity, school_id):
        require_school(db, identity, school_id, settings, {"admin", "school"}, human=True)
        school = db.get(Record, school_id)
        if not school or school.tenant_id != identity["tenant_id"] or school.kind != "school":
            raise HTTPException(404, "School not found")
        return school

    def version(school, expected):
        if school.value.get("version", 1) != expected:
            raise HTTPException(409, "School changed; reload before running onboarding")

    def command_record(db, identity, school, kind, body):
        record_id = "school-run:" + digest({"tenant_id": identity["tenant_id"], "school_id": school.id, "kind": kind, "idempotency_key": body.idempotency_key})[:48]
        row = db.get(Record, record_id)
        request_hash = digest(body.model_dump())
        if row and (row.tenant_id != identity["tenant_id"] or row.kind != kind or row.parent_id != school.id or scope_id(db, row) != school.id or row.value.get("request_sha256") != request_hash):
            raise HTTPException(409, "Onboarding key already belongs to another request")
        return record_id, request_hash, row

    @app.get("/api/school-curriculum/catalog")
    def setup_catalog(school_id: str | None = None, identity=Depends(actor)):
        with database.factory() as db:
            if school_id:
                school_access(db, identity, school_id)
            elif not global_admin(identity):
                raise HTTPException(403, "Human administrator permission required")
            return {**catalog_descriptor(), "modes": ["state", "custom", "none"], "custom_ingestion_supported": False, "planner": {"mode": "reviewed_source_planner", "live_model_used": False, "network_fetch": False}, "course_creation_requires_confirmation": True}

    @app.get("/api/schools/{school_id}/curriculum")
    def curriculum(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            school = school_access(db, identity, school_id)
            rows = workflow_rows(db, identity["tenant_id"], school_id, ("school_curriculum_run", "school_curriculum_publication"))
            return {"school_id": school_id, "school_version": school.value.get("version", 1), "curriculum_revision": school.value.get("curriculum_revision", 1), "configuration": school_configuration(school), "runs": [workflow_descriptor(db, row, school) for row in rows if row.kind == "school_curriculum_run"], "publications": [workflow_descriptor(db, row, school) for row in rows if row.kind == "school_curriculum_publication"], "capabilities": {"run_planner": True, "create_classes": True, "custom_ingestion": False}}

    @app.post("/api/schools/{school_id}/curriculum/runs", status_code=201)
    def run(school_id: str, body: CurriculumRunInput, identity=Depends(actor)):
        with database.write() as db:
            school = school_access(db, identity, school_id)
            record_id, request_hash, existing = command_record(db, identity, school, "school_curriculum_run", body)
            if existing:
                return workflow_descriptor(db, existing, school)
            version(school, body.expected_school_version)
            configuration = school_configuration(school)
            plan = plan_curriculum(configuration)
            mapped_count = sum(item.get("alignment_mapping_available", False) for item in plan["courses"])
            scope_count = len(plan["courses"]) - mapped_count
            value = {"school_id": school_id, "school_version": school.value.get("version", 1), "curriculum_revision": school.value.get("curriculum_revision", 1), "configuration": configuration, "configuration_sha256": digest(configuration), "status": plan["status"], "agent": {"name": "School curriculum planner", "mode": "reviewed_source_planner", "live_model_used": False, "network_fetch": False}, "steps": [{"code": "configuration", "label": "Read school curriculum intent", "status": "completed", "detail": f"Mode: {configuration['mode']}."}, {"code": "standards", "label": "Select reviewed curriculum sources", "status": "completed" if plan["courses"] else "skipped" if configuration["mode"] == "none" else "blocked", "detail": f"{mapped_count} combinations have partial alignment packages; {scope_count} have source-scope planning reviews only. Unsupported combinations are listed as gaps."}, {"code": "plan", "label": "Assemble grade-year planning draft", "status": "completed" if plan["courses"] else "skipped", "detail": "Cited planning topics are distinct from mapped standard objectives. A human chooses classes to create; source-scope plans still need exact alignment and approved teaching materials. This run creates no classes or approvals."}], "plan": plan, "plan_sha256": digest(plan), "request_sha256": request_hash, "completed_at": now()}
            row = workflow_record(db, identity, school_id, "school_curriculum_run", value, record_id)
            audit_actor(db, identity, "school.curriculum_planned", {"school_id": school_id, "run_id": row.id, "curriculum_revision": value["curriculum_revision"], "configuration_sha256": value["configuration_sha256"], "plan_sha256": value["plan_sha256"], "status": value["status"], "planned_course_keys": [item["key"] for item in plan["courses"]], "live_model_used": False})
            return workflow_descriptor(db, row, school)

    @app.post("/api/schools/{school_id}/curriculum/publish", status_code=201)
    def publish(school_id: str, body: CurriculumPublishInput, identity=Depends(actor)):
        with database.write() as db:
            school = school_access(db, identity, school_id)
            record_id, request_hash, existing = command_record(db, identity, school, "school_curriculum_publication", body)
            if existing:
                return workflow_descriptor(db, existing, school)
            version(school, body.expected_school_version)
            run_row = db.get(Record, body.run_id)
            if not run_row or run_row.kind != "school_curriculum_run" or run_row.tenant_id != identity["tenant_id"] or run_row.parent_id != school_id or scope_id(db, run_row) != school_id:
                raise HTTPException(404, "School curriculum run not found")
            run = workflow_descriptor(db, run_row, school)
            if not run["current"] or run["status"] != "ready" or body.plan_sha256 != run["plan_sha256"] or digest(run["plan"]) != run["plan_sha256"]:
                raise HTTPException(409, "Review a current exact curriculum plan before creating classes")
            planned = {item["key"]: item for item in run["plan"]["courses"]}
            if not set(body.course_keys) <= set(planned):
                raise HTTPException(422, "Select only courses from this exact plan")
            courses = []
            for key in body.course_keys:
                item = planned[key]
                course_id = "school-class:" + digest({"tenant_id": identity["tenant_id"], "school_id": school_id, "curriculum_revision": run["curriculum_revision"], "key": key})[:48]
                lineage = {"school_id": school_id, "curriculum_revision": run["curriculum_revision"], "configuration_sha256": run["configuration_sha256"], "course_key": key, "grade": item["grade"], "academic_year": item["academic_year"], "subject": item["subject"], "catalog_sha256": item["catalog_sha256"], "coverage": item["coverage"], "curriculum_review_sha256": item.get("curriculum_review_sha256")}
                data = {"title": item["title"], "description": item["description"], "curriculum": item["curriculum"], "school_curriculum": lineage}
                course = db.get(Record, course_id)
                created = course is None
                if course:
                    if course.tenant_id != identity["tenant_id"] or course.kind != "course" or scope_id(db, course) != school_id or any(course.value.get(field) != data[field] for field in data):
                        raise HTTPException(409, "An existing planned class changed; review it before creating another copy")
                else:
                    course = Record(id=course_id, tenant_id=identity["tenant_id"], kind="course", owner_id=identity["id"], data=canonical(data))
                    db.add(course)
                    db.flush()
                    bind_scope(db, course, school_id)
                    # Same class-creation semantics as the existing human API.
                    enrollment = Record(id="school-enroll:" + digest({"course_id": course_id, "teacher_id": identity["id"]})[:48], tenant_id=identity["tenant_id"], kind="enrollment", parent_id=course_id, owner_id=identity["id"], data=canonical({"role": "teacher"}))
                    db.add(enrollment)
                    db.flush()
                    bind_scope(db, enrollment, school_id)
                    audit_actor(db, identity, "course.created", {"school_id": school_id, "course_id": course_id, "teacher_id": identity["id"], "curriculum_run_id": run_row.id, **data})
                corpus = build_course_corpus(db, identity, course, school_id, audit_actor)
                ensure_class_group(db, identity, course, school_id, audit_actor)
                courses.append({"key": key, "course_id": course_id, "title": item["title"], "grade": item["grade"], "academic_year": item["academic_year"], "subject": item["subject"], "created": created, "corpus": {field: corpus.get(field) for field in ("id", "snapshot_sha256", "status", "coverage", "approval", "ready_for_teaching", "gaps", "instructional_material_count")}})
            value = {"school_id": school_id, "school_version": school.value.get("version", 1), "curriculum_revision": run["curriculum_revision"], "run_id": run_row.id, "plan_sha256": run["plan_sha256"], "request_sha256": request_hash, "status": "completed", "courses": courses, "steps": [{"code": "classes", "label": "Create selected grade-year classes", "status": "completed", "detail": f"{sum(item['created'] for item in courses)} created; {sum(not item['created'] for item in courses)} existing exact classes reused."}, {"code": "corpora", "label": "Build class knowledge corpora", "status": "completed", "detail": "Content-addressed reviewed-source snapshots created or reused. Human corpus and teacher approvals remain required."}], "authority_granted": False, "human_approval_required": True, "completed_at": now()}
            row = workflow_record(db, identity, school_id, "school_curriculum_publication", value, record_id)
            audit_actor(db, identity, "school.curriculum_classes_created", {"school_id": school_id, "run_id": run_row.id, "publication_id": row.id, "plan_sha256": run["plan_sha256"], "courses": [{"course_id": item["course_id"], "corpus_id": item["corpus"]["id"], "snapshot_sha256": item["corpus"]["snapshot_sha256"], "created": item["created"]} for item in courses], "human_approval_required": True})
            return workflow_descriptor(db, row, school)

    @app.get("/api/schools/{school_id}/onboarding/runs")
    def onboarding_runs(school_id: str, identity=Depends(actor)):
        with database.factory() as db:
            school = school_access(db, identity, school_id)
            return [workflow_descriptor(db, row, school) for row in workflow_rows(db, identity["tenant_id"], school_id)]
