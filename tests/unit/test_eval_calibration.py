"""离线评测校准：不访问 Milvus/Redis/LLM，不动历史数据。"""
import copy
import csv
import io
import json

import pytest

from scripts.calibrate_eval_set import (audit, canonical_body, digest, group_sources,
                                        load_catalog, probe_archived_diagnosis, probe_evidence, propose_anchors,
                                        truths)


def item(source, text, topic='ALM-AMF-REG', row=None, scope=None):
    body = canonical_body(text) if row is None else json.dumps(row, sort_keys=True)
    h = digest(body.encode())
    return {'source': source, 'file': source if row is None else 'data.csv', 'file_sha256': 'hash',
            'body_sha256': h, 'body_family': 'body:' + h, 'topics': [topic] if topic else [],
            'text': text, 'row': row, 'authorization_verified': False,
            'scope_snapshot': scope or {'tenant_id': 'default'},
            'location': {'kind': 'markdown', 'line_start': 1} if row is None else
                        {'kind': 'csv', 'record_number': 1, 'line_start': 2, 'line_end': 2}}


def case(cid='R01', source='sop_operation_001.md', split='dev', template='sop_by_alarm',
         category='retrieval', negative=None):
    return {'id': cid, 'query': '流程有哪些步骤？', 'split': split, 'template_id': template,
            'category': category, 'expect_hit': source, 'expect_route': 'retrieval',
            'expect_reject': False, 'hard_negatives': negative or []}


@pytest.mark.parametrize('change', ['软件 V2', '有效期 2027', '对象 SMF', '成功率 95%'])
def test_body_canonicalization_keeps_real_conditions(change):
    a = '# SOP 001\n软件 V1\n有效期 2026\n对象 AMF\n成功率 90%'
    assert canonical_body(a) == canonical_body(a.replace('SOP 001', 'SOP 081'))
    assert canonical_body(a) != canonical_body('# SOP 001\n' + change)


def test_group_keeps_identical_bodies_and_topic_variants_together():
    catalog = {'a': item('a', '# a\n步骤1：检查版本'),
               'b': item('b', '# b\n步骤1：检查版本', scope={'tenant_id': 'other'}),
               'c': item('c', '# c\n实验指标'),
               'd': item('d', '# d\n其他流程', 'ALM-SMF-PDU')}
    mapping, groups = group_sources(catalog)
    assert mapping['a'] == mapping['b'] == mapping['c']
    assert mapping['d'] != mapping['a']
    assert {g['proposed_split'] for g in groups} <= {'dev', 'regression'}
    assert not any(g['independent_frozen_eligible'] for g in groups)


def test_grouping_is_deterministic_independent_of_inventory_order():
    catalog = {'a': item('a', 'a'), 'b': item('b', 'b', 'ALM-SMF-PDU')}
    assert group_sources(catalog) == group_sources(dict(reversed(list(catalog.items()))))


def test_conflicting_negative_is_flagged_not_deleted_or_accepted_as_alias():
    source, negative = 'sop_operation_001.md', 'sop_operation_021.md'
    catalog = {source: item(source, '# 001\n步骤 1：确认影响范围。'),
               negative: item(negative, '# 021\n步骤 1：确认影响范围。')}
    payload = {'kb_version': 'r3', 'cases': [case(negative=[negative])]}
    original = copy.deepcopy(payload)
    report, draft, _ = audit(payload, catalog, {'a.md': 'hash', 'meta.json': 'hash'}, 'dataset-hash')
    assert report['summary']['hard_negative_conflicts'] == 1
    assert draft['review_cases'][0]['legacy_labels']['hard_negatives'] == [negative]
    assert draft['review_cases'][0]['accepted_aliases'] == []
    assert draft['review_cases'][0]['body_equivalent_sources_for_review_only'] == [negative]
    assert draft['review_cases'][0]['review_status'] == 'pending_review'
    assert payload == original


def test_frozen_audit_only_exports_identity_and_split_not_query():
    catalog = {'a': item('a', 'body1'), 'b': item('b', 'body2')}
    payload = {'kb_version': 'r3', 'cases': [case(source='a'),
                dict(case('FROZEN01', source='b', split='frozen'), query='DO_NOT_EXPORT_FROZEN_QUERY')]}
    report, draft, partition = audit(payload, catalog, {'meta.json': 'hash'}, 'hash')
    assert 'DO_NOT_EXPORT_FROZEN_QUERY' not in json.dumps([report, draft, partition])
    assert len(draft['review_cases']) == 1
    assert report['summary']['legacy_cross_split_groups'] == 1
    assert 'cases' not in draft and not draft['metrics_publishable']
    assert report['summary']['independent_frozen_cases'] == 0


