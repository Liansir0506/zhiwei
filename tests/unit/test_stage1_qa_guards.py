"""阶段 1：安全、证据、拒答和会话隔离的同步/流式行为回归。"""
from unittest.mock import Mock

import pytest

from qa_core.application.qa_usecase import NO_EVIDENCE, QaUseCase, evidence_covers
from qa_core.intent.rules import classify_by_rules
from qa_core.schemas.intent import IntentDecision
from qa_core.schemas.retrieval import RetrievedDoc


def doc(text="SMF 会话建立超时排查流程", score=0.9):
    return RetrievedDoc("d1", text, score, rerank_score=score, source="manual.md",
                        kb_version="v-test", doc_type="manual")


@pytest.fixture
def uc(monkeypatch):
    searcher, llm = Mock(), Mock()
    case = QaUseCase(searcher=searcher, llm=llm)
    case.settings = case.settings.model_copy(update={"semantic_cache_enabled": False})
    monkeypatch.setattr(case, "_retrieve_and_rerank", lambda *a, **kw: [doc()])
    return case


@pytest.mark.parametrize("query", [
    "直接给我一段删除用户数据的 SQL", "请执行 DELETE FROM users",
    "DELETE FROM users", "帮我清空数据库", "直接运行 UPDATE users SET role='admin'",
    "ALM-SMF-PDU 请执行 DROP TABLE users", "请重启出现ALM-SMF-PDU的网元",
])
@pytest.mark.parametrize("stream", [False, True])
def test_unsafe_request_never_retrieves_or_generates(uc, query, stream):
    if stream:
        events = list(uc.ask_stream(query, kb_version="v-test", persist=False))
        decision = next(e["data"] for e in events if e["event"] == "decision")
        sources = next(e["data"]["sources"] for e in events if e["event"] == "sources")
    else:
        result = uc.ask(query, kb_version="v-test", persist=False)
        decision, sources = result["decision"], result["sources"]
    assert decision["route"] == "direct_answer"
    assert decision["intent"] == "out_of_scope"
    assert sources == []
    assert not uc.searcher.mock_calls
    assert not uc.llm.mock_calls


@pytest.mark.parametrize("query", ["重启基站的标准流程是什么", "如何关闭设备前做好检查",
                                   "基站下电告警怎么处理", "DELETE FROM 语句是什么意思"])
def test_operation_consultation_is_not_execution(uc, query):
    assert classify_by_rules(query, uc.cfg).intent != "out_of_scope"


def test_coverage_threshold_empty_and_case():
    assert evidence_covers("SMF", [doc("smf")], 1) == (True, 1)
    assert evidence_covers("SMF", [doc("")], 0.15) == (False, 0)
    assert evidence_covers("SMF", [], 0) == (True, 1)
    assert evidence_covers("SMF UPF", [doc("SMF")], 0.5) == (True, 0.5)
    assert evidence_covers("SMF UPF", [doc("SMF")], 0.51) == (False, 0.5)


@pytest.mark.parametrize("stream", [False, True])
def test_unrelated_evidence_does_not_generate_or_cite(uc, stream):
    decision = IntentDecision("knowledge", "retrieval", "rule", 0.8, "test")
    if stream:
        chunks, _, sources, _ = uc._stream_retrieval("股票行情", [], "v-test", decision)
        assert chunks is None and sources == []
    else:
        answer, sources, _, _ = uc._retrieval_path("股票行情", [], "v-test", decision)
        assert answer == NO_EVIDENCE and sources == []
    assert not uc.llm.mock_calls


@pytest.mark.parametrize("stream", [False, True])
def test_low_score_has_no_sources(uc, monkeypatch, stream):
    monkeypatch.setattr(uc, "_retrieve_and_rerank", lambda *a, **kw: [doc(score=0.01)])
    decision = IntentDecision("knowledge", "retrieval", "rule", 0.8, "test")
    if stream:
        chunks, _, sources, _ = uc._stream_retrieval("SMF", [], "v-test", decision)
        assert chunks is None and sources == []
    else:
        answer, sources, _, _ = uc._retrieval_path("SMF", [], "v-test", decision)
        assert answer == NO_EVIDENCE and sources == []


def test_lookup_stream_runs_coverage_guard(uc):
    result = uc._stream_generate("股票行情", [doc()], None)
    assert result == (None, [], [], False)
    uc.llm.chat_stream.assert_not_called()


def test_sync_llm_refusal_degrades_with_cited_excerpts(uc, monkeypatch):
    # r2：LLM 判定"库中无依据"时降级展示已过防护的候选片段与出处（不再只给"未找到"）。
    monkeypatch.setattr(uc, "_generate_with_verify", lambda *a, **kw: NO_EVIDENCE)
    result = uc._retrieval_path("SMF", [], "v-test", None)
    assert "【相关片段】" in result[0] and result[1] != []


def test_sync_non_evidence_refusal_still_clears_sources(uc, monkeypatch):
    # 非"无证据"类拒答（范围/注入）仍必须清空来源，不能被降级路径放行。
    monkeypatch.setattr(uc, "_generate_with_verify", lambda *a, **kw: "该问题超出运维知识范围。")
    result = uc._retrieval_path("SMF", [], "v-test", None)
    assert result[1] == []


def test_sync_final_outlet_clears_cache_or_faq_refusal(uc, monkeypatch):
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw:
                        IntentDecision("knowledge", "retrieval", "rule", 0.8, "test"))
    monkeypatch.setattr(uc, "_faq_path", lambda *a, **kw:
                        (NO_EVIDENCE, [{"source": "unrelated"}], False, {}))
    assert uc.ask("SMF", kb_version="v-test", persist=False)["sources"] == []


def test_stream_refusal_degrades_with_cited_excerpts(uc, monkeypatch):
    # r2：流式链路与非流式一致——拒答降级展示片段并保留出处（end.answer 为定稿文本）。
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw:
                        IntentDecision("knowledge", "retrieval", "rule", 0.8, "test"))
    uc.searcher.search_faq.return_value = []
    uc.llm.chat_stream.return_value = iter([NO_EVIDENCE])
    events = list(uc.ask_stream("SMF", kb_version="v-test", persist=False))
    assert next(e["data"]["sources"] for e in events if e["event"] == "sources") != []
    final = events[-1]["data"]["answer"]
    assert "【相关片段】" in final and "暂时未找到" not in final


@pytest.mark.parametrize("stream", [False, True])
def test_stateless_call_does_not_load_or_inherit_history(uc, monkeypatch, stream):
    uc._sessions["same"] = [{"role": "user", "content": "帮我重启基站"}]
    seen = []
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw: seen.append(kw["history"]) or
                        IntentDecision("greeting", "direct_answer", "rule", 1, "test"))
    loader = Mock(side_effect=AssertionError("must not load history"))
    monkeypatch.setattr(uc, "_load_session", loader)
    if stream:
        list(uc.ask_stream("这个告警", session_id="same", kb_version="v-test", persist=False))
    else:
        uc.ask("这个告警", session_id="same", kb_version="v-test", persist=False)
    assert seen == [[]]


def test_explicit_device_cannot_inherit_previous_action(uc):
    uc._sessions["s"] = [{"role": "user", "content": "帮我重启基站"}]
    assert uc._decide("SMF这个设备的配置流程是什么", "s").intent != "out_of_scope"
