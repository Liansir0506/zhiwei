#!/usr/bin/env python
"""检测知识库空文档：ingest_records 有记录但 Milvus 无对应 chunk 的来源。

--clean：删除这些不一致的记录行（Milvus 无数据，记录视为失效残留）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from qa_core.config.settings import get_settings
from qa_core.storage.milvus_client import query_with_retry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="删除空记录行")
    args = parser.parse_args()

    settings = get_settings()
    engine = sa.create_engine(settings.mysql_dsn)
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT kb_version, filename, doc_type, chunk_count FROM ingest_records "
            "ORDER BY kb_version, filename")).mappings().all()

    # 工单类 source=工单号（非文件名），按 (kb_version, doc_type=fault_case) 全量校验
    fault_expected: dict[str, int] = {}
    for r in rows:
        if r["doc_type"] == "fault_case":
            fault_expected[r["kb_version"]] = fault_expected.get(r["kb_version"], 0) + r["chunk_count"]

    grouped: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["doc_type"] == "fault_case":
            continue
        key = (r["kb_version"], r["filename"])
        grouped[key] = grouped.get(key, 0) + r["chunk_count"]

    empty = []
    for (kbv, fname), rec_chunks in sorted(grouped.items()):
        real = 0
        try:
            real += len(query_with_retry(settings.milvus_uri, settings.doc_collection,
                filter=f'source == "{fname}" and kb_version == "{kbv}"', output_fields=["id"], limit=500))
        except Exception:
            real += -1  # 查询失败标记
        try:
            real += len(query_with_retry(settings.milvus_uri, settings.faq_collection,
                filter=f'source == "{fname}" and kb_version == "{kbv}"', output_fields=["id"], limit=500))
        except Exception:
            real += -1
        if real <= 0:
            empty.append((kbv, fname, rec_chunks))
            print(f"[空文档] {kbv} / {fname}（记录 chunks={rec_chunks}，Milvus 实际=0）")

    # 工单校验：记录总数 vs Milvus 实际 fault_case chunk 数
    for kbv, expected in sorted(fault_expected.items()):
        try:
            real = len(query_with_retry(settings.milvus_uri, settings.doc_collection,
                filter=f'doc_type == "fault_case" and kb_version == "{kbv}"',
                output_fields=["id"], limit=500))
        except Exception:
            real = -1
        if real != expected:
            empty.append((kbv, "fault_case(全部工单)", expected))
            print(f"[工单数不一致] {kbv}：记录 {expected}，Milvus 实际 {real}")

    print(f"\n共 {len(grouped)} 个非工单来源 + {len(fault_expected)} 个版本工单集，异常 {len(empty)} 个")
    if args.clean and empty:
        with engine.begin() as conn:
            for kbv, fname, _ in empty:
                n = conn.execute(sa.text(
                    "DELETE FROM ingest_records WHERE kb_version=:v AND filename=:f"),
                    {"v": kbv, "f": fname}).rowcount
                print(f"[清理] {kbv}/{fname} 删除记录 {n} 行")
        print("清理完成")
    elif empty:
        print("（加 --clean 执行清理）")


if __name__ == "__main__":
    main()
