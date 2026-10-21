"""阶段2/3 纯逻辑测试：配置默认值与 /health 响应结构。不打任何真实服务。"""
import pytest

from qa_core.api.route_health import build_health_payload
from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import QUERY_MAX_CHARS, Settings


@pytest.mark.unit
def test_settings_defaults(monkeypatch):
    # 完整回归在 APP_ENV=test 下执行；默认值测试必须显式隔离进程环境。
    for key in ("APP_ENV", "DOC_COLLECTION", "FAQ_COLLECTION"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)  # 不读 .env，验证代码内默认值
    assert s.app_env == "dev"
    assert s.doc_collection == "doc_collection"
    assert s.faq_collection == "faq_collection"
    assert s.scenario == "telecom_ops"
    assert s.dense_ef == 64
    assert s.top_n_final == 5
    assert s.faq_direct > s.faq_gap >= 0
    assert s.rerank_min < 1.0


@pytest.mark.unit
def test_isolated_environment_flags():
    test = Settings(_env_file=None, app_env="test")
    demo = Settings(_env_file=None, app_env="demo")
    assert test.is_test and not test.is_demo
    assert demo.is_demo and not demo.is_test


@pytest.mark.unit
def test_isolated_environment_validation():
    isolated = Settings(
        _env_file=None,
        app_env="test",
        mysql_dsn="mysql+pymysql://u:p@localhost/rag_kb_test",
        redis_url="redis://localhost:6379/15",
        doc_collection="test_doc_collection",
        faq_collection="test_faq_collection",
        # 文件目录同样要隔离：test_ingest_worker_e2e 会真实写入 data_dir/raw 并删除文件，
        # 只隔离库/集合/Redis 而目录仍指向开发环境，同样会污染开发数据。
        data_dir="/srv/rag/.runtime/test/data",
        reports_dir="/srv/rag/.runtime/test/reports",
    )
    assert validate_isolated_environment(isolated, "test") == []
    unsafe = isolated.model_copy(update={
        "app_env": "dev",
        "mysql_dsn": "mysql+pymysql://u:p@localhost/rag_kb",
        "redis_url": "redis://localhost:6379/0",
        "doc_collection": "doc_collection",
        "faq_collection": "faq_collection",
        "data_dir": "/srv/rag/data",
        "reports_dir": "/srv/rag/reports",
    })
    problems = validate_isolated_environment(unsafe, "test")
    assert problems
    # 六个维度都应被检出，避免只修了存储、漏了目录
    joined = " | ".join(problems)
    for expected in ("APP_ENV", "MySQL", "DOC_COLLECTION", "FAQ_COLLECTION", "Redis",
                     "DATA_DIR", "REPORTS_DIR"):
        assert expected in joined


@pytest.mark.unit
def test_health_payload_all_not_checked_is_degraded():
    payload = build_health_payload(None)
    assert payload["status"] == "degraded"
    assert payload["environment"] in {"dev", "test", "demo", "prod"}
    assert set(payload["components"]) == {"mysql", "redis", "milvus", "models"}


@pytest.mark.unit
def test_health_payload_all_ok():
    ok = {k: "ok" for k in ("mysql", "redis", "milvus", "models")}
    payload = build_health_payload(ok)
    assert payload["status"] == "ok"


@pytest.mark.unit
def test_health_payload_milvus_version_still_ok():
    status = {"mysql": "ok", "redis": "ok", "milvus": "ok (v2.5.4)", "models": "ok"}
    payload = build_health_payload(status)
    assert payload["status"] == "ok"


@pytest.mark.unit
def test_health_payload_partial_error_is_degraded():
    status = {"mysql": "ok", "redis": "ok", "milvus": "error: timeout", "models": "ok"}
    payload = build_health_payload(status)
    assert payload["status"] == "degraded"


@pytest.mark.unit
@pytest.mark.parametrize("env,enabled,auth,expected", [
    ("test", True, True, True), ("test", False, True, False),
    ("test", True, False, False), ("dev", True, True, False),
    ("demo", True, True, False), ("prod", True, True, False),
])
def test_health_agent_capability_matches_route_gate(monkeypatch, env, enabled, auth, expected):
    settings = Settings(_env_file=None, app_env=env, agent_enabled=enabled, auth_required=auth)
    monkeypatch.setattr("qa_core.api.route_health.get_settings", lambda: settings)
    assert build_health_payload()["agent_enabled"] is expected


@pytest.mark.unit
def test_query_limit_constant():
    assert QUERY_MAX_CHARS == 500
