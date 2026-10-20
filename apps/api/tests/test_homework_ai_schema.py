"""Schema 11 preserves student evidence and separates metadata delivery authority."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError

from academy.db import Audit, Record, canonical, homework_ai_schema_id
from academy.audit_delivery import AuditDeliveryOutbox, AuditDeliveryReceipt, AuditDeliveryState
from academy.migrate import migrate
from conftest import login


def test_explicit_owner_rerun_reconciles_late_old_api_audit_without_rewriting_history(app, monkeypatch):
    from academy import audit_delivery
    from academy.db import audit
    database, tenant = app.state.database, app.state.settings.tenant_id
    with monkeypatch.context() as old_api:
        old_api.setattr(audit_delivery, 'enqueue_audit_metadata', lambda *args: None)
        with database.write() as db:
            audit(db, tenant, 'synthetic-old-api', 'synthetic.late_old_api', {'private': 'synthetic historical question'})
    with database.factory() as db:
        before = [(r.id, r.payload, r.hash) for r in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert db.scalar(select(AuditDeliveryOutbox).where(AuditDeliveryOutbox.audit_id == before[-1][0])) is None
    migrate(app.state.settings)
    migrate(app.state.settings)
    with database.factory() as db:
        after = [(r.id, r.payload, r.hash) for r in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert after == before
        entry = db.scalar(select(AuditDeliveryOutbox).where(AuditDeliveryOutbox.audit_id == before[-1][0]))
        assert entry and 'synthetic historical question' not in entry.metadata_json


def test_real_postgres_dispatcher_boundary_and_lease_recovery(monkeypatch):
    configured = os.getenv('ACADEMY_TEST_POSTGRES_URL', '')
    if not configured:
        pytest.skip('Set ACADEMY_TEST_POSTGRES_URL for isolated dispatcher privilege checks')
    owner_url = make_url(configured)
    if owner_url.drivername != 'postgresql+psycopg' or owner_url.host not in {'127.0.0.1', 'localhost', '::1'} or owner_url.database != 'academy_privilege_test':
        pytest.fail('Dispatcher privilege test refuses a non-loopback or non-synthetic database')
    from academy.config import Settings
    from core_delivery import DeliveryStore
    monkeypatch.delenv('ACADEMY_RUNTIME_DB_USERNAME', raising=False)
    monkeypatch.delenv('ACADEMY_RUNTIME_DB_PASSWORD', raising=False)
    settings = Settings(configured, 'local', 'http://localhost:8090', 'synthetic-dispatcher-role-check-long-enough', False, True)
    migrate(settings)
    from academy.db import Database, audit
    tenant = 'dispatcher-test-' + uuid4().hex
    database = Database(settings)
    try:
        with database.write() as db:
            audit(db, tenant, 'synthetic-operator', 'synthetic.dispatcher_first', {})
            audit(db, tenant, 'synthetic-operator', 'synthetic.dispatcher_second', {})
    finally:
        database.engine.dispose()
    role = 'academy_dispatcher_' + uuid4().hex[:12]
    password = 'Synthetic dispatcher password ' + uuid4().hex
    script = (Path(__file__).resolve().parents[3] / 'deploy/archive/core-delivery-role.sql').read_text()
    script = script.replace('academy_metadata_dispatcher', role).replace('DATABASE academy ', 'DATABASE academy_privilege_test ')
    script = '\n'.join(line for line in script.splitlines() if not line.lstrip().startswith('--'))
    owner = create_engine(owner_url, hide_parameters=True)
    worker_engine = create_engine(owner_url.set(username=role, password=password), hide_parameters=True)
    try:
        with owner.begin() as connection:
            for statement in script.split(';'):
                if statement.strip():
                    connection.exec_driver_sql(statement)
            # CI uses password authentication, rather than native loopback
            # trust. Compose the generated synthetic credential as a literal.
            from psycopg import sql
            with connection.connection.driver_connection.cursor() as cursor:
                cursor.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        # This checks real SQL authority/locking. Transport TLS admission is
        # separately tested; this disposable fixture permits loopback only.
        store = object.__new__(DeliveryStore)
        store.engine = worker_engine
        store.config = SimpleNamespace(tenant_id=tenant, destination='a'*64, lease_seconds=60)
        store.verify_privileges()
        first = store.claim(clock=100)
        assert first and first['attempts'] == 1
        second = store.claim(clock=101)
        assert second and first['id'] != second['id']
        recovered = store.claim(clock=161)
        assert recovered['id'] == first['id'] and recovered['attempts'] == 2
        record_id = 9000000000000001
        store.finish(recovered, {'outcome': 'created', 'http_status': 201, 'core_record_id': record_id}, clock=162)
        with worker_engine.connect() as connection:
            receipts = connection.execute(text('SELECT outcome,core_record_id FROM audit_delivery_receipts WHERE event_id=:id ORDER BY attempt'), {'id': first['id']}).all()
            assert receipts == [('lease_expired', 0), ('created', record_id)]
        for statement in ('SELECT * FROM records', 'SELECT * FROM audit_events', 'SELECT * FROM logins',
                          "UPDATE audit_delivery_outbox SET metadata_json='{}'", 'DELETE FROM audit_delivery_state',
                          'DELETE FROM audit_delivery_receipts', 'CREATE TABLE forbidden_dispatcher (id integer)'):
            with pytest.raises(DatabaseError) as caught:
                with worker_engine.begin() as connection:
                    connection.execute(text(statement))
            assert caught.value.orig.sqlstate == '42501', statement
        for statement in ("UPDATE audit_delivery_outbox SET metadata_json='{}'", 'DELETE FROM audit_delivery_receipts'):
            with pytest.raises(DatabaseError) as caught:
                with owner.begin() as connection:
                    connection.execute(text(statement))
            assert caught.value.orig.sqlstate == 'P0001', statement
    finally:
        worker_engine.dispose()
        with owner.begin() as connection:
            if connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'), {'role':role}).first():
                connection.exec_driver_sql('DROP OWNED BY "' + role + '"')
                connection.exec_driver_sql('DROP ROLE "' + role + '"')
        owner.dispose()


def test_schema11_readiness_requires_explicit_owner_migration_and_preserves_evidence(app, client):
    database, tenant = app.state.database, app.state.settings.tenant_id
    with database.factory() as db:
        before = [(r.id, r.payload, r.hash) for r in db.scalars(select(Audit).order_by(Audit.sequence))]
    with database.write() as db:
        db.delete(db.get(Record, homework_ai_schema_id(tenant)))
    assert client.get('/api/ready').status_code == 503
    migrate(app.state.settings)
    with database.factory() as db:
        after = [(r.id, r.payload, r.hash) for r in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert after[:len(before)] == before and len(after) == len(before) + 1
        assert len(list(db.scalars(select(AuditDeliveryOutbox)))) == len(after)
        assert len(list(db.scalars(select(AuditDeliveryState)))) == len(after)
    migrate(app.state.settings)
    with database.factory() as db:
        assert len(list(db.scalars(select(Audit)))) == len(after)
    assert client.get('/api/ready').status_code == 200


def test_metadata_outbox_and_receipts_have_database_immutability(app):
    database = app.state.database
    with database.factory() as db:
        entry = db.scalar(select(AuditDeliveryOutbox))
        entry_id = entry.id
    for statement in ["UPDATE audit_delivery_outbox SET metadata_json='{}' WHERE id=:id", "DELETE FROM audit_delivery_outbox WHERE id=:id"]:
        with pytest.raises(DatabaseError):
            with database.engine.begin() as connection:
                connection.execute(text(statement), {'id': entry_id})
    with database.write() as db:
        db.add(AuditDeliveryReceipt(id='synthetic-delivery-receipt', tenant_id=app.state.settings.tenant_id,
            event_id=entry_id, attempt=1, outcome='created', http_status=201,
            core_record_id=1, destination_sha256='a'*64))
    with pytest.raises(DatabaseError):
        with database.engine.begin() as connection:
            connection.execute(text("DELETE FROM audit_delivery_receipts WHERE id='synthetic-delivery-receipt'"))


def test_status_is_school_scoped_and_never_contains_student_content_or_network_credentials(app, client):
    login(client, 'demo-admin')
    response = client.get('/api/homework-help/audit-delivery')
    assert response.status_code == 200
    assert response.json()['mode'] == 'metadata_only'
    assert response.json()['network_from_api'] is False
    login(client)
    assert client.get('/api/homework-help/audit-delivery').status_code == 403
    with app.state.database.factory() as db:
        values = [json.loads(row.metadata_json) for row in db.scalars(select(AuditDeliveryOutbox))]
        assert values
        assert all(not {'payload','student_id','actor_id','message','answer','prompt','images','api_key','authorization'} & value.keys() for value in values)


@pytest.mark.parametrize('kind', ['homework_ai_session', 'homework_ai_send'])
def test_new_immutable_ai_records_reject_sql_deletion(app, kind):
    with app.state.database.write() as db:
        db.add(Record(id='synthetic-ai-immutable-'+kind, tenant_id=app.state.settings.tenant_id,
            kind=kind, owner_id='demo-student', data=canonical({'synthetic': True})))
    with pytest.raises(DatabaseError):
        with app.state.database.engine.begin() as connection:
            connection.execute(text('DELETE FROM records WHERE id=:id'), {'id':'synthetic-ai-immutable-'+kind})
