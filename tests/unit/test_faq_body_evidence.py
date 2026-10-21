from qa_core.application.qa_usecase import _citation_answer, evidence_covers
from qa_core.retrieval.evidence_text import rerank_text
from qa_core.retrieval.relevance import relevant_docs
from qa_core.schemas.retrieval import RetrievedDoc


def faq(answer):
    return RetrievedDoc('faq', '接口说明', .8, doc_type='faq', question='接口说明', answer=answer)


def test_body_supports_followup_beyond_title_and_first_1500_characters():
    doc = faq('前言。' * 600 + '\nPFCP请求源端口由本地分配，响应发往请求源端口。')
    assert evidence_covers('PFCP请求源端口响应发往哪里', [doc], .4)[0]
    assert 'PFCP' in rerank_text('PFCP请求源端口', doc)[:100]
    assert not evidence_covers('驻波比天线故障', [doc], .4)[0]


def test_complete_faq_keeps_limits_and_separates_original_footnotes():
    raw = '正文' * 300 + '\nFR2不能套用FR1表格。[1][4]'
    doc = faq(raw)
    answer = _citation_answer([doc], generation_disabled=True)
    assert 'FR2不能套用FR1表格。〔原文注1〕〔原文注4〕' in answer
    assert answer.count('[1]') == 1 and '[4]' not in answer
    assert doc.answer == raw


def test_protocol_and_band_entities_exclude_neighboring_faqs():
    ports = faq('AMF SCTP 38412；N4 PFCP UDP 8805，TCP Reserved。')
    power = faq('FR1 n78 PC2 26 dBm；FR2和CA不能直接套用。')
    ngap = faq('AMF SCTP NG SETUP FAILURE。')
    assert relevant_docs('PFCP请求源端口8805吗', [ports, power, ngap]) == [ports]
    assert relevant_docs('AMF动态SCTP关联只能38412吗', [ports, ngap]) == [ports]
    assert relevant_docs('n78 PC2永远26 dBm吗', [ports, power]) == [power]


def test_faq_topic_filter_runs_before_five_candidate_budget(monkeypatch):
    from unittest.mock import Mock
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.schemas.retrieval import RetrievalResult
    from qa_core.schemas.intent import IntentDecision
    case = QaUseCase(searcher=Mock(), llm=Mock())
    case.settings = case.settings.model_copy(update={'workflow_evidence_enabled': False})
    case.searcher.search_docs.return_value = RetrievalResult([], [])
    correct = faq('PFCP请求目的端口8805，源端口由本地分配。')
    correct.id = 'supported'
    noise = [faq('PFCP协议概述，不包含端口依据。') for _ in range(5)]
    ranker = Mock()
    ranker.rerank.side_effect = lambda q, docs, **kw: docs
    monkeypatch.setattr('qa_core.rerank.reranker.get_reranker', lambda *a: ranker)
    docs = case._retrieve_and_rerank('PFCP请求源端口8805吗', ['admin'], 'v',
        IntentDecision('knowledge','retrieval','rule',.9,'test'), extra_docs=noise+[correct])
    assert docs == [correct]
    assert not case.llm.mock_calls


def test_body_lookup_keeps_access_scope_and_does_not_claim_dense_match():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from qa_core.retrieval.searcher import Searcher
    from qa_core.security.policy import AccessScope
    searcher = Searcher.__new__(Searcher)
    searcher.settings = SimpleNamespace(faq_collection='faqs',mysql_dsn='unused',dense_ef=64)
    searcher.cache = Mock();searcher.cache.get.return_value = None
    searcher._field_cache = {'faqs': {'question'}}
    guard = 'tenant_id == "tenant-a" and kb_version == "v" and visibility == "role_restricted"'
    searcher._filter_expr = Mock(return_value=guard)
    searcher._supported_output_fields = lambda c, f: f
    searcher._embed_query_cached = Mock(return_value=[0.0])
    row = dict(id='body',question='接口协议说明',answer='PFCP UDP 8805，请求源端口本地分配。',
        source='faq.csv',kb_version='v',tenant_id='tenant-a',source_id='approved',source_system='local')
    searcher._call = Mock(side_effect=[[],[[]],[row]])
    docs = searcher.search_faq('PFCP请求源端口8805吗',['ops'],'v',
        scope=AccessScope.build(tenant_id='tenant-a',roles=['ops']))
    assert docs[0].answer == row['answer'] and docs[0].dense_score == 0
    request = searcher._call.call_args.kwargs
    assert request['filter'].startswith(guard+' and ') and '8805' in request['filter'] and 'PFCP' in request['filter']
    assert request['consistency_level']=='Strong' and request['limit']==20


def test_generation_context_does_not_mix_source_footnotes_with_kb_ids():
    from qa_core.generation.context import build_messages
    raw='请求目的端口8805，源端口本地分配。[2][4]'
    doc=faq(raw)
    prompt=build_messages('请求源端口是什么',[doc])[0]['content']
    assert '〔原文注2〕〔原文注4〕' in prompt
    assert '[2]' not in prompt and '[4]' not in prompt
    assert '[1] 来源:' in prompt and doc.answer==raw
