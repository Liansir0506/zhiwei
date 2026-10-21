#!/usr/bin/env python
"""幂等应用第2步企业领域模型，不删除或覆盖现有业务数据。

用法：python scripts/migrate_domain_model.py --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings

MIGRATION_VERSION = "002_enterprise_domain"
SQL_PATH = Path(__file__).resolve().parents[1] / "docker" / "mysql" / "init" / "02_enterprise_domain.sql"


def split_sql_statements(source: str) -> list[str]:
    """切分本项目的纯 DDL 文件；拒绝存储过程/动态 SQL 等非本迁移语法。"""
    clean_lines = []
    for line in source.replace("\r\n", "\n").splitlines():
        if line.lstrip().startswith("--"):
            continue
        clean_lines.append(line)
    return [statement.strip() for statement in "\n".join(clean_lines).split(";")
            if statement.strip()]


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": column}).scalar())


def _index_exists(conn, table: str, index: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema=DATABASE() AND table_name=:t AND index_name=:i"
    ), {"t": table, "i": index}).scalar())


def _ensure_compat_columns(conn) -> list[str]:
    """为旧库补充上下文字段；全部可空或有默认值，原数据保持有效。"""
    changes: list[str] = []
    columns = [
        ("sessions", "tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default' AFTER user_id"),
        ("sessions", "dept_id", "VARCHAR(64) DEFAULT NULL AFTER tenant_id"),
        ("api_keys", "tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default' AFTER user_id"),
        ("api_keys", "dept_id", "VARCHAR(64) DEFAULT NULL AFTER tenant_id"),
        ("ingest_records", "asset_id", "VARCHAR(64) DEFAULT NULL AFTER filename"),
    ]
    for table, column, definition in columns:
        if not _column_exists(conn, table, column):
            conn.exec_driver_sql(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
            changes.append(f"{table}.{column}")
    indexes = [
        ("sessions", "idx_sessions_tenant", "tenant_id, dept_id"),
        ("api_keys", "idx_api_keys_tenant", "tenant_id, dept_id"),
        ("ingest_records", "idx_ingest_asset", "asset_id"),
    ]
    for table, index, columns_sql in indexes:
        if not _index_exists(conn, table, index):
            conn.exec_driver_sql(f"ALTER TABLE `{table}` ADD INDEX `{index}` ({columns_sql})")
            changes.append(f"index:{index}")
    return changes


def apply_migration(dsn: str) -> dict:
    if not SQL_PATH.is_file():
        raise FileNotFoundError(SQL_PATH)
    statements = split_sql_statements(SQL_PATH.read_text(encoding="utf-8"))
    engine = sa.create_engine(dsn, pool_pre_ping=True)
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
        compat_changes = _ensure_compat_columns(conn)
        tables = [r[0] for r in conn.execute(sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name IN "
            "('tenants','departments','users','user_departments','devices','monitoring_metrics','alarms',"
            "'work_orders','change_records','knowledge_assets','knowledge_relations') "
            "ORDER BY table_name"
        )).all()]
        applied = conn.execute(sa.text(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=:v"
        ), {"v": MIGRATION_VERSION}).scalar()
    return {"version": MIGRATION_VERSION, "tables": tables,
            "compat_changes": compat_changes, "recorded": bool(applied)}


def main() -> None:
    parser = argparse.ArgumentParser(description="应用企业统一领域模型迁移")
    parser.add_argument("--yes", action="store_true", help="确认执行非破坏性 DDL")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("未执行：确认目标数据库后显式添加 --yes")
    settings = get_settings()
    result = apply_migration(settings.mysql_dsn)
    print({"environment": settings.app_env, **result})


if __name__ == "__main__":
    main()
