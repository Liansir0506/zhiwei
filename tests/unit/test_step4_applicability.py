"""Condition evidence requests and opt-in lab boundaries, without generation."""
from unittest.mock import Mock

import pytest

from qa_core.application.qa_usecase import QaUseCase, _citation_answer
from qa_core.retrieval.workflow import is_workflow_query, lab_source_applicable, lab_version_applicable
from qa_core.schemas.intent import IntentDecision
from qa_core.schemas.retrieval import RetrievedDoc, RetrievalResult


@pytest.mark.parametrize("query,expected", [
    ("AMF Lab-2026.09 ALM-AMF-REG，排除项、停止条件和验证恢复所需采样证据分别是什么？", True),
    ("ALM-UPF-DROP恢复验证有哪些证据？", True),
    ("AMF Lab-2026.09 ALM-AMF-REG，恢复SOP应该选哪个分支？", True),
    ("SOPX产品是什么意思", False),
    ("Lab-2026.10 UPF规则丢弃比例怎么算？", False),
    ("停止条件是什么意思？", False),
    ("实验的排除项是什么？", False),
])
def test_evidence_requests_use_the_ordered_workflow(query, expected):
    assert is_workflow_query(query) is expected


@pytest.mark.parametrize("query,text,expected", [
    ("AMF Lab-2026.09怎么算", "适用：AMF；Lab-2026.09 与 Lab-2026.10。", True),
    ("AMF Lab-2026.10怎么算", "适用：AMF；Lab-2026.09 与 Lab-2026.10。", True),
    ("NRF Lab-2026.09", "适用：NRF；Lab-2026.10；不适用Lab-2026.09。", False),
    ("UPF Lab-2026.11可以沿用Lab-2026.10吗", "适用：UPF；Lab-2026.10。", False),
    ("UPF Lab-2026.09", "适用：UPF；Lab-2026.10。\nLab-2026.09模式需要另外采样。", False),
    ("AMF真实生产设备", "适用：AMF；Lab-2026.09。", False),
    ("AMF怎么算", "适用：AMF；Lab-2026.09。", False),
    ("AMF Lab-2026.09怎么算", "通用AMF介绍", False),
    ("AMF是什么", "通用AMF介绍", True),
])
def test_positive_scope_excludes_counterexamples_and_unknown_versions(query, text, expected):
    assert lab_version_applicable(query, text) is expected


@pytest.mark.parametrize("enabled", [True, False])
def test_opt_in_filters_doc_and_faq_before_rerank(monkeypatch, enabled):
    allowed = RetrievedDoc("ok", "适用：UPF；Lab-2026.10。", .8)
    wrong = RetrievedDoc("wrong", "适用：UPF；Lab-2026.09。", .9)
    searcher = Mock()
    searcher.search_docs.return_value = RetrievalResult([], [wrong, allowed])
    reranker = Mock()
    reranker.rerank.side_effect = lambda q, docs, top_n: docs
    monkeypatch.setattr("qa_core.rerank.reranker.get_reranker", lambda *a: reranker)
    uc = QaUseCase(searcher=searcher, llm=Mock())
    uc.settings = uc.settings.model_copy(update={"workflow_evidence_enabled": enabled})
    result = uc._retrieve_and_rerank("Lab-2026.10 UPF比例怎么算？", ["ops"], "v1",
        IntentDecision("knowledge", "retrieval", "rule", .9, "test"),
        extra_docs=[RetrievedDoc("faq", "适用：UPF；Lab-2026.09。", .9, doc_type="faq")])
    assert [d.id for d in result] == (["ok"] if enabled else ["wrong", "ok", "faq"])
    uc.llm.chat.assert_not_called()


def test_lab_device_scope_does_not_use_negative_mentions():
    text = "适用：NRF；Lab-2026.10。\n不能据此判断UPF问题。"
    assert not lab_source_applicable("UPF Lab-2026.10", text)
    assert lab_source_applicable("NRF Lab-2026.10", text)


def test_verified_lab_citation_retains_evidence_with_the_prompt_budget():
    text = "适用：AMF；Lab-2026.10。" + "采样记录。"*65 + "计数重置时停止区间计算。"
    doc = RetrievedDoc("lab", text, .8, source="lab.md", extra={"lab_scope_verified": True})
    assert "计数重置时停止区间计算。" in _citation_answer([doc])
    doc.extra.clear()
    assert "计数重置时停止区间计算。" not in _citation_answer([doc])
