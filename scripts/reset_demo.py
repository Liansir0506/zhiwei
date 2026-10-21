#!/usr/bin/env python
"""Safely clean or rebuild the isolated demo environment.

Examples:
  python scripts/reset_demo.py --yes
  python scripts/reset_demo.py --yes --full --kb-version demo-v1

The command refuses to run unless APP_ENV, MySQL, Redis and both Milvus
collection names all satisfy the demo isolation policy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.config.environment import validate_isolated_environment
from qa_core.config.settings import get_settings
from qa_core.storage.kb_dao import KbDao
from qa_core.storage.milvus_client import get_milvus_client
from scripts.rebuild_kb import delete_file_chunks, guess_doc_type

TEST_FILE_PREFIXES = ("faq_matrix_test", "test_")


def _is_test_file(name: str) -> bool:
    return name.lower().startswith(TEST_FILE_PREFIXES)


def _clean_test_artifacts(settings) -> dict:
    """Remove only well-known automated-test artifacts from demo."""
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    dao = KbDao(settings.mysql_dsn)
    client = get_milvus_client(settings.milvus_uri)
    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT DISTINCT filename, kb_version FROM ingest_records "
            "WHERE LOWER(filename) LIKE 'faq_matrix_test%' "
            "OR LOWER(filename) LIKE 'test\\_%' ESCAPE '\\\\'"
        )).all()

    deleted_chunks = 0
    deleted_records = 0
    for filename, kb_version in rows:
        result = delete_file_chunks(client, dao, filename, kb_version)
        deleted_chunks += result["chunks_deleted"]
        deleted_records += result["records_deleted"]

    with engine.begin() as conn:
        conn.execute(sa.text(
            "DELETE FROM feedback WHERE message_id IN "
            "(SELECT message_id FROM messages WHERE session_id LIKE 'matrix-%' "
            "OR session_id LIKE 'test-%')"))
        deleted_messages = conn.execute(sa.text(
            "DELETE FROM messages WHERE session_id LIKE 'matrix-%' OR session_id LIKE 'test-%'"
        )).rowcount
        deleted_sessions = conn.execute(sa.text(
            "DELETE FROM sessions WHERE session_id LIKE 'matrix-%' OR session_id LIKE 'test-%'"
        )).rowcount
        deleted_keys = conn.execute(sa.text(
            "DELETE FROM api_keys WHERE user_id LIKE 'matrix-%' OR user_id LIKE 'test-%'"
        )).rowcount
        conn.execute(sa.text(
            "DELETE FROM bad_cases WHERE query_text LIKE '[test]%' OR cause='automated_test'"))

    removed_files = 0
    raw_dir = Path(settings.data_dir) / "raw"
    if raw_dir.is_dir():
        for path in raw_dir.iterdir():
            if path.is_file() and _is_test_file(path.name):
                path.unlink()
                removed_files += 1

    return {
        "chunks": deleted_chunks,
        "ingest_records": deleted_records,
        "messages": deleted_messages,
        "sessions": deleted_sessions,
        "api_keys": deleted_keys,
        "source_files": removed_files,
    }


def _full_reset(settings, kb_version: str) -> None:
    """Rebuild demo stores from the remaining immutable/raw demo corpus."""
    raw_dir = Path(settings.data_dir) / "raw"
    if not raw_dir.is_dir():
        raise SystemExit(f"demo 原始语料目录不存在：{raw_dir}")
    corpus = []
    for path in raw_dir.iterdir():
        if not path.is_file() or path.name == "meta.json" or _is_test_file(path.name):
            continue
        try:
            guess_doc_type(path.name)
        except ValueError:
            continue
        corpus.append(path)
    if not corpus:
        raise SystemExit(f"{raw_dir} 中没有可重建的 demo 语料，已中止且未清空存储")
    model_path = Path(settings.embedding_model_path)
    if not model_path.is_dir():
        raise SystemExit(f"Embedding 模型目录不存在：{model_path}，已中止且未清空存储")

    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    with engine.begin() as conn:
        for table in ("feedback", "messages", "sessions", "bad_cases", "eval_runs",
                      "ingest_records", "kb_versions", "api_keys", "audit_log"):
            conn.execute(sa.text(f"DELETE FROM {table}"))

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "scripts" / "init_collections.py"),
                    "--recreate"], check=True)
    subprocess.run([sys.executable, str(root / "scripts" / "rebuild_kb.py"),
                    "--mode", "full", "--kb-version", kb_version,
                    "--raw-dir", str(raw_dir)], check=True)
    subprocess.run([sys.executable, str(root / "scripts" / "run_quality_gate.py"),
                    "--kb-version", kb_version], check=True)

    with engine.begin() as conn:
        conn.execute(sa.text(
            "UPDATE kb_versions SET status='ACTIVE', published_at=NOW(), "
            "activated_by='reset_demo' WHERE version=:v"), {"v": kb_version})
        conn.execute(sa.text(
            "INSERT INTO audit_log (action, actor, payload_json) "
            "VALUES ('demo_reset', 'reset_demo', :p)"),
            {"p": json.dumps({"version": kb_version, "corpus_files": len(corpus)})})


def main() -> None:
    parser = argparse.ArgumentParser(description="清理或完整重建隔离的 demo 环境")
    parser.add_argument("--yes", action="store_true", help="确认执行写操作")
    parser.add_argument("--full", action="store_true", help="从 DATA_DIR/raw 完整重建 demo")
    parser.add_argument("--kb-version", default="demo-v1")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("未执行：请检查目标后显式添加 --yes")

    settings = get_settings()
    problems = validate_isolated_environment(settings, "demo")
    if problems:
        raise SystemExit("拒绝执行 demo 重置：\n- " + "\n- ".join(problems))

    cleaned = _clean_test_artifacts(settings)
    if args.full:
        _full_reset(settings, args.kb_version)

    import redis

    redis.Redis.from_url(settings.redis_url).flushdb()
    print(json.dumps({
        "environment": settings.app_env,
        "mode": "full" if args.full else "clean",
        "kb_version": args.kb_version if args.full else None,
        "cleaned": cleaned,
        "redis_cache": "flushed",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
