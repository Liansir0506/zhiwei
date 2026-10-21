#!/usr/bin/env python
"""幂等应用第4步安全治理迁移；只新增字段、索引和审计表。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings
from scripts.migrate_domain_model import split_sql_statements

SQL_PATH = Path(__file__).resolve().parents[1] / "docker" / "mysql" / "init" / "03_security_governance.sql"


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=:table AND column_name=:column"),
        {"table": table, "column": column}).scalar())


def apply_migration(dsn: str) -> dict:
    engine = sa.create_engine(dsn, pool_pre_ping=True)
    statements = split_sql_statements(SQL_PATH.read_text(encoding="utf-8"))
    changes: list[str] = []
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
        for column, definition in (
            ("device_scope", "JSON DEFAULT NULL AFTER dept_id"),
            ("security_levels", "JSON DEFAULT NULL AFTER device_scope"),
        ):
            if not _column_exists(conn, "api_keys", column):
                conn.exec_driver_sql(f"ALTER TABLE api_keys ADD COLUMN `{column}` {definition}")
                changes.append(f"api_keys.{column}")
        exists = conn.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='permission_audit'"
        )).scalar()
    return {"version": "003_security_governance", "recorded": True,
            "permission_audit": bool(exists), "compat_changes": changes}


def main() -> None:
    parser = argparse.ArgumentParser(description="应用安全治理迁移")
    parser.add_argument("--yes", action="store_true", help="确认执行非破坏性 DDL")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("未执行：确认目标数据库后显式添加 --yes")
    settings = get_settings()
    print({"environment": settings.app_env, **apply_migration(settings.mysql_dsn)})


if __name__ == "__main__":
    main()
