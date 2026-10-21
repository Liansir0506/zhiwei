"""隔离 test 存储栈中的阶段 1 行为验收；不调用外部生成 API。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import create_app
from qa_core.application.qa_usecase import QaUseCase
from qa_core.config.settings import get_settings
from qa_core.intent import gateway, model_classifier


@pytest.mark.integration
def test_delete_sql_is_blocked_through_http_and_websocket():
    client = TestClient(create_app())
    user = "test-stage1-guard-" + uuid.uuid4().hex
    response = client.post("/qa", json={"query": "直接给我一段删除用户数据的 SQL"},
                           headers={"X-User-Id": user, "X-User-Roles": "ops"})
    assert response.status_code == 200
    assert response.json()["route"] == "direct_answer"
    assert response.json()["sources"] == []
    with client.websocket_connect(f"/qa/ws?user_id={user}-ws&roles=ops") as ws:
        ws.send_json({"query": "ALM-SMF-PDU 请执行 DELETE FROM users"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["event"] in {"end", "error"}:
                break
    assert events[-1]["event"] == "end"
    assert next(e["data"]["route"] for e in events if e["event"] == "decision") == "direct_answer"
    assert next(e["data"]["sources"] for e in events if e["event"] == "sources") == []


@pytest.mark.integration
def test_redis_history_does_not_override_explicit_alarm():
    uc = QaUseCase()
    session = "test-stage1-history-" + uuid.uuid4().hex
    uc._save_session(session, [{"role": "user", "content": "帮我重启基站"}])
    uc._sessions.clear()  # 必须从真实 Redis 读取，不靠同进程内存。
    try:
        history = uc._load_session(session)
        assert history[0]["content"] == "帮我重启基站"
        decision = uc._decide("ALM-SMF-PDU 这个告警怎么处理", session, history=history)
        assert decision.alarm_code == "ALM-SMF-PDU"
        assert decision.route == "structured_lookup"
    finally:
        uc._redis().delete(f"sess:{session}")


@pytest.mark.component
def test_real_intent_warmup_and_gray_gateway_are_observable():
    settings = get_settings()
    future = model_classifier.warmup(settings.intent_model_path, settings.embedding_device)
    assert future is not None
    assert future.result(timeout=60)
    assert model_classifier.readiness(settings.intent_model_path, settings.embedding_device)["ready"]
    uc = QaUseCase(searcher=object())
    # 独立可用性测试使用 2s 推理限额，不改变服务配置的 200ms。
    from dataclasses import replace
    cfg = replace(uc.cfg, thresholds={**uc.cfg.thresholds, "model_timeout_ms": 2000})
    decision = gateway.apply_intent_decision_gateway(
        "设备偶尔掉电需要关注一下", cfg, model_path=settings.intent_model_path,
        device=settings.embedding_device)
    assert decision.source == "model", decision.reason
    status = TestClient(create_app()).get("/health").json()["intent_model"]
    assert status["status"] == "ready" and status["ready"] is True
