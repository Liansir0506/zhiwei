"""bad case 审核工具：统计/列出/导出待审核清单，供人工确认后回流入库。

用法：
  python scripts/review_bad_cases.py --list              # 列出 open 的 bad cases
  python scripts/review_bad_cases.py --stats             # 按原因统计
  python scripts/review_bad_cases.py --export out.csv    # 导出待审核 CSV（人工补充答案后走 rebuild 入库）

回流约定：人工在导出的 CSV 中补充 question/answer 两列后另存为
data/raw/faq_badcase_fixed.csv（FAQ 类型），即可随下次 rebuild_kb 入新版本。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from qa_core.config.settings import get_settings


def _engine():
    return sa.create_engine(get_settings().mysql_dsn)


def list_open() -> list[dict]:
    with _engine().begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT id, query_text, cause, created_at FROM bad_cases "
            "WHERE status='open' ORDER BY id")).mappings().all()
    return [dict(r) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--export", metavar="CSV_PATH")
    args = parser.parse_args()

    rows = list_open()
    if args.stats:
        from collections import Counter

        by_cause = Counter(r["cause"] or "未标注" for r in rows)
        for cause, n in by_cause.most_common():
            print(f"{cause}: {n}")
        print(f"open 总数: {len(rows)}")
    elif args.export:
        path = Path(args.export)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "question", "answer(人工补充后删除本列说明)", "cause"])
            for r in rows:
                writer.writerow([r["id"], r["query_text"], "", r["cause"] or ""])
        print(f"已导出 {len(rows)} 条到 {path}；人工补充答案后另存为 data/raw/faq_badcase_fixed.csv，"
              f"文件名以 faq_ 开头即可随 rebuild_kb 入库")
    else:
        for r in rows:
            print(f"[{r['id']}] ({r['cause'] or '未标注'}) {r['query_text'][:60]} @ {r['created_at']}")
        print(f"open 总数: {len(rows)}")


if __name__ == "__main__":
    main()
