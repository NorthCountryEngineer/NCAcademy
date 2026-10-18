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
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
    engine = create_engine(runtime_url, hide_parameters=True)
    try:
        for statement in (
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
