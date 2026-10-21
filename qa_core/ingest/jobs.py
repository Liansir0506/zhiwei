"""MySQL 持久化任务队列：原子领取、租约恢复、重试、取消和进度。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import sqlalchemy as sa


TERMINAL_STATUSES = {"success", "failed", "cancelled"}


def _decode(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for field in ("payload_json", "stats_json"):
        value = item.get(field)
        if isinstance(value, str):
            item[field] = json.loads(value)
        elif value is None:
            item[field] = {}
    for key, value in list(item.items()):
        if isinstance(value, datetime):
            item[key] = value.isoformat()
    item["cancel_requested"] = bool(item.get("cancel_requested"))
    return item


class IngestJobDao:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.engine = sa.create_engine(dsn, pool_pre_ping=True)

    def create(self, *, job_type: str, tenant_id: str, actor: str, kb_version: str,
               mode: str = "incremental", payload: dict | None = None,
               idempotency_key: str, max_attempts: int = 3,
               job_id: str | None = None) -> tuple[dict, bool]:
        """创建任务；同租户同幂等键返回原任务，不重复排队。"""
        if not idempotency_key or len(idempotency_key) > 64:
            raise ValueError("idempotency_key must contain 1..64 characters")
        from qa_core.config.settings import get_settings
        from qa_core.ingest.write_guard import candidate_write
        settings = get_settings().model_copy(update={"mysql_dsn": self.dsn})
        from qa_core.ingest.binding import execution_binding
        payload = dict(payload or {})
        # APIs pass their captured configuration; ordinary scripts capture current settings.
        payload.setdefault("execution_binding", execution_binding(settings, job_type, payload))
        with candidate_write(kb_version, settings=settings):
            from qa_core.storage.version_manifest import bind_candidate
            bound = payload["execution_binding"]["version_binding"]
            selected = settings.model_copy(update={"doc_collection": bound["doc_collection"], "faq_collection": bound["faq_collection"]})
            # Do not silently rebind endpoint/model identities supplied by callers.
            if execution_binding(selected, job_type, payload) != payload["execution_binding"]:
                raise ValueError("任务绑定与当前处理配置不一致")
            bind_candidate(selected, kb_version)
            return self._create(job_type=job_type, tenant_id=tenant_id, actor=actor, kb_version=kb_version,
                                mode=mode, payload=payload, idempotency_key=idempotency_key,
                                max_attempts=max_attempts, job_id=job_id)

    def _create(self, *, job_type, tenant_id, actor, kb_version, mode, payload,
                idempotency_key, max_attempts, job_id):
        job_id = job_id or str(uuid.uuid4())
        params = {"id": job_id, "type": job_type, "tenant": tenant_id, "actor": actor,
                  "version": kb_version, "mode": mode,
                  "payload": json.dumps(payload or {}, ensure_ascii=False),
                  "idem": idempotency_key, "max_attempts": max(1, min(max_attempts, 10))}
        created = False
        with self.engine.begin() as conn:
            result = conn.execute(sa.text(
                "INSERT IGNORE INTO ingest_jobs "
                "(job_id, job_type, tenant_id, actor, kb_version, mode, payload_json, "
                " idempotency_key, max_attempts) "
                "VALUES (:id,:type,:tenant,:actor,:version,:mode,:payload,:idem,:max_attempts)"),
                params)
            created = bool(result.rowcount)
            row = conn.execute(sa.text(
                "SELECT * FROM ingest_jobs WHERE tenant_id=:tenant AND idempotency_key=:idem"),
                params).mappings().first()
        return _decode(row), created

    def get(self, job_id: str, tenant_id: str) -> dict | None:
        with self.engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT * FROM ingest_jobs WHERE job_id=:id AND tenant_id=:tenant"),
                {"id": job_id, "tenant": tenant_id}).mappings().first()
        return _decode(row)

    def list(self, tenant_id: str, limit: int = 50) -> list[dict]:
        with self.engine.begin() as conn:
            rows = conn.execute(sa.text(
                "SELECT * FROM ingest_jobs WHERE tenant_id=:tenant "
                "ORDER BY created_at DESC LIMIT :limit"),
                {"tenant": tenant_id, "limit": max(1, min(limit, 200))}).mappings().all()
        return [_decode(row) for row in rows]

    def recover_stale(self, lease_seconds: int = 120) -> int:
        """Worker 异常退出后，超过租约的 running 任务重新回到 pending。"""
        with self.engine.begin() as conn:
            result = conn.execute(sa.text(
                "UPDATE ingest_jobs SET "
                "stage=CASE WHEN cancel_requested=1 THEN 'cancelled' "
                "WHEN attempt_count>=max_attempts THEN 'failed' ELSE 'recovered' END, "
                "status=CASE WHEN cancel_requested=1 THEN 'cancelled' "
                "WHEN attempt_count>=max_attempts THEN 'failed' ELSE 'pending' END, "
                "finished_at=CASE WHEN cancel_requested=1 OR attempt_count>=max_attempts "
                "THEN NOW() ELSE NULL END, worker_id=NULL, "
                "locked_at=NULL, heartbeat_at=NULL, error_message='worker lease expired; recovered' "
                "WHERE status='running' AND heartbeat_at < "
                "DATE_SUB(NOW(), INTERVAL :lease SECOND)"), {"lease": max(10, lease_seconds)})
        return int(result.rowcount)

    def claim_next(self, worker_id: str, *, job_id: str | None = None) -> dict | None:
        """使用行锁 + SKIP LOCKED 原子领取；多 Worker 不会领取同一任务。"""
        with self.engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT job_id FROM ingest_jobs "
                "WHERE status IN ('pending','retry_wait') AND cancel_requested=0 "
                "AND (next_run_at IS NULL OR next_run_at<=NOW()) "
                "AND (:target IS NULL OR job_id=:target) "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"), {"target": job_id}).first()
            if row is None:
                return None
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET status='running', stage='starting', worker_id=:worker, "
                "locked_at=NOW(), heartbeat_at=NOW(), started_at=COALESCE(started_at,NOW()), "
                "attempt_count=attempt_count+1, error_message=NULL WHERE job_id=:id"),
                {"worker": worker_id, "id": row[0]})
            claimed = conn.execute(sa.text(
                "SELECT * FROM ingest_jobs WHERE job_id=:id"), {"id": row[0]}).mappings().first()
        return _decode(claimed)

    def defer_busy(self, job_id: str, worker_id: str) -> None:
        """Lock contention is waiting, not a failed ingestion attempt."""
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET status='retry_wait', stage='waiting_write_lock', "
                "attempt_count=GREATEST(attempt_count-1,0), next_run_at=DATE_ADD(NOW(), INTERVAL 2 SECOND), "
                "worker_id=NULL, locked_at=NULL, heartbeat_at=NULL, error_message=NULL "
                "WHERE job_id=:id AND worker_id=:worker AND status='running'"),
                {"id": job_id, "worker": worker_id})

    def update_progress(self, job_id: str, worker_id: str, *, stage: str,
                        done: int | None = None, total: int | None = None,
                        stats: dict | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET stage=:stage, progress_done=COALESCE(:done,progress_done), "
                "progress_total=COALESCE(:total,progress_total), "
                "stats_json=COALESCE(:stats,stats_json), heartbeat_at=NOW() "
                "WHERE job_id=:id AND worker_id=:worker AND status='running'"),
                {"stage": stage, "done": done, "total": total,
                 "stats": json.dumps(stats, ensure_ascii=False) if stats is not None else None,
                 "id": job_id, "worker": worker_id})

    def cancel_requested(self, job_id: str) -> bool:
        with self.engine.begin() as conn:
            return bool(conn.execute(sa.text(
                "SELECT cancel_requested FROM ingest_jobs WHERE job_id=:id"),
                {"id": job_id}).scalar())

    def request_cancel(self, job_id: str, tenant_id: str) -> dict | None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET cancel_requested=1, "
                "stage=IF(status IN ('pending','retry_wait'),'cancelled',stage), "
                "finished_at=IF(status IN ('pending','retry_wait'),NOW(),finished_at), "
                "status=IF(status IN ('pending','retry_wait'),'cancelled',status) "
                "WHERE job_id=:id AND tenant_id=:tenant AND status NOT IN ('success','failed','cancelled')"),
                {"id": job_id, "tenant": tenant_id})
            row = conn.execute(sa.text(
                "SELECT * FROM ingest_jobs WHERE job_id=:id AND tenant_id=:tenant"),
                {"id": job_id, "tenant": tenant_id}).mappings().first()
        return _decode(row)

    def retry(self, job_id: str, tenant_id: str) -> dict | None:
        current = self.get(job_id, tenant_id)
        if current is None:
            return None
        from qa_core.config.settings import get_settings
        from qa_core.ingest.write_guard import candidate_write
        settings = get_settings().model_copy(update={"mysql_dsn": self.dsn})
        with candidate_write(current["kb_version"], settings=settings):
            return self._retry(job_id, tenant_id)

    def _retry(self, job_id: str, tenant_id: str) -> dict | None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET status='pending', stage='pending', cancel_requested=0, "
                "error_message=NULL, next_run_at=NULL, worker_id=NULL, locked_at=NULL, "
                "heartbeat_at=NULL, finished_at=NULL, max_attempts=GREATEST(max_attempts,attempt_count+1) "
                "WHERE job_id=:id AND tenant_id=:tenant AND status IN ('failed','cancelled')"),
                {"id": job_id, "tenant": tenant_id})
            row = conn.execute(sa.text(
                "SELECT * FROM ingest_jobs WHERE job_id=:id AND tenant_id=:tenant"),
                {"id": job_id, "tenant": tenant_id}).mappings().first()
        return _decode(row)

    def finish(self, job_id: str, worker_id: str, stats: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET status='success', stage='done', stats_json=:stats, "
                "progress_done=progress_total, heartbeat_at=NOW(), finished_at=NOW() "
                "WHERE job_id=:id AND worker_id=:worker"),
                {"stats": json.dumps(stats, ensure_ascii=False), "id": job_id,
                 "worker": worker_id})

    def cancelled(self, job_id: str, worker_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET status='cancelled', stage='cancelled', "
                "heartbeat_at=NOW(), finished_at=NOW() WHERE job_id=:id AND worker_id=:worker"),
                {"id": job_id, "worker": worker_id})

    def fail(self, job_id: str, worker_id: str, message: str, retry_delay: int = 10) -> None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET "
                "status=IF(attempt_count<max_attempts,'retry_wait','failed'), stage='failed', "
                "error_message=:message, heartbeat_at=NOW(), "
                "next_run_at=IF(attempt_count<max_attempts,DATE_ADD(NOW(),INTERVAL :delay SECOND),NULL), "
                "finished_at=IF(attempt_count<max_attempts,NULL,NOW()) "
                "WHERE job_id=:id AND worker_id=:worker"),
                {"message": message[:2000], "delay": max(1, retry_delay),
                 "id": job_id, "worker": worker_id})
