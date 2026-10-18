import copy
import hashlib
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('verify_audit', Path(__file__).parents[1] / 'verify_audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    previous = '0' * 64
    events = []
    for sequence in (1, 2):
        row = dict(id=str(sequence), tenant_id='a', sequence=sequence, session_id='s', actor_id='u', event_type='test', payload={'work':'student attempt'}, previous_hash=previous, created_at='2026-10-17T14:00:00+00:00')
        previous = hashlib.sha256(module.canonical(row).encode()).hexdigest()
        events.append({**row, 'hash': previous})
    return {'tenant_id':'a', 'events':events, 'head':{'sequence':2,'digest':previous}}


def test_matches_independent_checkpoint():
    data = fixture()
    result = module.verify(data, {'tenant_id':'a', **data['head']})
    assert result['independently_anchored']


def test_modification_detected():
    data = fixture()
    data['events'][0]['payload']['work'] = 'altered'
    with pytest.raises(ValueError):
        module.verify(data)


def test_truncation_with_recomputed_local_head_rejected_by_independent_head():
    data = fixture()
    anchor = {'tenant_id':'a', **data['head']}
    data['events'].pop()
    data['head'] = {'sequence':1, 'digest':data['events'][0]['hash']}
    assert not module.verify(data)['independently_anchored']
    with pytest.raises(ValueError):
        module.verify(data, anchor)


def test_cross_tenant_checkpoint_rejected():
    data = fixture()
    with pytest.raises(ValueError):
        module.verify(data, {'tenant_id':'other', **data['head']})
