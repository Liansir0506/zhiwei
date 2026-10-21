"""SOP 标注复核边界：修改候选不改历史、权限缺口阻止等价来源批准。"""
import copy
from types import SimpleNamespace

import pytest

from scripts.calibrate_eval_set import SCOPE_FIELDS, audit, canonical_body, digest
from scripts.sop_label_review import STEP_ACTIONS, check_test_index_visibility, review_sop_candidate


def fixture():
    scope = dict(zip(SCOPE_FIELDS, ['default', 'public', 'public', [], [], 'internal', 'active',
                                  'r3', '2026-09-19T00:00:00+00:00', '2028-09-19T00:00:00+00:00']))
    catalog = {}
    for name in ('sop_operation_001.md', 'sop_operation_021.md'):
        body = '适用：AMF/5G；模拟告警：ALM-AMF-REG。\n\n' + '\n\n'.join(
            f'步骤 {n}：{action}。针对注册信令拥塞记录对象、版本、时间和前后值；证据不足时停止并转人工复核。'
            for n, action in enumerate(STEP_ACTIONS, 1))
        text = f'# {name}\n' + body
        h = digest(canonical_body(text).encode())
        catalog[name] = {'source': name, 'file': name, 'file_sha256': digest(text.encode()), 'body_sha256': h,
                         'body_family': 'body:' + h, 'topics': ['ALM-AMF-REG'], 'text': text, 'row': None,
                         'scope_snapshot': copy.deepcopy(scope), 'authorization_verified': False,
                         'location': {'kind': 'markdown', 'line_start': 1}}
    old = {'kb_version': 'r3', 'cases': [{'id': 'R01', 'query': 'ALM-AMF-REG 标准流程有哪些步骤？',
            'category': 'retrieval', 'template_id': 'sop_by_alarm', 'split': 'dev', 'expect_route': 'retrieval',
            'expect_hit': 'sop_operation_001.md', 'hard_negatives': ['sop_operation_021.md'], 'expect_reject': False}]}
    _, candidate, _ = audit(old, catalog, {'meta.json': 'hash'}, 'hash')
    decisions = {'reviewer_kind': 'ai_agent', 'human_approved': False, 'origin_dataset_sha256': 'hash',
                 'rationale': '完整流程十步，副本不作内容相反负例', 'cases': [
                   {'id': 'R01', 'query': old['cases'][0]['query'], 'source': 'sop_operation_001.md',
                    'alarm_code': 'ALM-AMF-REG', 'equipment_type': 'AMF', 'condition': '注册信令拥塞',
                    'body_family_size': 2, 'remove_negatives': ['sop_operation_021.md']}]}
    return candidate, catalog, decisions


AS_OF = '2026-09-26T13:00:00+00:00'


def test_review_changes_only_proposal_preserves_legacy_and_blocks_aliases():
    candidate, catalog, decisions = fixture()
    before = copy.deepcopy(candidate)
    result, report = review_sop_candidate(candidate, catalog, decisions, AS_OF)
    c = result['review_cases'][0]
    assert candidate == before
    assert c['legacy_labels'] == before['review_cases'][0]['legacy_labels']
    assert c['proposed_labels']['hard_negatives'] == []
    assert c['proposed_labels']['hard_negative_coverage'] == 'missing_not_zero_violations'
    assert c['accepted_aliases'] == []
    assert not c['proposed_labels']['source_equivalence_runtime_approved']
    assert not report['summary']['human_approved'] and not result['metrics_publishable']
    assert report['summary']['ai_reviewed_sop_cases'] == 1


def test_review_keeps_ten_steps_adds_applicability_and_stop_guard():
    candidate, catalog, decisions = fixture()
    result, _ = review_sop_candidate(candidate, catalog, decisions, AS_OF)
    c = result['review_cases'][0]
    assert len(c['evidence_requirements']) == 12
    assert c['proposed_labels']['answer_contract']['step_numbers_in_order'] == list(range(1, 11))
    assert {a['atom_id'] for a in c['evidence_requirements'][-2:]} == {'applicability', 'stop_guard'}
    assert not c['ai_review']['step_count_reduced']


