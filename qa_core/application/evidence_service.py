"""Read-only evidence access. Every return re-reads scoped, current vector rows."""
from qa_core.schemas.evidence import decode_reference, evidence_of
from qa_core.retrieval.workflow import is_workflow_query, retrieve_workflow, assemble_sop
from qa_core.security.withdrawals import WithdrawalPolicy


def create_evidence_service(settings, searcher_type=None):
    """Shared lazy construction: source reads never load query models."""
    from qa_core.retrieval.searcher import Searcher
    class LazyEmbedder:
        def embed_query(self, query):
            from qa_core.indexing.embedder.embedder import get_embedder
            return get_embedder(settings.embedding_model_path, settings.embedding_device).embed_query(query)
    class LazyReranker:
        def rerank(self, *args, **kwargs):
            from qa_core.rerank.reranker import get_reranker
            return get_reranker(settings.reranker_model_path, settings.rerank_device).rerank(*args, **kwargs)
    return EvidenceService((searcher_type or Searcher)(settings=settings, embedder=LazyEmbedder()), LazyReranker(), settings)


class EvidenceService:
    def __init__(self, searcher, reranker, settings):
        self.searcher, self.reranker, self.settings = searcher, reranker, settings

    def source(self, token, scope, *, constraint=None):
        version, kind, chunk, asset, revision, digest = decode_reference(token)
        doc = self.searcher.fetch_evidence_chunk(chunk, version, scope=scope, collection_kind=kind)
        if doc is None or doc.asset_id != asset or doc.source_version != revision:
            return None
        if constraint is not None and not constraint.allows(doc):
            return None
        if not WithdrawalPolicy(self.settings).filter_docs([doc], scope.tenant_id):
            return None
        result = evidence_of(doc, kind)
        if decode_reference(result["evidence_id"])[5] != digest:
            return None
        return result

    def search(self, query, version, scope, top_n, *, constraint=None):
        if not scope.security_levels:
            return {"status": "NO_EVIDENCE", "kb_version": version, "evidence": []}
        if constraint is not None and constraint.exact:
            from qa_core.retrieval.constraints import RecordUnavailable
            try:
                docs = self.searcher.fetch_record(version, scope=scope, constraint=constraint)
            except RecordUnavailable as exc:
                return {'status': 'NO_EVIDENCE', 'reason': exc.reason, 'kb_version': version, 'evidence': []}
            except ValueError:
                return {'status': 'NO_EVIDENCE', 'reason': 'record_incomplete', 'kb_version': version, 'evidence': []}
            return {'status': 'EVIDENCE_FOUND' if docs else 'NO_EVIDENCE', 'reason': 'record_incomplete' if not docs else '',
                    'kb_version': version, 'evidence': [evidence_of(doc) for doc in docs]}
        options = {'constraint': constraint} if constraint is not None else {}
        if is_workflow_query(query):
            result = retrieve_workflow(self.searcher, self.reranker, query, list(scope.roles),
                version, tenant_id=scope.tenant_id, scope=scope, min_score=self.settings.rerank_min, **options)
            if not result.ready:
                reason = ('related_evidence_unavailable' if constraint is not None and result.reason == 'no_applicable_sop' else result.reason)
                return {"status": "NEEDS_CLARIFICATION" if result.question else "NO_EVIDENCE", "reason": reason,
                        "question": result.question,
                        "kb_version": version, "evidence": []}
            # Return individual real chunks; never mint a reference to synthetic aggregate text.
            docs = self.searcher.fetch_sop_source(result.packet, list(scope.roles), version,
                tenant_id=scope.tenant_id, scope=scope)
            verified = assemble_sop(docs, result.packet)
            if not verified.ready:
                return {"status": "NO_EVIDENCE", "reason": verified.reason,
                        "kb_version": version, "evidence": []}
            docs.sort(key=lambda d: d.extra["step_index"][0])
        else:
            self.searcher.require_evidence_schema("doc")
            recalled = self.searcher.search_docs([query], list(scope.roles), version,
                tenant_id=scope.tenant_id, scope=scope, **options)
            candidates = recalled.docs
            if getattr(self.settings, "workflow_evidence_enabled", False):
                from qa_core.retrieval.workflow import lab_source_applicable
                candidates = [doc for doc in candidates if lab_source_applicable(query, doc.text)]
            docs = self.reranker.rerank(query, candidates[:10], top_n=top_n)
            docs = [d for d in docs if d.rerank_score is not None
                    and d.rerank_score >= self.settings.rerank_min]
        evidence = []
        for doc in docs:
            # Revalidate even cache hits immediately before emitting text.
            current = self.searcher.fetch_evidence_chunk(doc.id, version, scope=scope)
            if (current is not None and current.asset_id and current.source_version
                    and (constraint is None or constraint.allows(current))
                    and current.text == doc.text and current.asset_id == doc.asset_id
                    and current.source_version == doc.source_version):
                current.dense_score, current.sparse_score = doc.dense_score, doc.sparse_score
                current.fusion_score, current.rerank_score = doc.fusion_score, doc.rerank_score
                evidence.append(evidence_of(current))
            elif is_workflow_query(query):
                return {"status": "NO_EVIDENCE", "kb_version": version, "evidence": []}
        return {"status": "EVIDENCE_FOUND" if evidence else "NO_EVIDENCE",
                "reason": 'related_evidence_unavailable' if constraint is not None and not evidence else '',
                "kb_version": version, "evidence": evidence}
