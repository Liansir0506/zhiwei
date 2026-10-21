"""实验与知识卡 AI 复核：指标类别/数值分离，歧义题阻止打分，接续不覆盖旧任务。"""
import copy

import pytest

from scripts.calibrate_eval_set import SCOPE_FIELDS, audit, canonical_body, digest, probe_evidence, propose_anchors
from scripts.guide_card_label_review import (review_guide_card_candidate, validate_parent_candidate)

AS_OF = '2026-09-27T00:00:00+00:00'


def fixture(scope='checks_and_metrics'):
    properties = dict(zip(SCOPE_FIELDS, ['default', 'public', 'public', [], [], 'internal', 'active',
                                         'r3', '2026-09-19T00:00:00+00:00', '2028-09-19T00:00:00+00:00']))
    topic = 'AMF注册成功率下降'
    guide = '# SIMULATED 实验 EXP-001\n\n' + '\n\n'.join(
        f'## 阶段 {n}\n对象为AMF，注入条件为注册信令拥塞，观测告警ALM-AMF-REG。'
        f'本阶段采集控制面成功率{89+n}%、模拟时延{20+n}毫秒、队列占用{30+n}%。'
        '先核对拓扑和版本，再执行只读诊断。' for n in range(1, 11))
    card = f'# {topic}运维知识卡\n\n本文为项目自编的{topic}知识卡（REF-001），不是规范原文；使用时应回查来源索引。\n\n'
    card += ('确认AMF的对象标识、软件版本、所属区域和最近变更窗口。\n'
             '按时间线核对ALM-AMF-REG首次出现、派生告警、恢复事件和相邻设备状态。\n'
             '采集控制面成功率、用户面时延、丢包、CPU、内存和接口队列指标，并保留采样时间与单位。\n'
             '对比同区域健康对象和故障对象的配置差异。\n'
             '先执行只读检查，再进行可回滚操作；涉及业务中断的动作必须进入维护窗口并经过复核。\n'
             '恢复后持续观察三个采样周期，验证主指标、关联告警和用户体验同时恢复。')
    catalog, metadata = {}, {}
    texts = {'guide_experiment_001.md': guide, 'manual_reference_01.md': card,
             'negative.md': card.replace('AMF', 'SMF').replace('REG', 'PDU'), 'old_sop.md': '# Old SOP\n步骤 1：核对。'}
    for source, text in texts.items():
        h = digest(canonical_body(text).encode())
        catalog[source] = {'source': source, 'file': source, 'file_sha256': digest(text.encode()), 'body_sha256': h,
                            'body_family': 'body:' + h, 'topics': ['ALM-SMF-PDU'] if source == 'negative.md' else ['ALM-AMF-REG'],
                            'text': text, 'row': None, 'scope_snapshot': copy.deepcopy(properties),
                            'location': {'kind': 'markdown', 'line_start': 1}, 'authorization_verified': False}
        metadata[source] = {**copy.deepcopy(properties), 'equipment_type': 'AMF', 'evidence_refs': ['reference-index-only']}
    queries = {'checks_and_metrics': f'关于{topic}，应核对哪些内容和指标？',
               'card_overview': f'{topic} 这张知识卡说明了什么？',
               'ambiguous_basis': f'{topic} 的知识卡依据是什么？'}
    cases = []
    for cid, template, source, query in [('E01', 'exp_guide_topic', 'guide_experiment_001.md', 'AMF 在注册信令拥塞场景下记录哪些指标？'),
                                         ('M01', 'manual_ref_card', 'manual_reference_01.md', queries[scope]),
                                         ('R01', 'sop_by_alarm', 'old_sop.md', '原先已审 SOP')]:
        cases.append({'id': cid, 'query': query, 'template_id': template, 'category': 'retrieval', 'split': 'dev',
                      'expect_hit': source, 'expect_route': 'retrieval', 'expect_reject': False, 'hard_negatives': ['negative.md']})
    _, baseline, _ = audit({'kb_version': 'r3', 'cases': cases}, catalog, {'meta.json': 'hash'}, 'hash')
    parent = copy.deepcopy(baseline)
    parent['review_cases'][-1]['ai_review'] = {'prior_review': True}
    parent['review_cases'][-1]['review_status'] = 'ai_semantically_reviewed_runtime_and_negative_coverage_blocked'
    decisions = {'origin_dataset_sha256': 'hash', 'reviewer_kind': 'ai_agent', 'human_approved': False, 'rationale': '按原文与题目范围复核',
                  'cases': [{'id': 'E01', 'query': cases[0]['query'], 'source': cases[0]['expect_hit'],
                             'scope': 'metric_categories', 'equipment_type': 'AMF', 'condition': '注册信令拥塞', 'alarm_code': 'ALM-AMF-REG'},
                            {'id': 'M01', 'query': queries[scope], 'source': cases[1]['expect_hit'], 'scope': scope,
                             'equipment_type': 'AMF', 'topic': topic, 'alarm_code': 'ALM-AMF-REG'}]}
    if scope == 'ambiguous_basis':
        decisions['cases'][1]['proposed_query'] = f'关于{topic}，这张知识卡的来源性质是什么，能否直接当作规范原文？'
    return parent, baseline, catalog, metadata, decisions


