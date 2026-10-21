from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from qa_core.application.qa_usecase import QaUseCase, NO_EVIDENCE
from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.schemas.intent import IntentDecision
from qa_core.retrieval.relevance import relevant_docs
from qa_core.retrieval.searcher import Searcher


def doc(text, **kwargs):
    return RetrievedDoc('d1', text, .9, rerank_score=.9, source='manual.md',
                        kb_version='v-test', **kwargs)


@pytest.mark.parametrize('query,text', [
    ('乒乓切换怎么处理', 'NR切换失败率升高处置，请核对邻区参数'),
    ('BBU掉电告警怎么处理', 'NGAP连接中断，检查SCTP路径'),
    ('驻波比多少需要处理', '天线故障，检查设备版本'),
    ('N2和N4接口用什么协议端口', 'N2接口使用SCTP'),
])
@pytest.mark.parametrize('stream', [False, True])
def test_wrong_subject_never_becomes_answer_or_citation(monkeypatch, query, text, stream):
    case = QaUseCase(searcher=Mock(), llm=Mock())
    case.settings = case.settings.model_copy(update={'generation_enabled': False})
    monkeypatch.setattr(case, '_retrieve_and_rerank', lambda *a, **kw: [doc(text)])
    decision = IntentDecision('knowledge', 'retrieval', 'rule', .9, 'test')
    if stream:
        chunks, _, sources, _ = case._stream_retrieval(query, [], 'v-test', decision)
        assert chunks is None and sources == []
    else:
        answer, sources, _, _ = case._retrieval_path(query, [], 'v-test', decision)
        assert answer == NO_EVIDENCE and sources == []
    assert not case.llm.mock_calls


def test_subject_alias_and_comparison_keep_complete_evidence():
    ping = doc('Ping-pong 切换：同一终端反复往返小区')
    assert relevant_docs('乒乓切换的含义', [ping]) == [ping]
    amf, smf = doc('AMF注册'), doc('SMF会话')
    assert relevant_docs('AMF与SMF有什么区别', [amf, smf]) == [amf, smf]
    assert relevant_docs('AMF与SMF有什么区别', [amf]) == []


def test_clarification_without_subject_text_is_preserved():
    question = doc('请补充设备和软件版本', extra={'needs_clarification': True})
    assert relevant_docs('AMF恢复步骤', [question]) == [question]


def test_faq_original_question_uses_strong_exact_read_without_embedding():
    searcher = Searcher.__new__(Searcher)
    searcher.settings = SimpleNamespace(faq_collection='faqs', mysql_dsn='unused')
    searcher.cache = Mock()
    searcher.cache.get.return_value = None
    searcher._field_cache = {'faqs': {'question'}}
    searcher._filter_expr = Mock(return_value='tenant_id == "tenant-a" and kb_version == "v-test"')
    searcher._supported_output_fields = lambda c, f: f
    searcher._embed_query_cached = Mock(side_effect=AssertionError('exact FAQ must not embed'))
    searcher._call = Mock(return_value=[{'id': 'f1', 'question': 'AMF是什么？',
        'answer': 'AMF负责接入和移动性管理', 'source': 'faq.md', 'kb_version': 'v-test',
        'tenant_id': 'tenant-a', 'source_system': 'local', 'source_id': 'faq'}])
    from qa_core.security.policy import AccessScope
    scope = AccessScope.build(tenant_id='tenant-a', roles=['ops'])
    docs = searcher.search_faq('AMF是什么？', ['ops'], 'v-test', scope=scope)
    assert docs[0].answer == 'AMF负责接入和移动性管理' and docs[0].dense_score == 1
    assert searcher._call.call_args.args == ('query',)
    assert 'question == "AMF是什么？"' in searcher._call.call_args.kwargs['filter']
    assert searcher._call.call_args.kwargs['consistency_level'] == 'Strong'
    searcher._embed_query_cached.assert_not_called()


def test_recommendations_resolve_version_and_pass_current_scope(monkeypatch):
    from qa_core.api import route_qa, route_evidence
    from fastapi import Response
    ctx = SimpleNamespace(roles=['ops'], tenant_id='tenant-a', scope=object())
    resolver = Mock(return_value='v-active')
    monkeypatch.setattr(route_evidence, 'resolve_version', resolver)
    monkeypatch.setattr(route_qa, '_enforce_rate_limit', lambda ctx: None)
    searcher = Mock()
    searcher.recommended_faqs.return_value = [doc('question', doc_type='faq',
        question='AMF案例SIM-FAQ-0001是什么？', answer='answer', synthetic=True)]
    monkeypatch.setattr(route_qa, 'get_usecase', lambda: SimpleNamespace(searcher=searcher))
    response = Response()
    result = route_qa.recommendations(response, None, ctx)
    resolver.assert_called_once_with(None, ctx)
    searcher.recommended_faqs.assert_called_once_with(['ops'], 'v-active', tenant_id='tenant-a', scope=ctx.scope)
    assert result['items'][0]['query'] == 'AMF案例SIM-FAQ-0001是什么？'
    assert result['items'][0]['synthetic'] is True
    assert response.headers['cache-control'] == 'no-store'