def test_proposed_positive_topic_group_never_crosses_splits():
    catalog = {'a': item('a', 'a'), 'b': item('b', 'b'), 'c': item('c', 'c', 'ALM-SMF-PDU')}
    payload = {'kb_version': 'r3', 'cases': [case('A', 'a'), case('B', 'b'), case('C', 'c')]}
    _, draft, _ = audit(payload, catalog, {'meta.json': 'hash'}, 'hash')
    assignments = {}
    for c in draft['review_cases']:
        assignments.setdefault(c['group_id'], set()).add(c['proposed_split'])
    assert all(len(splits) == 1 for splits in assignments.values())


def test_missing_truth_fails_instead_of_silently_dropping_case():
    with pytest.raises(ValueError, match='missing truth'):
        audit({'cases': [case()], 'kb_version': 'r3'}, {}, {'meta.json': 'h'}, 'h')


def test_multi_topic_truth_cannot_be_partitioned_arbitrarily():
    catalog = {'a': item('a', 'a'), 'b': item('b', 'b', 'ALM-SMF-PDU')}
    with pytest.raises(ValueError, match='multi-group'):
        audit({'cases': [case(source=['a', 'b'])], 'kb_version': 'r3'}, catalog, {'meta.json': 'h'}, 'h')


def test_sop_anchor_requires_step_evidence_not_title():
    text = '# SOP\n适用：AMF；ALM-AMF-REG\n' + '\n'.join(f'步骤 {i}：执行检查{i}。条件不全转人工。' for i in range(1, 11))
    catalog = {'sop_operation_001.md': item('sop_operation_001.md', text)}
    atoms = propose_anchors(case(), catalog)
    assert len(atoms) == 10
    assert [a['atom_id'] for a in atoms] == [f'step_{i:02d}' for i in range(1, 11)]
    draft = {'id': 'R01', 'review_status': 'pending_review',
             'legacy_labels': {'expect_hit': 'sop_operation_001.md'}, 'evidence_requirements': atoms}
    heading = probe_evidence(draft, [{'source': 'sop_operation_001.md', 'text': '# SOP\n适用：AMF；ALM-AMF-REG'}])
    assert len(heading['missing_atoms']) == 10
    full = probe_evidence(draft, [{'source': 'sop_operation_001.md', 'text': text}])
    assert full['all_required_present'] and not full['metrics_publishable']
    assert not full['scope_verified']
    assert not probe_evidence(draft, [{'source': 'wrong', 'text': text}])['all_required_present']


def test_same_body_review_alias_is_not_eligible_in_evidence_probe():
    draft = {'id': 'R', 'review_status': 'pending_review', 'legacy_labels': {'expect_hit': 'a'},
             'body_equivalent_sources_for_review_only': ['b'],
             'evidence_requirements': [{'atom_id': 'step', 'any_of': [{'source': 'a', 'quote': '步骤1：核对'}]}]}
    assert not probe_evidence(draft, [{'source': 'b', 'text': '步骤1：核对'}])['all_required_present']
    content = probe_evidence(draft, [{'source': 'b', 'text': '步骤1：核对'}], review_body_equivalence=True)
    assert content['all_required_present']
    assert content['source_policy'] == 'review_body_equivalence_not_approved'
    assert not content['metrics_publishable'] and not content['scope_verified']
    assert not probe_evidence(draft, [{'source': 'b', 'text': '# 处置流程'}], review_body_equivalence=True)['all_required_present']


def test_experiment_requires_concrete_metrics_units_and_context():
    text = '# 实验\n## 阶段1\n对象为NRF，注入条件为注册过期，观测告警ALM-NRF-DISC。控制面成功率93%、模拟时延46毫秒、队列占用32%。'
    catalog = {'guide.md': item('guide.md', text, 'ALM-NRF-DISC')}
    c = case(source='guide.md', template='exp_guide_topic')
    atoms = propose_anchors(c, catalog)
    assert {a['atom_id'] for a in atoms} == {'success_rate', 'latency', 'queue', 'experiment_context'}
    draft = {'id': 'E', 'review_status': 'pending_review', 'legacy_labels': {'expect_hit': 'guide.md'}, 'evidence_requirements': atoms}
    assert not probe_evidence(draft, [{'source': 'guide.md', 'text': '执行采集关键指标'}])['all_required_present']
    assert probe_evidence(draft, [{'source': 'guide.md', 'text': text}])['all_required_present']


def test_faq_anchor_uses_answer_not_question():
    row = {'source': 'FAQ-1', 'question': 'AMF如何处理？', 'answer': '先核对对象版本。然后复核。'}
    catalog = {'FAQ-1': item('FAQ-1', 'AMF如何处理？\n先核对对象版本。然后复核。', row=row)}
    atoms = propose_anchors(case(source='FAQ-1', category='faq_exact', template='faq_verbatim'), catalog)
    assert atoms[0]['any_of'][0]['location']['field'] == 'answer'
    assert atoms[0]['any_of'][0]['quote'] == '先核对对象版本'


def test_empty_anchor_cannot_match_every_snippet():
    draft = {'id': 'R', 'review_status': 'pending_review', 'legacy_labels': {'expect_hit': 'a'},
             'evidence_requirements': [{'atom_id': 'bad', 'any_of': [{'source': 'a', 'quote': ''}]}]}
    with pytest.raises(ValueError, match='empty evidence'):
        probe_evidence(draft, [{'source': 'a', 'text': 'anything'}])


