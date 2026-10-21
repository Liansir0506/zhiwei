"""RRF 融合策略单测（对接设计报告 §5.1[6] RRF 建议）。"""
from qa_core.retrieval.filters import fuse


def _hits(ids_scores):
    return [{"id": i, "distance": s, "entity": {"source": "t"}} for i, s in ids_scores]


def test_rrf_interleaves_both_routes():
    dense = _hits([("a", 0.9), ("b", 0.8), ("c", 0.7)])
    sparse = _hits([("b", 12.0), ("d", 10.0)])
    out = fuse(dense, sparse, method="rrf")
    ids = [d.id for d in out]
    # b 在两路都出现，RRF 后应排最前
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c", "d"}


def test_rrf_score_formula():
    dense = _hits([("a", 0.9)])
    out = fuse(dense, [], method="rrf", rrf_k=60)
    assert abs(out[0].fusion_score - 1 / 61) < 1e-5


def test_weighted_default_unchanged():
    dense = _hits([("a", 0.9)])
    out = fuse(dense, [], w_dense=1.0, w_sparse=0.0)
    assert abs(out[0].fusion_score - 0.9) < 1e-6
