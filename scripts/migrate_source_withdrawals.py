"""Add withdrawal ledger only; do not retire or delete any existing source."""
import argparse
from pathlib import Path
import sys
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_core.config.settings import get_settings


def apply_migration(dsn):
    engine = sa.create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql((Path(__file__).resolve().parents[1] / "docker/mysql/init/06_source_withdrawals.sql").read_text(encoding="utf-8"))
    finally:
        engine.dispose()
    return {"migration": "006_source_withdrawals", "withdrawn": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true")
    if not parser.parse_args().yes:
        raise SystemExit("确认目标数据库后显式添加 --yes")
    s = get_settings()
    print({"environment": s.app_env, **apply_migration(s.mysql_dsn)})
