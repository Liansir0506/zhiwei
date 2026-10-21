import copy
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api import route_evidence as api
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.application.evidence_service import EvidenceService
from qa_core.schemas.evidence import evidence_of, decode_reference
from qa_core.schemas.retrieval import RetrievedDoc, RetrievalResult
from qa_core.security.policy import AccessScope
from qa_core.retrieval.searcher import Searcher


@pytest.fixture(autouse=True)
def isolate_withdrawal_ledger_for_evidence_unit_tests(monkeypatch):
    from qa_core.security.withdrawals import WithdrawalPolicy
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda *a: set())


def doc():
    return RetrievedDoc("chunk-1", "原文：只读检查", .8, rerank_score=.8,
        asset_id="asset-1", kb_version="v1", source_version="r1", source="a.md")


def service():
    searcher = Mock()
    searcher.fetch_evidence_chunk.return_value = doc()
    searcher.search_docs.return_value = RetrievalResult(["检查依据"], [doc()])
    reranker = Mock(); reranker.rerank.return_value = [doc()]
    return EvidenceService(searcher, reranker, SimpleNamespace(rerank_min=.3))


def test_content_bound_reference_and_legacy_location():
    d = doc(); e = evidence_of(d)
    assert decode_reference(e["evidence_id"])[:5] == ["v1", "doc", "chunk-1", "asset-1", "r1"]
    assert e["location"] == {"kind": "legacy", "available": False}
    d.text += "修订"; assert evidence_of(d)["evidence_id"] != e["evidence_id"]


def test_reference_service_does_not_load_models(monkeypatch):
    from qa_core.indexing.embedder.embedder import get_embedder
    import qa_core.indexing.embedder.embedder as emb
    import qa_core.rerank.reranker as rr
    embed_loader, rank_loader = Mock(side_effect=AssertionError("model loaded")), Mock(side_effect=AssertionError("model loaded"))
    monkeypatch.setattr(emb, "get_embedder", embed_loader)
    monkeypatch.setattr(rr, "get_reranker", rank_loader)
    monkeypatch.setattr("qa_core.retrieval.searcher.get_milvus_client", lambda uri: Mock())
    api.get_evidence_service.cache_clear()
    try:
        assert api.get_evidence_service() is not None
        embed_loader.assert_not_called(); rank_loader.assert_not_called()
    finally: api.get_evidence_service.cache_clear()


@pytest.mark.parametrize("token", ["!", "a" * 2049, "e30", "W10"])
def test_invalid_reference(token):
    with pytest.raises(ValueError): decode_reference(token)


@pytest.mark.parametrize("change", ["text", "source_version", "asset_id", "missing"])
def test_reference_revalidates_content_and_identity(change):
    s = service(); token = evidence_of(doc())["evidence_id"]
    if change == "missing": s.searcher.fetch_evidence_chunk.return_value = None
    else: setattr(s.searcher.fetch_evidence_chunk.return_value, change, "changed")
    assert s.source(token, AccessScope.build(roles=["ops"])) is None


def test_search_rechecks_cached_hits_and_uses_same_reference():
    s = service(); scope = AccessScope.build(tenant_id="t1", roles=["ops"], dept_ids=["d1"])
    result = s.search("检查依据", "v1", scope, 5)
    assert result["status"] == "EVIDENCE_FOUND"
    e = result["evidence"][0]
    assert s.source(e["evidence_id"], scope)["text"] == e["text"]
    assert s.searcher.fetch_evidence_chunk.call_args.kwargs["scope"] == scope
    s.searcher.fetch_evidence_chunk.return_value = None
    assert s.search("检查依据", "v1", scope, 5)["evidence"] == []


def test_no_security_levels_cannot_fallback_to_public():
    s = service(); scope = AccessScope(roles=("ops",), security_levels=())
    assert s.search("检查依据", "v1", scope, 5)["evidence"] == []
    s.searcher.search_docs.assert_not_called()


@pytest.fixture
def client(monkeypatch):
    app = FastAPI(); app.include_router(api.router)
    ctx = AuthContext("u1", ["ops"], "api_key")
    app.dependency_overrides[get_auth_context] = lambda: ctx
    monkeypatch.setattr(api, "record", Mock())
    monkeypatch.setattr(api, "_enforce_rate_limit", Mock())
    monkeypatch.setattr(api, "resolve_version", lambda requested, ctx: requested or "v1")
    s = service(); monkeypatch.setattr(api, "get_evidence_service", lambda: s)
    with TestClient(app) as c: yield c, s, ctx


