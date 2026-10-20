"""Provision the restricted PostgreSQL API identity during owner-only migration.

The running API never receives these bootstrap inputs or the database owner
credential. SQL identifiers and password literals are composed by psycopg; no
password-bearing statement is passed through SQLAlchemy's exception formatter.
"""
import os
import re

from psycopg import sql


def runtime_credentials(settings, dialect):
    username = os.getenv("ACADEMY_RUNTIME_DB_USERNAME", "")
    password = os.getenv("ACADEMY_RUNTIME_DB_PASSWORD", "")
    if not username and not password:
        if settings.auth_mode == "core" and dialect == "postgresql":
            raise ValueError("Core migration requires a separate runtime database identity")
        return None
    if dialect != "postgresql" or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", username) or not 32 <= len(password) <= 256 or "\x00" in password:
        raise ValueError("Invalid runtime database bootstrap configuration")
    return username, password


def configure_runtime_role(connection, username, password):
    """Grant only the DML used by the API; the migration owner retains DDL.

This intentionally refuses an existing privileged/owning/member role rather
than trying to repurpose an identity belonging to another application.
"""
    try:
        with connection.connection.driver_connection.cursor() as cursor:
            cursor.execute("SELECT current_user, current_database()")
            owner, database = cursor.fetchone()
            if username == owner:
                raise ValueError()
            cursor.execute("SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = %s", (username,))
            existing = cursor.fetchone()
            if existing:
                role_oid, *privileged = existing
                cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member = %s) OR EXISTS(SELECT 1 FROM pg_class WHERE relowner = %s) OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner = %s) OR EXISTS(SELECT 1 FROM pg_database WHERE datdba = %s)", (role_oid,) * 4)
                if any(privileged) or cursor.fetchone()[0]:
                    raise ValueError()
            else:
                cursor.execute(sql.SQL("CREATE ROLE {} NOINHERIT").format(sql.Identifier(username)))
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN NOINHERIT PASSWORD {}").format(sql.Identifier(username), sql.Literal(password)))
            role, db = sql.Identifier(username), sql.Identifier(database)
            cursor.execute(sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(db))
            cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            cursor.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(db, role))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, role))
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(role))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(role))
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role))
            cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON records, record_scopes, logins, oauth_flows TO {}").format(role))
            cursor.execute(sql.SQL("GRANT SELECT, INSERT ON core_identity_bindings TO {}").format(role))
            cursor.execute(sql.SQL("GRANT SELECT, INSERT ON audit_events TO {}").format(role))
            cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON audit_heads TO {}").format(role))
            cursor.execute(sql.SQL("GRANT SELECT, INSERT ON audit_delivery_outbox, audit_delivery_state TO {}").format(role))
            cursor.execute(sql.SQL("GRANT SELECT ON audit_delivery_receipts TO {}").format(role))
    except Exception:
        # Never include SQL or provider/database diagnostics in migration logs:
        # ALTER ROLE necessarily contains the new password as a SQL literal.
        raise RuntimeError("Restricted runtime database identity provisioning failed") from None
