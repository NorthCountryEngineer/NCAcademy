"""Independent adversarial regressions for private multimodal lesson release."""
import base64
import json
import pytest
from academy import curated_lesson_api
from academy.db import Record, canonical
from conftest import login
from test_curated_lessons import environment, curate, photo, selection, stored
from test_teacher_onboarding import assign, approve

@pytest.mark.parametrize('attack', ['reflection_list','reflection_object','document_list','visual_list','sections_object','sections_boolean','duplicate_json_key','unknown_section','usage_boolean','usage_negative','usage_sum','output_overcap','total_overreservation'])
def test_malformed_selection_and_usage_are_terminal_withheld_not_server_errors(client, app, monkeypatch, attack):
    env=environment(client,app,monkeypatch)
    def malicious(*args):
        env['calls'].append(args)
        document=json.loads(args[3][1]['content'])['documents'][0]['id']
        result=selection(document)
        payload=json.loads(result['choices'][0]['message']['content'])
        section=payload['sections'][0]
        if attack=='reflection_list':section['reflection_id']=[]
        elif attack=='reflection_object':section['reflection_id']={}
        elif attack=='document_list':section['document_id']=[]
        elif attack=='visual_list':section['visual_id']=[]
        elif attack=='sections_object':payload['sections']={}
        elif attack=='sections_boolean':payload['sections']=True
        elif attack=='unknown_section':payload['sections'].append({'document_id':'private-key','visual_id':'none','reflection_id':'explain'})
        elif attack=='usage_boolean':result['usage']['total_tokens']=True
        elif attack=='usage_negative':result['usage']={'prompt_tokens':-1,'completion_tokens':40,'total_tokens':39}
        elif attack=='usage_sum':result['usage']['total_tokens']=131
        elif attack=='output_overcap':result['usage']={'prompt_tokens':90,'completion_tokens':701,'total_tokens':791}
        elif attack=='total_overreservation':result['usage']={'prompt_tokens':999999,'completion_tokens':40,'total_tokens':1000039}
        result['choices'][0]['message']['content']=json.dumps(payload) if attack!='duplicate_json_key' else '{"sections":[],"sections":[]}'
        return result
    monkeypatch.setattr(app.state,'classify',malicious)
    response=curate(client,env)
    assert response.status_code==200,response.text
    turn=response.json()
    assert turn['status']=='withheld' and turn['lesson'] is None
    assert curate(client,env).json()==turn and len(env['calls'])==1

@pytest.mark.parametrize('attack',['bytes','metadata','mime','invalid_cipher'])
def test_stored_attachment_integrity_blocks_private_delivery(client,app,monkeypatch,attack):
    env=environment(client,app,monkeypatch)
    turn=curate(client,env,images=[{'name':'private.png','data_url':photo()}],image_use_confirmed=True).json()
    value=stored(app,turn);cipher=value['images_cipher'];original=app.state.auth.unseal
    def tampered(sealed):
        result=original(sealed)
        if sealed==cipher:
            if attack=='bytes':result['images'][0]['data_base64']=base64.b64encode(b'ALTERED_PRIVATE_BYTES').decode()
            elif attack=='metadata':result['images'][0]['width']+=1
            elif attack=='mime':result['images'][0]['media_type']='image/png'
            else:return original('INVALID_REPLACED_CIPHER')
        return result
    monkeypatch.setattr(app.state.auth,'unseal',tampered)
    result=client.get(turn['images'][0]['url'])
    assert result.status_code in {401,409},result.text
    assert result.headers['content-type']=='application/json'
    assert cipher not in result.text and 'ALTERED_PRIVATE_BYTES' not in result.text

@pytest.mark.parametrize('bound',[False,True])
def test_pre_send_core_or_managed_teacher_authority_cannot_leak_revoked_image_input(client,app,monkeypatch,bound):
    env=environment(client,app,monkeypatch,bound=bound)
    original=curated_lesson_api.authorize_lesson_teacher
    def revoke(*args):
        proof=original(*args)
        with app.state.database.write() as db:
            connection=db.get(Record,env['connection']['id'])
            connection.set({**connection.value,'enabled':False})
        return proof
    monkeypatch.setattr(curated_lesson_api,'authorize_lesson_teacher',revoke)
    result=curate(client,env,images=[{'name':'task.png','data_url':photo()}],image_use_confirmed=True).json()
    assert result['status']=='withheld' and result['lesson'] is None and env['calls']==[]
    assert 'data:image' not in canonical(result)


