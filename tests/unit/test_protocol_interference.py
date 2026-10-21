"""Guardrails for retrieval-only diagnostic distractor selection."""
from unittest.mock import Mock

from scripts.compare_protocol_interference import assess, copy_row, select_distractors


def test_distractors_are_deterministic_and_only_live_default_rows():
    rows = [{"id": str(i), "text": "普通资料", "dense": [1.0],
             "tenant_id": "default", "status": "active", "source": str(i)} for i in range(270)]
    rows += [{"id": "foreign", "text": "HTTP 503", "dense": [1.0],
              "tenant_id": "other", "status": "active", "source": "foreign"},
             {"id": "retired", "text": "HTTP 429", "dense": [1.0],
              "tenant_id": "default", "status": "retired", "source": "retired"}]
    selected = select_distractors(rows)
    assert len(selected) == 250
    assert {r["id"] for r in selected}.isdisjoint({"foreign", "retired"})
    assert [r["id"] for r in selected] == [r["id"] for r in select_distractors(list(reversed(rows)))]


def test_copy_keeps_unreviewed_source_identity_and_does_not_mutate_origin():
    row = {"id": "old", "kb_version": "old-version", "source_version": "old-version",
           "review_status": "unreviewed", "sparse": {0: 1.0}, "text": "old content"}
    copied = copy_row(row, "test-diag", distractor=True)
    assert copied["id"] != row["id"]
    assert copied["kb_version"] == "test-diag"
    assert copied["source_version"] == "old-version"
    assert copied["review_status"] == "unreviewed"
    assert "sparse" not in copied
    assert row["kb_version"] == "old-version" and "sparse" in row


def test_required_quote_uses_existing_ingestion_normalization():
    case = {"id": "one", "query": "问题", "expect_hit": "approved.md",
            "evidence_units": [{"quote": "原因可以是过载；响应可以重试。"}]}
    doc = Mock(source="approved.md", text="原因可以是过载;响应可以重试。", rerank_score=0.8)
    searcher = Mock()
    searcher.search_docs.return_value.docs = [doc]
    reranker = Mock()
    reranker.rerank.return_value = [doc]
    result = assess([case], searcher, reranker, "isolated")
    assert result[0]["target_rank"] == 1
    assert result[0]["target_quote_count"] == result[0]["required_quote_count"] == 1