def write_corpus(tmp_path, data):
    manifest = {'files': [{'filename': name, 'sha256': digest(text.encode('utf-8'))} for name, text in data.items()]}
    for name, text in data.items():
        (tmp_path / name).write_text(text, encoding='utf-8', newline='')
    (tmp_path / 'meta.json').write_text(json.dumps({n: {'tenant_id': 'default'} for n in data}), encoding='utf-8')
    return manifest


def test_raw_hash_drift_fails(tmp_path):
    manifest = write_corpus(tmp_path, {'a.md': '# a\nbody'})
    (tmp_path / 'a.md').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        load_catalog(tmp_path, manifest)


def test_untracked_raw_file_is_not_silently_ignored(tmp_path):
    manifest = write_corpus(tmp_path, {'a.md': 'body'})
    (tmp_path / 'unexpected.md').write_text('more', encoding='utf-8')
    with pytest.raises(ValueError, match='inventory'):
        load_catalog(tmp_path, manifest)


def test_metadata_missing_entry_fails(tmp_path):
    manifest = write_corpus(tmp_path, {'a.md': 'body'})
    (tmp_path / 'meta.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='metadata/raw'):
        load_catalog(tmp_path, manifest)


def test_duplicate_csv_source_identity_fails(tmp_path):
    manifest = write_corpus(tmp_path, {'a.csv': 'source,answer\nsame,one\nsame,two\n'})
    with pytest.raises(ValueError, match='duplicate'):
        load_catalog(tmp_path, manifest)


def test_multiline_csv_quote_has_physical_lines_and_record_number(tmp_path):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['source', 'question', 'answer'])
    writer.writerow(['FAQ-1', '问题', '第一行\n第二行'])
    manifest = write_corpus(tmp_path, {'a.csv': stream.getvalue()})
    catalog, hashes = load_catalog(tmp_path, manifest)
    assert catalog['FAQ-1']['location'] == {'kind': 'csv', 'record_number': 1, 'line_start': 2, 'line_end': 3}
    assert 'meta.json' in hashes
    assert catalog['FAQ-1']['scope_snapshot']['tenant_id'] == 'default'
    assert not catalog['FAQ-1']['authorization_verified']


@pytest.mark.parametrize('value', ['', 1, [], ['a', 2]])
def test_invalid_truth_shape_fails(value):
    with pytest.raises(ValueError, match='invalid truth'):
        truths({'expect_hit': value})


def probe_fixture():
    draft = {'kb_version': 'r3', 'origin_dataset_sha256': 'hash',
             'review_cases': [{'id': 'R', 'query': 'q', 'review_status': 'pending_review',
                               'legacy_labels': {'expect_hit': 'a'},
                               'evidence_requirements': [{'atom_id': 'step', 'any_of': [{'source': 'a', 'quote': '步骤1：检查'}]}]}]}
    diagnosis = {'run': {'dataset_sha256': 'hash', 'split': 'dev', 'kb_version': 'r3'},
                 'cases': [{'id': 'R', 'query': 'q', 'case': {'split': 'dev'},
                            'result': {'sources': []},
                            'trace': {'rerank_top5': [{'source': 'a', 'id': 'chunk1', 'text': '步骤1：检查'}]}}]}
    return draft, diagnosis


@pytest.mark.parametrize('split', ['dev', 'dev_failures_only'])
def test_archived_probe_distinguishes_preguard_and_returned_evidence(split):
    draft, diagnosis = probe_fixture()
    diagnosis['run']['split'] = split
    result = probe_archived_diagnosis(diagnosis, draft)
    assert result['cases'][0]['before_answer_guard']['all_required_present']
    assert not result['cases'][0]['returned_evidence']['all_required_present']
    assert not result['metrics_publishable'] and not result['current_retrieval_rerun']


def test_archived_probe_rejects_frozen_and_mismatched_dataset():
    draft, diagnosis = probe_fixture()
    diagnosis['run']['split'] = 'frozen'
    with pytest.raises(ValueError, match='mismatch'):
        probe_archived_diagnosis(diagnosis, draft)
    diagnosis['run']['split'] = 'dev'
    diagnosis['run']['dataset_sha256'] = 'other'
    with pytest.raises(ValueError, match='mismatch'):
        probe_archived_diagnosis(diagnosis, draft)


def test_archived_probe_rejects_unknown_case_and_changed_query():
    draft, diagnosis = probe_fixture()
    diagnosis['cases'][0]['query'] = 'changed'
    with pytest.raises(ValueError, match='identity'):
        probe_archived_diagnosis(diagnosis, draft)
    diagnosis['cases'][0]['query'] = 'q'
    diagnosis['cases'][0]['id'] = 'not-dev'
    with pytest.raises(ValueError, match='identity'):
        probe_archived_diagnosis(diagnosis, draft)
