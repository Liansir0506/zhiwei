from types import SimpleNamespace
from unittest.mock import MagicMock
from copy import deepcopy

import pytest

from qa_core.config.settings import get_settings
from qa_core.ingest.binding import execution_binding, version_binding
from qa_core.application.request_view import request_scoped
from scripts import ingest_worker


@pytest.mark.parametrize("field", ["milvus_uri", "doc_collection", "faq_collection", "embedding_model_path", "scenario"])
def test_worker_mismatch_denied_before_guard_and_mutation(monkeypatch, field):
    settings = get_settings().model_copy()
    payload = {}
    payload["execution_binding"] = execution_binding(settings, "rebuild", payload)
    changed = settings.model_copy(update={field: "changed"})
    monkeypatch.setattr(ingest_worker, "get_settings", lambda: changed)
    monkeypatch.setattr(ingest_worker, "candidate_write", lambda *a, **kw: pytest.fail("must reject before write lock"))
    with pytest.raises(ValueError, match="绑定"):
        ingest_worker.execute_job({"job_type": "rebuild", "kb_version": "candidate", "payload_json": payload}, MagicMock(), "test")


def test_legacy_task_without_binding_is_not_silently_adopted(monkeypatch):
    monkeypatch.setattr(ingest_worker, "candidate_write", lambda *a, **kw: pytest.fail("must not start"))
    with pytest.raises(ValueError, match="缺少配置绑定"):
        ingest_worker.execute_job({"job_type": "rebuild", "kb_version": "candidate", "payload_json": {}}, MagicMock(), "test")


def test_binding_contains_no_keys_or_raw_endpoints():
    import json
    settings = get_settings().model_copy(update={"milvus_uri": "http://user:secret@host:19530", "llm_api_key": "secret-key"})
    encoded = json.dumps(version_binding(settings))
    assert "secret" not in encoded and "host:19530" not in encoded


def test_sync_and_stream_views_freeze_configuration_without_mutating_shared_searcher():
    from qa_core.retrieval.searcher import Searcher
    shared = object.__new__(Searcher)
    settings = get_settings().model_copy()
    shared.settings = settings
    class Owner:
        def __init__(self):
            self.settings, self.cfg, self._searcher = settings, {"flag": "old"}, shared
        @request_scoped
        def ask(self):
            settings.doc_collection = "changed-after-start"
            return self.settings.doc_collection, self._searcher.settings.doc_collection
        @request_scoped
        def ask_stream(self):
            yield self.settings.doc_collection
            settings.doc_collection = "stream-change"
            yield self.settings.doc_collection
    owner = Owner()
    old = settings.doc_collection
    assert owner.ask() == (old, old)
    iterator = owner.ask_stream()
    first = next(iterator)
    assert next(iterator) == first
    assert shared.settings is settings and shared.settings.doc_collection == "stream-change"


@pytest.mark.parametrize("stream", [False, True])
def test_real_usecase_pins_version_before_intent_processing(monkeypatch, stream):
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.schemas.intent import IntentDecision
    uc = QaUseCase(searcher=MagicMock(), llm=MagicMock())
    state = {"active": "old", "reads": 0}
    def active():
        state["reads"] += 1
        return state["active"]
    def decide(*a, **kw):
        state["active"] = "new"
        uc.settings.doc_collection = "changed"
        return IntentDecision("greeting", "direct_answer", "rule", .9, "test")
    monkeypatch.setattr(uc, "_active_kb_version", active)
    monkeypatch.setattr(uc, "_decide", decide)
    if stream:
        events = list(uc.ask_stream("hello", persist=False))
        assert next(e["data"] for e in events if e["event"] == "start")["kb_version"] == "old"
        assert next(e["data"] for e in events if e["event"] == "end")["kb_version"] == "old"
    else:
        assert uc.ask("hello", persist=False)["diagnostics"]["kb_version"] == "old"
    assert state["reads"] == 1