def review(scope='checks_and_metrics'):
    parent, baseline, catalog, metadata, decisions = fixture(scope)
    validate_parent_candidate(parent, baseline)
    result, proof = review_guide_card_candidate(parent, catalog, metadata, decisions, AS_OF)
    return parent, result, proof


def test_continuation_preserves_prior_sop_and_all_legacy_labels():
    parent, result, proof = review()
    assert parent['review_cases'][-1] == result['review_cases'][-1]
    assert [c['legacy_labels'] for c in result['review_cases']] == [c['legacy_labels'] for c in parent['review_cases']]
    assert [c['query'] for c in result['review_cases']] == [c['query'] for c in parent['review_cases']]
    assert all(not c['accepted_aliases'] for c in result['review_cases'])
    assert proof['summary']['total_ai_reviewed_cases'] == 3
    assert not result['metrics_publishable'] and not proof['summary']['human_approved']


def test_experiment_queries_require_categories_and_units_not_arbitrary_phase_values():
    _, result, proof = review()
    c = result['review_cases'][0]
    assert [a['any_of'][0]['quote'] for a in c['evidence_requirements'][:3]] == ['控制面成功率', '模拟时延', '队列占用']
    assert [a['semantic_unit'] for a in c['evidence_requirements'][:3]] == ['%', '毫秒', '%']
    assert not c['proposed_labels']['answer_contract']['particular_phase_value_required']
    assert not c['proposed_labels']['answer_contract']['arbitrary_phase_selection_allowed']
    assert all(not o['values_are_required'] and not o['production_threshold'] for o in proof['cases'][0]['phase_observations'])


def test_metric_evidence_does_not_require_optional_action_instructions():
    _, result, _ = review()
    c = result['review_cases'][0]
    snippet = {'source': 'guide_experiment_001.md', 'text': '对象为AMF，注入条件为注册信令拥塞。控制面成功率、模拟时延、队列占用。'}
    probed = probe_evidence(c, [snippet])
    assert probed['required_atoms'] == 4 and probed['all_required_present']
    assert 'readonly_guard' not in probed['missing_atoms']
    assert not probed['metrics_publishable']


def test_checks_card_has_conditional_action_and_recovery_evidence():
    _, result, _ = review()
    c = result['review_cases'][1]
    optional = {a['atom_id'] for a in c['evidence_requirements'] if not a['required']}
    assert optional == {'readonly_approval', 'recovery'}
    assert sum(a['required'] for a in c['evidence_requirements']) == 6
    assert not c['proposed_labels']['answer_contract']['external_reference_support_verified']


def test_overview_card_retains_six_content_units_and_source_context():
    _, result, _ = review('card_overview')
    c = result['review_cases'][1]
    assert len(c['evidence_requirements']) == 8 and all(a['required'] for a in c['evidence_requirements'])


def test_ambiguous_basis_question_is_preserved_not_scored_with_proposed_query():
    parent, result, proof = review('ambiguous_basis')
    c = result['review_cases'][1]
    assert c['query'] == parent['review_cases'][1]['query']
    assert c['original_query_status'] == 'ambiguous_needs_clarification'
    assert c['proposed_query'] != c['query']
    assert len(c['proposed_evidence_requirements']) == 2
    assert all(not a['required'] for a in c['evidence_requirements'])
    assert proof['summary']['ambiguous_original_questions'] == 1
    with pytest.raises(ValueError, match='ambiguous original'):
        probe_evidence(c, [{'source': c['legacy_labels']['expect_hit'], 'text': 'anything'}])


