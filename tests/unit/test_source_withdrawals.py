from unittest.mock import Mock
import pytest

from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.security.withdrawals import allowed_source, identity_hash, WithdrawalPolicy, assert_publishable_sources


@pytest.mark.parametrize("system,source_id,allowed", [("wiki", "removed", False), ("wiki", "good", True),
                                                     ("", "", False), ("wiki", "", False)])
def test_source_identity_and_legacy_fail_closed(system, source_id, allowed):
    row = {"tenant_id": "t", "source_system": system, "source_id": source_id}
    entries = {identity_hash("t", "wiki", "removed")}
    assert allowed_source(row, "t", entries) is allowed
    assert allowed_source(row, "t", set()) is True


def test_identity_is_version_independent_and_tenant_isolated():
    assert identity_hash("t1", "wiki", "id") != identity_hash("t2", "wiki", "id")
    doc = RetrievedDoc("old", "body", .9, tenant_id="t", kb_version="old", source_system="wiki", source_id="id")
    entries = {identity_hash("t", "wiki", "id")}
    assert not allowed_source(doc, "t", entries)
    doc.kb_version = "new"
    assert not allowed_source(doc, "t", entries)


def test_publish_withdrawn_or_unknown_source_denied(monkeypatch):
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda *a: {identity_hash("t", "wiki", "id")})
    for sources in ([('t', 'wiki', 'id')], [('t', '', '')]):
        with pytest.raises(ValueError, match="禁止发布"):
            assert_publishable_sources(Mock(), sources)
    assert_publishable_sources(Mock(), [('t', 'wiki', 'good')])


@pytest.mark.parametrize("stream", [False, True])
def test_withdrawn_retrieval_never_calls_llm(monkeypatch, stream):
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.schemas.intent import IntentDecision
    doc = RetrievedDoc("old", "packet evidence", .9, rerank_score=.9,
                       source_system="wiki", source_id="removed")
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda *a: {identity_hash("default", "wiki", "removed")})
    uc = QaUseCase(searcher=Mock(), llm=Mock())
    uc.settings = uc.settings.model_copy(update={"semantic_cache_enabled": False})
    uc._retrieve_and_rerank = lambda *a, **kw: [doc]
    decision = IntentDecision("knowledge", "retrieval", "rule", .9, "test")
    if stream:
        assert uc._stream_retrieval("packet", ["ops"], "v1", decision)[0] is None
    else:
        assert uc._retrieval_path("packet", ["ops"], "v1", decision)[1] == []
    uc.llm.chat.assert_not_called()
    uc.llm.chat_stream.assert_not_called()


def test_semantic_cache_withdrawal_does_not_reuse_answer(monkeypatch):
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.schemas.intent import IntentDecision
    from qa_core.cache.semantic_cache import SemanticCache
    monkeypatch.setattr(WithdrawalPolicy, "entries", lambda *a: {identity_hash("default", "wiki", "removed")})
    monkeypatch.setattr(SemanticCache, "lookup", lambda *a: {"answer": "unsafe cached answer", "similarity": .99,
        "sources": [{"tenant_id": "default", "source_system": "wiki", "source_id": "removed"}]})
    uc = QaUseCase(searcher=Mock(), llm=Mock())
    uc.searcher._embed_query_cached.return_value = [1., 0.]
    uc._retrieve_and_rerank = lambda *a, **kw: []
    result = uc._retrieval_path("packet", ["ops"], "v1", IntentDecision("knowledge", "retrieval", "rule", .9, "test"))
    assert result[0] != "unsafe cached answer" and result[1] == []
    uc.llm.chat.assert_not_called()
