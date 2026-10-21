import copy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from qa_core.retrieval.workflow import applicable, assemble_sop, is_workflow_query, retrieve_workflow
from qa_core.schemas.retrieval import RetrievedDoc, RetrievalResult
from qa_core.schemas.intent import IntentDecision
from qa_core.application.qa_usecase import QaUseCase, FLOW_NO_EVIDENCE, _citation_answer
from qa_core.generation.context import build_evidence, build_messages


def chunks(safe=True):
    texts = ["# 设备处置 SOP\n适用：AMF；告警 ALM-AMF-REG。",
             "步骤 1：读取指标并核对设备版本；证据不足时停止并转人工。",
             "步骤 2：" + ("需人工审批，保存快照；异常时回滚。" if safe else "执行低风险处置。")]
    return [RetrievedDoc(str(i), t, .9, rerank_score=.9, doc_type="sop", source="sop_a.md",
                         asset_id="asset-a", kb_version="v-test", source_version="v-test", tenant_id="default",
                         security_level="internal", status="active", extra={"step_index": [i]})
            for i, t in enumerate(texts)]


@pytest.mark.parametrize("query", ["AMF 运维处置流程有哪些步骤？", "ALM-AMF-REG SOP", "如何取证、排除原因并验证恢复？"])
def test_workflow_purpose(query): assert is_workflow_query(query)


@pytest.mark.parametrize("query", ["SOP 是什么？", "SIM-FAQ-0001 的流程", "实验需要哪些指标", "AMF 实验步骤", "PCF 是什么", "告警依据是什么"])
def test_nonworkflow_purpose(query): assert not is_workflow_query(query)


def test_packet_order_real_ids_and_no_truncation():
    docs = chunks()
    docs[2].text += "资料说明" * 260 + "末尾恢复验证"
    result = assemble_sop([docs[2], docs[0], docs[1]], docs[1])
    assert result.ready
    assert result.packet.id == "0"
    assert result.packet.extra["workflow_steps"] == [1, 2]
    assert result.packet.extra["child_chunk_ids"] == ["0", "1", "2"]
    assert "末尾恢复验证" in build_evidence([result.packet])
    assert "末尾恢复验证" in _citation_answer([result.packet])
    assert "沙箱记录不用于现网操作" in build_messages("流程", [result.packet])[0]["content"]


@pytest.mark.parametrize("kind", ["gap", "duplicate", "text_mismatch", "mixed_version", "mixed_kb", "mixed_scope", "seed_missing", "oversized"])
def test_malformed_or_mixed_source_denied(kind):
    docs = chunks()
    seed = copy.deepcopy(docs[1])
    if kind == "gap": docs.pop(1)
    if kind == "duplicate": docs.append(copy.deepcopy(docs[1]))
    if kind == "text_mismatch": docs[1].text = "步骤 2：错误"
    if kind == "mixed_version": docs[2].source_version = "other"
    if kind == "mixed_kb": docs[2].kb_version = "other"
    if kind == "mixed_scope": docs[2].security_level = "restricted"
    if kind == "seed_missing": seed.id = "withdrawn"
    if kind == "oversized": docs[2].text += "x" * 6001
    assert not assemble_sop(docs, seed).ready


def test_original_source_revision_can_assemble_inside_a_new_release():
    docs = chunks()
    for doc in docs:
        doc.source_version = "original-source-r7"
    result = assemble_sop(docs, docs[1])
    assert result.ready and result.packet.source_version == "original-source-r7"
    assert result.packet.kb_version == "v-test"


@pytest.mark.parametrize("field,value", [("tenant_id", "other"), ("kb_version", "other"),
    ("source_version", ""), ("asset_id", ""), ("doc_type", "manual")])
def test_sop_invalid_seed_is_rejected_before_evidence_query(field, value):
    from qa_core.retrieval.searcher import Searcher
    from qa_core.retrieval.workflow import REQUIRED_WORKFLOW_FIELDS
    client = Mock()
    client.describe_collection.return_value = {"fields": [{"name": name} for name in REQUIRED_WORKFLOW_FIELDS]}
    searcher = Searcher(client=client, embedder=Mock(), cache=Mock(),
                        settings=SimpleNamespace(doc_collection="docs"))
    seed = chunks()[1]; setattr(seed, field, value)
    with pytest.raises(ValueError, match="identity mismatch"):
        searcher.fetch_sop_source(seed, ["ops"], "v-test")
    client.query.assert_not_called()


@pytest.mark.parametrize("unsafe", ["无需人工审批", "不能回滚", "回滚不可用"])
def test_negative_safety_statement_not_approval(unsafe):
    docs = chunks()
    docs[2].text += unsafe
    assert not assemble_sop(docs, docs[1]).ready


def test_old_template_is_complete_but_not_operationally_ready():
    result = assemble_sop(chunks(False), chunks(False)[1])
    assert result.packet is not None
    assert not result.ready
    with pytest.raises(ValueError, match="unsafe"):
        build_evidence([result.packet])


