"""在已复核 SOP 的候选上接续实验/知识卡 AI 复核；仅离线标注，不执行评测。"""
from __future__ import annotations

import copy
import re
from collections import Counter

from scripts.calibrate_eval_set import SCOPE_FIELDS, normalize, propose_anchors, quote_ref
from scripts.sop_label_review import _timestamp

TEMPLATES = {'exp_guide_topic', 'manual_ref_card'}
EXPERIMENT_METRICS = (('success_rate', '控制面成功率', '%'),
                      ('latency', '模拟时延', '毫秒'), ('queue', '队列占用', '%'))
CARD_METRICS = ('控制面成功率', '用户面时延', '丢包', 'CPU', '内存', '接口队列')
SCOPES = {'metric_categories', 'checks_and_metrics', 'card_overview', 'ambiguous_basis'}


def validate_parent_candidate(parent: dict, baseline: dict) -> None:
    if (parent.get('origin_dataset_sha256') != baseline['origin_dataset_sha256']
            or parent.get('kb_version') != baseline['kb_version']
            or parent.get('status') != 'draft_not_executable'
            or parent.get('metrics_publishable') is not False):
        raise ValueError('parent dataset/version/draft policy mismatch')
    originals = {c['id']: c for c in baseline['review_cases']}
    cases = parent['review_cases']
    if len(cases) != len(originals) or {c['id'] for c in cases} != set(originals):
        raise ValueError('parent case coverage mismatch')
    identity = ('id', 'query', 'category', 'template_id', 'origin_split', 'group_id', 'proposed_split', 'legacy_labels')
    for case in cases:
        original = originals[case['id']]
        if any(case.get(k) != original.get(k) for k in identity) or case['origin_split'] != 'dev':
            raise ValueError(f"parent legacy identity changed: {case['id']}")
        if case.get('accepted_aliases'):
            raise ValueError('parent aliases require separate runtime approval')


def _atom(item: dict, atom_id: str, label: str, quote: str, *, required=True) -> dict:
    return {'atom_id': atom_id, 'label': label, 'required': required,
            'any_of': [quote_ref(item, quote)], 'status': 'ai_semantically_reviewed'}


def _static_scope(item: dict, version: str, as_of: str) -> None:
    scope = item['scope_snapshot']
    if any(scope.get(k) is None for k in SCOPE_FIELDS):
        raise ValueError('incomplete static source scope')
    if (scope['source_version'] != version or scope['status'] != 'active'
            or not (_timestamp(scope['effective_from']) <= _timestamp(as_of) < _timestamp(scope['effective_to']))):
        raise ValueError('wrong version/inactive/expired static source')


def _negative_proof(case: dict, catalog: dict, source: str, *, version: str, as_of: str) -> list[dict]:
    positive = catalog[source]
    if len(positive['topics']) != 1:
        raise ValueError('positive topic is not uniquely scoped')
    output = []
    for negative in case['legacy_labels']['hard_negatives']:
        if negative not in catalog:
            raise ValueError('missing negative source')
        item = catalog[negative]
        _static_scope(item, version, as_of)
        if (not item['topics'] or set(item['topics']) & set(positive['topics'])
                or item['body_family'] == positive['body_family']):
            raise ValueError('negative is same-topic/body, requires separate review')
        output.append({'source': negative, 'file_sha256': item['file_sha256'], 'topics': item['topics'],
                       'classification': 'context_mismatch_not_content_contradiction',
                       'shared_metric_labels': [m for m in dict.fromkeys(CARD_METRICS + tuple(x[1] for x in EXPERIMENT_METRICS))
                                                if m in item['text'] and m in positive['text']]})
    return output


