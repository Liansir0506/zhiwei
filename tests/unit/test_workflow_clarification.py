from unittest.mock import Mock
import pytest
from qa_core.schemas.retrieval import RetrievedDoc, RetrievalResult
from qa_core.retrieval.workflow import retrieve_workflow, missing_context
from qa_core.application.qa_usecase import QaUseCase
from qa_core.schemas.intent import IntentDecision


def source():
    return [RetrievedDoc(str(i), text, .8, rerank_score=.8, doc_type="sop", source="sop.md", asset_id="a",
        kb_version="v1", source_version="v1", status="active", extra={"step_index": [i]})
        for i, text in enumerate(["# AMF SOP\nLab-2026.09；条件：队列增长；",
                                 "步骤 1：只读检查，停止并转人工。", "步骤 2：审批后异常回滚。"])]


@pytest.mark.parametrize("query,expected", [("AMF SOP", True), ("AMF Lab-2026.09 SOP", True),
    ("AMF Lab-2026.08 SOP", False), ("AMF V1.2 SOP", False), ("UPF流程", False), ("UPF SOP", False), ("AMF Lab-2026.09 队列增长 SOP", False)])
def test_only_known_missing_conditions_clarify(query, expected):
    assert missing_context(query, source()[0].text) == expected


@pytest.mark.parametrize("denied", [False, True])
def test_clarification_requires_complete_authorized_safe_source(denied):
    docs = source(); searcher, ranker = Mock(), Mock()
    searcher.search_docs.return_value = RetrievalResult([], [docs[0]])
    ranker.rerank.return_value = [docs[0]]
    if denied: searcher.fetch_sop_source.side_effect = ValueError("denied")
    else: searcher.fetch_sop_source.return_value = docs
    result = retrieve_workflow(searcher, ranker, "AMF SOP", ["ops"], "v1")
    assert bool(result.question) == (not denied)
    assert result.packet is None


@pytest.mark.parametrize("stream", [False, True])
def test_sync_and_stream_clarify_without_llm_or_sources(monkeypatch, stream):
    uc = QaUseCase(searcher=Mock(), llm=Mock())
    uc.settings = uc.settings.model_copy(update={"workflow_evidence_enabled": True})
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw: IntentDecision("procedure_query", "retrieval", "rule", .9, "test"))
    sentinel = RetrievedDoc("", "请补充设备版本与实际症状。", 0,
                            extra={"needs_clarification": True, "reason": "missing_workflow_context"})
    monkeypatch.setattr(uc, "_retrieve_and_rerank", lambda *a, **kw: [sentinel])
    if stream:
        events = list(uc.ask_stream("AMF SOP", roles=["ops"], kb_version="v1", persist=False))
        result = next(e["data"] for e in events if e["event"] == "end")
        assert next(e["data"]["sources"] for e in events if e["event"] == "sources") == []
    else:
        result = uc.ask("AMF SOP", roles=["ops"], kb_version="v1", persist=False)
        assert result["sources"] == []
    assert result["status"] == "NEEDS_CLARIFICATION" and result["answer"] == sentinel.text
    uc.llm.chat.assert_not_called(); uc.llm.chat_stream.assert_not_called()


def test_ambiguous_branches_recheck_permissions_before_clarification():
    import copy
    left=source(); right=copy.deepcopy(left)
    for d in right: d.id='b'+d.id; d.source='sop_b.md'; d.asset_id='b'
    right[0].text=right[0].text.replace('队列增长','认证变慢')
    searcher,ranker=Mock(),Mock()
    searcher.search_docs.return_value=RetrievalResult([], [left[0],right[0]])
    searcher.fetch_sop_source.side_effect=lambda seed,*a,**kw: left if seed.source=='sop.md' else right
    query='AMF Lab-2026.09 队列增长或认证变慢 SOP'
    assert retrieve_workflow(searcher,ranker,query,['ops'],'v1').question
    searcher.fetch_sop_source.side_effect=ValueError('withdrawn')
    assert not retrieve_workflow(searcher,ranker,query,['ops'],'v1').question