def test_lab_condition_only_positive_applicability_not_negative_mentions():
    text = "Lab-2026.09；条件：入口队列增长；分支：入口积压。不适用：鉴权响应变慢"
    assert applicable("Lab-2026.09，入口队列增长", text)
    assert not applicable("Lab-2026.09，鉴权响应变慢", text)
    assert not applicable("Lab-2027.09，入口队列增长", text)
    assert not applicable("入口队列增长", text)
    assert not applicable("Lab-2026.09，原因不清", text)


def test_workflow_calls_strict_sop_and_no_faq():
    docs = chunks()
    searcher = Mock()
    searcher.search_docs.return_value = RetrievalResult([], [docs[1]])
    searcher.fetch_sop_source.return_value = docs
    reranker = Mock()
    reranker.rerank.return_value = [docs[1]]
    result = retrieve_workflow(searcher, reranker, "AMF 流程", ["ops"], "v-test")
    assert result.ready
    assert searcher.search_docs.call_args.kwargs["strict_doc_types"] is True
    assert searcher.search_docs.call_args.kwargs["doc_types"] == ["sop"]
    searcher.search_faq.assert_not_called()


@pytest.mark.parametrize("stream", [False, True])
def test_unsafe_packet_never_cited_or_generated(monkeypatch, stream):
    packet = assemble_sop(chunks(False), chunks(False)[1]).packet
    uc = QaUseCase(searcher=Mock(), llm=Mock())
    uc.settings = uc.settings.model_copy(update={"workflow_evidence_enabled": True, "semantic_cache_enabled": False})
    monkeypatch.setattr(uc, "_retrieve_and_rerank", lambda *a, **kw: [packet])
    decision = IntentDecision("procedure_query", "retrieval", "rule", .9, "test")
    if stream:
        result = uc._stream_retrieval("AMF 流程", ["ops"], "v-test", decision)
        assert result == (None, [], [], False)
    else:
        result = uc._retrieval_path("AMF 流程", ["ops"], "v-test", decision, use_llm=False)
        assert result[0] == FLOW_NO_EVIDENCE and result[1] == []
    assert not uc.llm.mock_calls


def test_workflow_does_not_override_execution_guard_or_explicit_faq():
    uc = QaUseCase(searcher=Mock(), llm=Mock())
    uc.settings = uc.settings.model_copy(update={"workflow_evidence_enabled": True})
    blocked = IntentDecision("out_of_scope", "direct_answer", "rule", 1, "unsafe")
    uc._workflow_route("执行删除数据库的步骤", blocked)
    assert blocked.route == "direct_answer"
    faq = IntentDecision("faq", "faq_exact", "rule", 1, "explicit")
    uc._workflow_route("SIM-FAQ-0001 的流程", faq)
    assert faq.route == "faq_exact"


@pytest.mark.parametrize("safe", [True, False])
@pytest.mark.parametrize("stream", [True, False])
def test_online_sync_stream_workflow_bypasses_faq_and_cached_answer(monkeypatch, safe, stream):
    searcher, llm = Mock(), Mock()
    uc = QaUseCase(searcher=searcher, llm=llm)
    uc.settings = uc.settings.model_copy(update={"workflow_evidence_enabled": True})
    packet = assemble_sop(chunks(safe), chunks(safe)[1]).packet
    monkeypatch.setattr(uc, "_decide", lambda *a, **kw: IntentDecision("faq", "faq_exact", "rule", .9, "test"))
    monkeypatch.setattr(uc, "_retrieve_and_rerank", lambda *a, **kw: [packet])
    llm.chat_stream.return_value = iter(["依据引用给出只读排查说明 [1]"])
    if stream:
        events = list(uc.ask_stream("AMF 流程", roles=["ops"], kb_version="v-test", persist=False))
        sources = next(e["data"]["sources"] for e in events if e["event"] == "sources")
        status = next(e["data"]["status"] for e in events if e["event"] == "end")
        assert status in {"ANSWERED", "DEGRADED"} if safe else status == "NO_EVIDENCE"
        assert next(e["data"]["route"] for e in events if e["event"] == "decision") == "retrieval"
    else:
        result = uc.ask("AMF 流程", roles=["ops"], kb_version="v-test", persist=False, use_llm=False)
        sources = result["sources"]
        assert result["status"] == ("ANSWERED" if safe else "NO_EVIDENCE")
        assert result["route"] == "retrieval"
    assert bool(sources) == safe
    if safe:
        assert sources[0]["child_chunk_ids"] == ["0", "1", "2"]
    else:
        assert not llm.mock_calls
    searcher.search_faq.assert_not_called()
    searcher._embed_query_cached.assert_not_called()


def test_ambiguous_lab_conditions_need_clarification():
    a, b = chunks()[1], copy.deepcopy(chunks()[1])
    a.text = "Lab-2026.09；条件：队列增长；分支：拥塞"
    b.id = "b"; b.source = "sop_b.md"
    b.text = "Lab-2026.09；条件：鉴权变慢；分支：依赖"
    searcher, ranker = Mock(), Mock()
    searcher.search_docs.return_value = RetrievalResult([], [a, b])
    result = retrieve_workflow(searcher, ranker, "Lab-2026.09 队列增长或鉴权变慢的流程", ["ops"], "v-test")
    assert result.reason == "ambiguous_conditions"
    ranker.rerank.assert_not_called()