def _experiment(case: dict, item: dict, decision: dict) -> tuple[list[dict], dict, list[dict]]:
    context = f"对象为{decision['equipment_type']}，注入条件为{decision['condition']}"
    if decision['scope'] != 'metric_categories' or any(t not in case['query'] for t in
            (decision['equipment_type'], decision['condition'])):
        raise ValueError('experiment query/scope mismatch')
    if [int(x) for x in re.findall(r'^## 阶段 (\d+)[ \t\r]*$', item['text'], re.M)] != list(range(1, 11)):
        raise ValueError('experiment phase inventory changed')
    paragraphs = re.findall(r'^对象为[^\n]+$', item['text'], re.M)
    if len(paragraphs) != 10 or any(not p.startswith(context + f"，观测告警{decision['alarm_code']}。") for p in paragraphs):
        raise ValueError('experiment condition/alarm mismatch')
    atoms, observations = [], []
    for atom_id, label, unit in EXPERIMENT_METRICS:
        pattern = re.compile(re.escape(label) + r'(\d+)' + re.escape(unit))
        if any(len(pattern.findall(p)) != 1 for p in paragraphs):
            raise ValueError('experiment metric/unit missing in some phase')
        atom = _atom(item, atom_id, label, label)
        atom['semantic_unit'] = unit
        # 数值仅留在支持性原文证据中，不作为题目必须回答的值或生产门槛。
        atom['any_of'][0]['supporting_quote'] = pattern.search(paragraphs[0])[0]
        atoms.append(atom)
        observations.append({'metric': label, 'unit': unit,
                             'values_by_phase': [int(pattern.search(p)[1]) for p in paragraphs],
                             'values_are_required': False, 'production_threshold': False})
    atoms.append(_atom(item, 'experiment_context', '正确设备与注入条件', context))
    guard = _atom(item, 'readonly_guard', '拓扑版本确认与只读诊断', '先核对拓扑和版本，再执行只读诊断', required=False)
    guard['required_when'] = 'answer recommends diagnostic actions, rather than only naming metrics'
    atoms.append(guard)
    contract = {'question_scope': 'metric_categories_not_phase_values',
                'metrics': [{'name': label, 'unit': unit} for _, label, unit in EXPERIMENT_METRICS],
                'particular_phase_value_required': False, 'arbitrary_phase_selection_allowed': False,
                'clarify_when_requesting_numeric_values': ['experiment_id', 'phase_number'],
                'conditional_readonly_guard': True}
    return atoms, contract, observations


def _card(case: dict, item: dict, decision: dict) -> tuple[list[dict], dict, list[dict]]:
    scope, topic = decision['scope'], decision['topic']
    if scope not in {'checks_and_metrics', 'card_overview', 'ambiguous_basis'} or topic not in case['query']:
        raise ValueError('card query/scope mismatch')
    title = f'# {topic}运维知识卡'
    disclosure = next((line for line in item['text'].splitlines() if
                       line.startswith(f'本文为项目自编的{topic}知识卡') and '不是规范原文' in line), None)
    if item['text'].splitlines()[0] != title or not disclosure:
        raise ValueError('card topic/authorship disclosure changed')
    atoms = copy.deepcopy(case['evidence_requirements'])
    for atom in atoms:
        atom['status'] = 'ai_semantically_reviewed'
        atom['required'] = scope == 'card_overview' or (scope == 'checks_and_metrics'
                            and atom['atom_id'] not in {'readonly_approval', 'recovery'})
        if not atom['required']:
            atom['required_when'] = ('answer recommends operations' if atom['atom_id'] == 'readonly_approval'
                                    else 'answer claims recovery' if atom['atom_id'] == 'recovery'
                                    else 'clarified question asks operational content')
    if any(metric not in item['text'] for metric in CARD_METRICS) or '采样时间与单位' not in item['text']:
        raise ValueError('card metric taxonomy/sampling context changed')
    context_atoms = [_atom(item, 'card_topic', '知识卡适用主题', title),
                     _atom(item, 'source_nature', '项目自编而非规范原文', disclosure)]
    contract = {'question_scope': scope, 'source_nature': 'self_authored_not_official_text',
                'metric_categories': list(CARD_METRICS), 'sampling_time_and_unit_required_when_metrics_reported': True,
                'read_only_approval_required_when_recommending_actions': True,
                'recovery_evidence_required_when_claiming_recovery': True,
                'external_reference_support_verified': False}
    if scope == 'ambiguous_basis':
        # 不拿澄清版的两项证据去评原问句，也不把旧六项一律算成必要证据。
        if '依据是什么' not in case['query'] or decision.get('proposed_query') != (
                f'关于{topic}，这张知识卡的来源性质是什么，能否直接当作规范原文？'):
            raise ValueError('ambiguous basis clarification proposal changed')
        contract['original_question_ready'] = False
        contract['clarification_alternatives'] = ['operational checks and metrics', 'authorship/source evidence']
        return atoms, contract, context_atoms
    if ((scope == 'checks_and_metrics' and '核对哪些内容和指标' not in case['query'])
            or (scope == 'card_overview' and '说明了什么' not in case['query'])):
        raise ValueError('card question type changed')
    atoms.extend(context_atoms)
    return atoms, contract, []


