"""评测校准的 SOP 复核组件；不修改原文/旧集，不实现在线证据组装。

语义决策由明确的逐题复核表提供，程序验证其对应的原文与元数据证据。
AI 复核不冒充人工金标；运行时检查只读，缺安全字段时不能批准别名。
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

from scripts.calibrate_eval_set import ALARM, SCOPE_FIELDS, normalize, quote_ref

STEP_ACTIONS = ('执行确认影响范围', '执行核对最近变更', '执行采集关键指标',
                '执行检查上下游依赖', '执行验证配置一致性', '执行执行低风险处置',
                '执行检查派生告警', '执行验证业务恢复', '执行观察三个周期', '执行记录复盘项')
REQUIRED_INDEX_FIELDS = set(SCOPE_FIELDS) | {'source', 'kb_version', 'text', 'step_index'}


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('naive lifecycle timestamp')
    return result


def review_sop_candidate(candidate: dict, catalog: dict, decisions: dict, as_of: str) -> tuple[dict, dict]:
    if (decisions.get('origin_dataset_sha256', '').lower() != candidate['origin_dataset_sha256'].lower()
            or decisions.get('reviewer_kind') != 'ai_agent' or decisions.get('human_approved') is not False):
        raise ValueError('review provenance/dataset mismatch')
    selected = {c['id']: c for c in candidate['review_cases'] if c['template_id'] == 'sop_by_alarm'}
    ledger = {d['id']: d for d in decisions['cases']}
    if len(ledger) != len(decisions['cases']) or set(ledger) != set(selected):
        raise ValueError('SOP decision coverage/identity mismatch')
    reviewed = copy.deepcopy(candidate)
    results = []
    now = _timestamp(as_of)
    for case in reviewed['review_cases']:
        if case['id'] not in ledger:
            continue
        decision = ledger[case['id']]
        source = case['legacy_labels']['expect_hit']
        if (case['origin_split'] != 'dev' or source != decision['source'] or case['query'] != decision['query']
                or ALARM.findall(case['query']) != [decision['alarm_code']]):
            raise ValueError(f"review case/query mismatch: {case['id']}")
        item = catalog[source]
        family = sorted(s for s, i in catalog.items() if i['body_family'] == item['body_family'])
        if (len(family) != decision['body_family_size']
                or set(family) != {source, *case['body_equivalent_sources_for_review_only']}):
            raise ValueError('body family changed')
        scope = item['scope_snapshot']
        if any(scope.get(k) is None for k in SCOPE_FIELDS):
            raise ValueError('incomplete static scope metadata')
        if (scope['status'] != 'active' or scope['source_version'] != candidate['kb_version']
                or not (_timestamp(scope['effective_from']) <= now < _timestamp(scope['effective_to']))):
            raise ValueError('inactive/out-of-time/wrong-version static source')
        if any(catalog[s]['scope_snapshot'] != scope for s in family):
            raise ValueError('body equality does not imply scope equality')
        applicability = f"适用：{decision['equipment_type']}/5G；模拟告警：{decision['alarm_code']}。"
        if applicability not in item['text']:
            raise ValueError('alarm/equipment applicability mismatch')
        matches = list(re.finditer(r'^步骤\s*(\d+)：([^\n]+)', item['text'], re.M))
        if [int(m[1]) for m in matches] != list(range(1, 11)):
            raise ValueError('SOP must contain ten ordered distinct steps')
        for number, (match, action) in enumerate(zip(matches, STEP_ACTIONS), 1):
            if (normalize(match[2].split('。', 1)[0]) != normalize(action)
                    or f"针对{decision['condition']}记录对象、版本、时间和前后值" not in match[2]
                    or '证据不足时停止并转人工复核' not in match[2]):
                raise ValueError(f'SOP action/condition/guard mismatch: step {number}')
        removed = decision['remove_negatives']
        if (len(removed) != len(set(removed)) or set(removed) != set(case['legacy_labels']['hard_negatives'])
                or any(s not in family for s in removed)):
            raise ValueError('cannot remove unrelated/unreviewed hard negative')
        expected_atoms = [f'step_{n:02d}' for n in range(1, 11)]
        atoms = case['evidence_requirements']
        if [a['atom_id'] for a in atoms] != expected_atoms:
            raise ValueError('step anchor draft changed')
        for atom, match in zip(atoms, matches):
            ref = quote_ref(item, match[0].split('。', 1)[0])
            if atom['any_of'] != [ref]:
                raise ValueError('step anchor provenance changed')
            atom['status'] = 'ai_semantically_reviewed'
        for atom_id, label, quote in [('applicability', '设备/技术/告警适用条件', applicability),
                                      ('stop_guard', '证据不足停止并转人工复核', '证据不足时停止并转人工复核')]:
            atoms.append({'atom_id': atom_id, 'label': label, 'required': True,
                          'any_of': [quote_ref(item, quote)], 'status': 'ai_semantically_reviewed'})
        case['review_status'] = 'ai_semantically_reviewed_runtime_and_negative_coverage_blocked'
        case['accepted_aliases'] = []
        case['blocking_issues'] = ['runtime_source_equivalence_permission_pending',
                                  'distinguishing_hard_negatives_missing', 'human_semantic_review_not_done']
        case['proposed_labels'] = {'expect_route': 'retrieval', 'expect_reject': False,
                                   'canonical_source': source, 'hard_negatives': [],
                                   'hard_negative_coverage': 'missing_not_zero_violations',
                                   'semantically_equivalent_sources': family,
                                   'source_equivalence_runtime_approved': False,
                                   'answer_contract': {'step_numbers_in_order': list(range(1, 11)),
                                                       'applicability_required': True, 'stop_guard_required': True,
                                                       'actions_are_consultation_not_execution': True}}
        case['ai_review'] = {'reviewer_kind': 'ai_agent', 'human_approved': False,
                             'reviewed_at': as_of, 'rationale': decisions['rationale'],
                             'query_changed': False, 'step_count_reduced': False,
                             'removed_candidate_negatives': removed,
                             'source_group_approved_only_for_static_semantics': True}
        results.append({'id': case['id'], 'query': case['query'], 'alarm_code': decision['alarm_code'],
                        'equipment_type': decision['equipment_type'], 'condition': decision['condition'],
                        'canonical_source': source, 'body_family_size': len(family),
                        'removed_candidate_negatives': removed, 'semantic_step_count': 10,
                        'required_atom_count': len(atoms), 'scope_snapshot': scope,
                        'family_proof': [{'source': s, 'file_sha256': catalog[s]['file_sha256'],
                                          'body_sha256': catalog[s]['body_sha256']} for s in family],
                        'all_static_scope_equal': True, 'runtime_equivalence_approved': False})
    reviewed['sop_review_status'] = 'ai_semantic_review_done_runtime_and_negative_coverage_blocked'
    summary = {'reviewer_kind': 'ai_agent', 'human_approved': False, 'ai_reviewed_sop_cases': len(results),
               'removed_candidate_negatives': sum(len(r['removed_candidate_negatives']) for r in results),
               'approved_runtime_aliases': 0, 'sop_required_atoms': sum(r['required_atom_count'] for r in results),
               'unreviewed_cases': len(reviewed['review_cases']) - len(results), 'metrics_publishable': False}
    return reviewed, {'summary': summary, 'cases': results, 'as_of': as_of,
                      'legacy_labels_modified': False, 'online_parameters_modified': False}


def check_test_index_visibility(candidate: dict, *, as_of: str) -> dict:
    """只读 schema/标量检查。不启动存储，不补字段，不加载模型或缓存。"""
    from pymilvus import MilvusClient
    from qa_core.config.environment import validate_isolated_environment
    from qa_core.config.settings import get_settings
    from qa_core.retrieval.filters import build_filter_expr
    from qa_core.security.policy import AccessScope

    settings = get_settings()
    problems = validate_isolated_environment(settings, 'test')
    if problems:
        raise ValueError('index review requires isolated test: ' + '; '.join(problems))
    client = MilvusClient(uri=settings.milvus_uri, timeout=8)
    try:
        fields = {f['name'] for f in client.describe_collection(collection_name=settings.doc_collection, timeout=8)['fields']}
        missing = sorted(REQUIRED_INDEX_FIELDS - fields)
        access = AccessScope.build(tenant_id='default', roles=['ops'])
        sources = sorted({s for c in candidate['review_cases'] if 'proposed_labels' in c
                          for s in c['proposed_labels']['semantically_equivalent_sources']})
        # 若连 tenant/版本/visibility 都没有，不做兼容查询，直接拒绝。
        if not {'tenant_id', 'kb_version', 'visibility', 'allowed_roles'} <= fields:
            return {'status': 'blocked_incomplete_security_schema', 'missing_fields': missing,
                    'permission_complete': False, 'as_of': as_of, 'access': access.to_dict()}
        expr = build_filter_expr(list(access.roles), candidate['kb_version'], tenant_id=access.tenant_id,
                                 security_levels=access.security_levels, as_of=as_of, available_fields=fields)
        expr += ' and source in ' + json.dumps(sources)
        output = sorted(REQUIRED_INDEX_FIELDS & fields)
        rows = client.query(collection_name=settings.doc_collection, filter=expr, output_fields=output,
                            limit=2048, timeout=8)
        visible = sorted({row['source'] for row in rows})
        return {'status': 'blocked_incomplete_security_schema' if missing else 'visibility_checked_only',
                'missing_fields': missing, 'permission_complete': False,
                'access': access.to_dict(), 'as_of': as_of, 'collection': settings.doc_collection,
                'filter': expr, 'expected_source_count': len(sources), 'visible_source_count': len(visible),
                'visible_sources': visible, 'visible_chunk_count': len(rows),
                'missing_sources': sorted(set(sources) - set(visible)),
                'notes': 'Current schema compatible visibility is not full ACL/lifecycle approval; no alias promotion.'}
    finally:
        client.close()
