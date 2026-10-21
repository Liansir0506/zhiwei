"""Milvus 检索封装：Dense + BM25 双路召回 → 客户端 Weighted Hybrid 融合 → 去重。

选择客户端自融合而非 Milvus WeightedRanker 的原因：需要把 dense/sparse 两路
原始分数分别暴露给 diagnostics（教学要求：四种分数不能混比），且 BM25 无界分数
需要 min-max 归一后再加权，Milvus ranker 不做归一。

软过滤策略：doc_type/network_tech 未命中时放宽重查一次（权限与版本永不放宽）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pymilvus import MilvusClient
from pymilvus.exceptions import ConnectionNotExistException

from qa_core.config.settings import get_settings
from qa_core.indexing.embedder.embedder import get_embedder
from qa_core.cache.redis_cache import CacheLayer
from qa_core.storage.milvus_client import get_milvus_client
from qa_core.retrieval.filters import build_filter_expr, cap_per_source, dedup_by_parent, fuse
from qa_core.schemas.retrieval import RetrievedDoc, RetrievalResult
from qa_core.security.policy import AccessScope
from qa_core.security.withdrawals import withdrawal_checked


def order_alarm_hits(rows: list[dict], limit: int) -> list[dict]:
    """结构化查询结果必须确定性排序。

    Milvus `query` 不带 order_by 时返回顺序**不保证稳定**（受 segment 合并、
    flush 时机影响）。同一告警码下有几十张工单，任取 limit 条，
    同一个问题两次调用可能给出不同答案——对测试、缓存命中和"可复现"都是硬伤。
    实测：174 条评测集上 alarm_lookup 的 hit@5 = 0.000（40/40 全部失败），
    路由正确、确实走了结构化查询，但返回的不是期望那张工单。

    排序键取 source（工单号）升序：稳定、可解释（"取最早 N 张"）、
    不依赖语料里并不存在的时间字段。
    注意必须**先取足量再排序**——只对无序返回的 5 条排序是假确定性。
    """
    return sorted(rows, key=lambda r: (str(r.get("source", "")), str(r.get("id", ""))))[:limit]


class Searcher:
    def __init__(self, client: MilvusClient | None = None, embedder=None, settings=None,
                 cache: CacheLayer | None = None):
        self.settings = settings or get_settings()
        self.client = client or get_milvus_client(self.settings.milvus_uri)
        self.embedder = embedder or get_embedder(
            self.settings.embedding_model_path, self.settings.embedding_device)
        self.cache = cache or CacheLayer(
            self.settings.redis_url, scenario=self.settings.scenario,
            emb_ttl=self.settings.cache_emb_ttl, ret_ttl=self.settings.cache_ret_ttl,
            faq_ttl=self.settings.cache_faq_ttl)
        self._field_cache: dict[str, set[str]] = {}

    @staticmethod
    def _scope(roles: list[str] | None, tenant_id: str, scope: AccessScope | None) -> AccessScope:
        return scope or AccessScope.build(tenant_id=tenant_id, roles=roles or [])

    def _mc(self):
        return self.client

    def _call(self, method_name: str, *args, **kwargs):
        """Milvus 调用统一入口：ConnectionNotExist 时重建客户端重试一次。"""
        for attempt in range(2):
            client = self._mc()
            try:
                return getattr(client, method_name)(*args, **kwargs)
            except ConnectionNotExistException:
                if attempt == 1:
                    raise
                from qa_core.storage.milvus_client import reset_milvus_client

                reset_milvus_client(self.settings.milvus_uri)
                self.client = get_milvus_client(self.settings.milvus_uri)

    def _supported_output_fields(self, collection: str, desired: list[str]) -> list[str]:
        """根据实际 collection schema 选字段，支持旧集合滚动升级。"""
        if collection not in self._field_cache:
            try:
                desc = self._call("describe_collection", collection_name=collection)
                fields = desc.get("fields", []) if isinstance(desc, dict) else []
                self._field_cache[collection] = {
                    f.get("name") for f in fields if isinstance(f, dict) and f.get("name")}
            except Exception:  # noqa: BLE001
                self._field_cache[collection] = set()
        available = self._field_cache[collection]
        return [name for name in desired if not available or name in available]

    def _filter_expr(self, collection: str, access: AccessScope, kb_version: str,
                     doc_types: list[str] | None = None,
                     network_tech: list[str] | None = None) -> str:
        # 触发一次 schema 探测；旧 Collection 仅使用实际存在的安全字段，支持滚动升级。
        self._supported_output_fields(collection, [])
        available = self._field_cache.get(collection) or None
        return build_filter_expr(
            list(access.roles), kb_version, doc_types, network_tech,
            tenant_id=access.tenant_id, dept_ids=access.dept_ids,
            security_levels=access.security_levels, device_types=access.device_types,
            as_of=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            available_fields=available)

    @withdrawal_checked
    def related_alarms(self, alarm_code: str, equipment_type: str, roles: list[str],
                       kb_version: str, limit: int = 3,
                       tenant_id: str = "default", scope: AccessScope | None = None) -> list[RetrievedDoc]:
        """关联告警推荐（知识图谱的轻量替代）：同设备类型的其他故障工单。"""
        if not equipment_type:
            return []
        access = self._scope(roles, tenant_id, scope)
        expr = (self._filter_expr(self.settings.doc_collection, access, kb_version) +
                f' and doc_type == "fault_case" and equipment_type == "{equipment_type}" '
                f'and alarm_code != "{alarm_code}"')
        try:
            rows = self._call("query", collection_name=self.settings.doc_collection, filter=expr,
                              output_fields=self._supported_output_fields(self.settings.doc_collection,
                                  ["text", "doc_type", "source", "alarm_code", "equipment_type", "kb_version",
                                   "tenant_id", "source_system", "source_id"]),
                              limit=limit)
        except Exception:  # noqa: BLE001
            return []
        return [RetrievedDoc(id=r["id"], text=r.get("text", ""), doc_type="fault_case",
                             fusion_score=0.0,
                             source=r.get("source", ""), alarm_code=r.get("alarm_code", ""),
                             equipment_type=r.get("equipment_type", ""),
                             tenant_id=r.get("tenant_id", tenant_id), source_system=r.get("source_system", ""),
                             source_id=r.get("source_id", ""),
                             kb_version=r.get("kb_version", "")) for r in rows]

    def _embed_query_cached(self, q: str) -> list[float]:
        sig = hashlib.sha256(("QI:" + q).encode()).hexdigest()[:24]
        cached = self.cache.get("emb", "global", "any", sig)
        if cached is not None:
            return cached
        vec = self.embedder.embed_query(q)
        self.cache.set("emb", "global", "any", sig, vec)
        return vec

    # ---------------- 文档检索（retrieval 路径核心） ----------------

    @withdrawal_checked
    def search_docs(self, query_variants: list[str], roles: list[str], kb_version: str,
                    doc_types: list[str] | None = None,
                    network_tech: list[str] | None = None,
                    top_k: int | None = None,
                    tenant_id: str = "default", scope: AccessScope | None = None,
                    strict_doc_types: bool = False, constraint=None) -> RetrievalResult:
        settings = self.settings
        top_k = top_k or settings.top_k_dense
        w_dense, w_sparse = self._hybrid_weights(doc_types)

        access = self._scope(roles, tenant_id, scope)
        ph = access.fingerprint
        signature = [query_variants, doc_types, network_tech]
        if strict_doc_types:
            signature.append("strict-doc-types-v1")
        if constraint is not None:
            if constraint.empty:
                return RetrievalResult(query_variants=query_variants, docs=[])
            self.require_evidence_schema('doc')
            signature.append(constraint.signature())
        sig = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()[:24]
        cached = self.cache.get_with_lock("ret", kb_version, ph, sig)
        if isinstance(cached, list):
            docs = [RetrievedDoc.from_dict(d) for d in cached]
            if constraint is not None:
                docs = [doc for doc in docs if constraint.allows(doc)]
            return RetrievalResult(query_variants=query_variants, docs=docs,
                                   candidates_dense=-1, candidates_sparse=-1,
                                   after_dedup=len(docs), filter_relaxed=False,
                                   cache_hit=True)

        hard_expr = self._filter_expr(self.settings.doc_collection, access, kb_version)
        soft_expr = self._filter_expr(self.settings.doc_collection, access, kb_version,
                                      doc_types, network_tech)
        if constraint is not None:
            hard_expr += ' and ' + constraint.expression()
            soft_expr += ' and ' + constraint.expression()

        dense_hits, sparse_hits = self._recall(query_variants, soft_expr, top_k)
        relaxed = False
        if not strict_doc_types and not dense_hits and not sparse_hits and (doc_types or network_tech):
            # 软过滤未命中 → 放宽重查
            dense_hits, sparse_hits = self._recall(query_variants, hard_expr, top_k)
            relaxed = True

        docs = fuse(dense_hits, sparse_hits, w_dense, w_sparse,
                    method=self.settings.fusion_method)
        if constraint is not None:
            docs = [doc for doc in docs if constraint.allows(doc)]
        docs = dedup_by_parent(docs)
        docs = cap_per_source(docs)
        self.cache.set("ret", kb_version, ph, sig, [d.to_dict() for d in docs])
        return RetrievalResult(
            query_variants=query_variants, docs=docs,
            candidates_dense=len(dense_hits), candidates_sparse=len(sparse_hits),
            after_dedup=len(docs), filter_relaxed=relaxed,
        )

    @withdrawal_checked
    def fetch_sop_source(self, seed: RetrievedDoc, roles: list[str], kb_version: str,
                         *, tenant_id: str = "default", scope: AccessScope | None = None) -> list[RetrievedDoc]:
        """Uncached strong read; incomplete schemas fail closed, never read local raw."""
        from qa_core.retrieval.workflow import REQUIRED_WORKFLOW_FIELDS, MAX_WORKFLOW_ROWS
        from qa_core.retrieval.filters import _literal, _to_doc
        collection = self.settings.doc_collection
        description = self._call("describe_collection", collection_name=collection)
        fields = {f["name"] for f in description["fields"]}
        if not REQUIRED_WORKFLOW_FIELDS <= fields:
            raise ValueError("workflow requires full security/lifecycle/step schema")
        access = self._scope(roles, tenant_id, scope)
        # Source revision is independent of the containing knowledge release.
        # Both identities remain pinned by the exact filters below.
        if (seed.tenant_id != access.tenant_id or seed.kb_version != kb_version
                or not seed.source_version or not seed.asset_id or seed.doc_type != "sop"):
            raise ValueError("workflow seed identity mismatch")
        self._field_cache[collection] = fields
        expr = self._filter_expr(collection, access, kb_version, ["sop"])
        expr += (f' and source == "{_literal(seed.source)}"'
                 f' and source_version == "{_literal(seed.source_version)}"'
                 f' and asset_id == "{_literal(seed.asset_id)}"')
        rows = self._call("query", collection_name=collection, filter=expr,
                          output_fields=sorted(fields - {"dense", "sparse"}),
                          limit=MAX_WORKFLOW_ROWS + 1, consistency_level="Strong", timeout=10)
        from qa_core.storage.kb_dao import KbDao
        expected = KbDao(self.settings.mysql_dsn).asset_chunk_count(seed.asset_id, kb_version)
        if expected is None or expected > MAX_WORKFLOW_ROWS or len(rows) != expected:
            raise ValueError("workflow source incomplete or too large")
        return [_to_doc({"id": row["id"], "entity": row}) for row in rows]

    def require_evidence_schema(self, collection_kind="doc"):
        collection = self.settings.faq_collection if collection_kind == "faq" else self.settings.doc_collection
        description = self._call("describe_collection", collection_name=collection)
        fields = {f["name"] for f in description["fields"]}
        required = {"id", "source", "kb_version", "source_version", "asset_id", "tenant_id",
                    "dept_id", "visibility", "allowed_roles", "role_scope", "security_level",
                    "device_type", "status", "effective_from", "effective_to"}
        required |= {"question", "answer"} if collection_kind == "faq" else {"text"}
        if not required <= fields:
            raise ValueError("evidence requires complete security/lifecycle schema")
        self._field_cache[collection] = fields
        return collection, fields

    def fetch_record(self, kb_version, *, scope, constraint):
        """Complete, uncached record read. No parent dedup, ranking, or raw-file fallback."""
        from qa_core.retrieval.filters import _to_doc
        from qa_core.retrieval.constraints import RecordUnavailable
        if not constraint.exact or constraint.empty or not scope.security_levels:
            return []
        collection, fields = self.require_evidence_schema('doc')
        if not {'ticket_id', 'parent_id', 'doc_type'} <= fields:
            raise RecordUnavailable('linked_record_unavailable')
        expr = self._filter_expr(collection, scope, kb_version, ['fault_case']) + ' and ' + constraint.expression()
        rows = self._call('query', collection_name=collection, filter=expr,
            output_fields=sorted(fields - {'dense', 'sparse'}), limit=4,
            consistency_level='Strong', timeout=10)
        if not rows or len(rows) > 3:
            raise RecordUnavailable()
        docs = [self.fetch_evidence_chunk(row['id'], kb_version, scope=scope) for row in rows]
        if any(doc is None or not constraint.allows(doc) or doc.source != doc.ticket_id for doc in docs):
            raise RecordUnavailable('linked_record_unavailable')
        from qa_core.security.withdrawals import WithdrawalPolicy
        if len(WithdrawalPolicy(self.settings).filter_docs(docs, scope.tenant_id)) != len(docs):
            raise RecordUnavailable('linked_record_unavailable')
        from qa_core.storage.location_dao import LocationDao
        from qa_core.storage.kb_dao import KbDao
        engine = KbDao(self.settings.mysql_dsn).engine
        try:
            groups = {}
            for doc in docs:
                loc = doc.extra.get('source_location') or {}
                matches = loc.get('matches', [])
                if (loc.get('format') != 'csv' or loc.get('ambiguous') or len(matches) != 1
                        or len(matches[0]) != 1 or matches[0][0].get('kind') != 'csv_record'
                        or loc.get('file_sha256') != constraint.checksum((doc.asset_id, doc.source_version))):
                    raise RecordUnavailable('record_location_unavailable')
                key = (loc['file_sha256'], matches[0][0]['record'])
                groups.setdefault(key, []).append(doc)
            if len(groups) != len({row[:2] for row in constraint.triples}):
                raise RecordUnavailable()
            for (checksum, record), group in groups.items():
                if LocationDao(engine).record_chunk_count(checksum, record) != len(group):
                    raise RecordUnavailable()
            return sorted(docs, key=lambda doc: (doc.asset_id, {'现象': 0, '根因': 1, '处理': 2}.get(doc.text[:2], 0), doc.id))
        finally:
            engine.dispose()

    @withdrawal_checked
    def fetch_evidence_chunk(self, chunk_id, kb_version, *, scope, collection_kind="doc"):
        from qa_core.retrieval.filters import _literal, _to_doc
        if not scope.security_levels:
            return None
        collection, fields = self.require_evidence_schema(collection_kind)
        expr = self._filter_expr(collection, scope, kb_version)
        expr += f' and id == "{_literal(chunk_id)}"'
        rows = self._call("query", collection_name=collection, filter=expr,
            output_fields=sorted(fields - {"dense", "sparse"}), limit=2,
            consistency_level="Strong", timeout=10)
        if len(rows) != 1:
            return None
        doc = _to_doc({"id": rows[0]["id"], "entity": rows[0]})
        if collection_kind == "faq":
            doc.question, doc.answer = rows[0].get("question", ""), rows[0].get("answer", "")
            doc.doc_type = "faq"
        from qa_core.storage.location_dao import LocationDao
        from qa_core.storage.kb_dao import KbDao
        engine = KbDao(self.settings.mysql_dsn).engine
        try:
            location = LocationDao(engine).get(doc)
            if location:
                doc.extra["source_location"] = location
        finally:
            engine.dispose()
        return doc

    def _recall(self, query_variants: list[str], expr: str, top_k: int):
        dense_hits: list[dict] = []
        sparse_hits: list[dict] = []
        # 注意：只请求 doc_collection 实际存在的字段（请求不存在的字段服务端报 UnexpectedError）
        output = self._supported_output_fields(self.settings.doc_collection, [
            "text", "doc_type", "source", "alarm_code", "kb_version", "network_tech",
            "equipment_type", "vendor", "clause", "parent_id", "tenant_id", "dept_id",
            "security_level", "device_type", "source_system", "source_id", "source_version",
            "effective_from", "effective_to", "status", "ticket_id", "cmdb_id", "change_id",
            "asset_id", "role_scope", "origin_type", "synthetic", "generator_version",
            "generation_seed", "review_status", "license_status", "evidence_refs",
        ])
        for q in query_variants:
            vec = self._embed_query_cached(q)
            dense_hits += self._call("search",
                collection_name=self.settings.doc_collection, data=[vec], anns_field="dense",
                search_params={"params": {"ef": self.settings.dense_ef}},
                limit=top_k, filter=expr, output_fields=output,
            )[0]
            sparse_hits += self._call("search",
                collection_name=self.settings.doc_collection, data=[q], anns_field="sparse",
                limit=top_k, filter=expr, output_fields=output,
            )[0]
        # 多变体命中合并：同 id 保留最高分
        return self._merge_hits(dense_hits), self._merge_hits(sparse_hits)

    @staticmethod
    def _merge_hits(hits: list[dict]) -> list[dict]:
        best: dict[str, dict] = {}
        for h in hits:
            key = h["id"]
            if key not in best or h["distance"] > best[key]["distance"]:
                best[key] = h
        return sorted(best.values(), key=lambda x: x["distance"], reverse=True)

    def _hybrid_weights(self, doc_types: list[str] | None) -> tuple[float, float]:
        default = (0.6, 0.4)
        if doc_types and "fault_case" in doc_types:
            return (0.5, 0.5)  # 工单字段词多，BM25 价值高（scenario 配置）
        return default

    # ---------------- FAQ 检索（faq_exact 路径用） ----------------

    @withdrawal_checked
    def catalog_docs(self, roles, kb_version, *, tenant_id="default", scope=None, kind="all"):
        """Bounded read-only catalog, no query embedding or ranking, full ACL retained."""
        from qa_core.retrieval.filters import _to_doc
        access = self._scope(roles, tenant_id, scope)
        if not access.security_levels:
            return []
        result = []
        for collection_kind in (['faq', 'doc'] if kind == 'all' else ['faq' if kind == 'faq' else 'doc']):
            collection, fields = self.require_evidence_schema(collection_kind)
            rows = self._call('query', collection_name=collection,
                filter=self._filter_expr(collection, access, kb_version),
                output_fields=sorted(fields - {'dense', 'sparse'}), limit=10000,
                consistency_level='Strong', timeout=20)
            if len(rows) >= 10000:
                raise ValueError('catalog exceeds bounded read limit')
            for row in rows:
                doc = _to_doc({'id': row['id'], 'entity': row})
                if not doc.asset_id or not doc.source_version:
                    continue
                if collection_kind == 'faq':
                    doc.doc_type = 'faq'; doc.text = doc.question = row.get('question', '')
                    doc.answer = row.get('answer', '')
                result.append(doc)
        return result

    @withdrawal_checked
    def recommended_faqs(self, roles, kb_version, *, tenant_id="default", scope=None):
        """Read current accessible FAQ questions; never invent demo questions."""
        from qa_core.retrieval.filters import _to_doc
        access = self._scope(roles, tenant_id, scope)
        collection, fields = self.require_evidence_schema('faq')
        rows = self._call('query', collection_name=collection,
            filter=self._filter_expr(collection, access, kb_version),
            output_fields=sorted(fields - {'dense', 'sparse'}), limit=1000,
            consistency_level='Strong', timeout=10)
        docs = []
        for row in sorted(rows, key=lambda row: (row.get('source', ''), str(row['id']))):
            if not row.get('question', '').strip() or not row.get('answer', '').strip():
                continue
            doc = _to_doc({'id': row['id'], 'entity': row})
            doc.doc_type = 'faq'
            doc.text = doc.question = row['question']
            doc.answer = row['answer']
            docs.append(doc)
        return docs

    @withdrawal_checked
    def search_faq(self, query: str, roles: list[str], kb_version: str,
                   top_k: int = 5, tenant_id: str = "default",
                   scope: AccessScope | None = None) -> list[RetrievedDoc]:
        access = self._scope(roles, tenant_id, scope)
        ph = access.fingerprint
        sig = hashlib.sha256(('faq-body-candidates-v4:' + query).encode()).hexdigest()[:24]
        cached = self.cache.get("faq", kb_version, ph, sig)
        if isinstance(cached, list) and cached:
            return [RetrievedDoc.from_dict(d) for d in cached]
        expr = self._filter_expr(self.settings.faq_collection, access, kb_version)
        exact = []
        if 'question' in (self._field_cache.get(self.settings.faq_collection) or {'question'}):
            exact = self._call('query', collection_name=self.settings.faq_collection,
                filter=expr + ' and question == ' + json.dumps(query, ensure_ascii=False),
                output_fields=self._supported_output_fields(self.settings.faq_collection,
                    ['question', 'answer', 'kb_version', 'source', 'tenant_id', 'dept_id',
                     'security_level', 'source_system', 'source_id', 'source_version', 'asset_id',
                     'status', 'effective_from', 'effective_to', 'role_scope', 'origin_type', 'synthetic']),
                limit=top_k, consistency_level='Strong')
        if exact:
            res = [{'id': row['id'], 'entity': row, 'distance': 1.0}
                   for row in sorted(exact, key=lambda row: str(row['id']))]
        else:
            vec = self._embed_query_cached(query)
            res = self._call("search",
            collection_name=self.settings.faq_collection, data=[vec], anns_field="dense",
            search_params={"params": {"ef": self.settings.dense_ef}},
            limit=top_k, filter=expr,
            output_fields=self._supported_output_fields(self.settings.faq_collection, [
                "question", "answer", "kb_version", "network_tech", "source", "tenant_id",
                "dept_id", "security_level", "source_system", "source_id", "source_version",
                "effective_from", "effective_to", "status", "ticket_id", "cmdb_id",
                "change_id", "asset_id", "role_scope", "origin_type", "synthetic",
                "generator_version", "generation_seed", "review_status", "license_status",
                "evidence_refs",
            ]),
            )[0]
        body_terms = []
        if not exact:
            import re
            body_terms = list(dict.fromkeys(re.findall(
                r'(?<![A-Za-z0-9])(?:PFCP|SCTP|FR1|FR2|[Pp][Cc]\d+|n\d+)(?![A-Za-z0-9])', query)))
            if body_terms:
                if re.search(r'端口|PFCP|SCTP', query, re.I):
                    body_terms.extend(re.findall(r'(?<![A-Za-z0-9])\d{4,5}(?![A-Za-z0-9])', query))
                clauses = []
                for term in body_terms:
                    variants = [term]
                    if term.upper().startswith('PC'):
                        variants.append('Power Class ' + term[2:])
                    clauses.append('(' + ' or '.join('answer like '+json.dumps('%'+v+'%') for v in variants) + ')')
                fields = self._supported_output_fields(self.settings.faq_collection,
                    ['question','answer','kb_version','network_tech','source','tenant_id','dept_id',
                     'security_level','source_system','source_id','source_version','effective_from',
                     'effective_to','status','asset_id','role_scope','origin_type','synthetic',
                     'generator_version','generation_seed','review_status','license_status','evidence_refs'])
                supplements = self._call('query', collection_name=self.settings.faq_collection,
                    filter=expr + ' and (' + ' and '.join(clauses) + ')', output_fields=fields,
                    limit=20, consistency_level='Strong')
                seen = {str(h['id']) for h in res}
                res = list(res) + [{'id': row['id'], 'entity': row, 'distance': 0.0}
                                   for row in supplements if str(row['id']) not in seen]
        docs = []
        for h in res:
            d = RetrievedDoc(
                id=h["id"], text=h["entity"].get("question", ""),
                fusion_score=round(float(h["distance"]), 4),
                dense_score=round(float(h["distance"]), 4),
                doc_type="faq", kb_version=h["entity"].get("kb_version", ""),
                source=h["entity"].get("source", ""),
                network_tech=h["entity"].get("network_tech", ""),
                tenant_id=h["entity"].get("tenant_id", tenant_id),
                dept_id=h["entity"].get("dept_id", ""),
                security_level=h["entity"].get("security_level", ""),
                source_system=h["entity"].get("source_system", ""),
                source_id=h["entity"].get("source_id", ""),
                source_version=h["entity"].get("source_version", ""),
                effective_from=h["entity"].get("effective_from", ""),
                effective_to=h["entity"].get("effective_to", ""),
                status=h["entity"].get("status", ""),
                ticket_id=h["entity"].get("ticket_id", ""),
                cmdb_id=h["entity"].get("cmdb_id", ""),
                change_id=h["entity"].get("change_id", ""),
                asset_id=h["entity"].get("asset_id", ""),
                origin_type=h["entity"].get("origin_type", ""),
                synthetic=bool(h["entity"].get("synthetic", False)),
                generator_version=h["entity"].get("generator_version", ""),
                generation_seed=h["entity"].get("generation_seed", ""),
                review_status=h["entity"].get("review_status", ""),
                license_status=h["entity"].get("license_status", ""),
                evidence_refs=h["entity"].get("evidence_refs", []) or [],
                role_scope=h["entity"].get("role_scope", []) or [],
                question=h["entity"].get("question", ""),
                answer=h["entity"].get("answer", ""),
            )
            docs.append(d)
        if body_terms:
            def support(doc):
                text = (doc.question + '\n' + doc.answer).casefold()
                return sum(term.casefold() in text or (term.upper().startswith('PC')
                           and ('power class ' + term[2:]).casefold() in text) for term in body_terms)
            docs.sort(key=lambda doc: (support(doc), doc.dense_score), reverse=True)
        if docs:
            self.cache.set("faq", kb_version, ph, sig, [d.to_dict() for d in docs])
        else:
            self.cache.set_empty("faq", kb_version, ph, sig)
        return docs

    @withdrawal_checked
    def lookup_faq_by_source(self, sources: list[str], roles: list[str], kb_version: str,
                             limit: int = 5, tenant_id: str = "default",
                             scope: AccessScope | None = None) -> list[RetrievedDoc]:
        """按 source 精确取 FAQ 条目（问句里已明确给出编号时使用）。

        r3 的 FAQ 由模板批量生成、高度同质，编号是唯一区分 token，向量模型对它不敏感：
        实测问句里写了"案例SIM-FAQ-0199"，search_faq 仍会返回别的条目。
        因此编号类实体只能走精确匹配。查不到返回空列表，由调用方决定降级。
        dense_score 置 1.0 表示"精确命中"，供上层直出判定直接通过。
        """
        if not sources:
            return []
        access = self._scope(roles, tenant_id, scope)
        quoted = ", ".join('"%s"' % s for s in sources)
        expr = self._filter_expr(self.settings.faq_collection, access, kb_version) + \
            f" and source in [{quoted}]"
        rows = self._call("query",
            collection_name=self.settings.faq_collection, filter=expr, limit=limit,
            output_fields=self._supported_output_fields(self.settings.faq_collection, [
                "question", "answer", "kb_version", "network_tech", "source", "tenant_id",
                "dept_id", "security_level", "source_system", "source_id", "source_version",
                "effective_from", "effective_to", "status", "ticket_id", "cmdb_id",
                "change_id", "asset_id", "role_scope", "origin_type", "synthetic",
                "generator_version", "generation_seed", "review_status", "license_status",
                "evidence_refs",
            ]),
        )
        return [RetrievedDoc(
            id=r["id"], text=r.get("question", ""), fusion_score=1.0, dense_score=1.0,
            doc_type="faq", kb_version=r.get("kb_version", ""), source=r.get("source", ""),
            network_tech=r.get("network_tech", ""), tenant_id=r.get("tenant_id", tenant_id),
            dept_id=r.get("dept_id", ""), security_level=r.get("security_level", ""),
            source_system=r.get("source_system", ""), source_id=r.get("source_id", ""),
            source_version=r.get("source_version", ""),
            effective_from=r.get("effective_from", ""), effective_to=r.get("effective_to", ""),
            status=r.get("status", ""), ticket_id=r.get("ticket_id", ""),
            cmdb_id=r.get("cmdb_id", ""), change_id=r.get("change_id", ""),
            asset_id=r.get("asset_id", ""), origin_type=r.get("origin_type", ""),
            synthetic=bool(r.get("synthetic", False)),
            generator_version=r.get("generator_version", ""),
            generation_seed=r.get("generation_seed", ""),
            review_status=r.get("review_status", ""),
            license_status=r.get("license_status", ""),
            evidence_refs=r.get("evidence_refs", []) or [],
            role_scope=r.get("role_scope", []) or [],
            question=r.get("question", ""), answer=r.get("answer", ""),
        ) for r in rows]

    # ---------------- structured_lookup 路径（告警码精确查询） ----------------

    @withdrawal_checked
    def lookup_alarm(self, alarm_code: str, roles: list[str], kb_version: str,
                     limit: int = 5, tenant_id: str = "default",
                     scope: AccessScope | None = None) -> list[RetrievedDoc]:
        access = self._scope(roles, tenant_id, scope)
        expr = self._filter_expr(self.settings.doc_collection, access, kb_version) + \
            f' and alarm_code == "{alarm_code}"'
        output = self._supported_output_fields(self.settings.doc_collection, [
            "text", "doc_type", "source", "alarm_code", "kb_version", "equipment_type",
            "vendor", "network_tech", "clause", "parent_id", "tenant_id", "dept_id",
            "security_level", "device_type", "source_system", "source_id", "source_version",
            "effective_from", "effective_to", "status", "ticket_id", "cmdb_id", "change_id",
            "asset_id", "role_scope", "origin_type", "synthetic", "generator_version",
            "generation_seed", "review_status", "license_status", "evidence_refs",
        ])
        # 先取足量再在应用层排序：只取 limit 条再排序是假确定性
        # （Milvus 返回哪 5 条本身就不稳定）。见 order_alarm_hits 的说明。
        rows = self._call("query",
            collection_name=self.settings.doc_collection, filter=expr,
            limit=max(limit, 200),
            output_fields=output,
        )
        rows = order_alarm_hits(rows, limit)
        return [RetrievedDoc(
            id=r["id"], text=r.get("text", ""), fusion_score=1.0,
            doc_type=r.get("doc_type", ""), source=r.get("source", ""),
            alarm_code=r.get("alarm_code", ""), kb_version=r.get("kb_version", ""),
            equipment_type=r.get("equipment_type", ""), vendor=r.get("vendor", ""),
            network_tech=r.get("network_tech", ""), parent_id=r.get("parent_id", ""),
            tenant_id=r.get("tenant_id", tenant_id), dept_id=r.get("dept_id", ""),
            security_level=r.get("security_level", ""), device_type=r.get("device_type", ""),
            source_system=r.get("source_system", ""), source_id=r.get("source_id", ""),
            source_version=r.get("source_version", ""), effective_from=r.get("effective_from", ""),
            effective_to=r.get("effective_to", ""), status=r.get("status", ""),
            ticket_id=r.get("ticket_id", ""), cmdb_id=r.get("cmdb_id", ""),
            change_id=r.get("change_id", ""), asset_id=r.get("asset_id", ""),
            origin_type=r.get("origin_type", ""), synthetic=bool(r.get("synthetic", False)),
            generator_version=r.get("generator_version", ""),
            generation_seed=r.get("generation_seed", ""),
            review_status=r.get("review_status", ""), license_status=r.get("license_status", ""),
            evidence_refs=r.get("evidence_refs", []) or [],
            role_scope=r.get("role_scope", []) or [],
        ) for r in rows]
