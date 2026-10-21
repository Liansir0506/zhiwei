"""检索子系统 component 测试：打真实 Milvus（需 compose up 且已执行 rebuild_kb v1）。"""
import time

import pytest

from qa_core.config.settings import get_settings
from qa_core.retrieval.searcher import Searcher


@pytest.fixture(scope="module")
def searcher():
    return Searcher()


@pytest.mark.component
def test_dense_bm25_hybrid_recall_5g_transmission(searcher):
    """'5G基站传输中断' 应召回 ALM-1201 工单与 SOP。"""
    result = searcher.search_docs(
        ["5G基站传输中断排查步骤"], roles=["ops"], kb_version="v1",
    )
    assert result.docs, "无召回"
    if not result.cache_hit:  # 缓存命中时召回计数为 -1（未重新执行双路检索）
        assert result.candidates_dense > 0 and result.candidates_sparse > 0
    ids_texts = " ".join(d.text for d in result.docs[:10])
    assert "传输" in ids_texts
    top = result.docs[0]
    assert 0 <= top.fusion_score <= 1.0 + 1e-6
    assert top.dense_score > 0 or top.sparse_score > 0


@pytest.mark.component
def test_soft_filter_relax_when_empty(searcher):
    """要求一个不存在的组合 → 触发放宽重查，仍能返回结果。"""
    # 唯一查询串避免命中其他用例写入的 L2 缓存
    result = searcher.search_docs(
        [f"BBU 配置-随机{time.time_ns()}"], roles=["ops"], kb_version="v1",
        doc_types=["fault_case"], network_tech=["不存在的制式"],
    )
    assert result.filter_relaxed is True and not result.cache_hit
    assert result.docs


@pytest.mark.component
def test_faq_direct_hit(searcher):
    docs = searcher.search_faq("5G基站传输中断排查步骤", roles=["ops"], kb_version="v1")
    assert docs and docs[0].question.startswith("5G基站传输中断")
    assert docs[0].dense_score >= 0.80  # faq_direct 阈值经实测校准


@pytest.mark.component
def test_structured_lookup_by_alarm_code(searcher):
    docs = searcher.lookup_alarm("ALM-1201", roles=["ops"], kb_version="v1")
    assert len(docs) == 1
    assert docs[0].alarm_code == "ALM-1201" and "光缆" in docs[0].text


@pytest.mark.component
def test_structured_lookup_miss_returns_empty(searcher):
    assert searcher.lookup_alarm("ALM-9999", roles=["ops"], kb_version="v1") == []


@pytest.mark.component
def test_rerank_scores_in_unit_range(searcher):
    """reranker 真实加载（慢），验证 sigmoid 分数与排序。"""
    from qa_core.rerank.reranker import get_reranker

    s = get_settings()
    result = searcher.search_docs(["BBU 掉电怎么处理"], roles=["ops"], kb_version="v1")
    assert result.docs
    reranker = get_reranker(s.reranker_model_path, s.rerank_device)
    ranked = reranker.rerank("BBU 掉电怎么处理", result.docs, top_n=5)
    assert ranked
    scores = [d.rerank_score for d in ranked]
    assert all(0.0 <= x <= 1.0 for x in scores)
    assert scores == sorted(scores, reverse=True)
    assert len(ranked) <= 5
