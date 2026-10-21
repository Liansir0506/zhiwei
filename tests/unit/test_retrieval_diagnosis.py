import pytest

from scripts.diagnose_retrieval_failures import (MemoryOnlyCache, NoGeneration,
                                                canonical_body, heading_only, loss_stage,
                                                select_failed_dev, source_rank)


def test_title_only_equivalence_preserves_real_conditions():
    assert canonical_body('# SOP 001\n适用 AMF\n步骤1：检查版本') == canonical_body('# SOP 081\n适用 AMF\n步骤1：检查版本')
    assert canonical_body('# SOP 001\n软件 V1') != canonical_body('# SOP 081\n软件 V2')


def test_document_identity_does_not_prove_chunk_evidence():
    assert heading_only('# AMF SOP 001\n适用：AMF/5G；告警 ALM-AMF-REG')
    assert not heading_only('# AMF SOP\n步骤 1：核对最近变更')
    assert not heading_only('步骤 1：核对最近变更')


def test_diagnostic_ranks_use_exact_source_identity():
    assert source_rank([{'source': 'FAQ-00010'}, {'source': 'FAQ-0001'}], ['FAQ-0001']) == 2
    assert source_rank([{'source': 'other'}], ['expected']) is None


def test_failure_selection_excludes_frozen():
    data = {'cases': [{'id': 'dev1', 'query': 'q', 'split': 'dev'},
                      {'id': 'frozen1', 'query': 'q2', 'split': 'frozen'}]}
    baseline = {'run': {'split': 'dev', 'dataset_sha256': 'hash'},
                'results': [{'id': 'dev1', 'query': 'q', 'hit_ok': False}]}
    assert [c['id'] for c in select_failed_dev(data, baseline, 'hash')] == ['dev1']
    baseline['results'].append({'id': 'frozen1', 'query': 'q2', 'hit_ok': False})
    with pytest.raises(ValueError, match='dev'):
        select_failed_dev(data, baseline, 'hash')
    with pytest.raises(ValueError, match='哈希'):
        select_failed_dev(data, baseline, 'other')


def test_loss_stage_separates_candidate_cutoff_and_rejection():
    ranks = {key: 1 for key in ('dense', 'sparse', 'fused', 'parent_dedup',
                               'rerank_input', 'rerank_top5', 'returned_sources')}
    ranks['rerank_input'] = None
    assert loss_stage(ranks, True) == 'candidate_cutoff'
    ranks['rerank_input'] = 1
    ranks['returned_sources'] = None
    assert loss_stage(ranks, True) == 'answer_guard_or_route'
    assert loss_stage(ranks, False) == 'expected_not_visible_in_index'


def test_memory_cache_never_serves_retrieval_or_faq_and_reuses_embedding():
    cache = MemoryOnlyCache()
    cache.set('emb', 'v', 'scope', 'sig', [1, 2])
    assert cache.get('emb', 'v', 'scope', 'sig') == [1, 2]
    cache.set('ret', 'v', 'scope', 'sig', ['stale'])
    assert cache.get('ret', 'v', 'scope', 'sig') is None
    assert cache.get_with_lock('ret', 'v', 'scope', 'sig') is None
    with pytest.raises(AssertionError, match='generate'):
        NoGeneration().chat([])
