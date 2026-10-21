#!/usr/bin/env python
"""幂等创建第5步 ingest_jobs；只新增表，不修改现有知识数据。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings
from scripts.migrate_domain_model import split_sql_statements

SQL_PATH = Path(__file__).resolve().parents[1] / "docker" / "mysql" / "init" / "04_ingest_jobs.sql"


def apply_migration(dsn: str) -> dict:
    engine = sa.create_engine(dsn, pool_pre_ping=True)
    with engine.begin() as conn:
        for statement in split_sql_statements(SQL_PATH.read_text(encoding="utf-8")):
            conn.exec_driver_sql(statement)
        exists = conn.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='ingest_jobs'")).scalar()
    return {"version": "004_ingest_jobs", "recorded": True, "ingest_jobs": bool(exists)}


def main() -> None:
    parser = argparse.ArgumentParser(description="创建轻量知识入库任务表")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("未执行：确认目标数据库后显式添加 --yes")
    settings = get_settings()
    print({"environment": settings.app_env, **apply_migration(settings.mysql_dsn)})


if __name__ == "__main__":
    main()