def test_current_staff_cannot_receive_attachment_cipher_in_any_audit_export(client,app,monkeypatch):
    env=environment(client,app,monkeypatch)
    turn=curate(client,env,images=[{'name':'private.png','data_url':photo()}],image_use_confirmed=True).json()
    value=stored(app,turn)
    login(client,'demo-admin')
    for path in [f"/api/audit/sessions/{turn['session_id']}",f"/api/audit/sessions/{turn['session_id']}/export",'/api/admin/audit/export',f"/api/schools/{env['school']}/audit/export"]:
        result=client.get(path)
        assert result.status_code==200,(path,result.text)
        assert value['images_cipher'] not in result.text and 'images_cipher' not in result.text and 'data:image' not in result.text


def test_teacher_only_corpus_material_is_absent_from_curated_prompt_and_lesson(client,app,monkeypatch):
    env=environment(client,app,monkeypatch)
    admin=login(client,'demo-admin')
    resource=client.post(f"/api/schools/{env['school']}/resources",json={'name':'Private teaching key','content':'Carbon PRIVATE_TEACHER_ANSWER_KEY carbon in ecosystems.','audience':'teachers','course_id':env['course']['id']},headers=admin)
    assert resource.status_code==201,resource.text
    descriptor=client.post(f"/api/courses/{env['course']['id']}/corpus/refresh",headers=admin).json()
    reviewed=client.post(f"/api/courses/{env['course']['id']}/corpus/approve",json={'corpus_id':descriptor['id'],'snapshot_sha256':descriptor['snapshot_sha256']},headers=admin)
    assert reviewed.status_code==200,reviewed.text
    env['profile']=approve(client,admin,env['school'],assign(client,admin,env['school'],env['profile'],[env['course']['id']]))
    env['headers']=login(client,'demo-student')
    response=curate(client,env)
    assert response.status_code==200,response.text
    assert response.json()['status']=='released',response.text
    assert 'PRIVATE_TEACHER_ANSWER_KEY' not in canonical(env['calls'][0][3])
    assert 'PRIVATE_TEACHER_ANSWER_KEY' not in response.text


def test_approved_new_york_photo_lesson_contains_source_bound_visual_snapshot(client,app,monkeypatch):
    env=environment(client,app,monkeypatch)
    admin=login(client,'demo-admin')
    response=client.put(f"/api/courses/{env['course']['id']}",json={'title':'Synthetic New York environmental science','curriculum':{'state':'US-NY','subject':'environmental_science','grades':[10],'academic_year':'2026-2027'}},headers=admin)
    assert response.status_code==200,response.text
    descriptor=client.post(f"/api/courses/{env['course']['id']}/corpus/refresh",headers=admin).json()
    reviewed=client.post(f"/api/courses/{env['course']['id']}/corpus/approve",json={'corpus_id':descriptor['id'],'snapshot_sha256':descriptor['snapshot_sha256']},headers=admin)
    assert reviewed.status_code==200,reviewed.text
    env['profile']=approve(client,admin,env['school'],assign(client,admin,env['school'],env['profile'],[env['course']['id']]))
    env['headers']=login(client,'demo-student')
    response=curate(client,env,message='',images=[{'name':'worksheet.png','data_url':photo()}],image_use_confirmed=True)
    assert response.status_code==200,response.text
    turn=response.json();assert turn['status']=='released',turn
    section=turn['lesson']['sections'][0]
    assert section['document_id']=='reference:carbon-reservoirs-and-climate'
    assert section['visual']['id']=='carbon-reservoirs-v1'
    assert section['citation']['source_url']=='https://science.nasa.gov/earth/earth-observatory/the-carbon-cycle/'
    snapshot=stored(app,turn)['media_snapshot']
    assert snapshot['documents'][section['document_id']]==[section['visual']]
    assert len(env['calls'])==1
