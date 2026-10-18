"""External approvals gate actual lesson admission, release and student history."""
import json

import pytest
from sqlalchemy import select

from academy import curated_lesson_api, lesson_api
from academy.db import Audit, Record
from conftest import login
from test_lesson_tutoring import lesson_environment, ask, selection as qa_selection
from test_curated_lessons import curate, selection as curated_selection
from test_teacher_onboarding import approve as approve_teacher
from test_external_review import external_environment, submit, review, approve as approve_source


def refresh_teacher(client, env):
    admin=login(client,'demo-admin')
    descriptor=client.post(f"/api/courses/{env['course']['id']}/corpus/refresh",headers=admin).json()
    result=client.post(f"/api/courses/{env['course']['id']}/corpus/approve",json={'corpus_id':descriptor['id'],'snapshot_sha256':descriptor['snapshot_sha256']},headers=admin)
    assert result.status_code==200,result.text
    env['profile']=approve_teacher(client,admin,env['school'],env['profile'])
    env['headers']=login(client,'demo-student')


def setup(client,app,monkeypatch,video=False):
    env=lesson_environment(client,app,monkeypatch,allow_lesson_curation=True,max_total_tokens=400000)
    audit_env=external_environment(client,app,monkeypatch)
    audit_env['course']=env['course']['id']
    imported=client.post(f"/api/courses/{audit_env['course']}/external-resources/import-corpus",headers=audit_env['admin'])
    assert imported.status_code==200,imported.text
    resources=imported.json()['resources']
    if video:
        resources.append(submit(client,audit_env,'youtube',teaching_notes='Carbon moves through ecosystems as plants absorb carbon dioxide and decomposition returns carbon to the soil.'))
    for index,resource in enumerate(resources):
        job=review(client,audit_env,resource,key=f'external-lesson-review-{index:04d}')
        assert job['status']=='completed',job
        result=approve_source(client,audit_env,resource,job,watched_video=resource['kind']=='youtube',transcript_complete=resource['kind']=='youtube')
        assert result.status_code==200,result.text
    refresh_teacher(client,env)
    return env,audit_env,resources


def revoke(app,resource):
    with app.state.database.write() as db:
        row=db.get(Record,resource['id'])
        row.set({**row.value,'revoked':True})


def test_assignment_blocks_old_unreviewed_corpus_before_any_tutor_send(client,app,monkeypatch):
    env=lesson_environment(client,app,monkeypatch)
    auditor=external_environment(client,app,monkeypatch)
    env['headers']=login(client,'demo-student')
    turn=ask(client,env).json()
    assert turn['status']=='blocked' and turn['approved_text'] is None
    assert auditor['calls']==[]


@pytest.mark.parametrize('activity',['qa','curated'])
@pytest.mark.parametrize('stage',['before_send','before_release'])
def test_revoked_review_withholds_lesson_across_send_release_boundary(client,app,monkeypatch,activity,stage):
    env,auditor,resources=setup(client,app,monkeypatch)
    target=resources[0]
    calls=[]
    def selector(*args):
        calls.append(args)
        if stage=='before_release':
            revoke(app,target)
        document=json.loads(args[3][1]['content'])['documents'][0]
        return qa_selection(document['id']) if activity=='qa' else curated_selection(document['id'])
    monkeypatch.setattr(app.state,'classify',selector)
    if stage=='before_send':
        module=lesson_api if activity=='qa' else curated_lesson_api
        original=module.authorize_lesson_teacher
        def authorization(*args):
            revoke(app,target)
            return original(*args)
        monkeypatch.setattr(module,'authorize_lesson_teacher',authorization)
    turn=(ask(client,env) if activity=='qa' else curate(client,env)).json()
    assert turn['status']=='withheld',turn
    assert turn.get('approved_text') is None and turn.get('lesson') is None and turn['citations']==[]
    assert len(calls)==(1 if stage=='before_release' else 0)


@pytest.mark.parametrize('activity',['qa','curated'])
def test_history_and_idempotent_replay_withdraw_revoked_sources_but_reviewer_retains_evidence(client,app,monkeypatch,activity):
    env,auditor,resources=setup(client,app,monkeypatch)
    resource=next(item for item in resources if item['origin_document_id']=='resource:'+env['resource']['id'])
    document_id=resource['origin_document_id']
    monkeypatch.setattr(app.state,'classify',lambda *_:qa_selection(document_id) if activity=='qa' else curated_selection(document_id))
    request=lambda:ask(client,env) if activity=='qa' else curate(client,env)
    first=request().json()
    assert first['status']=='released',first
    revoke(app,resource)
    second=request().json()
    assert second['status']=='withheld' and not second.get('approved_text') and not second.get('lesson') and not second['citations']
    route='teacher-tutor' if activity=='qa' else 'curated-lessons'
    assert client.get(f"/api/courses/{env['course']['id']}/{route}").json()==[second]
    if activity=='curated':
        assert client.post(f"/api/courses/{env['course']['id']}/curated-lessons/{first['id']}/progress",json={'section_id':'section-1','event':'viewed'},headers=env['headers']).status_code==409
    with app.state.database.factory() as db:
        stored=db.get(Record,first['id']).value
        assert stored['status']=='released' and stored['citations']==first['citations']
        assert any(event.event_type.endswith('.released') for event in db.scalars(select(Audit).where(Audit.session_id==first['session_id'])))
    login(client,'demo-admin')
    replay=client.get(f"/api/audit/sessions/{first['session_id']}")
    assert replay.status_code==200 and document_id in replay.text


def test_curated_video_uses_reviewed_notes_and_reference_never_model_url(client,app,monkeypatch):
    env,auditor,resources=setup(client,app,monkeypatch,video=True)
    video=next(item for item in resources if item['kind']=='youtube')
    monkeypatch.setattr(app.state,'classify',lambda *_:curated_selection('external:'+video['id']))
    turn=curate(client,env).json()
    assert turn['status']=='released',turn
    section=turn['lesson']['sections'][0]
    assert section['video']=={'resource_id':video['id'],'course_id':env['course']['id'],'title':video['title']}
    assert section['text']==video['teaching_notes']
    assert section['citation']['external_review']['snapshot_sha256']==video['snapshot_sha256']
    assert 'embed/' not in json.dumps(turn)
    loaded=client.post(f"/api/external-resources/{video['id']}/playback",headers=env['headers'])
    assert loaded.status_code==200 and loaded.json()['embed_url'].startswith('https://www.youtube-nocookie.com/embed/')
    revoke(app,video)
    assert client.post(f"/api/external-resources/{video['id']}/playback",headers=env['headers']).status_code==404
    assert client.get(f"/api/courses/{env['course']['id']}/curated-lessons").json()[0]['lesson'] is None
