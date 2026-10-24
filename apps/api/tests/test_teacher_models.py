"""Separate model policies retain teacher identity and approved class knowledge."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from academy.db import Audit, Record, teacher_models_schema_id
from academy.migrate import migrate
from academy.teacher_models import model_policy_context, require_model_policy_snapshot
from conftest import login
from test_teacher_onboarding import create, approve, path, school_id, new_school
from test_lesson_tutoring import lesson_environment, ask


def endpoint(school, profile):
    return path(school, profile, 'models')


def connection(client, headers, school, models=None):
    response = client.post('/api/admin/connections', json={'school_id': school, 'name': 'Synthetic integrated models', 'endpoint': 'https://models.example.test/v1', 'models': models or ['economy', 'reasoning'], 'enabled': True, 'api_key': 'synthetic-model-key-never-live'}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def policy(model_connection, model='economy', **changes):
    return {'enabled': True, 'primary': {'connection_id': model_connection['id'], 'model': model}, 'lesson_enabled': True, 'homework_enabled': True, 'homework_max_output_tokens': 1500, **changes}


def save(client, headers, school, profile, model_policy, version=0):
    response = client.put(endpoint(school, profile['id']), json={'expected_version': version, 'policy': model_policy}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def activate(client, headers, school, profile, saved):
    response = client.post(endpoint(school, profile['id']) + '/approve', json={'expected_version': saved['version'], 'revision_id': saved['draft']['revision_id']}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def identity(app):
    return {'id': 'demo-student', 'tenant_id': app.state.settings.tenant_id, 'principal_kind': 'human'}


def test_model_swaps_and_drafts_preserve_exact_personality_class_corpus_and_profile_approval(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch)
    headers = login(client, 'demo-admin')
    integrated = connection(client, headers, env['school'])
    with app.state.database.factory() as db:
        preserved_rows = list(db.scalars(select(Record).where(Record.kind.in_(['teacher_profile', 'teacher_profile_course', 'teacher_profile_revision', 'course_corpus', 'corpus_approval']))))
        before = [(row.id, row.data) for row in preserved_rows]
    assert {row.kind for row in preserved_rows} == {'teacher_profile', 'teacher_profile_course', 'teacher_profile_revision', 'course_corpus', 'corpus_approval'}
    saved = save(client, headers, env['school'], env['profile'], policy(integrated))
    assert saved['draft_pending'] and saved['active'] is None
    with app.state.database.factory() as db:
        assert model_policy_context(db, identity(app), env['profile']['id'], env['school'], 'lesson') is None
    active = activate(client, headers, env['school'], env['profile'], saved)
    assert active['active_available'] and not active['draft_pending']
    with app.state.database.factory() as db:
        first = model_policy_context(db, identity(app), env['profile']['id'], env['school'], 'lesson')
        assert first['model'] == 'economy'
    second = save(client, headers, env['school'], env['profile'], policy(integrated, 'reasoning'), version=1)
    with app.state.database.factory() as db:
        assert model_policy_context(db, identity(app), env['profile']['id'], env['school'], 'lesson') == first
    activate(client, headers, env['school'], env['profile'], second)
    with app.state.database.factory() as db:
        assert model_policy_context(db, identity(app), env['profile']['id'], env['school'], 'lesson')['model'] == 'reasoning'
        assert [(row.id, row.data) for row in db.scalars(select(Record).where(Record.kind.in_(['teacher_profile', 'teacher_profile_course', 'teacher_profile_revision', 'course_corpus', 'corpus_approval'])))] == before
        with pytest.raises(HTTPException):
            require_model_policy_snapshot(db, identity(app), env['profile']['id'], env['school'], 'lesson', first)
        assert require_model_policy_snapshot(db, identity(app), env['profile']['id'], env['school'], 'lesson', first, current=False) == first
    serialized = str(active)
    assert 'key_cipher' not in serialized and 'credential_fingerprint' not in serialized and 'models.example' not in serialized and 'synthetic-model-key' not in serialized


def test_approved_teacher_execution_model_is_used_by_lesson_without_profile_reapproval(client, app, monkeypatch):
    env = lesson_environment(client, app, monkeypatch)
    headers = login(client, 'demo-admin')
    integrated = connection(client, headers, env['school'])
    activate(client, headers, env['school'], env['profile'], save(client, headers, env['school'], env['profile'], policy(integrated)))
    env['headers'] = login(client)
    turn = ask(client, env).json()
    assert turn['status'] == 'released'
    assert len(env['calls']) == 1 and env['calls'][0][2] == 'economy'
    with app.state.database.factory() as db:
        snapshot = db.get(Record, turn['id']).value['teacher_context_snapshot']['model_execution_snapshot']
        assert snapshot['selection_reason'] == 'approved_primary'
        assert snapshot['policy_sha256'] and snapshot['automatic_routing_enabled'] is False


@pytest.mark.parametrize('purpose', ['lesson', 'homework'])
def test_explicit_disabled_activity_blocks_while_overall_disabled_explicitly_restores_legacy(client, app, purpose):
    headers = login(client, 'demo-admin'); school = school_id(app)
    profile = approve(client, headers, school, create(client, headers, school, kind='ai'))
    integrated = connection(client, headers, school)
    active = activate(client, headers, school, profile, save(client, headers, school, profile, policy(integrated, **{purpose + '_enabled': False})))
    with app.state.database.factory() as db:
        with pytest.raises(HTTPException) as caught:
            model_policy_context(db, identity(app), profile['id'], school, purpose)
        assert caught.value.status_code == 403
    activate(client, headers, school, profile, save(client, headers, school, profile, policy(integrated, enabled=False), version=active['version']))
    with app.state.database.factory() as db:
        assert model_policy_context(db, identity(app), profile['id'], school, purpose) is None


def test_connection_revision_changes_revoke_current_and_historical_admission_without_fallback(client, app):
    headers = login(client, 'demo-admin'); school = school_id(app)
    profile = approve(client, headers, school, create(client, headers, school, kind='ai'))
    integrated = connection(client, headers, school)
    activate(client, headers, school, profile, save(client, headers, school, profile, policy(integrated)))
    with app.state.database.factory() as db:
        snapshot = model_policy_context(db, identity(app), profile['id'], school, 'homework')
    changed = client.put('/api/admin/connections/' + integrated['id'], json={**{key: integrated[key] for key in ['name','protocol','endpoint','models','version']}, 'enabled': False}, headers=headers)
    assert changed.status_code == 200, changed.text
    with app.state.database.factory() as db:
        for current in [True, False]:
            with pytest.raises(HTTPException):
                require_model_policy_snapshot(db, identity(app), profile['id'], school, 'homework', snapshot, current=current)
    # A revoked connection can always be explicitly taken out of use.
    activate(client, headers, school, profile, save(client, headers, school, profile, policy(integrated, enabled=False), version=1))


def test_model_policies_are_school_scoped_human_managed_ai_only_and_optimistic(client, app):
    headers = login(client, 'demo-admin'); school = school_id(app)
    profile = create(client, headers, school, kind='ai')
    integrated = connection(client, headers, school)
    saved = save(client, headers, school, profile, policy(integrated))
    assert client.put(endpoint(school, profile['id']), json={'expected_version': 0, 'policy': policy(integrated)}, headers=headers).status_code == 409
    assert client.post(endpoint(school, profile['id'])+'/approve', json={'expected_version':1,'revision_id':'another-revision'}, headers=headers).status_code == 409
    foreign = new_school(client, headers)
    foreign_connection = connection(client, headers, foreign)
    assert client.put(endpoint(school, profile['id']), json={'expected_version':1,'policy':policy(foreign_connection)}, headers=headers).status_code == 403
    assert client.get(endpoint(foreign, profile['id'])).status_code == 404
    human = create(client, headers, school, kind='human')
    assert client.get(endpoint(school, human['id'])).status_code == 422
    teacher = login(client, 'demo-instructor')
    assert client.get(endpoint(school, profile['id'])).status_code == 403
    assert client.put(endpoint(school, profile['id']), json={'expected_version':1,'policy':policy(integrated)}, headers=teacher).status_code == 403
    headers = login(client, 'demo-admin')
    assert client.delete(path(school,profile['id']),headers=headers).status_code == 200
    assert client.get(endpoint(school, profile['id'])).status_code == 409


def test_preview_is_bounded_deterministic_and_never_calls_candidate_or_claims_costs(client, app, monkeypatch):
    headers = login(client, 'demo-admin'); school = school_id(app)
    profile = create(client, headers, school, kind='ai'); integrated = connection(client, headers, school)
    routing = {'mode':'preview', 'candidates':[{'id':'deep','label':'Complex questions','connection_id':integrated['id'],'model':'reasoning','tasks':['reasoning']}], 'rules':[{'task':'reasoning','candidate_id':'deep'}], 'max_question_chars':3000}
    save(client, headers, school, profile, policy(integrated, routing=routing))
    calls = []
    monkeypatch.setattr(app.state, 'classify', lambda *args: calls.append(args))
    result = client.post(endpoint(school,profile['id'])+'/preview', json={'text':'Why do ecosystems change?'},headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()['task'] == 'reasoning'
    assert result.json()['executed']['model'] == 'economy' and result.json()['suggested']['model'] == 'reasoning'
    assert result.json()['no_provider_calls'] is True and result.json()['automatic_routing_enabled'] is False and result.json()['pricing_status'] == 'unknown'
    assert calls == []
    assert client.post(endpoint(school,profile['id'])+'/preview',json={'text':'x'*3001},headers=headers).status_code == 422
    invalid = deepcopy(routing); invalid['rules'][0]['candidate_id'] = 'missing'
    assert client.put(endpoint(school,profile['id']),json={'expected_version':1,'policy':policy(integrated,routing=invalid)},headers=headers).status_code == 422


def test_new_assignment_image_permission_requires_explicit_new_policy_attestation(client, app):
    headers=login(client,'demo-admin');school=school_id(app)
    profile=create(client,headers,school,kind='ai');integrated=connection(client,headers,school)
    body=policy(integrated,lesson_policy={'allow_lesson_curation':True,'allow_assignment_images':True})
    assert client.put(endpoint(school,profile['id']),json={'expected_version':0,'policy':body},headers=headers).status_code == 422
    saved=save(client,headers,school,profile,{**body,'assignment_images_reviewed':True})
    assert saved['draft']['policy']['assignment_images_reviewed'] is True
    assert saved['homework_images'] == 'school_models_only'


def test_schema12_requires_explicit_migration_and_model_approvals_are_immutable_at_database_layer(client, app):
    headers=login(client,'demo-admin');school=school_id(app)
    profile=create(client,headers,school,kind='ai');integrated=connection(client,headers,school)
    active=activate(client,headers,school,profile,save(client,headers,school,profile,policy(integrated)))
    for record_id in [active['active']['revision_id'], active['active']['approval_id']]:
        for statement in ['UPDATE records SET data=\'{}\' WHERE id=:id','DELETE FROM records WHERE id=:id']:
            with pytest.raises(DatabaseError):
                with app.state.database.engine.begin() as db:
                    db.execute(text(statement),{'id':record_id})
    with app.state.database.factory() as db:
        before=[(row.id,row.payload,row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
    with app.state.database.write() as db:
        db.delete(db.get(Record,teacher_models_schema_id(app.state.settings.tenant_id)))
    assert client.get('/api/ready').status_code == 503
    migrate(app.state.settings);migrate(app.state.settings)
    assert client.get('/api/ready').status_code == 200
    with app.state.database.factory() as db:
        after=[(row.id,row.payload,row.hash) for row in db.scalars(select(Audit).order_by(Audit.sequence))]
        assert after[:len(before)] == before and len(after)==len(before)+1


def test_policy_connection_change_during_lesson_withholds_without_retry_or_legacy_fallback(client, app, monkeypatch):
    env=lesson_environment(client,app,monkeypatch)
    headers=login(client,'demo-admin');integrated=connection(client,headers,env['school'])
    activate(client,headers,env['school'],env['profile'],save(client,headers,env['school'],env['profile'],policy(integrated)))
    classify=app.state.classify
    def changing(*args):
        result=classify(*args)
        with app.state.database.write() as db:
            row=db.get(Record,integrated['id'])
            row.set({**row.value,'version':row.value['version']+1})
        return result
    monkeypatch.setattr(app.state,'classify',changing)
    env['headers']=login(client)
    result=ask(client,env).json()
    assert result['status']=='withheld' and result['approved_text'] is None
    assert len(env['calls'])==1 and env['calls'][0][2]=='economy'
    assert ask(client,env).json()==result and len(env['calls'])==1
    assert ask(client,env,key='changed-connection-new-turn').json()['status']=='blocked'
    assert len(env['calls'])==1


def test_preview_only_candidate_revocation_does_not_hide_primary_model_history(client,app):
    headers=login(client,'demo-admin');school=school_id(app)
    profile=approve(client,headers,school,create(client,headers,school,kind='ai'))
    primary=connection(client,headers,school);candidate=connection(client,headers,school)
    routing={'mode':'preview','candidates':[{'id':'candidate','label':'Unused preview','connection_id':candidate['id'],'model':'reasoning','tasks':['reasoning']}],'rules':[{'task':'reasoning','candidate_id':'candidate'}]}
    activate(client,headers,school,profile,save(client,headers,school,profile,policy(primary,routing=routing)))
    with app.state.database.factory() as db:
        snapshot=model_policy_context(db,identity(app),profile['id'],school,'homework')
    with app.state.database.write() as db:
        row=db.get(Record,candidate['id']);row.set({**row.value,'enabled':False})
    with app.state.database.factory() as db:
        with pytest.raises(HTTPException):
            require_model_policy_snapshot(db,identity(app),profile['id'],school,'homework',snapshot)
        assert require_model_policy_snapshot(db,identity(app),profile['id'],school,'homework',snapshot,current=False)==snapshot


def test_agent_cannot_manage_model_policy_even_with_admin_role_summary(client,app,monkeypatch):
    headers=login(client,'demo-admin');school=school_id(app)
    profile=create(client,headers,school,kind='ai');integrated=connection(client,headers,school)
    spoofed={'id':'agent:synthetic','tenant_id':app.state.settings.tenant_id,'role':'admin','principal_kind':'agent','school_id':school,'permissions':['academy-use','academy-teach'],'csrf_token':''}
    monkeypatch.setattr(app.state.auth,'actor',lambda request:dict(spoofed))
    assert client.get(endpoint(school,profile['id'])).status_code==403
    assert client.put(endpoint(school,profile['id']),json={'expected_version':0,'policy':policy(integrated)}).status_code==403
