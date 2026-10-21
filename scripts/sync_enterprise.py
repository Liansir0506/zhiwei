#!/usr/bin/env python
"""从明确标注的模拟企业服务增量同步统一领域数据。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings
from qa_core.integrations.adapters import SYNC_ORDER, build_adapter_registry
from qa_core.integrations.state import MySQLSyncStateStore
from qa_core.integrations.sync_service import EnterpriseSyncService
from qa_core.storage.domain_dao import DomainDao


def main() -> None:
    parser = argparse.ArgumentParser(description="同步模拟企业系统数据（写数据库）")
    parser.add_argument("--sources", nargs="+", choices=SYNC_ORDER, default=list(SYNC_ORDER))
    parser.add_argument("--reset-cursor", action="store_true")
    parser.add_argument("--page-size", type=int, default=None)
    parser.add_argument("--yes", action="store_true", help="确认向当前环境写入 mock-* 数据")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("未执行：该命令会写入数据库，请确认环境后添加 --yes")
    settings = get_settings()
    service = EnterpriseSyncService(
        build_adapter_registry(settings), DomainDao(settings.mysql_dsn),
        MySQLSyncStateStore(settings.mysql_dsn))
    results = service.sync_all(sources=args.sources,
                               page_size=args.page_size or settings.adapter_page_size,
                               reset_cursor=args.reset_cursor)
    print(json.dumps({"environment": settings.app_env, "simulation": True,
                      "results": results}, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
