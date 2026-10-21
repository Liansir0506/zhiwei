#!/usr/bin/env python
"""离线审计旧集并生成 dev 评测候选；不联网、不访问存储、不修改旧真值。

输出不是 run_eval 输入：使用 review_cases 而非 cases，必须先审核语义、
权限和原文证据。正文族用于泄漏隔离，不自动转成可接受来源。
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'r3-calibration-draft-v1'
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ALARM = re.compile(r'ALM-[A-Z0-9]+(?:-[A-Z0-9]+)+')
SCOPE_FIELDS = ('tenant_id', 'dept_id', 'visibility', 'allowed_roles', 'role_scope',
                'security_level', 'status', 'source_version', 'effective_from', 'effective_to')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def canonical_body(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith('# '):
        lines = lines[1:]
    return normalize('\n'.join(lines))


def truths(case: dict) -> list[str]:
    value = case.get('expect_hit')
    if value is None:
        return []
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
        return value
    raise ValueError(f"invalid truth: {case.get('id')}")


def load_catalog(raw_dir: Path, manifest: dict) -> tuple[dict, dict]:
    """Markdown 保留条件/版本；CSV 按记录身份，不抹掉 ID 或数值凑重复。"""
    declared = {f['filename']: f for f in manifest['files']}
    actual = {p.name: p for p in raw_dir.iterdir() if p.is_file()}
    if set(actual) - set(declared) != {'meta.json'} or set(declared) - set(actual):
        raise ValueError('manifest/raw file inventory mismatch')
    metadata_path = actual.pop('meta.json')
    if metadata_path.resolve().parent != raw_dir.resolve():
        raise ValueError('metadata symlink leaves corpus')
    metadata_bytes = metadata_path.read_bytes()
    metadata = json.loads(metadata_bytes)
    if set(metadata) != set(declared):
        raise ValueError('metadata/raw file inventory mismatch')
    catalog, file_hashes = {}, {}
    file_hashes['meta.json'] = digest(metadata_bytes)

    def add(source, path, text, body, location, row=None):
        if not source or source in catalog:
            raise ValueError(f'duplicate/empty source identity: {source}')
        topics = sorted(set(ALARM.findall(text)))
        body_hash = digest(body.encode('utf-8'))
        catalog[source] = {'source': source, 'file': path.name, 'file_sha256': file_hashes[path.name],
                           'body_sha256': body_hash, 'body_family': f'body:{body_hash}',
                           'topics': topics, 'location': location, 'text': text, 'row': row,
                           'scope_snapshot': {k: (row or {}).get(k, metadata[path.name].get(k)) for k in SCOPE_FIELDS},
                           'authorization_verified': False}

    for name, path in sorted(actual.items()):
        if path.resolve().parent != raw_dir.resolve():
            raise ValueError(f'raw symlink leaves corpus: {name}')
        data = path.read_bytes()
        file_hashes[name] = digest(data)
        if declared[name]['sha256'].lower() != file_hashes[name]:
            raise ValueError(f'manifest checksum mismatch: {name}')
        text = data.decode('utf-8-sig')
        if path.suffix == '.md':
            add(name, path, text, canonical_body(text), {'kind': 'markdown', 'line_start': 1})
        elif path.suffix == '.csv':
            reader = csv.DictReader(io.StringIO(text, newline=''))
            previous_line = 1
            for record_number, row in enumerate(reader, 1):
                if None in row or any(v is None for v in row.values()):
                    raise ValueError(f'malformed CSV record: {name}:{record_number}')
                record = json.dumps(row, ensure_ascii=False, sort_keys=True)
                content = '\n'.join(f'{k}: {v}' for k, v in row.items())
                add(row.get('source'), path, content, normalize(record),
                    {'kind': 'csv', 'record_number': record_number,
                     'line_start': previous_line + 1, 'line_end': reader.line_num}, row)
                previous_line = reader.line_num
        else:
            raise ValueError(f'unsupported corpus file: {name}')
    return catalog, file_hashes


def group_sources(catalog: dict) -> tuple[dict, list[dict]]:
    """正文相同 OR 相同告警主题连通分组；权限不同仍需隔离泄漏但不能别名授权。"""
    parent = {s: s for s in catalog}

    def find(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    def union(a, b):
        aa, bb = sorted((find(a), find(b)))
        parent[bb] = aa

    by_key = {}
    for source, item in sorted(catalog.items()):
        for key in [item['body_family']] + [f'topic:{t}' for t in item['topics']]:
            if key in by_key:
                union(source, by_key[key])
            else:
                by_key[key] = source
    members = defaultdict(list)
    for source in sorted(catalog):
        members[find(source)].append(source)
    mapping, groups = {}, []
    for sources in members.values():
        topics = sorted({t for s in sources for t in catalog[s]['topics']})
        identity = '|'.join(topics) if topics else '|'.join(sorted({catalog[s]['body_family'] for s in sources}))
        group_id = 'group:' + digest(identity.encode())[:20]
        # 只做诊断重分组，历史资料不产生新的独立 frozen。
        split = 'regression' if int(digest(group_id.encode())[:8], 16) % 5 == 0 else 'dev'
        groups.append({'group_id': group_id, 'topics': topics, 'sources': sources,
                       'proposed_split': split, 'independent_frozen_eligible': False})
        mapping.update({s: group_id for s in sources})
    return mapping, sorted(groups, key=lambda g: g['group_id'])


def quote_ref(item: dict, quote: str, field: str | None = None) -> dict:
    result = {'source': item['source'], 'file': item['file'], 'file_sha256': item['file_sha256'],
              'quote': quote, 'location': dict(item['location'])}
    if field:
        if quote not in item['row'][field]:
            raise ValueError('quote absent from CSV field')
        result['location']['field'] = field
    else:
        offsets = [n for n, line in enumerate(item['text'].splitlines(), 1) if quote in line]
        if not offsets:
            raise ValueError('quote absent from raw line')
        result['location'] = {'kind': 'markdown', 'line_start': offsets[0], 'line_end': offsets[0]}
    return result


def propose_anchors(case: dict, catalog: dict) -> list[dict]:
    """原文定位的必要证据草案；规则抽取不是人工金标，不按当前返回反向标注。"""
    items = [catalog[s] for s in truths(case)]
    if not items:
        return []
    template = case.get('template_id')
    proposals = []

    def atom(atom_id, label, refs):
        proposals.append({'atom_id': atom_id, 'label': label, 'required': True, 'any_of': refs,
                          'status': 'machine_proposed'})

    if template == 'sop_by_alarm':
        item = items[0]
        # 问题询问完整流程，草案要求十步，而不是任意一个标题或前三个关键词。
        for match in re.finditer(r'^步骤\s*(\d+)：[^\n]+', item['text'], re.M):
            number = int(match[1])
            phrase = match[0].split('。', 1)[0]
            atom(f'step_{number:02d}', f'处置步骤 {number}', [quote_ref(item, phrase)])
    elif template == 'exp_guide_topic':
        item = items[0]
        for key, pattern in [('success_rate', r'控制面成功率\d+%'),
                             ('latency', r'模拟时延\d+毫秒'), ('queue', r'队列占用\d+%')]:
            phrases = list(dict.fromkeys(re.findall(pattern, item['text'])))
            if phrases:
                atom(key, key, [quote_ref(item, p) for p in phrases])
        conditions = list(dict.fromkeys(re.findall(r'对象为[^，\n]+，注入条件为[^，\n]+', item['text'])))
        if conditions:
            atom('experiment_context', '正确的设备与注入条件', [quote_ref(item, p) for p in conditions])
    elif template == 'manual_ref_card':
        item = items[0]
        patterns = [('object_version', r'确认[^。\n]*对象标识[^。\n]*'),
                    ('alarm_timeline', r'按时间线核对[^。\n]*'),
                    ('metrics', r'采集控制面成功率[^。\n]*'),
                    ('change_compare', r'对比同区域[^。\n]*'),
                    ('readonly_approval', r'先执行只读检查[^。\n]*'),
                    ('recovery', r'恢复后持续观察[^。\n]*')]
        for key, pattern in patterns:
            match = re.search(pattern, item['text'])
            if match:
                atom(key, key, [quote_ref(item, match[0])])
    elif case['category'] == 'faq_exact':
        refs = [quote_ref(i, i['row']['answer'].split('。', 1)[0], 'answer') for i in items]
        atom('faq_answer', '实际答案内容，不是 FAQ 问句', refs)
    elif case['category'] == 'alarm_lookup':
        refs = []
        for item in items:
            match = re.search(r'确认根因为[^；。]+', item['row']['root_cause'])
            if match:
                refs.append(quote_ref(item, match[0], 'root_cause'))
        if refs:
            atom('cause_basis', '工单中的根因证据', refs)
        refs = []
        for item in items:
            match = re.search(r'恢复后观察[^。]+', item['row']['resolution'])
            if match:
                refs.append(quote_ref(item, match[0], 'resolution'))
        if refs:
            atom('recovery_evidence', '实际处置后的恢复观察', refs)
    return proposals


def audit(payload: dict, catalog: dict, file_hashes: dict, dataset_hash: str) -> tuple[dict, dict, dict]:
    all_cases = payload['cases']
    ids = [c['id'] for c in all_cases]
    if len(ids) != len(set(ids)) or any(not c.get('split') for c in all_cases):
        raise ValueError('duplicate ID or missing split')
    dev = [c for c in all_cases if c['split'] == 'dev']
    if not dev:
        raise ValueError('no dev cases')
    source_groups, groups = group_sources(catalog)
    group_splits = {g['group_id']: g['proposed_split'] for g in groups}
    body_members = defaultdict(list)
    for s, item in sorted(catalog.items()):
        body_members[item['body_family']].append(s)

    # 非 dev 仅检查身份、来源、split 元数据，不导出/评测其问题或证据。
    legacy_exposure = defaultdict(lambda: defaultdict(list))
    missing_refs = []
    for case in all_cases:
        for s in truths(case):
            if s not in catalog:
                missing_refs.append({'case_id': case['id'], 'source': s, 'split': case['split']})
            else:
                legacy_exposure[source_groups[s]][case['split']].append(case['id'])
    if missing_refs:
        raise ValueError(f'missing truth sources: {missing_refs[:3]}')
    leakage = [{'group_id': g, 'cases_by_split': {s: sorted(set(v)) for s, v in x.items()}}
               for g, x in sorted(legacy_exposure.items()) if len(x) > 1]
    conflicts, candidates, duplicate_queries = [], [], defaultdict(list)
    for case in dev:
        expected = truths(case)
        duplicate_queries[normalize(case['query'])].append(case['id'])
        aliases = sorted({s for t in expected for s in body_members[catalog[t]['body_family']]} - set(expected))
        case_conflicts = []
        missing_negatives = []
        for negative in case.get('hard_negatives', []):
            if negative not in catalog:
                missing_negatives.append(negative)
                continue
            matches = [t for t in expected if catalog[t]['body_family'] == catalog[negative]['body_family']]
            if matches:
                issue = {'case_id': case['id'], 'negative': negative, 'equivalent_to': matches,
                         'classification': 'same_body_not_validated_hard_negative',
                         'scope_snapshot_equal': all(catalog[t]['scope_snapshot'] == catalog[negative]['scope_snapshot'] for t in matches),
                         'resolution': 'pending_review_no_automatic_relabel'}
                conflicts.append(issue)
                case_conflicts.append(issue)
        positive_groups = sorted({source_groups[s] for s in expected})
        if len(positive_groups) > 1:
            raise ValueError(f'multi-group truth requires manual partition: {case["id"]}')
        group = positive_groups[0] if positive_groups else f"synthetic:{case.get('template_id', case['category'])}"
        proposed_split = group_splits.get(group, 'regression')
        atoms = propose_anchors(case, catalog)
        problems = ['semantic_labels_pending_review', 'access_scope_pending_verification'] if expected else ['behavior_labels_pending_review']
        if case_conflicts:
            problems.append('same_body_hard_negative_conflict')
        if aliases:
            problems.append('source_equivalence_pending_scope_and_version_review')
        if missing_negatives:
            problems.append('missing_hard_negative_source')
        if expected and not atoms:
            problems.append('missing_evidence_anchor')
        if case['template_id'] == 'alarm_code_only':
            problems.append('ambiguous_business_purpose_ticket_vs_sop')
        if case['category'] == 'reject':
            problems.append('safety_refusal_vs_out_of_domain_route_review')
        candidates.append({'id': case['id'], 'query': case['query'], 'category': case['category'],
                           'template_id': case['template_id'], 'origin_split': 'dev',
                           'group_id': group, 'proposed_split': proposed_split,
                           'legacy_labels': copy.deepcopy({k: case.get(k) for k in
                               ('expect_hit', 'expect_route', 'expect_reject', 'hard_negatives')}),
                           'body_equivalent_sources_for_review_only': aliases,
                           'accepted_aliases': [], 'review_status': 'pending_review',
                           'blocking_issues': problems, 'evidence_requirements': atoms,
                           'human_review': {'reviewer': None, 'reviewed_at': None, 'decision': None}})
    duplicate_queries = [v for v in duplicate_queries.values() if len(v) > 1]
    sop = [i for s, i in catalog.items() if s.startswith('sop_operation_')]
    summary = {'dev_cases': len(dev), 'dev_positive_cases': sum(bool(truths(c)) for c in dev),
               'raw_files': len(file_hashes) - 1, 'metadata_sidecars': 1,
               'catalog_sources': len(catalog), 'topic_body_groups': len(groups),
               'sop_files': len(sop), 'distinct_sop_bodies': len({i['body_family'] for i in sop}),
               'hard_negative_conflicts': len(conflicts), 'conflict_case_count': len({c['case_id'] for c in conflicts}),
               'legacy_cross_split_groups': len(leakage), 'duplicate_dev_queries': len(duplicate_queries),
               'positive_cases_with_anchors': sum(bool(c['evidence_requirements']) for c in candidates),
               'required_atoms': sum(len(c['evidence_requirements']) for c in candidates),
               'proposed_splits': dict(Counter(c['proposed_split'] for c in candidates)),
               'approved_cases': 0, 'independent_frozen_cases': 0}
    candidate = {'schema_version': VERSION, 'kb_version': payload['kb_version'], 'status': 'draft_not_executable',
                 'origin_dataset_sha256': dataset_hash, 'not_compatible_with_run_eval': True,
                 'truth_modified': False, 'metrics_publishable': False,
                 'annotation_policy': {'source_aliases': 'none approved; body equivalence is not permission',
                                       'evidence_match': 'exact raw quotes after whitespace/NFKC normalization; diagnostic only',
                                       'step_completeness': 'all ten SOP steps proposed, reviewer must confirm question scope',
                                       'experimental_values': 'synthetic observations, not production thresholds',
                                       'non_dev': 'identity/source/split audit only; queries not exported'},
                 'review_cases': candidates}
    report = {'schema_version': VERSION, 'dataset_sha256': dataset_hash, 'summary': summary,
              'source_inventory': [{k: v for k, v in item.items() if k not in {'text', 'row'}}
                                   for _, item in sorted(catalog.items())],
              'hard_negative_conflicts': conflicts, 'legacy_cross_split_groups': leakage,
              'duplicate_dev_queries': duplicate_queries,
              'body_duplicate_groups': [{'body_family': b, 'sources': s} for b, s in sorted(body_members.items()) if len(s) > 1]}
    partition = {'schema_version': VERSION, 'purpose': 'diagnostic regrouping only',
                 'assignment_policy': 'connected identical body OR shared alarm topic; deterministic 4:1 hash buckets',
                 'old_frozen_independent': False, 'new_frozen_created': False, 'groups': groups,
                 'future_frozen_gate': ['new diverse source topics', 'unseen independently reviewed questions',
                                       'group and paraphrase isolation', 'no use for tuning before final acceptance']}
    return report, candidate, partition


def probe_evidence(case: dict, snippets: list[dict], *, review_body_equivalence: bool = False) -> dict:
    """精确原文锚点诊断，不是语义正确率/Recall，更不验证可见性或生成引用。"""
    if case.get('original_query_status') == 'ambiguous_needs_clarification':
        raise ValueError('ambiguous original query cannot be scored using a clarification proposal')
    allowed = set(truths({'expect_hit': case['legacy_labels']['expect_hit']}))
    review_aliases = set(case.get('body_equivalent_sources_for_review_only', [])) if review_body_equivalence else set()
    matched, missing = [], []
    for atom in case['evidence_requirements']:
        if atom.get('required', True) is False:
            continue
        if not atom.get('any_of') or any(not str(ref.get('quote', '')).strip() for ref in atom['any_of']):
            raise ValueError('empty evidence atom/quote')
        hit = any(ref['source'] in allowed
                  and (ref['source'] == snippet.get('source') or snippet.get('source') in review_aliases)
                  and normalize(ref['quote']) in normalize(snippet.get('text') or '')
                  for ref in atom['any_of'] for snippet in snippets)
        (matched if hit else missing).append(atom['atom_id'])
    return {'case_id': case['id'], 'required_atoms': len(matched) + len(missing),
            'matched_atoms': matched, 'missing_atoms': missing,
            'all_required_present': bool(matched) and not missing,
            'metrics_publishable': False, 'scope_verified': False,
            'label_status': case['review_status'], 'mode': 'lexical_diagnostic_only',
            'source_policy': 'review_body_equivalence_not_approved' if review_body_equivalence else 'legacy_exact'}


def probe_archived_diagnosis(diagnosis: dict, candidate: dict) -> dict:
    """消费既有 dev 追踪，而非重跑检索；失败子集不得公布整体 Recall。"""
    run = diagnosis['run']
    if (run.get('dataset_sha256', '').lower() != candidate['origin_dataset_sha256'].lower()
            or run.get('split') not in {'dev', 'dev_failures_only'}
            or run.get('kb_version') != candidate['kb_version']):
        raise ValueError('diagnosis dataset/split/version mismatch')
    lookup = {c['id']: c for c in candidate['review_cases']}
    checked, seen = [], set()
    for old in diagnosis['cases']:
        c = lookup.get(old['id'])
        if (not c or old['id'] in seen or old.get('query') != c['query']
                or old.get('case', {}).get('split') != 'dev'):
            raise ValueError('diagnosis case identity/dev mismatch')
        seen.add(old['id'])
        top5 = old.get('trace', {}).get('rerank_top5', [])
        doc_ids = {s['doc_id'] for s in old['result']['sources']}
        final_snippets = [d for d in top5 if d['id'] in doc_ids]
        checked.append({'id': c['id'], 'before_answer_guard': probe_evidence(c, top5),
                        'returned_evidence': probe_evidence(c, final_snippets),
                        'body_equivalent_content_before_guard': probe_evidence(c, top5, review_body_equivalence=True),
                        'body_equivalent_content_returned': probe_evidence(c, final_snippets, review_body_equivalence=True),
                        'returned_source_count': len(old['result']['sources']),
                        'heading_only_top5': sum(bool(d.get('heading_only')) for d in top5)})
    return {'mode': 'archived_failed_dev_subset_lexical_probe', 'metrics_publishable': False,
            'current_retrieval_rerun': False, 'diagnosis_run_id': run.get('run_id'),
            'case_count': len(checked), 'cases': checked,
            'warning': 'Draft atoms, strict legacy source identity, no ACL validation; not semantic Recall or overall Hit@5.'}


def review_markdown(report: dict, candidate: dict) -> str:
    lines = ['# r3 候选评测审核清单', '', '机器提议不是人工金标；原 0.827 保留，不能运行本候选计算正式指标。',
             '先核对问题目的与原文，再确认 ACL/版本、来源等价与必要证据；不按当前系统输出改标签。', '',
             '| ID | 诊断分组 | 必要/支持证据数 | 阻塞事项 |', '|---|---|---:|---|']
    for case in candidate['review_cases']:
        required = sum(a.get('required', True) for a in case['evidence_requirements'])
        support = len(case['evidence_requirements']) - required
        lines.append(f"| {case['id']} | {case['proposed_split']} | {required}/{support} | {', '.join(case['blocking_issues'])} |")
    lines += ['', '## 原文定位示例', '']
    for cid in ('R01', 'E06', 'AC08', 'FV01'):
        case = next((c for c in candidate['review_cases'] if c['id'] == cid), None)
        if case:
            lines += [f'### {cid}', '', case['query'], '']
            for atom in case['evidence_requirements']:
                ref = atom['any_of'][0]
                loc = ref['location']
                locator = f"line {loc['line_start']}" + (f", record {loc['record_number']}, field {loc['field']}" if loc['kind'] == 'csv' else '')
                lines.append(f"- {atom['atom_id']}：`{ref['file']}` / `{ref['source']}` / {locator}：{ref['quote']}")
            lines.append('')
    return '\n'.join(lines) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--expected-dataset-sha256', required=True)
    parser.add_argument('--diagnosis', type=Path, help='可选：只读验证既有 dev 失败追踪的必要证据')
    parser.add_argument('--sop-review-decisions', type=Path, help='可选：应用有逐题决策的 SOP AI 语义复核，只改新候选')
    parser.add_argument('--check-test-index', action='store_true', help='SOP 复核时只读检查隔离 test 索引字段与来源可见性')
    parser.add_argument('--continue-candidate', type=Path, help='接续已审候选，必须提供父文件哈希并结合实验/知识卡复核')
    parser.add_argument('--expected-parent-sha256')
    parser.add_argument('--guide-card-review-decisions', type=Path, help='逐题实验/知识卡 AI 复核表，保留已审 SOP')
    args = parser.parse_args()
    if args.check_test_index and not args.sop_review_decisions:
        parser.error('--check-test-index requires --sop-review-decisions')
    continuation = [bool(args.continue_candidate), bool(args.expected_parent_sha256), bool(args.guide_card_review_decisions)]
    if any(continuation) and (not all(continuation) or args.sop_review_decisions):
        parser.error('guide/card review requires parent candidate + expected hash + decisions; cannot replay SOP review')
    dataset_bytes = args.dataset.read_bytes()
    dataset_hash = digest(dataset_bytes)
    if dataset_hash.lower() != args.expected_dataset_sha256.lower():
        raise ValueError('dataset checksum mismatch')
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    payload = json.loads(dataset_bytes)
    if manifest['dataset_version'] != payload['kb_version'] or args.raw_dir.name != payload['kb_version']:
        raise ValueError('corpus version mismatch')
    catalog, hashes = load_catalog(args.raw_dir, manifest)
    report, candidate, partition = audit(payload, catalog, hashes, dataset_hash)
    parent_bytes = args.continue_candidate.read_bytes() if args.continue_candidate else None
    guide_bytes = args.guide_card_review_decisions.read_bytes() if args.guide_card_review_decisions else None
    guide_review = None
    if parent_bytes:
        if digest(parent_bytes).lower() != args.expected_parent_sha256.lower():
            raise ValueError('parent candidate checksum mismatch')
        from scripts.guide_card_label_review import validate_parent_candidate, review_guide_card_candidate
        parent = json.loads(parent_bytes)
        validate_parent_candidate(parent, candidate)
        as_of = datetime.now(timezone.utc).isoformat(timespec='seconds')
        candidate, guide_review = review_guide_card_candidate(parent, catalog,
            json.loads((args.raw_dir / 'meta.json').read_bytes()), json.loads(guide_bytes), as_of)
    review_bytes = args.sop_review_decisions.read_bytes() if args.sop_review_decisions else None
    sop_review = None
    if review_bytes:
        from scripts.sop_label_review import review_sop_candidate, check_test_index_visibility
        as_of = datetime.now(timezone.utc).isoformat(timespec='seconds')
        candidate, sop_review = review_sop_candidate(candidate, catalog, json.loads(review_bytes), as_of)
        sop_review['index_check'] = (check_test_index_visibility(candidate, as_of=as_of) if args.check_test_index
                                     else {'status': 'not_checked', 'permission_complete': False})
    diagnosis_bytes = args.diagnosis.read_bytes() if args.diagnosis else None
    probe = probe_archived_diagnosis(json.loads(diagnosis_bytes), candidate) if diagnosis_bytes else None
    # 出口前再次核对输入，不接受生成期间变化的原文/标注。
    if args.dataset.read_bytes() != dataset_bytes or args.manifest.read_bytes() != manifest_bytes:
        raise ValueError('input changed during audit')
    if any(digest((args.raw_dir / n).read_bytes()) != h for n, h in hashes.items()):
        raise ValueError('corpus changed during audit')
    if diagnosis_bytes and args.diagnosis.read_bytes() != diagnosis_bytes:
        raise ValueError('diagnosis changed during audit')
    if review_bytes and args.sop_review_decisions.read_bytes() != review_bytes:
        raise ValueError('review decisions changed during audit')
    if parent_bytes and (args.continue_candidate.read_bytes() != parent_bytes
                        or args.guide_card_review_decisions.read_bytes() != guide_bytes):
        raise ValueError('parent/guide-card decisions changed during audit')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    prefix = 'r3-guide-card-review' if guide_review else 'r3-sop-review' if sop_review else 'r3-calibration'
    target = ROOT / 'eval_sets' / 'candidates' / f'{prefix}-{run_id}'
    target.mkdir(parents=True, exist_ok=False)
    metadata = {'run_id': run_id, 'dataset': str(args.dataset.resolve()), 'dataset_sha256': dataset_hash,
                'raw_dir': str(args.raw_dir.resolve()), 'manifest_sha256': digest(manifest_bytes),
                'corpus_sha256': digest(json.dumps(hashes, sort_keys=True).encode()),
                'tool_sha256': digest(Path(__file__).read_bytes()), 'input_changed_during_run': False,
                'diagnosis_sha256': digest(diagnosis_bytes) if diagnosis_bytes else None,
                'sop_decisions_sha256': digest(review_bytes) if review_bytes else None,
                'sop_review_component_sha256': digest((ROOT / 'scripts/sop_label_review.py').read_bytes()) if sop_review else None,
                'parent_candidate': str(args.continue_candidate.resolve()) if parent_bytes else None,
                'parent_candidate_sha256': digest(parent_bytes) if parent_bytes else None,
                'guide_card_decisions_sha256': digest(guide_bytes) if guide_bytes else None,
                'guide_card_component_sha256': digest((ROOT / 'scripts/guide_card_label_review.py').read_bytes()) if guide_review else None,
                'index_rechecked_in_this_run': bool(args.check_test_index),
                'production_parameters_modified': False, 'llm_called': False, 'frozen_evaluated': False}
    for name, value in [('audit.json', report), ('candidate-review.json', candidate),
                        ('partition-plan.json', partition), ('run-metadata.json', metadata)]:
        (target / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (target / 'REVIEW.md').write_text(review_markdown(report, candidate), encoding='utf-8')
    if probe:
        (target / 'evidence-probe.json').write_text(json.dumps(probe, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if sop_review:
        (target / 'sop-review.json').write_text(json.dumps(sop_review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if guide_review:
        (target / 'guide-card-review.json').write_text(json.dumps(guide_review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(target), 'summary': report['summary'],
                      'sop_review': sop_review['summary'] if sop_review else None,
                      'guide_card_review': guide_review['summary'] if guide_review else None,
                      'index_status': sop_review['index_check']['status'] if sop_review else None}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
