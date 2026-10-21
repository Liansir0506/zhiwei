"""环境隔离校验单测。

`validate_isolated_environment` 是防止测试污染开发数据/存储的最后一道闸门：
`tests/conftest.py` 在 `APP_ENV=test` 时依赖它决定是否解除有状态测试的跳过。
只校验数据库/集合/Redis 是不够的——`test_ingest_worker_e2e.py` 会真实写入
`settings.data_dir/raw` 并删除文件，目录没隔离同样会污染开发数据。
"""
from __future__ import annotations

from qa_core.config.environment import validate_isolated_environment


class FakeSettings:
    """只带校验所需字段的替身，避免依赖真实环境变量。"""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _settings(**overrides) -> FakeSettings:
    base = dict(
        app_env="test",
        mysql_dsn="mysql+pymysql://rag:rag_pass@127.0.0.1:3306/rag_kb",
        doc_collection="doc_collection",
        faq_collection="faq_collection",
        redis_url="redis://127.0.0.1:6379/0",
        data_dir="F:/heima/myproject/data",
        reports_dir="F:/heima/myproject/reports",
    )
    base.update(overrides)
    return FakeSettings(**base)


def _isolated_settings(**overrides) -> FakeSettings:
    base = dict(
        app_env="test",
        mysql_dsn="mysql+pymysql://rag:rag_pass@127.0.0.1:3306/rag_kb_test",
        doc_collection="test_doc_collection",
        faq_collection="test_faq_collection",
        redis_url="redis://127.0.0.1:6379/1",
        data_dir="F:/heima/myproject/.runtime/test/data",
        reports_dir="F:/heima/myproject/.runtime/test/reports",
    )
    base.update(overrides)
    return FakeSettings(**base)


class TestIsolationChecks:
    def test_dev_config_reports_storage_and_dirs(self):
        problems = validate_isolated_environment(_settings(), "test")
        assert len(problems) == 6
        joined = " | ".join(problems)
        for expected in ("MySQL", "DOC_COLLECTION", "FAQ_COLLECTION", "Redis",
                         "DATA_DIR", "REPORTS_DIR"):
            assert expected in joined

    def test_storage_isolated_but_dirs_not_is_blocked(self):
        """本步修复的场景：库/集合/Redis 都切到 test，但文件目录仍指向开发。"""
        problems = validate_isolated_environment(
            _isolated_settings(data_dir="F:/heima/myproject/data",
                               reports_dir="F:/heima/myproject/reports"), "test")
        assert len(problems) == 2
        joined = " | ".join(problems)
        assert "DATA_DIR" in joined and "REPORTS_DIR" in joined

    def test_fully_isolated_passes(self):
        assert validate_isolated_environment(_isolated_settings(), "test") == []

    def test_app_env_mismatch_detected(self):
        problems = validate_isolated_environment(
            _isolated_settings(app_env="dev"), "test")
        assert any("APP_ENV" in p for p in problems)

    def test_backslash_paths_are_normalized(self):
        """Windows 路径用反斜杠时也应能识别 'test' 段。"""
        problems = validate_isolated_environment(
            _isolated_settings(data_dir="F:\\heima\\myproject\\.runtime\\test\\data",
                               reports_dir="F:\\heima\\myproject\\.runtime\\test\\reports"),
            "test")
        assert problems == []
