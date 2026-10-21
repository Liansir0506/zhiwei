"""第5步持久化任务队列：幂等、原子领取、取消、重试与租约恢复。"""
from __future__ import annotations

import uuid

import sqlalchemy as sa

from qa_core.config.settings import get_settings
from qa_core.ingest.jobs import IngestJobDao
from scripts.migrate_ingest_jobs import apply_migration


def test_ingest_job_lifecycle_and_idempotency():
    settings = get_settings()
    apply_migration(settings.mysql_dsn)
    dao = IngestJobDao(settings.mysql_dsn)
    suffix = uuid.uuid4().hex
    tenant = f"job-test-{suffix}"
    version = "job-it-" + suffix[:12]
    try:
        first, created = dao.create(job_type="rebuild", tenant_id=tenant, actor="tester",
                                    kb_version=version, payload={"raw_dir": "/tmp"},
                                    idempotency_key=suffix)
        duplicate, duplicate_created = dao.create(
            job_type="rebuild", tenant_id=tenant, actor="tester", kb_version=version,
            payload={"raw_dir": "/tmp"}, idempotency_key=suffix)
        assert created is True
        assert duplicate_created is False
        assert duplicate["job_id"] == first["job_id"]

        claimed = dao.claim_next("worker-test", job_id=first["job_id"])
        assert claimed["job_id"] == first["job_id"]
        assert claimed["status"] == "running"
        dao.defer_busy(first["job_id"], "worker-test")
        busy = dao.get(first["job_id"], tenant)
        assert busy["status"] == "retry_wait" and busy["attempt_count"] == 0
        assert busy["stage"] == "waiting_write_lock"
        with dao.engine.begin() as conn:
            conn.execute(sa.text("UPDATE ingest_jobs SET next_run_at=NOW() WHERE job_id=:id"), {"id": first["job_id"]})
        claimed = dao.claim_next("worker-test", job_id=first["job_id"])
        assert claimed["attempt_count"] == 1
        dao.update_progress(first["job_id"], "worker-test", stage="indexing",
                            done=1, total=2, stats={"done": 1})
        dao.fail(first["job_id"], "worker-test", "synthetic failure", retry_delay=1)
        waiting = dao.get(first["job_id"], tenant)
        assert waiting["status"] == "retry_wait"
        assert waiting["error_message"] == "synthetic failure"

        # 手动模拟达到重试上限，验证错误可见且可以再次手动重试。
        with dao.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET status='failed', attempt_count=max_attempts "
                "WHERE job_id=:id"), {"id": first["job_id"]})
        retried = dao.retry(first["job_id"], tenant)
        assert retried["status"] == "pending"
        assert retried["max_attempts"] > retried["attempt_count"]
    finally:
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_jobs WHERE tenant_id=:tenant"),
                         {"tenant": tenant})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})


def test_pending_job_can_be_cancelled_without_worker():
    settings = get_settings()
    apply_migration(settings.mysql_dsn)
    dao = IngestJobDao(settings.mysql_dsn)
    suffix = uuid.uuid4().hex
    tenant = f"job-cancel-{suffix}"
    version = "job-it-" + suffix[:12]
    try:
        job, _ = dao.create(job_type="upload", tenant_id=tenant, actor="tester",
                            kb_version=version, payload={"filename": "faq_x.csv"},
                            idempotency_key=suffix)
        cancelled = dao.request_cancel(job["job_id"], tenant)
        assert cancelled["status"] == "cancelled"
        assert cancelled["stage"] == "cancelled"
        assert cancelled["cancel_requested"] is True
    finally:
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_jobs WHERE tenant_id=:tenant"),
                         {"tenant": tenant})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})


def test_stale_running_job_is_recovered_after_worker_restart():
    settings = get_settings()
    apply_migration(settings.mysql_dsn)
    dao = IngestJobDao(settings.mysql_dsn)
    suffix = uuid.uuid4().hex
    tenant = f"job-recover-{suffix}"
    version = "job-it-" + suffix[:12]
    try:
        job, _ = dao.create(job_type="rebuild", tenant_id=tenant, actor="tester",
                            kb_version=version, payload={}, idempotency_key=suffix)
        claimed = dao.claim_next("dead-worker", job_id=job["job_id"])
        assert claimed["job_id"] == job["job_id"]
        # 心跳必须用数据库侧时间。宿主 datetime.now() 取的是本地时区（UTC+8），
        # 而容器 MySQL 的 NOW() 是 UTC，两者相差 8 小时——写进去的"1 小时前"
        # 在数据库看来是未来时间，recover_stale 永远匹配不到，测试必然失败。
        with dao.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE ingest_jobs SET heartbeat_at=DATE_SUB(NOW(), INTERVAL 1 HOUR) "
                "WHERE job_id=:id"),
                {"id": job["job_id"]})
        assert dao.recover_stale(30) >= 1
        recovered = dao.get(job["job_id"], tenant)
        assert recovered["status"] == "pending"
        assert recovered["stage"] == "recovered"
        assert recovered["worker_id"] is None
    finally:
        with dao.engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ingest_jobs WHERE tenant_id=:tenant"),
                         {"tenant": tenant})
            conn.execute(sa.text("DELETE FROM kb_versions WHERE version=:v AND status='STAGED'"), {"v": version})
