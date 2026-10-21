from unittest.mock import Mock
import pytest
from qa_core.application.qa_usecase import QaUseCase
from qa_core.schemas.intent import IntentDecision
from qa_core.schemas.retrieval import RetrievedDoc


@pytest.mark.parametrize("route", ["retrieval", "structured_lookup"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("safe", [True, False])
def test_disabled_generation_keeps_evidence_guards_without_any_llm(monkeypatch, route, stream, safe):
    searcher, llm = Mock(), Mock()
    doc = RetrievedDoc("a", "AMF 注册告警检查指标", .9, rerank_score=.9, source="fault.csv",
        kb_version="v-test", tenant_id="default", source_version="revision-1", asset_id="asset-1",
        doc_type="fault_case", extra={} if safe else {"workflow_evidence": True, "workflow_ready": False})
    searcher.lookup_alarm.return_value = [doc]
    searcher.related_alarms.return_value = []
    searcher.search_faq.return_value = []
    uc = QaUseCase(searcher=searcher, llm=llm)
    uc.settings = uc.settings.model_copy(update={"generation_enabled": False, "semantic_cache_enabled": True})
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw: IntentDecision("knowledge", route, "rule", .9, "test", alarm_code="ALM-AMF-REG"))
    monkeypatch.setattr(uc, "_retrieve_and_rerank", lambda *a, **kw: [doc])
    if stream:
        events = list(uc.ask_stream("AMF 注册告警", kb_version="v-test", roles=["ops"], persist=False))
        end = next(e["data"] for e in events if e["event"] == "end")
        sources = next(e["data"]["sources"] for e in events if e["event"] == "sources")
        assert end["answer_mode"] == "citation"
        answer, status = end["answer"], end["status"]
    else:
        result = uc.ask("AMF 注册告警", kb_version="v-test", roles=["ops"], persist=False, use_llm=True)
        assert result["diagnostics"]["answer_mode"] == "citation"
        sources, answer, status = result["sources"], result["answer"], result["status"]
    assert status == ("ANSWERED" if safe else "NO_EVIDENCE")
    assert bool(sources) == safe
    if safe:
        assert "引用模式" in answer and "AMF 注册告警检查指标" in answer
    assert not llm.mock_calls
    searcher._embed_query_cached.assert_not_called()  # disabled mode cannot reuse generated semantic cache


@pytest.mark.parametrize("stream", [False, True])
def test_disabled_mode_still_hides_withdrawn_evidence(monkeypatch, stream, unit_withdrawal_ledger):
    doc = RetrievedDoc("a", "AMF 注册告警", .9, rerank_score=.9, source="fault.csv",
        kb_version="v-test", tenant_id="default", source_version="revision-1", asset_id="asset-1",
        source_system="wiki", source_id="withdrawn")
    uc = QaUseCase(searcher=Mock(), llm=Mock())
    uc.settings = uc.settings.model_copy(update={"generation_enabled": False})
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw: IntentDecision("knowledge", "structured_lookup", "rule", .9, "test"))
    uc.searcher.lookup_alarm.return_value = [doc]
    uc.searcher.related_alarms.return_value = []
    from qa_core.security.withdrawals import identity_hash
    unit_withdrawal_ledger["default"] = {identity_hash("default", "wiki", "withdrawn")}
    if stream:
        events = list(uc.ask_stream("AMF 注册告警", kb_version="v-test", persist=False))
        assert next(e["data"]["sources"] for e in events if e["event"] == "sources") == []
    else:
        assert uc.ask("AMF 注册告警", kb_version="v-test", persist=False)["sources"] == []
    assert not uc.llm.mock_calls