def review_guide_card_candidate(parent: dict, catalog: dict, metadata: dict, decisions: dict,
                                as_of: str) -> tuple[dict, dict]:
    if (decisions.get('origin_dataset_sha256', '').lower() != parent['origin_dataset_sha256'].lower()
            or decisions.get('reviewer_kind') != 'ai_agent' or decisions.get('human_approved') is not False):
        raise ValueError('review provenance/dataset mismatch')
    selected = {c['id']: c for c in parent['review_cases'] if c['template_id'] in TEMPLATES}
    ledger = {d['id']: d for d in decisions['cases']}
    if len(ledger) != len(decisions['cases']) or set(ledger) != set(selected):
        raise ValueError('guide/card decision coverage mismatch')
    result, records = copy.deepcopy(parent), []
    for case in result['review_cases']:
        if case['id'] not in ledger:
            continue
        d = ledger[case['id']]
        source = case['legacy_labels']['expect_hit']
        if (case['origin_split'] != 'dev' or source != d['source'] or case['query'] != d['query']
                or d['scope'] not in SCOPES or case['review_status'] != 'pending_review'):
            raise ValueError(f"guide/card identity or already-reviewed mismatch: {case['id']}")
        item = catalog[source]
        _static_scope(item, parent['kb_version'], as_of)
        if (item['topics'] != [d['alarm_code']] or metadata[item['file']]['equipment_type'] != d['equipment_type']
                or any(metadata[item['file']].get(k) != item['scope_snapshot'][k] for k in SCOPE_FIELDS)):
            raise ValueError('source topic/device/metadata mismatch')
        original = propose_anchors({'expect_hit': source, 'template_id': case['template_id'], 'category': case['category']}, catalog)
        if original != case['evidence_requirements']:
            raise ValueError('original draft anchor provenance changed')
        negatives = _negative_proof(case, catalog, source, version=parent['kb_version'], as_of=as_of)
        case['original_evidence_requirements_draft'] = copy.deepcopy(case['evidence_requirements'])
        if case['template_id'] == 'exp_guide_topic':
            atoms, contract, observations = _experiment(case, item, d)
            proposed_atoms = []
        else:
            atoms, contract, proposed_atoms = _card(case, item, d)
            observations = []
        ambiguous = d['scope'] == 'ambiguous_basis'
        case['evidence_requirements'] = atoms
        case['accepted_aliases'] = []
        case['review_status'] = ('ai_scope_reviewed_original_query_ambiguous' if ambiguous
                                else 'ai_semantically_reviewed_runtime_and_human_review_blocked')
        case['original_query_status'] = 'ambiguous_needs_clarification' if ambiguous else 'scope_reviewed'
        case['blocking_issues'] = ['access_scope_pending_verification', 'human_semantic_review_not_done']
        if ambiguous:
            case['blocking_issues'] += ['original_question_intent_ambiguous', 'clarified_query_not_approved']
            case['proposed_query'] = d['proposed_query']
            case['proposed_evidence_requirements'] = proposed_atoms
        if case['template_id'] == 'manual_ref_card':
            case['blocking_issues'].append('external_reference_support_not_verified')
        case['proposed_labels'] = {'canonical_source': source, 'expect_route': 'retrieval',
                                   'hard_negatives': copy.deepcopy(case['legacy_labels']['hard_negatives']),
                                   'negative_contract': 'source-context contrast, not contradictory answer',
                                   'negative_difficulty_empirically_verified': False,
                                   'source_equivalence_runtime_approved': False, 'answer_contract': contract}
        case['ai_review'] = {'reviewer_kind': 'ai_agent', 'human_approved': False, 'reviewed_at': as_of,
                             'rationale': decisions['rationale'], 'original_query_changed': False,
                             'reference_ids': metadata[item['file']].get('evidence_refs', []),
                             'external_references_fetched': False}
        records.append({'id': case['id'], 'query': case['query'], 'source': source, 'scope': d['scope'],
                        'query_ambiguous': ambiguous, 'required_atoms': sum(a['required'] for a in atoms),
                        'optional_atoms': sum(not a['required'] for a in atoms),
                        'proposed_query': d.get('proposed_query'), 'negative_source_proofs': negatives,
                        'original_draft_quote_count': sum(len(a['any_of']) for a in original),
                        'file_sha256': item['file_sha256'], 'phase_observations': observations,
                        'runtime_acl_verified': False})
    result['guide_card_review_status'] = 'ai_scope_review_done_four_original_queries_blocked'
    reviewed_count = sum('ai_review' in c for c in result['review_cases'])
    summary = {'ai_reviewed_guide_card_cases': len(records), 'scope_counts': dict(Counter(r['scope'] for r in records)),
               'ambiguous_original_questions': sum(r['query_ambiguous'] for r in records),
               'retained_context_negative_sources': sum(len(r['negative_source_proofs']) for r in records),
               'total_ai_reviewed_cases': reviewed_count, 'remaining_unreviewed_cases': len(result['review_cases']) - reviewed_count,
               'human_approved': False, 'metrics_publishable': False, 'runtime_aliases_approved': 0}
    return result, {'summary': summary, 'cases': records, 'as_of': as_of,
                    'legacy_labels_modified': False, 'original_queries_modified': False,
                    'retrieval_parameters_modified': False, 'index_migrated': False}
