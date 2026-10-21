#!/usr/bin/env python
"""只读追踪 dev bad cases：不改线上参数、真值、缓存、版本或知识内容。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.application.qa_usecase import QaUseCase
from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.intent.rules import classify_by_rules
from qa_core.retrieval import searcher as searcher_module
from qa_core.retrieval.searcher import Searcher
from qa_core.rerank import reranker as reranker_module
from qa_core.security.policy import AccessScope
from scripts.run_eval import (_assert_version_registered, _config_metadata, _git_metadata,
                              judge, select_cases)


def canonical_body(text: str) -> str:
    """仅忽略首行标题与空白；不抹掉正文编号、条件、版本、设备或步骤。"""
    lines = unicodedata.normalize('NFKC', text).splitlines()
    if lines and lines[0].lstrip().startswith('# '):
        lines = lines[1:]
    return re.sub(r'\s+', '', '\n'.join(lines))


def expected_sources(case: dict) -> list[str]:
    value = case.get('expect_hit')
    return [value] if isinstance(value, str) else list(value or [])


def source_rank(items: list[dict], expected: list[str]) -> int | None:
    wanted = set(expected)
    return next((i for i, item in enumerate(items, 1) if item.get('source') in wanted), None)


def heading_only(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines and lines[0].startswith('#') and len(lines) <= 2
                and not re.search(r'步骤\s*\d+', text))


def summarize_doc(doc) -> dict:
    return {'id': doc.id, 'source': doc.source, 'doc_type': doc.doc_type,
            'text': doc.text, 'clause': doc.clause, 'parent_id': doc.parent_id,
            'dense_score': doc.dense_score, 'sparse_score': doc.sparse_score,
            'fusion_score': doc.fusion_score, 'rerank_score': doc.rerank_score,
            'heading_only': heading_only(doc.text or '')}


def summarize_hit(hit: dict) -> dict:
    entity = hit.get('entity') or {}
    return {'id': hit['id'], 'source': entity.get('source', ''),
            'doc_type': entity.get('doc_type', ''), 'text': entity.get('text', ''),
            'distance': float(hit.get('distance', 0)), 'parent_id': entity.get('parent_id', '')}


class MemoryOnlyCache:
    """本进程复用 embedding；不读写 Redis，不把追踪候选写回线上缓存。"""
    def __init__(self):
        self.values = {}

    def get(self, *key):
        return self.values.get(key) if key and key[0] == 'emb' else None

    def get_with_lock(self, *key):
        return None

    def set(self, *args, **kwargs):
        if args and args[0] == 'emb':
            self.values[args[:-1]] = args[-1]

    def set_empty(self, *args, **kwargs):
        pass


class NoGeneration:
    def chat(self, *args, **kwargs):
        raise AssertionError('diagnosis must not generate')

    chat_stream = chat


class TracingSearcher(Searcher):
    def __init__(self, **kwargs):
        super().__init__(cache=MemoryOnlyCache(), **kwargs)
        self.trace = {}

    def _recall(self, variants, expr, top_k):
        dense, sparse = super()._recall(variants, expr, top_k)
        self.trace.setdefault('recalls', []).append({
            'query_variants': variants, 'filter': expr, 'top_k_per_variant': top_k,
            'dense': [summarize_hit(h) for h in dense],
            'sparse': [summarize_hit(h) for h in sparse]})
        return dense, sparse

    def search_docs(self, query_variants, roles, kb_version, **kwargs):
        self.trace['requested_doc_types'] = kwargs.get('doc_types')
        result = super().search_docs(query_variants, roles, kb_version, **kwargs)
        self.trace['filter_relaxed'] = result.filter_relaxed
        return result


class TracingReranker:
    def __init__(self, real, trace):
        self.real, self.trace = real, trace

    def rerank(self, query, docs, top_n=5):
        self.trace['rerank_query'] = query
        self.trace['rerank_input'] = [summarize_doc(d) for d in docs]
        result = self.real.rerank(query, docs, top_n)
        self.trace['rerank_all'] = [summarize_doc(d) for d in sorted(
            docs, key=lambda d: d.rerank_score, reverse=True)]
        self.trace['rerank_top5'] = [summarize_doc(d) for d in result]
        return result


def stage_ranks(trace: dict, result: dict, expected: list[str]) -> dict:
    recall = (trace.get('recalls') or [{}])[-1]
    ranks = {name: source_rank(recall.get(name, []), expected) for name in ('dense', 'sparse')}
    for name in ('fused', 'parent_dedup', 'source_cap', 'rerank_input', 'rerank_all', 'rerank_top5'):
        ranks[name] = source_rank(trace.get(name, []), expected)
    ranks['returned_sources'] = source_rank(result.get('sources') or [], expected)
    return ranks


def loss_stage(ranks: dict, visible: bool) -> str:
    if not visible:
        return 'expected_not_visible_in_index'
    if ranks['dense'] is None and ranks['sparse'] is None:
        return 'not_in_recall'
    for stage, label in [('fused', 'lost_in_fusion'), ('parent_dedup', 'parent_dedup'),
                         ('rerank_input', 'candidate_cutoff'), ('rerank_top5', 'rerank_demoted'),
                         ('returned_sources', 'answer_guard_or_route')]:
        if ranks[stage] is None:
            return label
    return 'replay_hit_different_from_baseline'


def select_failed_dev(payload: dict, baseline: dict, dataset_hash: str) -> list[dict]:
    if baseline.get('run', {}).get('split') != 'dev':
        raise ValueError('只接受 dev 基线；不得借诊断读取 frozen/test')
    if baseline['run'].get('dataset_sha256') != dataset_hash:
        raise ValueError('数据集哈希与基线不符')
    failed = {item['id']: item for item in baseline['results'] if item.get('hit_ok') is False}
    selected = [case for case in select_cases(payload, 'dev') if case['id'] in failed]
    if not selected or len(selected) != len(failed):
        raise ValueError('基线失败题并非全部属于当前 dev 集')
    for case in selected:
        if case['query'] != failed[case['id']]['query']:
            raise ValueError('基线与题目 query 不同')
    return selected


def document_audit(raw_dir: Path, case: dict, result: dict) -> dict:
    expected = expected_sources(case)
    comparisons = []
    positives = {}
    for source in expected:
        file = raw_dir / Path(source).name
        if source.endswith('.md') and file.is_file():
            positives[source] = canonical_body(file.read_text(encoding='utf-8'))
    sources = list(dict.fromkeys([s.get('source', '') for s in result.get('sources', [])]
                                + list(case.get('hard_negatives') or [])))
    for source in sources:
        file = raw_dir / Path(source).name
        if not source.endswith('.md') or not file.is_file():
            continue
        body = canonical_body(file.read_text(encoding='utf-8'))
        matches = [name for name, wanted in positives.items() if body == wanted]
        comparisons.append({'source': source, 'raw_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                            'body_matches_expected': matches,
                            'declared_hard_negative': source in (case.get('hard_negatives') or [])})
    return {'comparison_rule': 'ignore_first_heading_and_whitespace_only',
            'expected_raw_hashes': {s: hashlib.sha256((raw_dir / s).read_bytes()).hexdigest()
                                    for s in positives}, 'comparisons': comparisons,
            'equivalent_document_is_not_a_complete_evidence_claim': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--raw-dir', required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    problems = validate_isolated_environment(settings, 'test')
    if problems:
        parser.error('仅限隔离 test：' + '; '.join(problems))
    dataset_path, baseline_path, raw_dir = Path(args.dataset), Path(args.baseline), Path(args.raw_dir)
    dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    payload = json.loads(dataset_path.read_text(encoding='utf-8'))
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    try:
        cases = select_failed_dev(payload, baseline, dataset_hash)
    except ValueError as exc:
        parser.error(str(exc))
    version = payload['kb_version']
    if baseline['kb_version'] != version:
        parser.error('基线知识版本与数据集不同')
    if raw_dir.name != version or not raw_dir.is_dir():
        parser.error('原文目录必须存在且以固定知识版本命名，避免审核错版本')
    _assert_version_registered(version)
    searcher = TracingSearcher(settings=settings)
    uc = QaUseCase(searcher=searcher, llm=NoGeneration())
    config_before, code_before = _config_metadata(uc), _git_metadata()
    baseline_code = baseline['run'].get('code', {}).get('code_sha256')
    if baseline_code != code_before['code_sha256']:
        print('注意：当前代码指纹与基线不同（含新增诊断脚本），报告记录两者，不冒充完全同版复跑。')
    original_fuse, original_dedup, original_cap = searcher_module.fuse, searcher_module.dedup_by_parent, searcher_module.cap_per_source
    original_reranker = reranker_module.get_reranker
    rows = []
    for case in cases:
        trace = searcher.trace = {}
        def capture(name, function):
            def wrapped(*a, **kw):
                docs = function(*a, **kw)
                trace[name] = [summarize_doc(d) for d in docs]
                return docs
            return wrapped
        with ExitStack() as stack:
            stack.enter_context(patch.object(searcher_module, 'fuse', capture('fused', original_fuse)))
            stack.enter_context(patch.object(searcher_module, 'dedup_by_parent', capture('parent_dedup', original_dedup)))
            stack.enter_context(patch.object(searcher_module, 'cap_per_source', capture('source_cap', original_cap)))
            stack.enter_context(patch.object(reranker_module, 'get_reranker',
                lambda *a, **kw: TracingReranker(original_reranker(*a, **kw), trace)))
            result = uc.ask(case['query'], roles=['ops'], kb_version=version, persist=False, use_llm=False)
        expected = expected_sources(case)
        access = AccessScope.build(tenant_id='default', roles=['ops'])
        expr = searcher._filter_expr(settings.doc_collection, access, version)
        expr += ' and source in ' + json.dumps(expected, ensure_ascii=False)
        fields_out = searcher._supported_output_fields(settings.doc_collection, ['text', 'source', 'doc_type', 'clause', 'parent_id'])
        visible = searcher._call('query', collection_name=settings.doc_collection, filter=expr,
                                output_fields=fields_out, limit=2048)
        ranks = stage_ranks(trace, result, expected)
        row = {'id': case['id'], 'query': case['query'], 'case': case,
               'initial_rule': classify_by_rules(case['query'], uc.cfg).to_dict(),
               'result': result, 'judged': judge(case, result), 'trace': trace,
               'expected_visible_chunk_count': len(visible),
               'expected_visible_samples': visible[:5],
               'stage_ranks': ranks, 'loss_stage': loss_stage(ranks, bool(visible)),
               'document_audit': document_audit(raw_dir, case, result)}
        rows.append(row)
        print(f"[{case['id']}] {row['loss_stage']} ranks={ranks} returned={row['judged']['got_sources']}", flush=True)
    code_after = _git_metadata()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    report = {'run': {'run_id': run_id, 'created_at': datetime.now(timezone.utc).isoformat(),
                      'dataset_sha256': dataset_hash, 'baseline_path': str(baseline_path),
                      'baseline_sha256': hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                      'baseline_code_sha256': baseline_code, 'code': code_before,
                      'code_changed_during_run': code_before['code_sha256'] != code_after['code_sha256'],
                      'config': config_before, 'kb_version': version, 'split': 'dev_failures_only',
                      'llm_mode': 'blocked', 'cache_policy': 'process_local_embedding_only_no_redis',
                      'truth_modified': False, 'production_parameters_modified': False,
                      'notes': '只诊断已知失败题，不能据此计算新总体 Hit@5 或泛化提升'},
              'summary': dict(Counter(row['loss_stage'] for row in rows)), 'cases': rows}
    output = Path(settings.reports_dir) / f'retrieval_diagnosis_{run_id}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'report -> {output}', flush=True)
    print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
    if report['run']['code_changed_during_run']:
        raise SystemExit('诊断期间代码发生变化，不可作为固定代码报告')


if __name__ == '__main__':
    main()
