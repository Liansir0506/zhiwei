from unittest.mock import Mock
import pytest
from qa_core.indexing.chunkers.base import make_chunk
from qa_core.indexing.chunkers.token_budget import bounded_chunks
from qa_core.schemas.qa_status import outcome


class CharacterTokenizer:
    def encode(self, text, **kwargs): return [0] * (len(text) + 2)


def test_token_budget_preserves_text_and_metadata():
    text = "中文与code.Mixed!" * 70
    original = make_chunk(text, "manual", source="a.md", chapter_path="A/B")
    parts = bounded_chunks([original], CharacterTokenizer(), max_tokens=30)
    assert "".join(p.text for p in parts) == text
    assert all(len(CharacterTokenizer().encode(p.text)) <= 30 for p in parts)
    assert all(p.metadata["chapter_path"] == "A/B" for p in parts)
    assert original.text == text


@pytest.mark.parametrize("kind", ["sop", "fault_case", "faq"])
def test_atomic_chunk_never_truncated(kind):
    with pytest.raises(ValueError): bounded_chunks([make_chunk("长" * 1300, kind)], CharacterTokenizer())


def test_real_local_tokenizer_limits():
    from transformers import AutoTokenizer
    from qa_core.config.settings import get_settings
    tokenizer = AutoTokenizer.from_pretrained(get_settings().embedding_model_path, local_files_only=True)
    text = "通信运维证据验证。AMF_registration counter 123456789。" * 120
    parts = bounded_chunks([make_chunk(text, "manual")], tokenizer)
    assert len(parts) > 1 and "".join(p.text for p in parts) == text
    assert all(len(tokenizer.encode(p.text, add_special_tokens=True)) <= 450 for p in parts)


@pytest.mark.parametrize("answer,refusal,degraded,intent,expected", [
    ("有依据", False, False, "knowledge", "ANSWERED"),
    ("引用模式", False, True, "knowledge", "DEGRADED"),
    ("拒答", True, True, "knowledge", "NO_EVIDENCE"),
    ("超纲", True, False, "out_of_scope", "OUT_OF_SCOPE")])
def test_shared_outcomes(answer, refusal, degraded, intent, expected):
    assert outcome(answer, refusal=refusal, degraded=degraded, intent=intent) == expected


def test_workflow_children_are_real_content_bound_refs():
    from qa_core.retrieval.workflow import assemble_sop
    from qa_core.schemas.retrieval import RetrievedDoc
    from qa_core.schemas.evidence import decode_reference
    from qa_core.application.qa_usecase import QaUseCase
    docs = [RetrievedDoc(str(i), text, .8, doc_type="sop", source="sop.md", kb_version="v1", source_version="v1",
        asset_id="a", extra={"step_index": [i]}) for i, text in enumerate([
        "# 流程", "步骤 1：只读检查，停止并转人工。", "步骤 2：审批后回滚。"])]
    packet = assemble_sop(docs, docs[0]).packet
    refs = QaUseCase._sources_of([packet])[0]["child_evidence"]
    assert [decode_reference(r["evidence_id"])[2] for r in refs] == ["0", "1", "2"]
    assert "evidence_id" not in QaUseCase._sources_of([packet])[0]


def test_http_backend_failure_has_terminal_status_without_secret(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qa_core.api import route_qa
    from qa_core.api.auth import AuthContext, get_auth_context
    app = FastAPI(); app.include_router(route_qa.router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext("u", ["ops"], "api_key")
    monkeypatch.setattr(route_qa, "_enforce_rate_limit", Mock())
    monkeypatch.setattr(route_qa, "audit_access", Mock())
    uc = Mock(); uc.ask.side_effect = RuntimeError("secret key and backend path")
    monkeypatch.setattr(route_qa, "get_usecase", lambda: uc)
    with TestClient(app) as client:
        r = client.post("/qa", json={"query": "设备检查"})
    assert r.status_code == 503 and r.json()["detail"]["status"] == "FAILED"
    assert "secret" not in r.text
