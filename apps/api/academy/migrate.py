"""Explicit schema admission; never run migrations during login/readiness."""
from sqlalchemy import text
from .config import Settings
from .db import Base, Database, AuditHead, Record, audit, canonical, teaching_schema_id, external_review_schema_id, learning_preferences_schema_id, homework_voice_schema_id, school_curriculum_schema_id, chat_schema_id, classroom_schema_id, family_schema_id, IMMUTABLE_RECORD_KINDS, SEALED_JOB_KINDS
from .database_roles import configure_runtime_role, runtime_credentials
from .permissions import migrate_school_scopes


def install_activity_indexes(connection, dialect):
    # Keep large encrypted operation payloads out of historical metadata scans.
    connection.execute(text("CREATE INDEX IF NOT EXISTS academy_record_owner_created ON records (tenant_id,kind,owner_id,created_at)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS academy_record_parent_created ON records (tenant_id,kind,parent_id,created_at)"))
    for name, field in (("key", "idempotency_key"), ("day", "budget_day"), ("status", "status")):
        expression = f"json_extract(data,'$.{field}')" if dialect == "sqlite" else f"CAST(CAST(data AS JSON)->>'{field}' AS VARCHAR)"
        connection.execute(text(f"CREATE INDEX IF NOT EXISTS academy_voice_{name} ON records (tenant_id,owner_id,({expression})) WHERE kind='voice_operation'"))