@pytest.mark.parametrize('key,value', [('query', '另一问句'), ('source', 'another.md'), ('alarm_code', 'ALM-SMF-PDU')])
def test_review_rejects_changed_case_identity(key, value):
    candidate, catalog, decisions = fixture()
    decisions['cases'][0][key] = value
    with pytest.raises(ValueError, match='case/query'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_review_does_not_pretend_ai_was_human():
    candidate, catalog, decisions = fixture()
    decisions['human_approved'] = True
    with pytest.raises(ValueError, match='provenance'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_body_equivalent_cross_tenant_source_is_rejected():
    candidate, catalog, decisions = fixture()
    catalog['sop_operation_021.md']['scope_snapshot']['tenant_id'] = 'another'
    with pytest.raises(ValueError, match='scope equality'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


@pytest.mark.parametrize('key,value', [('source_version', 'r2'), ('status', 'archived'),
                                      ('effective_to', '2026-09-20T00:00:00+00:00')])
def test_wrong_version_withdrawn_and_expired_sources_are_rejected(key, value):
    candidate, catalog, decisions = fixture()
    catalog['sop_operation_001.md']['scope_snapshot'][key] = value
    with pytest.raises(ValueError, match='inactive/out-of-time/wrong-version'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_missing_security_metadata_cannot_be_treated_as_same_scope():
    candidate, catalog, decisions = fixture()
    for item in catalog.values():
        item['scope_snapshot']['security_level'] = None
    with pytest.raises(ValueError, match='incomplete static'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_unknown_or_duplicate_decision_cannot_silently_skip_review():
    candidate, catalog, decisions = fixture()
    decisions['cases'].append(copy.deepcopy(decisions['cases'][0]))
    with pytest.raises(ValueError, match='coverage/identity'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_not_all_steps_or_missing_safety_clause_cannot_pass_review():
    candidate, catalog, decisions = fixture()
    catalog['sop_operation_001.md']['text'] = catalog['sop_operation_001.md']['text'].replace('步骤 10：', '附注 10：')
    with pytest.raises(ValueError, match='ten ordered'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_unrelated_negative_cannot_be_removed_to_raise_metric():
    candidate, catalog, decisions = fixture()
    decisions['cases'][0]['remove_negatives'] = ['another-source']
    with pytest.raises(ValueError, match='unrelated/unreviewed'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def test_step_anchor_tampering_is_detected():
    candidate, catalog, decisions = fixture()
    candidate['review_cases'][0]['evidence_requirements'][0]['any_of'][0]['quote'] = '标题'
    with pytest.raises(ValueError, match='provenance changed'):
        review_sop_candidate(candidate, catalog, decisions, AS_OF)


def mock_index(monkeypatch, fields):
    client = SimpleNamespace(queried=False, closed=False)
    client.describe_collection = lambda **kw: {'fields': [{'name': n} for n in fields]}

    def query(**kw):
        client.queried = True
        client.filter = kw['filter']
        return [{'source': 'sop_operation_001.md'}]

    client.query = query
    client.close = lambda: setattr(client, 'closed', True)
    monkeypatch.setattr('pymilvus.MilvusClient', lambda **kw: client)
    monkeypatch.setattr('qa_core.config.settings.get_settings', lambda: SimpleNamespace(
        milvus_uri='test-only', doc_collection='test_doc_collection'))
    monkeypatch.setattr('qa_core.config.environment.validate_isolated_environment', lambda *a: [])
    return client


def test_old_schema_visibility_does_not_approve_acl_or_aliases(monkeypatch):
    candidate, catalog, decisions = fixture()
    reviewed, _ = review_sop_candidate(candidate, catalog, decisions, AS_OF)
    client = mock_index(monkeypatch, {'tenant_id', 'kb_version', 'visibility', 'allowed_roles', 'source', 'text', 'step_index'})
    result = check_test_index_visibility(reviewed, as_of=AS_OF)
    assert client.queried and client.closed
    assert result['status'] == 'blocked_incomplete_security_schema'
    assert {'security_level', 'status', 'effective_from', 'effective_to'} <= set(result['missing_fields'])
    assert not result['permission_complete']
    assert reviewed['review_cases'][0]['accepted_aliases'] == []
    assert 'tenant_id' in client.filter and 'kb_version' in client.filter


def test_missing_basic_tenant_scope_skips_even_compatible_query(monkeypatch):
    candidate, catalog, decisions = fixture()
    reviewed, _ = review_sop_candidate(candidate, catalog, decisions, AS_OF)
    client = mock_index(monkeypatch, {'source', 'text'})
    result = check_test_index_visibility(reviewed, as_of=AS_OF)
    assert not client.queried and client.closed
    assert not result['permission_complete']


def test_index_check_rejects_nonisolated_environment_before_connect(monkeypatch):
    candidate, catalog, decisions = fixture()
    reviewed, _ = review_sop_candidate(candidate, catalog, decisions, AS_OF)
    client = mock_index(monkeypatch, {'source', 'text'})
    monkeypatch.setattr('qa_core.config.environment.validate_isolated_environment', lambda *a: ['not test'])
    with pytest.raises(ValueError, match='isolated test'):
        check_test_index_visibility(reviewed, as_of=AS_OF)
    assert not client.queried
