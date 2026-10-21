"""Safety policy for tests that write to external services."""
from __future__ import annotations

from pathlib import Path

import pytest

from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings


LIVE_TEST_DIRS = {"component", "integration", "smoke"}


def _is_stateful(item: pytest.Item) -> bool:
    return bool(set(Path(str(item.path)).parts) & LIVE_TEST_DIRS)


def pytest_report_header(config: pytest.Config) -> list[str]:
    """在报告头部暴露"设了 APP_ENV=test 但实际没隔离"这种配置错误。

    只判断 APP_ENV 是不够的：如果同时还在加载开发环境的 .env，测试会连到
    rag_kb / Redis DB 0 / doc_collection，而下面的 modifyitems 又会解除跳过，
    结果是直接污染开发知识库。这里把校验结果显式打在头部，避免静默放行。
    （不用 pytest_configure + get_terminal_writer：该阶段 terminalreporter 尚未创建。）
    """
    settings = get_settings()
    if not settings.is_test:
        return []
    problems = validate_isolated_environment(settings, "test")
    if not problems:
        return ["环境隔离校验：APP_ENV=test 校验通过"]
    return ["环境隔离校验：APP_ENV=test 未通过，有状态测试保持跳过"] + [
        "   - %s" % problem for problem in problems
    ]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip stateful suites unless a *verified* isolated test profile is active.

    解除跳过的条件从"APP_ENV=test"收紧为"APP_ENV=test 且通过隔离校验"，
    校验项与 scripts 里维护命令共用 environment.validate_isolated_environment：
    MySQL 库名含 test、Milvus 集合以 test_ 开头、Redis 不使用 DB 0。
    校验不通过时保持跳过，并把具体违规项写进跳过原因，便于定位配置问题。
    """
    settings = get_settings()
    if not settings.is_test:
        marker = pytest.mark.skip(
            reason="stateful suite requires APP_ENV=test (use the isolated test profile)",
        )
        for item in items:
            if _is_stateful(item):
                item.add_marker(marker)
        return

    problems = validate_isolated_environment(settings, "test")
    if problems:
        marker = pytest.mark.skip(
            reason="APP_ENV=test 未通过隔离校验，保持跳过以防污染："
                   + "; ".join(problems),
        )
        for item in items:
            if _is_stateful(item):
                item.add_marker(marker)
