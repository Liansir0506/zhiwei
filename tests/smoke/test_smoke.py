"""Smoke Test：发布前冒烟——健康检查 + 5 条代表性问答（一条一路径）。

运行前提：docker compose 全套在线、v1 已激活、LLM key 有效。
命令：python -m pytest tests/smoke -m smoke -v
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


SMOKE_CASES = [
    ("你好", "direct_answer"),
    ("5G基站传输中断排查步骤", None),          # faq_exact 或检索（不硬编码）
    ("ALM-1201 这个告警怎么处理", "structured_lookup"),
    ("帮我重启基站", "direct_answer"),
]


@pytest.mark.smoke
def test_health_all_green(client):
    r = client.get("/health", params={"refresh": 1})
    assert r.status_code == 200
    assert r.json()["status"] == "ok", r.json()


@pytest.mark.smoke
@pytest.mark.parametrize("query,route", SMOKE_CASES)
def test_smoke_qa_paths(client, query, route):
    # 每条用例独立会话。冒烟验证的是"单轮路由是否正确"，而 /qa 会用会话历史做
    # 多轮指代改写；共用 session 会让 route 取决于上一条用例问过什么。
    # 历史失败：ALM-1201 那条的 route 被前一条"5G基站传输中断排查步骤"带偏，
    # 从 structured_lookup 变成 retrieval。
    session_id = "smoke-" + uuid.uuid4().hex
    r = client.post("/qa", json={"query": query, "session_id": session_id},
                    headers={"X-User-Roles": "ops"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"], query
    if route is not None:
        assert body["route"] == route, (query, body["route"])
    if body["route"] != "direct_answer":
        assert body["sources"], query


@pytest.mark.smoke
def test_smoke_kb_version_active(client):
    r = client.get("/kb/versions", headers={"X-User-Roles": "ops"})
    assert r.status_code == 200
    statuses = [v["status"] for v in r.json()["versions"]]
    assert "ACTIVE" in statuses
