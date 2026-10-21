"""过滤表达式、融合与去重的纯逻辑测试（不打 Milvus）。"""
import pytest

from qa_core.retrieval.filters import (
    build_filter_expr,
    dedup_by_parent,
    fuse,
    normalize_minmax,
)
from qa_core.schemas.retrieval import RetrievedDoc


@pytest.mark.unit
def test_expr_contains_permission_first():
    expr = build_filter_expr(["ops"], "v1", doc_types=["fault_case"], network_tech=["5G"],
                             dept_ids=["netops"], security_levels=["public", "internal"],
                             device_types=["gNodeB"])
    # 权限子句必须存在且在最前
    assert expr.startswith('(visibility == "public" or')
    assert 'json_contains_any(allowed_roles, ["ops"])' in expr
    assert 'tenant_id == "default"' in expr
    assert 'security_level in ["public", "internal"]' in expr
    assert 'dept_id in ["netops"]' in expr
    assert 'device_type == "" or device_type in ["gNodeB"]' in expr
    assert 'kb_version == "v1"' in expr
    assert 'doc_type in ["fault_case"]' in expr
    assert 'network_tech in ["5G"]' in expr
    # 顺序：权限 < 版本 < 软过滤
    assert expr.index("visibility") < expr.index("kb_version") < expr.index("doc_type")


@pytest.mark.unit
def test_expr_no_roles_public_only():
    expr = build_filter_expr([], "v1")
    assert expr == '(visibility == "public") and tenant_id == "default" and security_level in ["public"] and kb_version == "v1"'


@pytest.mark.unit
def test_expr_department_and_confidential_are_not_candidates_without_scope():
    expr = build_filter_expr(["ops"], "v1", security_levels=["public", "internal"])
    assert 'department_restricted' not in expr
    assert '"confidential"' not in expr


@pytest.mark.unit
def test_lifecycle_filter_excludes_unreviewed_or_expired_knowledge():
    fields = {"visibility", "tenant_id", "security_level", "kb_version", "status",
              "effective_from", "effective_to"}
    expr = build_filter_expr(["ops"], "v4", security_levels=["public", "internal"],
                             as_of="2026-09-19T00:00:00+00:00", available_fields=fields)
    assert 'status == "active"' in expr
    assert 'effective_from == "" or effective_from <=' in expr
    assert 'effective_to == "" or effective_to >' in expr


@pytest.mark.unit
def test_normalize_minmax():
    assert normalize_minmax([]) == []
    assert normalize_minmax([5.0, 5.0, 5.0]) == [1.0, 1.0, 1.0]
    out = normalize_minmax([10.0, 0.0, 5.0])
    assert out == [1.0, 0.0, 0.5]


def _hit(id_, dist, **entity):
    return {"id": id_, "distance": dist, "entity": {"text": f"t-{id_}", **entity}}


@pytest.mark.unit
def test_fuse_combines_two_routes():
    dense = [_hit("A", 0.9, doc_type="manual"), _hit("B", 0.7, doc_type="manual")]
    sparse = [_hit("C", 8.0, doc_type="manual"), _hit("A", 5.0, doc_type="manual")]
    docs = fuse(dense, sparse, w_dense=0.6, w_sparse=0.4)
    ids = [d.id for d in docs]
    assert set(ids) == {"A", "B", "C"}
    a = next(d for d in docs if d.id == "A")
    # sparse min-max 归一：5.0 是本批最低 → 0 分（min-max 的已知局限，RRF 是备选方案）
    assert abs(a.fusion_score - 0.54) < 0.01
    assert a.dense_score == 0.9 and a.sparse_score == 0.0
    c = next(d for d in docs if d.id == "C")
    assert c.dense_score == 0.0 and c.sparse_score == 1.0  # 只在 sparse 路出现


@pytest.mark.unit
def test_fuse_sorted_desc():
    docs = fuse([_hit("A", 0.9)], [_hit("C", 8.0)])
    assert docs[0].fusion_score >= docs[-1].fusion_score


def _doc(id_, parent, score):
    return RetrievedDoc(id=id_, text="x", fusion_score=score, parent_id=parent)


@pytest.mark.unit
def test_dedup_by_parent_keeps_best():
    docs = [_doc("A1", "P", 0.9), _doc("B", "", 0.8), _doc("A2", "P", 0.7), _doc("C", "", 0.6)]
    out = dedup_by_parent(docs)
    assert [d.id for d in out] == ["A1", "B", "C"]  # A2 同 parent 被丢弃


@pytest.mark.unit
def test_dedup_no_parent_untouched():
    docs = [_doc("A", "", 0.9), _doc("B", "", 0.8)]
    assert len(dedup_by_parent(docs)) == 2
