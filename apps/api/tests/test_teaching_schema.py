"""Schema-4 upgrades preserve old evidence and protect new teaching snapshots."""
from dataclasses import replace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from academy.config import Settings
from academy.db import Audit, AuditHead, Base, Database, Record, audit, canonical, teaching_schema_id, external_review_schema_id
from academy.main import create_app
from academy.migrate import migrate
from academy.permissions import migrate_school_scopes


PROTECTED = ('submission', 'teacher_profile_revision', 'course_corpus', 'corpus_approval', 'external_resource_snapshot', 'external_audit_agent_revision', 'external_review_result', 'external_publication')


def test_upgrade_preserves_legacy_records_and_audit_prefix_and_is_idempotent(tmp_path):
    settings = Settings('sqlite:///' + str(tmp_path / 'legacy.db'), 'local', 'http://localhost:8090', 'synthetic-test-secret', False, False)
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    with database.write() as db:
        db.add(AuditHead(tenant_id=settings.tenant_id, sequence=0, digest='0' * 64))
        db.add(Record(id='legacy-student', tenant_id=settings.tenant_id, kind='user', data=canonical({'name':'Synthetic student','role':'student'})))
        db.add(Record(id='legacy-submission', tenant_id=settings.tenant_id, kind='submission', parent_id='legacy-assignment', owner_id='legacy-student', data=canonical({'content':'Original work bytes \u00e9','created_at':'2025-01-02'})))
        db.flush()
        audit(db, settings.tenant_id, 'legacy-student', 'student.submitted', {'content':'Original work bytes \u00e9'})
        migrate_school_scopes(db, settings)
    with database.factory() as db:
        before_records = {r.id: (r.kind,r.parent_id,r.owner_id,r.data,r.created_at) for r in db.scalars(select(Record))}
        before_events = [(r.id,r.sequence,r.payload,r.hash,r.previous_hash,r.created_at) for r in db.scalars(select(Audit).order_by(Audit.sequence))]
    migrate(settings)
    with database.factory() as db:
        assert all((db.get(Record,k).kind,db.get(Record,k).parent_id,db.get(Record,k).owner_id,db.get(Record,k).data,db.get(Record,k).created_at)==v for k,v in before_records.items())
        after = [(r.id,r.sequence,r.payload,r.hash,r.previous_hash,r.created_at) for r in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert after[:len(before_events)] == before_events
        assert len(after) == len(before_events)+2
        marker = db.get(Record,teaching_schema_id(settings.tenant_id))
        assert marker.value == {'version':3,'immutable_teaching_snapshots':True}
        assert db.get(Record,external_review_schema_id(settings.tenant_id)).value == {'version':4,'immutable_external_reviews':True}
    migrate(settings)
    with database.factory() as db:
        assert [(r.id,r.sequence,r.payload,r.hash,r.previous_hash,r.created_at) for r in db.scalars(select(Audit).order_by(Audit.sequence))] == after
    database.engine.dispose()


@pytest.mark.parametrize('kind', PROTECTED)
@pytest.mark.parametrize('operation', ['update','delete','change_kind'])
def test_sql_snapshot_mutations_are_denied(app, kind, operation):
    database=app.state.database
    with database.write() as db:
        db.add(Record(id='protected', tenant_id=app.state.settings.tenant_id, kind=kind, data='{"original":true}'))
    statement={'update':"UPDATE records SET data = '{}' WHERE id = 'protected'",'delete':"DELETE FROM records WHERE id = 'protected'",'change_kind':"UPDATE records SET kind = 'draft' WHERE id = 'protected'"}[operation]
    with pytest.raises(DatabaseError,match='immutable'):
        with database.write() as db:
            db.execute(text(statement))
    with database.factory() as db:
        row=db.get(Record,'protected')
        assert row.kind==kind and row.data=='{"original":true}'


def test_orm_cannot_disguise_immutable_revision_as_mutable(app):
    with app.state.database.write() as db:
        db.add(Record(id='protected-revision', tenant_id=app.state.settings.tenant_id, kind='teacher_profile_revision', data='{}'))
    with pytest.raises(ValueError,match='immutable'):
        with app.state.database.write() as db:
            db.get(Record,'protected-revision').kind='draft'


def test_new_api_readiness_requires_teaching_schema_marker(app, client):
    assert client.get('/api/ready').status_code==200
    with app.state.database.write() as db:
        db.delete(db.get(Record,teaching_schema_id(app.state.settings.tenant_id)))
    result=client.get('/api/ready')
    assert result.status_code==503 and result.json()['status']=='migration_required'
    migrate(app.state.settings)
    assert client.get('/api/ready').status_code==200


@pytest.mark.parametrize('kind', ['turn','lesson_turn','external_review_job'])
def test_tutor_send_marker_and_completion_allowed_then_terminal_turn_is_sealed(app,kind):
    database=app.state.database
    with database.write() as db:
        db.add(Record(id='pending-turn',tenant_id=app.state.settings.tenant_id,kind=kind,data=canonical({'status':'pending','message':'Synthetic question'})))
    with database.write() as db:
        row=db.get(Record,'pending-turn');row.set({**row.value,'send_marker':'synthetic-marker'})
    with database.write() as db:
        row=db.get(Record,'pending-turn');row.set({**row.value,'status':'released','approved_text':'Approved source excerpt'})
    for statement in ("UPDATE records SET data='{}' WHERE id='pending-turn'", "UPDATE records SET kind='draft' WHERE id='pending-turn'", "DELETE FROM records WHERE id='pending-turn'"):
        with pytest.raises(DatabaseError,match='immutable'):
            with database.write() as db:db.execute(text(statement))
    with pytest.raises(ValueError,match='immutable'):
        with database.write() as db:db.get(Record,'pending-turn').set({'status':'pending'})


def test_schema4_upgrade_keeps_schema3_marker_and_requires_owner_migration(app, client):
    database=app.state.database
    with database.factory() as db:
        teaching=db.get(Record,teaching_schema_id(app.state.settings.tenant_id)).data
        before=[(row.id,row.payload,row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
    with database.write() as db:
        db.delete(db.get(Record,external_review_schema_id(app.state.settings.tenant_id)))
    assert client.get('/api/ready').status_code == 503
    migrate(app.state.settings)
    with database.factory() as db:
        assert db.get(Record,teaching_schema_id(app.state.settings.tenant_id)).data == teaching
        after=[(row.id,row.payload,row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert after[:len(before)] == before
        assert len(after) == len(before)+1
    assert client.get('/api/ready').status_code == 200
    from conftest import login
    login(client,'demo-admin')
    assert client.get('/api/admin/settings').json()['schema_version'] == 4
