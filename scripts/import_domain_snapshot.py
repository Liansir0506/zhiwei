#!/usr/bin/env python
"""校验并幂等导入 CMDB/告警/工单/变更统一快照 JSON。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings
from qa_core.domain.models import EnterpriseSnapshot
from qa_core.storage.domain_dao import DomainDao


def main() -> None:
    parser = argparse.ArgumentParser(description="导入企业领域快照")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--yes", action="store_true", help="校验通过后写入数据库")
    parser.add_argument("--allow-prod", action="store_true", help="显式允许写入 prod")
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"文件不存在：{args.input}")
    try:
        snapshot = EnterpriseSnapshot.model_validate_json(
            args.input.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise SystemExit(f"快照校验失败：\n{exc}") from exc

    summary = {
        name: len(getattr(snapshot, name))
        for name in ("tenants", "departments", "users", "user_departments", "devices",
                     "monitoring_metrics", "alarms", "work_orders", "changes", "knowledge_assets",
                     "knowledge_relations")
    }
    if not args.yes:
        print(json.dumps({"validated": True, "written": False, "counts": summary},
                         ensure_ascii=False, indent=2))
        return
    settings = get_settings()
    if settings.app_env == "prod" and not args.allow_prod:
        raise SystemExit("拒绝写入 prod：复核目标后显式添加 --allow-prod")
    counts = DomainDao(settings.mysql_dsn).upsert_snapshot(snapshot)
    print(json.dumps({"validated": True, "written": True,
                      "environment": settings.app_env, "counts": counts},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
