-- 第5步：不依赖消息中间件的持久化入库任务队列。
CREATE TABLE IF NOT EXISTS ingest_jobs (
  job_id            VARCHAR(36) PRIMARY KEY,
  job_type          ENUM('rebuild','upload') NOT NULL,
  tenant_id         VARCHAR(64) NOT NULL DEFAULT 'default',
  actor             VARCHAR(64) NOT NULL,
  kb_version        VARCHAR(32) NOT NULL,
  mode              ENUM('incremental','full') NOT NULL DEFAULT 'incremental',
  status            ENUM('pending','running','retry_wait','success','failed','cancelled') NOT NULL DEFAULT 'pending',
  stage             VARCHAR(32) NOT NULL DEFAULT 'pending',
  payload_json      JSON NOT NULL,
  idempotency_key   CHAR(64) NOT NULL,
  progress_done     INT NOT NULL DEFAULT 0,
  progress_total    INT NOT NULL DEFAULT 0,
  stats_json        JSON DEFAULT NULL,
  error_message     TEXT DEFAULT NULL,
  attempt_count     INT NOT NULL DEFAULT 0,
  max_attempts      INT NOT NULL DEFAULT 3,
  cancel_requested  BOOLEAN NOT NULL DEFAULT FALSE,
  worker_id         VARCHAR(128) DEFAULT NULL,
  locked_at         DATETIME DEFAULT NULL,
  heartbeat_at      DATETIME DEFAULT NULL,
  next_run_at       DATETIME DEFAULT NULL,
  started_at        DATETIME DEFAULT NULL,
  finished_at       DATETIME DEFAULT NULL,
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_ingest_job_idempotency (tenant_id, idempotency_key),
  KEY idx_ingest_job_claim (status, next_run_at, created_at),
  KEY idx_ingest_job_tenant_created (tenant_id, created_at),
  KEY idx_ingest_job_heartbeat (status, heartbeat_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO schema_migrations (version, description)
VALUES ('004_ingest_jobs', '轻量可恢复知识入库任务');