def migrate(settings=None):
    settings = settings or Settings.load()
    database = Database(settings)
    runtime = runtime_credentials(settings, database.engine.dialect.name)
    Base.metadata.create_all(database.engine)
    protected = ",".join("'" + kind + "'" for kind in sorted(IMMUTABLE_RECORD_KINDS))
    sealed_jobs = ",".join("'" + kind + "'" for kind in sorted(SEALED_JOB_KINDS))
    with database.engine.begin() as connection:
        install_activity_indexes(connection, database.engine.dialect.name)
        if database.engine.dialect.name == "sqlite":
            for operation in ("UPDATE", "DELETE"):
                connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_audit_{operation.lower()} BEFORE {operation} ON audit_events BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END"))
                connection.execute(text(f"DROP TRIGGER IF EXISTS immutable_submission_{operation.lower()}"))
                connection.execute(text(f"CREATE TRIGGER immutable_submission_{operation.lower()} BEFORE {operation} ON records WHEN OLD.kind IN ({protected}) BEGIN SELECT RAISE(ABORT, 'Submitted work and approved teaching snapshots are immutable'); END"))
                connection.execute(text(f"DROP TRIGGER IF EXISTS immutable_turn_{operation.lower()}"))
                condition = "1" if operation == "DELETE" else "(OLD.kind <> NEW.kind OR COALESCE(json_extract(OLD.data, '$.status'), '') <> 'pending')"
                connection.execute(text(f"CREATE TRIGGER immutable_turn_{operation.lower()} BEFORE {operation} ON records WHEN OLD.kind IN ({sealed_jobs}) AND {condition} BEGIN SELECT RAISE(ABORT, 'Completed tutoring turns are immutable'); END"))
        else:
            connection.execute(text("CREATE OR REPLACE FUNCTION academy_reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Audit events are append-only'; END $$"))
            connection.execute(text(f"CREATE OR REPLACE FUNCTION academy_reject_submission_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.kind IN ({protected}) THEN RAISE EXCEPTION 'Submitted work and approved teaching snapshots are immutable'; END IF; IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$"))
            connection.execute(text("DROP TRIGGER IF EXISTS immutable_audit ON audit_events"))
            connection.execute(text("CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION academy_reject_audit_mutation()"))
            connection.execute(text("DROP TRIGGER IF EXISTS immutable_submission ON records"))
            connection.execute(text("CREATE TRIGGER immutable_submission BEFORE UPDATE OR DELETE ON records FOR EACH ROW EXECUTE FUNCTION academy_reject_submission_mutation()"))
            connection.execute(text(f"CREATE OR REPLACE FUNCTION academy_reject_turn_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.kind IN ({sealed_jobs}) THEN IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Tutoring turns cannot be deleted'; END IF; IF OLD.kind <> NEW.kind OR COALESCE(OLD.data::jsonb->>'status','') <> 'pending' THEN RAISE EXCEPTION 'Completed tutoring turns are immutable'; END IF; END IF; IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$"))
            connection.execute(text("DROP TRIGGER IF EXISTS immutable_turn ON records"))
            connection.execute(text("CREATE TRIGGER immutable_turn BEFORE UPDATE OR DELETE ON records FOR EACH ROW EXECUTE FUNCTION academy_reject_turn_mutation()"))
            if runtime:
                configure_runtime_role(connection, *runtime)
    with database.write() as db:
        if db.get(AuditHead, settings.tenant_id) is None:
            db.add(AuditHead(tenant_id=settings.tenant_id, sequence=0, digest="0" * 64))
            db.flush()
            audit(db, settings.tenant_id, "deployment-migration", "schema.initialized", {"version": 1})
        if settings.auth_mode == "local" and settings.demo_seed and not db.get(Record, "demo-admin"):
            for role, key, name in (("admin", "demo-admin", "Demo administrator"), ("school", "demo-school", "Demo school administrator"), ("teacher", "demo-instructor", "Demo teacher"), ("student", "demo-student", "Jordan (student)"), ("student", "demo-student2", "Casey (student)")):
                db.add(Record(id=key, tenant_id=settings.tenant_id, kind="user", data=canonical({"name": name, "role": role})))
            db.add(Record(id="demo-course", tenant_id=settings.tenant_id, kind="course", data=canonical({"title": "Evidence and reasoning", "description": "Practice making a claim, evaluating evidence and explaining your reasoning."})))
            for user in ("demo-instructor", "demo-student", "demo-student2"):
                db.add(Record(id="enrollment-" + user, tenant_id=settings.tenant_id, kind="enrollment", parent_id="demo-course", owner_id=user, data=canonical({"role": "instructor" if user == "demo-instructor" else "student"})))
            db.add(Record(id="demo-assignment", tenant_id=settings.tenant_id, kind="assignment", parent_id="demo-course", data=canonical({"title": "Explain your evidence", "module": "Building an argument", "instructions": "Choose a claim about a topic you are studying. Write your own claim, identify evidence and explain how the evidence supports it. Describe one uncertainty or alternative explanation.", "version": 1, "tutor_policy": {"mode": "curated", "connection_id": None, "model": None, "max_turns": 8, "max_input_chars": 3000, "max_output_tokens": 80, "max_total_tokens": 20000, "require_attempt_chars": 40, "hints": [{"id": "claim", "text": "Underline the claim in your draft. Which part could another person reasonably question?"}, {"id": "evidence", "text": "Choose one piece of evidence you already found. Explain in your own words how it relates to your claim."}], "questions": [{"id": "uncertainty", "text": "What would you need to learn to rule out an alternative explanation?"}]}})))
            audit(db, settings.tenant_id, "deployment-migration", "demo.seeded", {"synthetic": True})
        if migrate_school_scopes(db, settings):
            audit(db, settings.tenant_id, "deployment-migration", "schema.school_scopes_initialized", {"version": 2, "historical_records_preserved": True})
        marker_id = teaching_schema_id(settings.tenant_id)
        if not db.get(Record, marker_id):
            db.add(Record(id=marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 3, "immutable_teaching_snapshots": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.teaching_snapshots_initialized", {"version": 3, "historical_records_preserved": True})
        review_marker_id = external_review_schema_id(settings.tenant_id)
        if not db.get(Record, review_marker_id):
            db.add(Record(id=review_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 4, "immutable_external_reviews": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.external_reviews_initialized", {"version": 4, "historical_records_preserved": True})
        learning_marker_id = learning_preferences_schema_id(settings.tenant_id)
        if not db.get(Record, learning_marker_id):
            db.add(Record(id=learning_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 5, "immutable_student_preferences_feedback": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.student_preferences_initialized", {"version": 5, "historical_records_preserved": True})
        homework_marker_id = homework_voice_schema_id(settings.tenant_id)
        if not db.get(Record, homework_marker_id):
            db.add(Record(id=homework_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 6, "immutable_homework_voice_evidence": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.homework_voice_initialized", {"version": 6, "historical_records_preserved": True})
        curriculum_marker_id = school_curriculum_schema_id(settings.tenant_id)
        if not db.get(Record, curriculum_marker_id):
            db.add(Record(id=curriculum_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 7, "immutable_school_curriculum_workflows": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.school_curriculum_initialized", {"version": 7, "historical_records_preserved": True})
        chat_marker_id = chat_schema_id(settings.tenant_id)
        if not db.get(Record, chat_marker_id):
            db.add(Record(id=chat_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 8, "immutable_core_chat_evidence": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.core_chat_initialized", {"version": 8, "historical_records_preserved": True})
        classroom_marker_id = classroom_schema_id(settings.tenant_id)
        if not db.get(Record, classroom_marker_id):
            db.add(Record(id=classroom_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 9, "protected_classroom_operations": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.google_classroom_initialized", {"version": 9, "historical_records_preserved": True})
        family_marker_id = family_schema_id(settings.tenant_id)
        if not db.get(Record, family_marker_id):
            db.add(Record(id=family_marker_id, tenant_id=settings.tenant_id, kind="schema", data=canonical({"version": 10, "encrypted_local_family_accounts": True})))
            audit(db, settings.tenant_id, "deployment-migration", "schema.local_family_initialized", {"version": 10, "historical_records_preserved": True})
    database.engine.dispose()


if __name__ == "__main__":
    migrate()
    print("NCAcademy schema is ready")