def test_context_contrast_negatives_not_deleted_or_called_contradictory_answers():
    parent, result, proof = review()
    for old, new in zip(parent['review_cases'][:2], result['review_cases'][:2]):
        assert new['proposed_labels']['hard_negatives'] == old['legacy_labels']['hard_negatives']
        assert not new['proposed_labels']['negative_difficulty_empirically_verified']
    assert all(p['classification'] == 'context_mismatch_not_content_contradiction'
               for r in proof['cases'] for p in r['negative_source_proofs'])


@pytest.mark.parametrize('mutation', ['version', 'legacy', 'duplicate', 'aliases', 'publish'])
def test_invalid_parent_candidate_is_rejected(mutation):
    parent, baseline, *_ = fixture()
    if mutation == 'version': parent['kb_version'] = 'wrong'
    if mutation == 'legacy': parent['review_cases'][0]['legacy_labels']['expect_hit'] = 'changed'
    if mutation == 'duplicate': parent['review_cases'][0]['id'] = parent['review_cases'][1]['id']
    if mutation == 'aliases': parent['review_cases'][0]['accepted_aliases'] = ['unapproved']
    if mutation == 'publish': parent['metrics_publishable'] = True
    with pytest.raises(ValueError, match='parent'):
        validate_parent_candidate(parent, baseline)


@pytest.mark.parametrize('mutation', ['query', 'device', 'alarm', 'missing_decision', 'human'])
def test_changed_review_decision_is_rejected(mutation):
    parent, _, cat, meta, decisions = fixture()
    if mutation == 'query': decisions['cases'][0]['query'] = 'changed'
    if mutation == 'device': decisions['cases'][0]['equipment_type'] = 'SMF'
    if mutation == 'alarm': decisions['cases'][0]['alarm_code'] = 'ALM-SMF-PDU'
    if mutation == 'missing_decision': decisions['cases'].pop()
    if mutation == 'human': decisions['human_approved'] = True
    with pytest.raises(ValueError):
        review_guide_card_candidate(parent, cat, meta, decisions, AS_OF)


@pytest.mark.parametrize('mutation', ['phase', 'unit', 'condition', 'disclosure', 'anchor', 'expiry'])
def test_changed_original_content_or_scope_is_rejected(mutation):
    parent, _, cat, meta, decisions = fixture()
    guide = cat['guide_experiment_001.md']
    if mutation == 'phase': guide['text'] = guide['text'].replace('## 阶段 10', '## 第十阶段')
    if mutation == 'unit': guide['text'] = guide['text'].replace('毫秒', '秒')
    if mutation == 'condition': guide['text'] = guide['text'].replace('注册信令拥塞', '另一个条件')
    if mutation == 'disclosure': cat['manual_reference_01.md']['text'] = cat['manual_reference_01.md']['text'].replace('不是规范原文', '正式规范原文')
    if mutation == 'anchor': parent['review_cases'][0]['evidence_requirements'][0]['any_of'][0]['quote'] = 'changed'
    if mutation == 'expiry': guide['scope_snapshot']['effective_to'] = '2026-09-20T00:00:00+00:00'
    with pytest.raises(ValueError):
        review_guide_card_candidate(parent, cat, meta, decisions, AS_OF)


def test_same_topic_negative_is_not_automatically_accepted_as_context_contrast():
    parent, _, cat, meta, decisions = fixture()
    cat['negative.md']['topics'] = ['ALM-AMF-REG']
    with pytest.raises(ValueError, match='same-topic/body'):
        review_guide_card_candidate(parent, cat, meta, decisions, AS_OF)


def test_review_accepts_crlf_without_changing_source_semantics():
    parent, _, cat, meta, decisions = fixture()
    for c in parent['review_cases'][:2]:
        source = c['legacy_labels']['expect_hit']
        cat[source]['text'] = cat[source]['text'].replace('\n', '\r\n')
        cat[source]['file_sha256'] = digest(cat[source]['text'].encode())
        c['evidence_requirements'] = propose_anchors({'expect_hit': source, 'category': c['category'],
                                                     'template_id': c['template_id']}, cat)
    _, proof = review_guide_card_candidate(parent, cat, meta, decisions, AS_OF)
    assert proof['summary']['ai_reviewed_guide_card_cases'] == 2


def test_repeat_application_is_rejected_instead_of_adding_duplicate_anchors():
    parent, _, cat, meta, decisions = fixture()
    reviewed, _ = review_guide_card_candidate(parent, cat, meta, decisions, AS_OF)
    with pytest.raises(ValueError, match='already-reviewed'):
        review_guide_card_candidate(reviewed, cat, meta, decisions, AS_OF)
