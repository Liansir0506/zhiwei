"""Environment isolation checks shared by test/demo maintenance commands."""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy.engine import make_url


def validate_isolated_environment(settings, expected: str) -> list[str]:
    """Return safety violations for an isolated test or demo environment.

    The database and Milvus collections are the durable stores most likely to
    damage a demo when a maintenance command points at the wrong profile.
    """
    problems: list[str] = []
    if settings.app_env != expected:
        problems.append(f"APP_ENV={settings.app_env!r}, expected {expected!r}")

    try:
        database = make_url(settings.mysql_dsn).database or ""
    except Exception:  # noqa: BLE001
        database = ""
    if expected not in database.lower():
        problems.append(f"MySQL database {database!r} does not contain {expected!r}")

    for label, name in (("DOC_COLLECTION", settings.doc_collection),
                        ("FAQ_COLLECTION", settings.faq_collection)):
        if not name.lower().startswith(f"{expected}_"):
            problems.append(f"{label}={name!r} must start with {expected!r}_")

    try:
        redis_db = int((urlparse(settings.redis_url).path or "/0").strip("/") or "0")
    except ValueError:
        redis_db = 0
    if redis_db == 0:
        problems.append("isolated profiles must not use Redis DB 0")

    # 文件目录同样要隔离。tests/integration/test_ingest_worker_e2e.py 会真实写入
    # settings.data_dir/raw 并删除文件；如果数据库、Milvus 集合、Redis 都已切到 test，
    # 但目录仍指向开发环境，有状态测试会被放行并污染开发数据目录。
    for label, value in (("DATA_DIR", settings.data_dir),
                         ("REPORTS_DIR", settings.reports_dir)):
        normalized = str(value or "").replace("\\", "/").lower()
        if not normalized:
            problems.append(f"{label} 未配置")
        elif expected not in normalized:
            problems.append(
                f"{label}={value!r} 路径中不含 {expected!r}，可能仍指向开发目录")
    return problems
