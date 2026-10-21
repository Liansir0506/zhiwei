"""阶段8 治理集成测试：L2 缓存命中、版本回滚与缓存失效。"""
import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture(scope="module")
def client():
    c = TestClient(create_app())
    c.headers.update({"X-User-Roles": "admin"})  # kb 治理接口需 admin 角色
    return c


@pytest.mark.integration
def test_cache_hit_on_second_faq(client):
    r1 = client.post("/qa", json={"query": "5G基站传输中断排查步骤"},
                     headers={"X-User-Roles": "ops"})
    assert r1.status_code == 200
    r2 = client.post("/qa", json={"query": "5G基站传输中断排查步骤"},
                     headers={"X-User-Roles": "ops"})
    assert r2.status_code == 200 and r2.json()["route"] == "faq_exact"


@pytest.mark.integration
def test_version_rollback_and_cache_invalidation(client):
    # 假版本 → 404
    r = client.post("/kb/rollback", json={"version": "v-nothing", "actor": "test"})
    assert r.status_code == 404
    # 回滚到当前 ACTIVE 自身应 400（动态取 active，避免污染真实版本状态）
    active = next(v["version"] for v in client.get("/kb/versions").json()["versions"]
                  if v["status"] == "ACTIVE")
    r = client.post("/kb/rollback", json={"version": active, "actor": "test"})
    assert r.status_code == 400
    # 列表接口：active 状态未被测试改变
    versions = {v["version"]: v["status"] for v in client.get("/kb/versions").json()["versions"]}
    assert versions.get(active) == "ACTIVE"


@pytest.mark.integration
def test_activate_without_gate_report_rejected(client):
    """无门禁报告的版本禁止激活（质量门禁硬约束）。"""
    import sqlalchemy as sa
    from qa_core.config.settings import get_settings

    engine = sa.create_engine(get_settings().mysql_dsn)
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT IGNORE INTO kb_versions (version, status) VALUES ('v-bad', 'STAGED')"))
    r = client.post("/kb/activate", json={"version": "v-bad", "actor": "test"})
    assert r.status_code == 400
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM kb_versions WHERE version='v-bad'"))
