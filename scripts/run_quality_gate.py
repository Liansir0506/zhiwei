#!/usr/bin/env python
"""执行质量门禁并落报告；失败退出码 2。

用法：python scripts/run_quality_gate.py --kb-version v1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.settings import get_settings
from qa_core.quality.gate import run_gate, save_report
from qa_core.storage.kb_dao import KbDao


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-version", required=True)
    args = parser.parse_args()

    settings = get_settings()
    result = run_gate(settings, args.kb_version)
    report_path = save_report(result, args.kb_version, settings.reports_dir)

    dao = KbDao(settings.mysql_dsn)
    dao.set_quality_report(args.kb_version, report_path)

    print(json.dumps({"kb_version": args.kb_version, "passed": result.passed,
                      "report": report_path,
                      "checks": [{k: c[k] for k in ("check", "passed")} for c in result.checks]},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.passed else 2)


if __name__ == "__main__":
    main()