def test_api_readonly_and_no_store(client):
    c, s, ctx = client
    r = c.post("/kb/search-evidence", json={"query": "检查依据"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    token = r.json()["evidence"][0]["evidence_id"]
    viewed = c.get("/kb/evidence/" + token)
    assert viewed.status_code == 200 and viewed.headers["cache-control"] == "no-store"
    assert viewed.json()["source_revision"] == r.json()["evidence"][0]["source_revision"]
    s.searcher.fetch_evidence_chunk.return_value = None
    assert c.get("/kb/evidence/" + token).status_code == 404


@pytest.mark.parametrize("payload", [{"query": ""}, {"query": "   "}, {"query": "x", "top_n": 11},
    {"query": "x", "tenant_id": "other"}, {"query": "x", "roles": ["admin"]}])
def test_api_rejects_bad_requests(client, payload):
    assert client[0].post("/kb/search-evidence", json=payload).status_code == 422


def test_api_invalid_id_and_backend_failure(client):
    c, s, ctx = client
    assert c.get("/kb/evidence/invalid!").status_code == 404
    s.searcher.fetch_evidence_chunk.side_effect = RuntimeError("secret backend path")
    r = c.get("/kb/evidence/" + evidence_of(doc())["evidence_id"])
    assert r.status_code == 503 and "secret" not in r.text


def test_schema_incomplete_fails_closed():
    searcher = Searcher(client=Mock(), embedder=Mock(), cache=Mock(),
                        settings=SimpleNamespace(doc_collection="docs", faq_collection="faq"))
    searcher.client.describe_collection.return_value = {"fields": [{"name": "id"}]}
    with pytest.raises(ValueError): searcher.fetch_evidence_chunk("x", "v1", scope=AccessScope.build())
    searcher.client.query.assert_not_called()


def test_qa_citation_uses_same_reference_and_aggregate_has_no_fake_reference():
    from qa_core.application.qa_usecase import QaUseCase
    d = doc()
    assert QaUseCase._sources_of([d])[0]["evidence_id"] == evidence_of(d)["evidence_id"]
    d.extra.update(workflow_evidence=True, workflow_steps=[1], child_chunk_ids=["chunk-1"])
    assert "evidence_id" not in QaUseCase._sources_of([d])[0]


@pytest.mark.parametrize("mode", ["complete", "unsafe", "changed"])
def test_workflow_search_returns_real_ordered_chunks_or_fails_closed(monkeypatch, mode):
    from qa_core.retrieval.workflow import assemble_sop, WorkflowResult
    docs = [RetrievedDoc(str(i), text, .8, source="sop.md", doc_type="sop", asset_id="a",
            kb_version="v1", source_version="v1", status="active", extra={"step_index": [i]})
            for i, text in enumerate(["# AMF SOP", "步骤 1：只读检查，停止并转人工。",
                                     "步骤 2：审批保存快照，失败回滚。"])]
    result = assemble_sop(docs, docs[0]); assert result.ready
    s = service()
    if mode == "unsafe": result = WorkflowResult(None, False, "missing_safety_controls")
    monkeypatch.setattr("qa_core.application.evidence_service.retrieve_workflow", lambda *a, **kw: result)
    s.searcher.fetch_sop_source.return_value = list(reversed(docs))
    by_id = {d.id: d for d in docs}
    def fresh(chunk, *a, **kw):
        d = copy.deepcopy(by_id[chunk])
        if mode == "changed": d.text += "修订"
        return d
    s.searcher.fetch_evidence_chunk.side_effect = fresh
    r = s.search("AMF SOP", "v1", AccessScope.build(roles=["ops"]), 1)
    if mode == "complete":
        assert [e["chunk_id"] for e in r["evidence"]] == ["0", "1", "2"]
        assert all(e["text"] == by_id[e["chunk_id"]].text for e in r["evidence"])
    else: assert r["evidence"] == []


@pytest.mark.parametrize("status,role,allowed", [("ACTIVE", "ops", True), ("STAGED", "ops", False),
    ("STAGED", "admin", True), ("ARCHIVED", "admin", False)])
def test_version_publication_policy(monkeypatch, status, role, allowed):
    from qa_core.storage import version_manifest
    monkeypatch.setattr(version_manifest, "verify_read_binding", lambda *a: None)
    engine = MagicMock(); connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.all.return_value = [("v1", status)]
    monkeypatch.setattr(api.sa, "create_engine", lambda dsn: engine)
    ctx = AuthContext("u", [role], "api_key")
    if allowed: assert api.resolve_version("v1", ctx) == "v1"
    else:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e: api.resolve_version("v1", ctx)
        assert e.value.status_code == 404
    engine.dispose.assert_called_once()
