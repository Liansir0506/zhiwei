"""答辩增强集成测试：限流 429、语义缓存命中。"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.mark.integration
def test_rate_limit_429(client, monkeypatch):
    import time
    import uuid
    from types import SimpleNamespace

    from qa_core.api import rate_limit
    from qa_core.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_qps", 2)
    # 完整 API 含审计/会话数据库写入，在 Windows 上实测每次 >1s，
    # 连续五次并不属于同一 QPS 窗口。固定限流模块的窗口，不改全局时钟。
    window = int(time.time())
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: window))
    user = "test-stage1-rate-" + uuid.uuid4().hex
    rc = rate_limit._redis()
    assert rc is not None, "真实 Redis 限流通道不可用"
    key = f"rl:{user}:{window}"
    try:
        # 真实计数允许两次；第三次通过真实 HTTP 入口验证 429。
        rate_limit.check_rate_limit(user, 2)
        rate_limit.check_rate_limit(user, 2)
        assert int(rc.get(key)) == 2
        rc.expire(key, 30)  # 只延长本用例计数键，避免调度抖动使测试键过期。
        response = client.post("/qa", json={"query": "你好"},
                               headers={"X-User-Id": user})
        assert response.status_code == 429, response.text
        assert int(rc.get(key)) == 3
    finally:
        rc.delete(key)


@pytest.mark.integration
def test_semantic_cache_hit(client):
    """同义问题第二次应命中语义缓存（retrieval 类、LLM 生成路径）。"""
    from qa_core.config.settings import get_settings

    if not get_settings().llm_api_key:
        pytest.skip("LLM_API_KEY 未配置")
    q1 = "光缆中断后为什么会出现大量级联告警，网管怎么应对"
    # 清理语义缓存历史，保证命中条目就是首问写入的
    import redis as redis_lib
    rc = redis_lib.Redis.from_url(get_settings().redis_url)
    for k in rc.scan_iter(match="sem:*"):
        rc.delete(k)
    r1 = client.post("/qa", json={"query": q1}, headers={"X-User-Id": "sc-user"})
    assert r1.status_code == 200
    body1 = r1.json()
    if body1["route"] != "retrieval" or body1["diagnostics"]["degraded"]:
        pytest.skip(f"首问未走完整检索生成路径（route={body1['route']}）")
    # 换说法的同一问题：应命中首问写入的语义缓存
    r2 = client.post("/qa", json={"query": "光缆被挖断后网管出现上万条告警应该如何应对处理"},
                     headers={"X-User-Id": "sc-user"})
    body2 = r2.json()
    hit = body2["diagnostics"]["retrieval"].get("semantic_cache_hit")
    assert hit, f"语义缓存未命中：{body2['diagnostics']}"
    assert body2["answer"] == body1["answer"], "命中的应是首问写入的答案"
    assert body2["sources"], "语义缓存命中也要返回来源"
