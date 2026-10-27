"""Runtime database admission, including opt-in real PostgreSQL enforcement.

ACADEMY_TEST_POSTGRES_URL must identify an isolated loopback database named
academy_privilege_test. Never run these destructive synthetic checks on a
development application database or a remote service.
"""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from academy.config import Settings
from academy.database_roles import runtime_credentials
from academy.main import create_app
from academy.migrate import migrate
from academy.permissions import default_school_id


def test_core_postgres_migration_requires_separate_runtime_identity(monkeypatch):
    monkeypatch.delenv("ACADEMY_RUNTIME_DB_USERNAME", raising=False)
    monkeypatch.delenv("ACADEMY_RUNTIME_DB_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="separate runtime"):
        runtime_credentials(SimpleNamespace(auth_mode="core"), "postgresql")
    assert runtime_credentials(SimpleNamespace(auth_mode="local"), "postgresql") is None
    assert runtime_credentials(SimpleNamespace(auth_mode="core"), "sqlite") is None


@pytest.mark.parametrize("username,password", [
    ("", "x" * 40), ("academy_runtime", ""), ("academy_runtime", "short"),
    ("academy_runtime", "x" * 257),
    ("academy_runtime; DROP ROLE academy", "x" * 40),
])
def test_invalid_bootstrap_values_fail_without_disclosing_them(monkeypatch, username, password):
    monkeypatch.setenv("ACADEMY_RUNTIME_DB_USERNAME", username)
    monkeypatch.setenv("ACADEMY_RUNTIME_DB_PASSWORD", password)
    with pytest.raises(ValueError) as caught:
        runtime_credentials(SimpleNamespace(auth_mode="core"), "postgresql")
    assert str(caught.value) == "Invalid runtime database bootstrap configuration"


def test_real_postgres_runtime_can_operate_but_cannot_rewrite_audit_or_schema(monkeypatch):
    configured = os.getenv("ACADEMY_TEST_POSTGRES_URL", "")
    if not configured:
        pytest.skip("Set ACADEMY_TEST_POSTGRES_URL for isolated local PostgreSQL privilege checks")
    owner_url = make_url(configured)
    if owner_url.drivername != "postgresql+psycopg" or owner_url.host not in {"127.0.0.1", "localhost", "::1"} or owner_url.database != "academy_privilege_test":
        pytest.fail("PostgreSQL privilege test refuses a non-loopback or non-synthetic database")
    role = "academy_runtime_" + uuid4().hex[:12]
    password = "Synthetic-only 'quoted' \\ password " + uuid4().hex
    monkeypatch.setenv("ACADEMY_RUNTIME_DB_USERNAME", role)
    monkeypatch.setenv("ACADEMY_RUNTIME_DB_PASSWORD", password)
    settings = Settings(owner_url.render_as_string(hide_password=False), "local", "http://localhost:8090", "synthetic-local-role-bootstrap-long-enough", False, True)
    migrate(settings)
    migrate(settings)  # A redeployment preserves evidence and resets exact grants.
    runtime_url = owner_url.set(username=role, password=password)
    runtime_settings = Settings(runtime_url.render_as_string(hide_password=False), "local", settings.app_url, settings.session_secret, False, False)
    application = create_app(runtime_settings)
    with TestClient(application, base_url=settings.app_url) as client:
        assert client.get("/api/ready").status_code == 200
        response = client.post("/api/auth/local", json={"user_id": "demo-admin"}, headers={"Origin": settings.app_url})
        assert response.status_code == 200
        headers = {"Origin": settings.app_url, "X-CSRF-Token": response.json()["csrf_token"]}
        assert client.post("/api/courses", json={"title": "Synthetic restricted-role course"}, headers=headers).status_code == 201
        assert client.get("/api/admin/audit/export").status_code == 200
        # Real restricted-role administrative CRUD, local execution and JSONB
        # metadata pagination. No provider or student content enters this report.
        admin_base = "/api/schools/" + default_school_id(runtime_settings.tenant_id) + "/administration"
        admin_created = client.post(admin_base + "/agents", json={"name": "Synthetic PostgreSQL setup reviewer"}, headers=headers)
        assert admin_created.status_code == 201, admin_created.text
        admin_agent = admin_created.json()
        admin_run = client.post(admin_base + "/agents/" + admin_agent["id"] + "/runs", json={"expected_version": admin_agent["version"], "idempotency_key": "postgres-administrative-" + uuid4().hex}, headers=headers)
        assert admin_run.status_code == 201 and admin_run.json()["status"] == "complete", admin_run.text
        admin_activity = client.get(admin_base + "/activity", params={"kind": "administrative", "status": "complete", "limit": 1})
        assert admin_activity.status_code == 200 and admin_activity.json()["total"] >= 1, admin_activity.text
        assert set(admin_activity.json()["items"][0]) == {"id", "kind", "status", "name", "created_at", "completed_at", "agent_id", "can_open"}
        # Exercise PostgreSQL JSON ->> eligibility and required-class ordering
        # under the actual restricted runtime login. Core is explicitly mocked;
        # the student has a separate cookie jar and gains no native membership.
        from conftest import login
        from test_chat_groups import FakeCoreChat
        runtime_settings.core_chat_enabled = True
        runtime_settings.core_issuer = "https://core.example.test"
        runtime_settings.core_chat_installation_id = "33333333-3333-4333-8333-333333333333"
        mocked_chat = FakeCoreChat(application)
        application.state.core_chat_transport = mocked_chat
        school_id = default_school_id(runtime_settings.tenant_id)
        created_groups = []
        for visibility, policy in (("school", "open"), ("private", "invite")):
            created = client.post("/api/chat/groups", json={"school_id": school_id, "title": "Synthetic PostgreSQL " + visibility,
                "visibility": visibility, "join_policy": policy, "idempotency_key": "postgres-group-" + uuid4().hex}, headers=headers)
            assert created.status_code == 201, created.text
            created_groups.append(created.json())
        student = TestClient(application, base_url=settings.app_url)
        try:
            student_headers = login(student)
            bootstrapped = student.post("/api/chat/bootstrap", json={"school_id": school_id}, headers=student_headers)
            assert bootstrapped.status_code == 200, bootstrapped.text
            directory = student.get("/api/chat/groups", params={"school_id": school_id, "discover": True})
            assert directory.status_code == 200, directory.text
            groups = directory.json()["groups"]
            assert groups[0]["required"] and groups[0]["course_id"] == "demo-course"
            assert {course["id"] for course in directory.json()["courses"]} == {"demo-course"}
            assert created_groups[0]["id"] in {group["id"] for group in groups}
            assert created_groups[1]["id"] not in {group["id"] for group in groups}
            joined = student.post("/api/chat/groups/" + created_groups[0]["id"] + "/join", json={}, headers=student_headers)
            assert joined.status_code == 200 and joined.json()["membership"] == "active"
            joined_directory = student.get("/api/chat/groups", params={"school_id": school_id}).json()["groups"]
            assert created_groups[0]["id"] in {group["id"] for group in joined_directory}
            path = "/api/chat/groups/" + groups[0]["id"] + "/messages"
            delivered = student.post(path, json={"text": "Synthetic PostgreSQL class discussion.",
                "idempotency_key": "postgres-message-" + uuid4().hex}, headers=student_headers)
            assert delivered.status_code == 200 and delivered.json()["status"] == "delivered", delivered.text
            history = student.get(path)
            assert history.status_code == 200 and len(history.json()["messages"]) == 1, history.text
            assert history.json()["messages"][0]["text"] == "Synthetic PostgreSQL class discussion."
            assert history.json()["messages"][0]["author"]["id"] == "demo-student"
            assert len(mocked_chat.sends) == 1
        finally:
            student.close()
            runtime_settings.core_chat_enabled = False
        # New local accounts exercise the real PostgreSQL restricted role,
        # application-layer encryption and enrollment without any Core call.
        private_work = "Synthetic PostgreSQL private learning evidence."
        private_name = "Synthetic PostgreSQL Family Learner"
        private_password = "Synthetic PostgreSQL family password!"
        family = client.post("/api/admin/family-accounts", json={"kind": "student", "username": "pg-learner-" + uuid4().hex[:8], "password": private_password, "name": private_name, "must_change_password": False, "school_id": school_id, "academic_year": "2026-2027", "grade": "10", "course_ids": ["demo-course"]}, headers=headers)
        assert family.status_code == 201, family.text
        local_student = TestClient(application, base_url=settings.app_url)
        try:
            signed_in = local_student.post("/api/auth/password", json={"username": family.json()["username"], "password": private_password}, headers={"Origin": settings.app_url})
            assert signed_in.status_code == 200, signed_in.text
            local_headers = {"Origin": settings.app_url, "X-CSRF-Token": signed_in.json()["csrf_token"]}
            saved = local_student.put("/api/assignments/demo-assignment/draft", json={"content": private_work}, headers=local_headers)
            assert saved.status_code == 200, saved.text
            submitted = local_student.post("/api/assignments/demo-assignment/submit", json={"content": private_work}, headers=local_headers)
            assert submitted.status_code == 201, submitted.text
            assert local_student.get("/api/assignments/demo-assignment").json()["draft"]["content"] == private_work
            assert local_student.get("/api/chat/groups").status_code == 403
            with application.state.database.factory() as db:
                raw = " ".join(db.scalars(text("SELECT data FROM records"))) + " ".join(db.scalars(text("SELECT payload FROM audit_events")))
                assert all(secret not in raw for secret in (private_name, private_password, private_work, family.json()["username"]))
            assert client.get("/api/audit/verify").json()["valid"] is True
        finally:
            local_student.close()
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
    engine = create_engine(runtime_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL enable_seqscan=off"))
            for field,index_name in (("idempotency_key","academy_voice_key"),("budget_day","academy_voice_day"),("status","academy_voice_status")):
                plan = connection.execute(text(f"EXPLAIN SELECT id FROM records WHERE kind='voice_operation' AND tenant_id='local-academy' AND owner_id='synthetic-student' AND CAST(CAST(data AS JSON)->>'{field}' AS VARCHAR)='synthetic-value'")).all()
                assert any(index_name in str(row) for row in plan), plan
        for kind in ("admin_run", "homework_ai_message", "classroom_operation", "academy_chat_attempt", "turn", "lesson_turn", "external_review_job", "voice_operation"):
            identifier = "pending-" + uuid4().hex
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO records (id,tenant_id,kind,parent_id,owner_id,data,created_at) VALUES (:id,'local-academy',:kind,'','',:data,'2026-01-01')"), {"id": identifier, "kind": kind, "data": '{"status":"pending"}'})
                connection.execute(text("UPDATE records SET data=:data WHERE id=:id"), {"id": identifier, "data": '{"status":"pending","send_marker":"synthetic"}'})
                connection.execute(text("UPDATE records SET data=:data WHERE id=:id"), {"id": identifier, "data": '{"status":"released","approved_text":"Reviewed source"}'})
            for statement in ("UPDATE records SET data='{}' WHERE id=:id", "UPDATE records SET kind='draft' WHERE id=:id", "DELETE FROM records WHERE id=:id"):
                with pytest.raises(DBAPIError) as caught:
                    with engine.begin() as connection:
                        connection.execute(text(statement), {"id": identifier})
                assert caught.value.orig.sqlstate == "P0001", kind
        for kind in ("admin_agent_revision", "admin_skill_revision", "admin_run_send", "admin_run_result", "teacher_model_revision", "teacher_model_approval", "homework_ai_config_revision", "homework_ai_approval", "homework_ai_session", "homework_ai_send", "family_event", "classroom_link_event", "academy_chat_command", "academy_chat_receipt", "academy_chat_proposal", "academy_chat_membership_event", "school_curriculum_run", "school_curriculum_publication", "knowledge_workflow_run", "homework_session", "homework_message", "homework_outcome", "student_voice_preference", "student_preference_revision", "student_agent_feedback", "submission", "teacher_profile_revision", "course_corpus", "corpus_approval", "external_resource_snapshot", "external_audit_agent_revision", "external_review_result", "external_publication"):
            identifier = "protected-" + uuid4().hex
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO records (id,tenant_id,kind,parent_id,owner_id,data,created_at) VALUES (:id,'local-academy',:kind,'','',:data,'2026-01-01')"), {"id": identifier, "kind": kind, "data": '{"original":true}'})
            for statement in (
                "UPDATE records SET data = '{}' WHERE id = :id",
                "UPDATE records SET kind = 'draft' WHERE id = :id",
                "DELETE FROM records WHERE id = :id",
            ):
                with pytest.raises(DBAPIError) as caught:
                    with engine.begin() as connection:
                        connection.execute(text(statement), {"id": identifier})
                assert caught.value.orig.sqlstate == "P0001", kind
            with engine.connect() as connection:
                stored = connection.execute(text("SELECT kind,data FROM records WHERE id = :id"), {"id": identifier}).one()
                assert stored == (kind, '{"original":true}')
        for statement in (
            "UPDATE audit_delivery_outbox SET metadata_json = '{}'",
            "DELETE FROM audit_delivery_outbox",
            "TRUNCATE audit_delivery_outbox",
            "UPDATE audit_delivery_state SET status = 'delivered'",
            "DELETE FROM audit_delivery_state",
            "INSERT INTO audit_delivery_receipts (id) VALUES ('forbidden')",
            "UPDATE audit_delivery_receipts SET outcome = 'created'",
            "DELETE FROM audit_delivery_receipts",
            "UPDATE audit_events SET payload = '{}'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
            "DELETE FROM audit_heads",
            "UPDATE core_identity_bindings SET issuer = 'https://untrusted.invalid'",
            "DELETE FROM core_identity_bindings",
            "TRUNCATE core_identity_bindings",
            "TRUNCATE audit_heads",
            "TRUNCATE records",
            "CREATE TABLE forbidden_ddl (id integer)",
            "CREATE TEMPORARY TABLE forbidden_temp (id integer)",
            "ALTER TABLE audit_events DISABLE TRIGGER ALL",
            "DROP TABLE audit_events",
            "CREATE ROLE forbidden_role",
        ):
            with pytest.raises(DBAPIError) as caught:
                with engine.begin() as connection:
                    connection.execute(text(statement))
            assert caught.value.orig.sqlstate == "42501", statement
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM audit_events")).scalar_one() >= 4
            assert connection.execute(text("SELECT count(*) FROM audit_delivery_outbox")).scalar_one() >= 4
            assert connection.execute(text("SELECT count(*) FROM audit_delivery_state")).scalar_one() >= 4
            assert connection.execute(text("SELECT count(*) FROM audit_delivery_receipts")).scalar_one() >= 0
    finally:
        engine.dispose()
    # The migration secret can be rotated, including SQL quoting characters.
    rotated = "Synthetic rotation ' \\ " + uuid4().hex
    monkeypatch.setenv("ACADEMY_RUNTIME_DB_PASSWORD", rotated)
    migrate(settings)
    engine = create_engine(runtime_url.set(password=rotated), hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == role
    finally:
        engine.dispose()
    # A configuration mistake must not repurpose the owner or an elevated role.
    owner_engine = create_engine(owner_url, hide_parameters=True)
    privileged = "academy_privileged_" + uuid4().hex[:12]
    try:
        with owner_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE ROLE "{privileged}" CREATEROLE')
        for forbidden_role in (owner_url.username, privileged):
            monkeypatch.setenv("ACADEMY_RUNTIME_DB_USERNAME", forbidden_role)
            with pytest.raises(RuntimeError) as caught:
                migrate(settings)
            assert str(caught.value) == "Restricted runtime database identity provisioning failed"
        with owner_engine.connect() as connection:
            assert connection.execute(text("SELECT rolcreaterole FROM pg_roles WHERE rolname = :role"), {"role": privileged}).scalar_one() is True
    finally:
        owner_engine.dispose()
