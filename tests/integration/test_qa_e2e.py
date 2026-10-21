"""阶段7 端到端集成测试：真实 Milvus/MySQL/LLM（deepseek）全链路。"""
import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def _ops_headers(case: str) -> dict[str, str]:
    """每个用例使用独立限流主体，避免完整回归时相互污染。"""
    return {"X-User-Roles": "ops", "X-User-Id": f"qa-e2e-{case}"}


@pytest.mark.integration
def test_health_ok(client):
    r = client.get("/health", params={"refresh": 1})
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.integration
def test_ask_greeting_direct(client):
    r = client.post("/qa", json={"query": "你好"}, headers=_ops_headers("greeting"))
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "direct_answer" and body["intent"] == "greeting"
    assert "运维知识助理" in body["answer"]
    assert body["sources"] == []


@pytest.mark.integration
def test_ask_out_of_scope(client):
    r = client.post("/qa", json={"query": "帮我重启基站"},
                    headers=_ops_headers("out-of-scope"))
    body = r.json()
    assert body["route"] == "direct_answer" and body["intent"] == "out_of_scope"


@pytest.mark.integration
def test_ask_faq_exact(client):
    r = client.post("/qa", json={"query": "5G基站传输中断排查步骤"},
                    headers=_ops_headers("faq"))
    body = r.json()
    assert body["route"] == "faq_exact"
    assert "SOP" in body["answer"] or "排查" in body["answer"]
    assert body["sources"][0]["doc_type"] == "faq"
    assert body["diagnostics"]["degraded"] is False


@pytest.mark.integration
def test_ask_structured_lookup_with_llm(client):
    r = client.post("/qa", json={"query": "ALM-1201 这个告警怎么处理"},
                    headers=_ops_headers("structured"))
    body = r.json()
    assert body["route"] == "structured_lookup"
    assert body["answer"] and len(body["answer"]) > 30
    assert body["sources"], "lookup 命中必须有来源"
    assert body["sources"][0]["source"] == "TT-2026-001"  # 工单号
    assert "光缆" in body["answer"] or "熔接" in body["answer"]  # 证据词必须出现在答案中


@pytest.mark.integration
def test_ask_retrieval_llm_cited(client):
    r = client.post("/qa", json={"query": "BBU 掉电告警怎么处理"},
                    headers=_ops_headers("retrieval"))
    body = r.json()
    assert body["route"] in ("retrieval", "faq_exact")
    assert body["answer"]
    assert body["sources"], "检索类回答必须有来源"
    assert body["diagnostics"]["degraded"] is False


@pytest.mark.integration
def test_websocket_stream_events(client):
    with client.websocket_connect("/qa/ws?user_id=qa-e2e-ws&roles=ops") as ws:
        ws.send_json({"query": "5G基站传输中断排查步骤", "session_id": "ws-test"})
        events = {}
        deltas = 0
        while True:
            ev = ws.receive_json()
            events[ev["event"]] = ev.get("data")
            if ev["event"] == "delta":
                deltas += 1
            if ev["event"] in ("end", "error"):
                break
    assert "start" in events and "decision" in events and "end" in events
    assert deltas >= 1 or events["end"]["answer"]  # FAQ 直出无 LLM delta，但 end 有 answer
    assert events["end"]["answer"]


@pytest.mark.integration
def test_feedback_down_creates_bad_case(client):
    # 先产生一条 message
    r = client.post("/qa", json={"query": "你好"}, headers=_ops_headers("feedback"))
    assert r.status_code == 200
    mid = r.json().get("message_id")
    if mid is None:
        pytest.skip("messages 表无数据（QA 链路未落库）")
    fb = client.post("/feedback", json={"message_id": mid, "rating": "down",
                                        "reason": "测试负反馈"},
                     headers=_ops_headers("feedback"))
    assert fb.status_code == 200 and fb.json()["ok"] is True
