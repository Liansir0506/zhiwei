#!/usr/bin/env python
"""独立轻量入库 Worker；MySQL 即任务源，不依赖 Kafka/Celery。"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.cache.redis_cache import CacheLayer
from qa_core.config.settings import get_settings
from qa_core.ingest.jobs import IngestJobDao
from qa_core.ingest.write_guard import candidate_write, WriteBusy
from qa_core.storage.kb_dao import KbDao
from scripts.rebuild_kb import IngestCancelled, delete_file_chunks, run_rebuild


def _configure_logging() -> None:
    """Worker 不监听 HTTP，也没有 /health，容器日志是它唯一的可观测入口。

    未配置时 stdout 静默，进度只能靠 MySQL heartbeat 反查，排障成本很高。
    """
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )


LOG = logging.getLogger("ingest.worker")


def _quality_gate(kb_version: str) -> str:
    from qa_core.quality.gate import run_gate, save_report

    settings = get_settings()
    result = run_gate(settings, kb_version)
    report = save_report(result, kb_version, settings.reports_dir)
    KbDao(settings.mysql_dsn).set_quality_report(kb_version, report)
    if not result.passed:
        failed = [c.get("check") for c in result.checks if not c.get("passed")]
        raise RuntimeError(f"quality gate failed: {', '.join(str(v) for v in failed)}")
    return report


def execute_job(job: dict, dao: IngestJobDao, worker_id: str, *, embedder_override=None) -> dict:
    settings = get_settings()
    payload = job.get("payload_json") or {}
    from qa_core.ingest.binding import execution_binding
    if payload.get("strict_metadata") and (payload.get("doc_collection"), payload.get("faq_collection")) != (
        settings.doc_collection, settings.faq_collection
    ):
        raise ValueError("strict candidate job collection binding does not match worker settings")
    if payload.get("execution_binding") != execution_binding(settings, job.get("job_type"), payload):
        raise ValueError("任务缺少配置绑定或与 Worker 不匹配，禁止写入；请重新提交候选任务")
    if job.get("job_type") == "upload" and not payload.get("source_id"):
        raise ValueError("上传任务缺稳定来源 ID，禁止修改候选")
    with candidate_write(job["kb_version"], settings=settings):
        return _execute_job(job, dao, worker_id, embedder_override=embedder_override)


def _execute_job(job: dict, dao: IngestJobDao, worker_id: str, *, embedder_override=None) -> dict:
    settings = get_settings()
    payload = job.get("payload_json") or {}
    if payload.get("strict_metadata"):
        expected = (payload.get("doc_collection"), payload.get("faq_collection"))
        if expected != (settings.doc_collection, settings.faq_collection):
            raise ValueError("strict candidate job collection binding does not match worker settings")
    job_id = job["job_id"]
    short_id = job_id[:8]
    LOG.info("job=%s start type=%s version=%s mode=%s attempt=%s/%s",
             short_id, job["job_type"], job.get("kb_version"), job.get("mode"),
             job.get("attempt_count"), job.get("max_attempts"))

    def cancelled() -> bool:
        return dao.cancel_requested(job_id)

    last_stage = {"value": None}

    def progress(stage: str, done: int, total: int, stats: dict) -> None:
        dao.update_progress(job_id, worker_id, stage=stage, done=done,
                            total=total, stats=stats)
        # 每个文件都会回调一次；只打阶段切换与每 25 个文件的进度，避免刷屏。
        if stage != last_stage["value"]:
            last_stage["value"] = stage
            LOG.info("job=%s stage=%s %s/%s", short_id, stage, done, total)
        elif done == total or done % 25 == 0:
            percent = (100.0 * done / total) if total else 0.0
            LOG.info("job=%s %s/%s (%.1f%%)", short_id, done, total, percent)

    if job["job_type"] == "upload":
        staged = Path(payload["staging_path"])
        filename = Path(payload["filename"]).name
        if not staged.is_file():
            raise FileNotFoundError(f"staged upload missing: {staged}")
        # Candidate copies must never overwrite the original raw corpus.
        import hashlib
        tenant_dir = hashlib.sha256(job.get("tenant_id", "default").encode()).hexdigest()[:16]
        raw_dir = Path(settings.data_dir) / ".candidates" / job["kb_version"] / tenant_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / filename
        progress("preparing", 0, 1, {})
        if cancelled():
            raise IngestCancelled("cancel requested")
        kb_dao = KbDao(settings.mysql_dsn)
        old_records = kb_dao.get_records_by_filename(filename, job["kb_version"])
        if old_records:
            from qa_core.storage.milvus_client import get_milvus_client
            from qa_core.ingest.source_identity import verify_upload_replacement
            verify_upload_replacement(get_milvus_client(settings.milvus_uri), old_records, settings,
                                      job["kb_version"], job.get("tenant_id", "default"), payload["source_id"])
            delete_file_chunks(get_milvus_client(settings.milvus_uri), kb_dao,
                               filename, job["kb_version"])
        shutil.copy2(staged, target)
        stats = run_rebuild(job["kb_version"], "incremental", raw_dir,
                            paths=[target], progress=progress, should_cancel=cancelled,
                            record_locations=True, tokenizer_chunking=True, strict_metadata=True,
                            embedder_override=embedder_override,
                            metadata_override={"tenant_id": job.get("tenant_id", "default"),
                                               "source_system": "manual_upload", "source_id": payload["source_id"],
                                               "source_version": payload.get("sha256") or hashlib.sha256(staged.read_bytes()).hexdigest()})
    else:
        stats = run_rebuild(job["kb_version"], job["mode"], payload.get("raw_dir"),
                            progress=progress, should_cancel=cancelled,
                            strict_metadata=bool(payload.get("strict_metadata", False)),
                            embedder_override=embedder_override,
                            record_locations=bool(payload.get("record_locations", False)),
                            tokenizer_chunking=bool(payload.get("tokenizer_chunking", False)))
        if payload.get("run_quality_gate", True):
            progress("quality_gate", stats.get("files", 0), stats.get("files", 0), stats)
            stats["quality_report"] = _quality_gate(job["kb_version"])
    if stats.get("failed"):
        raise RuntimeError(f"{stats['failed']} file(s) failed; inspect ingest_records")
    CacheLayer(settings.redis_url).invalidate_version(job["kb_version"])
    return stats


def run_slot(slot: int, *, once: bool, poll_seconds: float, lease_seconds: int) -> None:
    settings = get_settings()
    dao = IngestJobDao(settings.mysql_dsn)
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{slot}"
    LOG.info("slot=%d ready worker_id=%s poll=%.1fs lease=%ds",
             slot, worker_id, poll_seconds, lease_seconds)
    recovered = dao.recover_stale(lease_seconds)
    if recovered:
        LOG.warning("slot=%d recovered %d stale job(s) beyond lease=%ds",
                    slot, recovered, lease_seconds)
    while True:
        job = dao.claim_next(worker_id)
        if job is None:
            LOG.debug("slot=%d idle, next poll in %.1fs", slot, poll_seconds)
            if once:
                return
            time.sleep(poll_seconds)
            continue
        job_id = job["job_id"]
        short_id = job_id[:8]
        try:
            stats = execute_job(job, dao, worker_id)
            dao.finish(job_id, worker_id, stats)
            LOG.info("job=%s finished done=%s skipped=%s failed=%s duplicates=%s",
                     short_id, stats.get("done"), stats.get("skipped"),
                     stats.get("failed"), stats.get("duplicates"))
        except IngestCancelled:
            dao.cancelled(job_id, worker_id)
            LOG.warning("job=%s cancelled by request", short_id)
        except WriteBusy:
            dao.defer_busy(job_id, worker_id)
            LOG.info("job=%s waiting for ingestion/publication lock", short_id)
        except Exception as exc:  # noqa: BLE001
            dao.fail(job_id, worker_id, f"{type(exc).__name__}: {exc}")
            LOG.exception("job=%s failed: %s: %s", short_id, type(exc).__name__, exc)
        if once:
            return


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="轻量知识入库 Worker")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=900)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    concurrency = max(1, min(args.concurrency, 4))
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="ingest") as pool:
        futures = [pool.submit(run_slot, slot, once=args.once,
                               poll_seconds=max(0.2, args.poll_seconds),
                               lease_seconds=max(30, args.lease_seconds))
                   for slot in range(concurrency)]
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
