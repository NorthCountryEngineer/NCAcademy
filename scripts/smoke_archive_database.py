#!/usr/bin/env python3
"""Disposable PostgreSQL TLS/archive-reader admission smoke; no cloud access."""
import argparse
import json
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from smoke_archive_store import docker, fixture, synthetic_source, ROOT
sys.path.insert(0, str(ROOT / "apps/archive"))
from academy_archive import ArchiveError, DatabaseSource, canonical


def run(image="postgres:16-alpine", native=False):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import URL
    checks = []
    name = "ncacademy-archive-pg-contract-" + uuid4().hex[:10]
    # Keep PostgreSQL's Unix socket path below its platform length limit.
    with tempfile.TemporaryDirectory(prefix=name + "-", dir="/tmp") as temporary:
        folder = Path(temporary)
        mounted = folder / "mounted"
        mounted.mkdir(mode=0o755)
        fixture(mounted)
        password, reader_password = uuid4().hex + uuid4().hex, uuid4().hex + uuid4().hex
        env = folder / "postgres.env"
        env.write_text("POSTGRES_DB=academy\nPOSTGRES_USER=archive_owner\nPOSTGRES_PASSWORD=" + password + "\n")
        env.chmod(0o600)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        try:
            if native:
                password_file = folder / "password.txt"
                password_file.write_text(password)
                password_file.chmod(0o600)
                (mounted / "tls.key").chmod(0o600)
                subprocess.run(["initdb", "-D", str(folder / "pgdata"), "-U", "archive_owner", "-A", "scram-sha-256", "--pwfile=" + str(password_file), "--no-sync"], check=True, capture_output=True)
                options_string = shlex.join(["-h", "127.0.0.1", "-p", str(port), "-k", str(folder), "-c", "ssl=on", "-c", "ssl_cert_file=" + str(mounted / "tls.crt"), "-c", "ssl_key_file=" + str(mounted / "tls.key")])
                try:
                    subprocess.run(["pg_ctl", "-D", str(folder / "pgdata"), "-l", str(folder / "postgres.log"), "-o", options_string, "-t", "60", "-w", "start"], check=True, capture_output=True)
                except subprocess.CalledProcessError:
                    raise RuntimeError("Synthetic native PostgreSQL startup failed: " + (folder / "postgres.log").read_text()[-2000:]) from None
                with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user="archive_owner", password=password, sslmode="verify-full", sslrootcert=str(mounted / "tls.crt"), autocommit=True) as owner:
                    owner.execute("CREATE DATABASE academy")
            else:
                docker("run", "--detach", "--name", name, "--env-file", str(env), "--publish", f"127.0.0.1:{port}:5432", "--mount", f"type=bind,source={mounted},target=/test-config,readonly", "--entrypoint", "sh", image, "-c", "cp /test-config/tls.crt /tmp/archive-server.crt && cp /test-config/tls.key /tmp/archive-server.key && chown postgres:postgres /tmp/archive-server.key /tmp/archive-server.crt && chmod 0600 /tmp/archive-server.key && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/archive-server.crt -c ssl_key_file=/tmp/archive-server.key")
            options = {"host": "127.0.0.1", "port": port, "dbname": "academy", "user": "archive_owner", "password": password, "sslmode": "verify-full", "sslrootcert": str(mounted / "tls.crt"), "connect_timeout": 2}
            last_reason = "database_unavailable"
            for _ in range(300):
                try:
                    with psycopg.connect(**options):
                        break
                except psycopg.OperationalError as error:
                    last_reason = type(error).__name__
                    time.sleep(1)
            else:
                raise RuntimeError("Disposable TLS PostgreSQL did not become ready: " + last_reason)
            original, sqlite_source = synthetic_source(folder)
            sqlite_source.engine.dispose()
            with psycopg.connect(**options, autocommit=True) as owner:
                owner.execute("CREATE TABLE audit_heads (tenant_id TEXT PRIMARY KEY, sequence INTEGER, digest TEXT)")
                owner.execute("CREATE TABLE audit_events (id TEXT, tenant_id TEXT, sequence INTEGER, session_id TEXT, actor_id TEXT, event_type TEXT, payload TEXT, previous_hash TEXT, created_at TEXT, hash TEXT)")
                for table in ("records", "logins", "oauth_flows", "core_identity_bindings"):
                    owner.execute(sql.SQL("CREATE TABLE {} (id TEXT)").format(sql.Identifier(table)))
                for event in original:
                    owner.execute("INSERT INTO audit_events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", tuple(canonical(event[key]).decode() if key == "payload" else event[key] for key in ("id", "tenant_id", "sequence", "session_id", "actor_id", "event_type", "payload", "previous_hash", "created_at", "hash")))
                owner.execute("INSERT INTO audit_heads VALUES (%s,%s,%s)", ("synthetic-tenant", len(original), original[-1]["hash"]))
                owner.execute("REVOKE CREATE,TEMPORARY ON DATABASE academy FROM PUBLIC")
                owner.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                owner.execute("CREATE ROLE academy_archive_reader LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
                owner.execute(sql.SQL("ALTER ROLE academy_archive_reader PASSWORD {}").format(sql.Literal(reader_password)))
                owner.execute("GRANT CONNECT ON DATABASE academy TO academy_archive_reader")
                owner.execute("GRANT USAGE ON SCHEMA public TO academy_archive_reader")
                owner.execute("GRANT SELECT ON audit_events,audit_heads TO academy_archive_reader")
                owner.execute("ALTER ROLE academy_archive_reader SET default_transaction_read_only = on")
            def url(user, secret, *, rootcert=None):
                return URL.create("postgresql+psycopg", username=user, password=secret, host="127.0.0.1", port=port, database="academy", query={"sslmode": "verify-full", "sslrootcert": rootcert or str(mounted / "tls.crt")}).render_as_string(hide_password=False)
            source = DatabaseSource(url("academy_archive_reader", reader_password), "synthetic-tenant")
            with source.snapshot() as snapshot:
                assert list(snapshot.events()) == original
            checks.append("verify_full_tls_repeatable_read_complete_chain_with_select_only_reader")
            try:
                with DatabaseSource(url("archive_owner", password), "synthetic-tenant").snapshot():
                    raise AssertionError("Database owner was admitted")
            except ArchiveError:
                checks.append("database_owner_rejected")
            with psycopg.connect(**{**options, "user": "academy_archive_reader", "password": reader_password}) as reader:
                try:
                    reader.execute("UPDATE audit_events SET payload='{}'")
                except (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege):
                    reader.rollback()
                    checks.append("reader_update_refused")
                else:
                    raise AssertionError("Archive reader could write audit evidence")
                try:
                    reader.execute("SELECT * FROM records")
                except psycopg.errors.InsufficientPrivilege:
                    reader.rollback()
                    checks.append("private_records_access_refused")
                else:
                    raise AssertionError("Archive reader could read private records")
            with psycopg.connect(**options, autocommit=True) as owner:
                owner.execute("GRANT SELECT ON records TO academy_archive_reader")
            try:
                with source.snapshot():
                    raise AssertionError("Overbroad reader was admitted")
            except ArchiveError:
                checks.append("unexpected_private_table_grant_rejected")
            source.engine.dispose()
            return {"status": "passed", "runtime": "private_native_postgresql" if native else "disposable_container", "checks": checks, "events": len(original), "cloud_changes": False}
        finally:
            if native and (folder / "pgdata").exists():
                subprocess.run(["pg_ctl", "-D", str(folder / "pgdata"), "-m", "immediate", "-w", "stop"], capture_output=True)
            elif not native:
                subprocess.run(["docker", "rm", "--force", "--volumes", name], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--image", default="postgres:16-alpine")
    parser.add_argument("--native", action="store_true", help="Use installed PostgreSQL binaries with a private disposable data directory")
    args = parser.parse_args()
    if not args.run:
        parser.error("Explicit --run is required")
    print(json.dumps(run(args.image, native=args.native), indent=2))


if __name__ == "__main__":
    main()
