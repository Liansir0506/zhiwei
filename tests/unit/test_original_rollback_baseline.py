import copy
import hashlib

import numpy as np
import pytest

from qa_core.indexing.chunkers.base import Chunk
from scripts import build_original_rollback_baseline as baseline
from scripts.build_legacy_identity_review import content_key
from scripts.rebuild_kb import to_milvus_row


def fixture(kind='manual'):
    meta = {'doc_type': kind, 'tenant_id': 'default', 'visibility': 'public', 'allowed_roles': [],
        'role_scope': [], 'asset_id': 'synthetic-asset', 'source_system': 'synthetic-fixture',
        'source_id': 'synthetic-source', 'source_version': 'fixture-r1', 'status': 'active',
        'review_status': 'human_chat_approved', 'synthetic': True, 'license_status': 'synthetic',
        'source': 'fixture.md', 'equipment_type': 'UPF', 'network_tech': '5G'}
    if kind == 'faq': meta.update(question='Synthetic question', answer='Synthetic answer')
    row = to_milvus_row(Chunk('Synthetic baseline text', kind, meta), 'legacy-id', [1.] + [0.] * 1023, 'v4-foundation-r3')
    trace = {'status': 'exact_trace', 'chunk_id': row['id'], 'content_sha256': hashlib.sha256(content_key(row).encode()).hexdigest(),
             'scope_conflicts': [], 'location': {'ambiguous': False, 'file_sha256': 'synthetic-hash'}}
    return row, trace, meta


@pytest.mark.parametrize('kind', ['manual', 'faq', 'sop'])
def test_baseline_preserves_full_old_body_structure_and_exact_vector(kind):
    row, trace, meta = fixture(kind)
    before = copy.deepcopy(row)
    result = baseline.baseline_row(row, trace, meta)
    assert row == before and result['id'] != row['id'] and result['kb_version'] == baseline.BASELINE
    assert result['text'] == row['text'] and result['source'] == row['source']
    assert np.array_equal(result['dense'], row['dense'])
    if kind == 'faq': assert result['question'] == row['question'] and result['answer'] == row['answer']
    else: assert result['parent_id'] == row['parent_id'] and result['step_index'] == row['step_index']


@pytest.mark.parametrize('change', ['text', 'faq_answer', 'wrong_id', 'ambiguous', 'scope_conflict', 'unresolved', 'pending', 'tenant'])
def test_baseline_refuses_unverified_content_or_scope(change):
    row, trace, meta = fixture('faq')
    if change == 'text': row['text'] += 'unreviewed'
    elif change == 'faq_answer': row['answer'] += 'unreviewed'
    elif change == 'wrong_id': trace['chunk_id'] = 'other'
    elif change == 'ambiguous': trace['location']['ambiguous'] = True
    elif change == 'scope_conflict': trace['scope_conflicts'] = ['tenant_id']
    elif change == 'unresolved': trace['location'] = None
    elif change == 'pending': meta['review_status'] = 'pending'
    elif change == 'tenant': meta['tenant_id'] = 'foreign'
    with pytest.raises(ValueError): baseline.baseline_row(row, trace, meta)


@pytest.mark.parametrize('vector', [[0.] * 1024, [1.] * 3, [float('nan')] + [0.] * 1023, [float('inf')] + [0.] * 1023])
def test_no_invalid_or_renormalized_fake_vectors(vector):
    with pytest.raises(ValueError): baseline.check_vector(vector)


def test_successor_is_an_exact_copy_with_separate_version_identity():
    row, _, _ = fixture()
    row['kb_version'] = 'local-citation-r1'
    result = baseline.successor_row(row)
    assert {k:v for k,v in result.items() if k not in {'id', 'kb_version'}} == {k:v for k,v in row.items() if k not in {'id', 'kb_version'}}
    assert result['id'] != baseline.rekey(baseline.BASELINE, row['id'])
    with pytest.raises(ValueError): baseline.rekey('v4-foundation-r3', row['id'])
    row['kb_version'] = 'other'
    with pytest.raises(ValueError): baseline.successor_row(row)


@pytest.mark.parametrize('uri', ['http://localhost:19530', 'http://127.0.0.1:19530'])
def test_baseline_keeps_exact_previously_admitted_host_uri_without_aliasing(monkeypatch, uri):
    from qa_core.config.settings import Settings
    values={name:field.default for name,field in Settings.model_fields.items()}
    values.update(app_env='dev',mysql_dsn='mysql+pymysql://fixture:fixture@127.0.0.1:3306/rag_kb',milvus_uri=uri,
                  auth_required=True,generation_enabled=False,agent_enabled=False,llm_api_key='')
    admitted=Settings(_env_file=None,**values)
    monkeypatch.setattr(baseline,'build_profile',lambda:admitted)
    assert baseline.settings_for().milvus_uri==uri
