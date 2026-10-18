"""Explicit schema admission; never run migrations during login/readiness."""
from sqlalchemy import text
from .config import Settings
from .db import Base, Database, AuditHead, Record, audit, canonical
from .database_roles import configure_runtime_role, runtime_credentials
from .permissions import migrate_school_scopes


def migrate(settings=None):
    settings = settings or Settings.load()
    database = Database(settings)
    runtime = runtime_credentials(settings, database.engine.dialect.name)
    Base.metadata.create_all(database.engine)
    with database.engine.begin() as connection:
        if database.engine.dialect.name == "sqlite":
            for operation in ("UPDATE", "DELETE"):
                connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_audit_{operation.lower()} BEFORE {operation} ON audit_events BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END"))
                connection.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_submission_{operation.lower()} BEFORE {operation} ON records WHEN OLD.kind = 'submission' BEGIN SELECT RAISE(ABORT, 'Submitted work is immutable'); END"))
        else:
            connection.execute(text("CREATE OR REPLACE FUNCTION academy_reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Audit events are append-only'; END $$"))
            connection.execute(text("CREATE OR REPLACE FUNCTION academy_reject_submission_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.kind = 'submission' THEN RAISE EXCEPTION 'Submitted work is immutable'; END IF; IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$"))
            connection.execute(text("DROP TRIGGER IF EXISTS immutable_audit ON audit_events"))
            connection.execute(text("CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION academy_reject_audit_mutation()"))
            connection.execute(text("DROP TRIGGER IF EXISTS immutable_submission ON records"))
            connection.execute(text("CREATE TRIGGER immutable_submission BEFORE UPDATE OR DELETE ON records FOR EACH ROW EXECUTE FUNCTION academy_reject_submission_mutation()"))
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
    database.engine.dispose()


if __name__ == "__main__":
    migrate()
    print("NCAcademy schema is ready")
