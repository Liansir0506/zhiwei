"""HTTP SSE contract and bounded blocking-stream bridge, without external LLM."""
import json
import threading
import time
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from qa_core.api import route_qa
from qa_core.api.auth import AuthContext, get_auth_context


def make_app(monkeypatch, usecase):
    app = FastAPI()
    app.include_router(route_qa.router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext("sse-user", ["ops"], "api_key")
    monkeypatch.setattr(route_qa, "get_usecase", lambda: usecase)
    monkeypatch.setattr(route_qa, "_enforce_rate_limit", Mock())
    monkeypatch.setattr(route_qa, "audit_access", Mock())
    monkeypatch.setattr(route_qa, "_sse_slots", threading.BoundedSemaphore(1))
    return app


def frames(body):
    result = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        result.append((lines[0].removeprefix("event: "),
                       json.loads(lines[1].removeprefix("data: "))))
    return result


def test_sse_maps_existing_events_and_only_done_is_final(monkeypatch):
    uc = Mock()
    uc.ask_stream.return_value = iter([
        {"event": "start", "data": {"kb_version": "v1"}},
        {"event": "stage", "data": {"step": "retrieve", "status": "done"}},
        {"event": "delta", "data": {"text": "草稿"}},
        {"event": "sources", "data": {"sources": [{"source": "a.md"}]}},
        {"event": "end", "data": {"status": "ANSWERED", "answer": "最终答案"}}])
    with TestClient(make_app(monkeypatch, uc)) as http:
        response = http.post("/qa/sse", json={"query": "问题", "session_id": "shared"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-store"
    got = frames(response.text)
    assert [name for name, _ in got] == ["meta", "stage", "token", "sources", "done"]
    assert got[0][1]["token_mode"] == "provisional_until_done"
    assert got[-1][1]["answer"] == "最终答案"
    assert uc.ask_stream.call_args.kwargs["session_id"] != "shared"


def test_sse_auth_and_capacity_fail_before_model(monkeypatch):
    uc = Mock()
    app = make_app(monkeypatch, uc)
    app.dependency_overrides[get_auth_context] = lambda: (_ for _ in ()).throw(HTTPException(401))
    with TestClient(app) as http:
        assert http.post("/qa/sse", json={"query": "问题"}).status_code == 401
    uc.ask_stream.assert_not_called()
    app.dependency_overrides[get_auth_context] = lambda: AuthContext("u", ["ops"], "api_key")
    assert route_qa._sse_slots.acquire(blocking=False)
    try:
        with TestClient(app) as http:
            assert http.post("/qa/sse", json={"query": "问题"}).status_code == 429
    finally:
        route_qa._sse_slots.release()
    uc.ask_stream.assert_not_called()


def test_sse_timeout_stops_new_work_and_keeps_slot_until_worker_exits(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    def slow(*args, **kwargs):
        yield {"event": "start", "data": {"kb_version": "v1"}}
        started.set()
        release.wait(2)
        yield {"event": "delta", "data": {"text": "late"}}
    uc = Mock(ask_stream=slow)
    app = make_app(monkeypatch, uc)
    monkeypatch.setattr(route_qa, "_SSE_TOTAL_TIMEOUT_S", .05)
    with TestClient(app) as http:
        response = http.post("/qa/sse", json={"query": "问题"})
    assert started.is_set()
    assert [name for name, _ in frames(response.text)] == ["meta", "error"]
    assert not route_qa._sse_slots.acquire(blocking=False)
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if route_qa._sse_slots.acquire(blocking=False):
            route_qa._sse_slots.release()
            break
        time.sleep(.01)
    else:
        raise AssertionError("worker did not release its slot")


def test_sse_backend_exception_is_sanitized(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret backend path")
        yield
    uc = Mock(ask_stream=broken)
    with TestClient(make_app(monkeypatch, uc)) as http:
        response = http.post("/qa/sse", json={"query": "问题"})
    assert [name for name, _ in frames(response.text)] == ["error"]
    assert "secret" not in response.text


def test_sse_staged_preview_is_gated_before_model_and_pins_version(monkeypatch):
    from qa_core.api import route_evidence

    uc = Mock()
    uc.ask_stream.return_value = iter([
        {"event": "start", "data": {"kb_version": "v4-protocol-dev-r1"}},
        {"event": "end", "data": {"status": "ANSWERED", "answer": "ok"}}])

    def gate(version, ctx):
        if "admin" not in ctx.roles:
            raise HTTPException(404, "not found")
        return version

    monkeypatch.setattr(route_evidence, "resolve_version", gate)
    app = make_app(monkeypatch, uc)
    with TestClient(app) as http:
        denied = http.post("/qa/sse", json={"query": "q", "kb_version": "v4-protocol-dev-r1"})
        assert denied.status_code == 404
        uc.ask_stream.assert_not_called()
        app.dependency_overrides[get_auth_context] = lambda: AuthContext("admin", ["admin"], "api_key")
        allowed = http.post("/qa/sse", json={"query": "q", "kb_version": "v4-protocol-dev-r1"})
    assert allowed.status_code == 200
    assert uc.ask_stream.call_args.kwargs["kb_version"] == "v4-protocol-dev-r1"
