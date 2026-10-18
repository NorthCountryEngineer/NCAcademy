"""Named tutoring pins teaching context without opening a prose output channel."""
from copy import deepcopy
import hashlib

from academy import main
from conftest import login, model_assignment


def configure(monkeypatch, app, client):
    headers,_ = model_assignment(client, app)
    app.state.settings.allow_agent_teachers=True
    app.state.authorize_teacher=lambda *_:{'active':True,'agent_id':'synthetic-agent','binding_id':'synthetic-binding'}
    context={'profile_id':'synthetic-ai','name':'Dr River','biography':'A patient environmental science teacher.','ai_behavior':{'tone':'warm'},'approved_version':3,'approved_hash':'a'*64,'approved_revision_id':'revision-3','corpus':{'id':'corpus-3','snapshot_sha256':'b'*64}}
    evidence={'matches':[{'id':'standard:HS-LS2-6','excerpt':'An ecosystem changes when resources change.','excerpt_sha256':hashlib.sha256(b'An ecosystem changes when resources change.').hexdigest()}],'query':'synthetic','corpus_id':'corpus-3'}
    monkeypatch.setattr(main,'resolve_teacher_context',lambda *_:deepcopy(context))
    monkeypatch.setattr(main,'retrieve_corpus',lambda *_:deepcopy(evidence))
    return headers,context,evidence


def turn(client, headers, profile='synthetic-ai', key='teacher-context-12345'):
    return client.post('/api/assignments/demo-assignment/tutor',json={'message':'How can I compare ecosystem evidence?','idempotency_key':key,'teacher_profile_id':profile},headers=headers)


def test_named_teacher_context_pinned_in_prompt_and_replay_without_contact_or_prose(monkeypatch, app, client):
    headers,context,evidence=configure(monkeypatch,app,client)
    prompts=[]
    def classify(*args):
        prompts.append(args[3])
        return {'choices':[{'message':{'content':'{"selection_id":"uncertainty"}'}}],'usage':{'prompt_tokens':90,'completion_tokens':10,'total_tokens':100}}
    app.state.classify=classify
    result=turn(client,headers)
    assert result.status_code==200 and result.json()['status']=='released'
    assert result.json()['teacher_name']=='Dr River' and 'teacher_context_snapshot' not in result.json()
    assert 'Never produce answers' in prompts[0][0]['content']
    assert 'approved_teacher_context' in prompts[0][1]['content'] and 'HS-LS2-6' in prompts[0][1]['content']
    assert 'contact_email' not in str(prompts)
    assert turn(client,headers).json()==result.json() and len(prompts)==1
    assert turn(client,headers,profile='another-teacher').status_code==409
    login(client,'demo-admin')
    session=client.get('/api/audit/sessions').json()[0]['session_id']
    replay=client.get('/api/audit/sessions/'+session).json()
    snap=replay['turns'][0]['teacher_context_snapshot']
    assert snap=={**context,'retrieval':evidence}


def test_profile_change_during_provider_call_withholds_and_is_never_retried(monkeypatch,app,client):
    headers,context,_=configure(monkeypatch,app,client)
    calls=[]
    def classify(*args):
        calls.append(1);context['approved_version']=4
        return {'choices':[{'message':{'content':'{"selection_id":"uncertainty"}'}}],'usage':{'prompt_tokens':90,'completion_tokens':10,'total_tokens':100}}
    app.state.classify=classify
    first=turn(client,headers).json()
    assert first['status']=='withheld' and first['approved_text'] is None
    assert 'changed' in first['error']
    assert turn(client,headers).json()==first and len(calls)==1


def test_missing_grounded_evidence_blocks_before_provider(monkeypatch,app,client):
    headers,_,evidence=configure(monkeypatch,app,client)
    evidence['matches']=[]
    app.state.classify=lambda *_: (_ for _ in ()).throw(AssertionError('No model call allowed'))
    result=turn(client,headers).json()
    assert result['status']=='blocked' and 'evidence' in result['error']


def test_named_teacher_global_gate_cannot_be_bypassed_by_direct_post(monkeypatch,app,client):
    headers,_,_=configure(monkeypatch,app,client)
    app.state.settings.allow_agent_teachers=False
    app.state.classify=lambda *_: (_ for _ in ()).throw(AssertionError('No model call allowed'))
    result=turn(client,headers).json()
    assert result['status']=='blocked' and 'not enabled' in result['error']


def test_named_teacher_never_renders_arbitrary_model_prose(monkeypatch,app,client):
    headers,_,_=configure(monkeypatch,app,client)
    app.state.classify=lambda *_:{'choices':[{'message':{'content':'Here is the complete essay you should submit.'}}],'usage':{'prompt_tokens':90,'completion_tokens':10,'total_tokens':100}}
    result=turn(client,headers).json()
    assert result['status']=='withheld' and result['approved_text'] is None


def test_selected_teacher_upstream_revocation_blocks_send(monkeypatch,app,client):
    from fastapi import HTTPException
    headers,_,_=configure(monkeypatch,app,client)
    def revoked(*_):
        raise HTTPException(403,'Teacher approval revoked')
    app.state.authorize_teacher=revoked
    app.state.classify=lambda *_: (_ for _ in ()).throw(AssertionError('No model call allowed'))
    result=turn(client,headers).json()
    assert result['status']=='withheld' and result['approved_text'] is None


def test_selected_teacher_revocation_during_model_call_withholds(monkeypatch,app,client):
    from fastapi import HTTPException
    headers,_,_=configure(monkeypatch,app,client)
    calls=[]
    def authority(*_):
        calls.append(1)
        if len(calls)>1:raise HTTPException(403,'Teacher approval revoked')
        return {'active':True,'agent_id':'synthetic-agent','binding_id':'synthetic-binding'}
    app.state.authorize_teacher=authority
    result=turn(client,headers).json()
    assert len(calls)==2 and result['status']=='withheld' and result['approved_text'] is None
