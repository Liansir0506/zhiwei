"""Milvus 标量过滤表达式构造（纯函数）+ 融合与去重（纯函数）。

过滤顺序契约（顺序不可换）：权限 → kb_version → 软过滤（doc_type/network_tech）。
权限过滤在任何表达式里都强制存在。
"""
from __future__ import annotations

import json

from qa_core.schemas.retrieval import RetrievedDoc


def _literal(value: str) -> str:
    """Milvus 字符串字面量转义，避免租户/过滤值改变表达式语义。"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def build_filter_expr(roles: list[str], kb_version: str,
                      doc_types: list[str] | None = None,
                      network_tech: list[str] | None = None,
                      tenant_id: str = "default", dept_ids: list[str] | tuple[str, ...] | None = None,
                      security_levels: list[str] | tuple[str, ...] | None = None,
                      device_types: list[str] | tuple[str, ...] | None = None,
                      as_of: str | None = None,
                      available_fields: set[str] | None = None) -> str:
    """权限（RBAC+ABAC，下推）+ 版本 + 软过滤。返回 Milvus 布尔表达式。"""
    clauses: list[str] = []
    has = lambda field: available_fields is None or field in available_fields
    safe_roles = json.dumps([str(r) for r in roles if r])
    access_clauses = ['visibility == "public"'] if has("visibility") else []
    if roles and has("visibility"):
        role_checks = []
        if has("role_scope"):
            role_checks.append(f'json_contains_any(role_scope, {safe_roles})')
        if has("allowed_roles"):
            role_checks.append(f'json_contains_any(allowed_roles, {safe_roles})')
        if role_checks:
            access_clauses.append(
                f'(visibility == "role_restricted" and ({" or ".join(role_checks)}))')
    safe_depts = [str(dept) for dept in (dept_ids or []) if dept]
    if safe_depts and has("visibility") and has("dept_id"):
        values = ", ".join(f'"{_literal(dept)}"' for dept in safe_depts)
        access_clauses.append(
            f'(visibility == "department_restricted" and dept_id in [{values}])')
    if access_clauses:
        clauses.append("(" + " or ".join(access_clauses) + ")")
    if has("tenant_id"):
        clauses.append(f'tenant_id == "{_literal(tenant_id)}"')
    levels = [str(level) for level in (security_levels or ["public"]) if level]
    safe_levels = ", ".join(f'"{_literal(level)}"' for level in levels)
    if has("security_level"):
        clauses.append(f"security_level in [{safe_levels}]")
    safe_device_types = [str(device) for device in (device_types or []) if device]
    if safe_device_types and has("device_type"):
        values = ", ".join(f'"{_literal(device)}"' for device in safe_device_types)
        # 无设备类型标记的通用规范仍可读取；带设备域的资料必须命中域范围。
        clauses.append(f'(device_type == "" or device_type in [{values}])')
    # 只有已探测到完整新 Schema 时才加生命周期条件，兼容旧 Collection 滚动升级。
    if available_fields is not None and "status" in available_fields:
        clauses.append('status == "active"')
    if as_of and available_fields is not None:
        safe_as_of = _literal(as_of)
        if "effective_from" in available_fields:
            clauses.append(f'(effective_from == "" or effective_from <= "{safe_as_of}")')
        if "effective_to" in available_fields:
            clauses.append(f'(effective_to == "" or effective_to > "{safe_as_of}")')
    if kb_version and has("kb_version"):
        clauses.append(f'kb_version == "{_literal(kb_version)}"')
    if doc_types:
        dt = ", ".join(f'"{_literal(d)}"' for d in doc_types)
        clauses.append(f"doc_type in [{dt}]")
    if network_tech:
        nt = ", ".join(f'"{_literal(d)}"' for d in network_tech)
        clauses.append(f"network_tech in [{nt}]")
    return " and ".join(clauses)


def normalize_minmax(scores: list[float]) -> list[float]:
    """BM25 分数无界，min-max 归一到 0-1 后才能与 COSINE 加权融合。"""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0] * len(scores)  # 全部同分：无法区分，给满分保持排名
    return [(s - lo) / (hi - lo) for s in scores]


def fuse(dense_hits: list[dict], sparse_hits: list[dict],
         w_dense: float = 0.6, w_sparse: float = 0.4,
         method: str = "weighted", rrf_k: int = 60) -> list[RetrievedDoc]:
    """两路命中（各含 id/entity/distance）→ 单一融合列表（降序）。

    method="weighted"：dense（COSINE）直接用，sparse min-max 归一后加权——保留四种分数可解释性；
    method="rrf"：Reciprocal Rank Fusion，score = Σ 1/(k+rank)，对分数尺度不敏感，
    答辩可对比两种融合策略的召回差异（settings.fusion_method 切换）。
    """
    if method == "rrf":
        return _fuse_rrf(dense_hits, sparse_hits, rrf_k)
    dense_norm = normalize_minmax([h["distance"] for h in dense_hits])
    sparse_norm = normalize_minmax([h["distance"] for h in sparse_hits])
    by_id: dict[str, RetrievedDoc] = {}

    for hit, ns in zip(dense_hits, dense_norm):
        doc = _to_doc(hit)
        doc.dense_score = round(float(hit["distance"]), 4)
        doc.sparse_score = 0.0
        doc.fusion_score = round(w_dense * doc.dense_score, 4)
        doc.extra["_norm"] = {"dense": ns}
        by_id[doc.id] = doc

    for hit, ns in zip(sparse_hits, sparse_norm):
        doc = by_id.get(hit["id"])
        if doc is None:
            doc = _to_doc(hit)
            doc.dense_score = 0.0
            doc.extra["_norm"] = {}
            by_id[hit["id"]] = doc
        doc.sparse_score = round(float(ns), 4)
        doc.extra["_norm"]["sparse"] = ns
        doc.fusion_score = round(w_dense * doc.dense_score + w_sparse * doc.sparse_score, 4)

    return sorted(by_id.values(), key=lambda d: d.fusion_score, reverse=True)


def _fuse_rrf(dense_hits: list[dict], sparse_hits: list[dict],
              k: int = 60) -> list[RetrievedDoc]:
    """Reciprocal Rank Fusion：按排名倒数融合，免归一、对异常分数鲁棒。"""
    by_id: dict[str, RetrievedDoc] = {}
    for rank, hit in enumerate(dense_hits, 1):
        doc = _to_doc(hit)
        doc.dense_score = round(float(hit["distance"]), 4)
        doc.extra["_rrf_dense_rank"] = rank
        by_id[doc.id] = doc
    for rank, hit in enumerate(sparse_hits, 1):
        doc = by_id.get(hit["id"])
        if doc is None:
            doc = _to_doc(hit)
            doc.extra["_rrf_dense_rank"] = None
            by_id[doc.id] = doc
        doc.extra["_rrf_sparse_rank"] = rank

    for doc in by_id.values():
        dr = doc.extra.get("_rrf_dense_rank")
        sr = doc.extra.get("_rrf_sparse_rank")
        score = (1.0 / (k + dr) if dr else 0.0) + (1.0 / (k + sr) if sr else 0.0)
        doc.fusion_score = round(score, 6)
        if not doc.dense_score:
            doc.dense_score = 0.0
        if not doc.sparse_score:
            doc.sparse_score = 0.0
    return sorted(by_id.values(), key=lambda d: d.fusion_score, reverse=True)


def dedup_by_parent(docs: list[RetrievedDoc]) -> list[RetrievedDoc]:
    """同一 parent_id（长工单拆块）只保留融合分最高的块；无 parent 的不受影响。"""
    seen_parents: set[str] = set()
    out: list[RetrievedDoc] = []
    for d in docs:  # 调用方保证已按 fusion 降序
        pid = d.parent_id
        if pid and pid in seen_parents:
            continue
        if pid:
            seen_parents.add(pid)
        out.append(d)
    return out


def cap_per_source(docs: list[RetrievedDoc], max_per_source: int = 2,
                   doc_type_limits: dict[str, int] | None = None) -> list[RetrievedDoc]:
    """同一来源文件最多保留 N 个块，缓解证据同质化。

    dedup_by_parent 只认 parent_id，而 r3 的 SOP/手册按步骤/章节切块后 parent_id 为空，
    于是候选里会挤满同一份文档的相邻块（实测 top5 五条全部来自 manual_reference_02.md），
    5 条引用实际只有 1 个来源，LLM 据此判"证据不足"而拒答。

    SOP 单独放宽到 5：它是按步骤切块的，同一份 SOP 的多个块是"步骤 1..N"互补关系，
    问"流程有哪些步骤"时必须拿到多块；而 manual/standard 的相邻块内容近似重复，
    才是真正要压制的同质化。超出配额的块不丢弃，只排到后面。
    """
    doc_type_limits = doc_type_limits if doc_type_limits is not None else {"sop": 5}
    counts: dict[str, int] = {}
    primary: list[RetrievedDoc] = []
    overflow: list[RetrievedDoc] = []
    for d in docs:  # 调用方保证已按 fusion 降序
        key = d.source or d.id
        limit = doc_type_limits.get(d.doc_type, max_per_source)
        used = counts.get(key, 0)
        if used < limit:
            counts[key] = used + 1
            primary.append(d)
        else:
            overflow.append(d)
    return primary + overflow


def _to_doc(hit: dict) -> RetrievedDoc:
    entity = hit.get("entity", {})
    return RetrievedDoc(
        id=hit["id"],
        text=entity.get("text", ""),
        fusion_score=0.0,
        doc_type=entity.get("doc_type", ""),
        source=entity.get("source", ""),
        alarm_code=entity.get("alarm_code", ""),
        kb_version=entity.get("kb_version", ""),
        network_tech=entity.get("network_tech", ""),
        equipment_type=entity.get("equipment_type", ""),
        vendor=entity.get("vendor", ""),
        tenant_id=entity.get("tenant_id", "default"),
        dept_id=entity.get("dept_id", ""),
        security_level=entity.get("security_level", ""),
        device_type=entity.get("device_type", ""),
        source_system=entity.get("source_system", ""),
        source_id=entity.get("source_id", ""),
        source_version=entity.get("source_version", ""),
        effective_from=entity.get("effective_from", ""),
        effective_to=entity.get("effective_to", ""),
        status=entity.get("status", ""),
        ticket_id=entity.get("ticket_id", ""),
        cmdb_id=entity.get("cmdb_id", ""),
        change_id=entity.get("change_id", ""),
        asset_id=entity.get("asset_id", ""),
        origin_type=entity.get("origin_type", ""),
        synthetic=bool(entity.get("synthetic", False)),
        generator_version=entity.get("generator_version", ""),
        generation_seed=entity.get("generation_seed", ""),
        review_status=entity.get("review_status", ""),
        license_status=entity.get("license_status", ""),
        evidence_refs=entity.get("evidence_refs", []) or [],
        role_scope=entity.get("role_scope", []) or [],
        clause=entity.get("clause", ""),
        parent_id=entity.get("parent_id", ""),
        question=entity.get("question", ""),
        answer=entity.get("answer", ""),
        extra={"step_index": entity.get("step_index", [])},
    )
