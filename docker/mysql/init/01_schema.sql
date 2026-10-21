-- 阶段3：MySQL 初始 schema（与 docs/DATA_MODEL.md 一致）。
-- 由 MySQL 容器在 MYSQL_DATABASE 指定的环境数据库中执行，不写死数据库名。

CREATE TABLE IF NOT EXISTS sessions (
  session_id     VARCHAR(36) PRIMARY KEY,
  user_id        VARCHAR(64) NOT NULL,
  tenant_id      VARCHAR(64) NOT NULL DEFAULT 'default',
  dept_id        VARCHAR(64) DEFAULT NULL,
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_sessions_user (user_id),
  KEY idx_sessions_tenant (tenant_id, dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS messages (
  message_id  VARCHAR(36) PRIMARY KEY,
  session_id  VARCHAR(36) NOT NULL,
  role        ENUM('user','assistant') NOT NULL,
  content     MEDIUMTEXT,
  route       VARCHAR(20) DEFAULT NULL,
  intent      VARCHAR(32) DEFAULT NULL,
  trace_id    VARCHAR(36) DEFAULT NULL,
  kb_version  VARCHAR(32) DEFAULT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_messages_session (session_id, created_at),
  KEY idx_messages_trace (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS feedback (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  message_id       VARCHAR(36) NOT NULL,
  rating           ENUM('up','down') NOT NULL,
  reason           VARCHAR(32) DEFAULT NULL,
  comment          TEXT,
  fixed_kb_version VARCHAR(32) DEFAULT NULL,
  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_feedback_message (message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS kb_versions (
  version             VARCHAR(32) PRIMARY KEY,
  scenario            VARCHAR(32) NOT NULL DEFAULT 'telecom_ops',
  status              ENUM('STAGED','ACTIVE','ARCHIVED') NOT NULL DEFAULT 'STAGED',
  published_at        DATETIME DEFAULT NULL,
  quality_report_path VARCHAR(256) DEFAULT NULL,
  activated_by        VARCHAR(64) DEFAULT NULL,
  updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ingest_records (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  file_hash   VARCHAR(64) NOT NULL,
  filename    VARCHAR(256) NOT NULL,
  asset_id    VARCHAR(64) DEFAULT NULL,
  doc_type    VARCHAR(16) NOT NULL,
  kb_version  VARCHAR(32) NOT NULL,
  status      ENUM('pending','parsed','chunked','embedded','loaded','done','failed') NOT NULL DEFAULT 'pending',
  error_msg   TEXT,
  chunk_count INT DEFAULT 0,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_file (file_hash, kb_version),
  KEY idx_ingest_asset (asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS eval_runs (
  run_id       VARCHAR(36) PRIMARY KEY,
  dataset      VARCHAR(64) NOT NULL,
  metrics_json JSON,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bad_cases (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  query_text     TEXT NOT NULL COMMENT '用户原问题（修复前误存助手答案）',
  answer_text    MEDIUMTEXT COMMENT '当时的助手回答',
  cause          VARCHAR(32) DEFAULT NULL,
  status         ENUM('open','fixed') NOT NULL DEFAULT 'open',
  fixed_version  VARCHAR(32) DEFAULT NULL,
  message_id     VARCHAR(36) DEFAULT NULL COMMENT '被差评的 assistant 消息（同一消息重复差评合并）',
  session_id     VARCHAR(36) DEFAULT NULL,
  kb_version     VARCHAR(32) DEFAULT NULL COMMENT '回答时的知识版本',
  assigned_to    VARCHAR(64) DEFAULT NULL COMMENT '认领负责人',
  verify_note    MEDIUMTEXT COMMENT '复测结果（新答案+引用+版本）',
  verified_at    DATETIME DEFAULT NULL,
  resolved_at    DATETIME DEFAULT NULL,
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_badcases_status (status),
  KEY idx_badcases_message (message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_log (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  action       VARCHAR(32) NOT NULL,
  actor        VARCHAR(64) NOT NULL,
  payload_json JSON,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 阶段10：API Key 认证
CREATE TABLE IF NOT EXISTS api_keys (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  key_hash   CHAR(64) NOT NULL,
  user_id    VARCHAR(64) NOT NULL,
  tenant_id  VARCHAR(64) NOT NULL DEFAULT 'default',
  dept_id    VARCHAR(64) DEFAULT NULL,
  device_scope JSON DEFAULT NULL,
  security_levels JSON DEFAULT NULL,
  roles      JSON NOT NULL,
  enabled    TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_key_hash (key_hash),
  KEY idx_api_keys_tenant (tenant_id, dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ingest_jobs (
  job_id VARCHAR(36) PRIMARY KEY,
  job_type ENUM('rebuild','upload') NOT NULL,
  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
  actor VARCHAR(64) NOT NULL,
  kb_version VARCHAR(32) NOT NULL,
  mode ENUM('incremental','full') NOT NULL DEFAULT 'incremental',
  status ENUM('pending','running','retry_wait','success','failed','cancelled') NOT NULL DEFAULT 'pending',
  stage VARCHAR(32) NOT NULL DEFAULT 'pending',
  payload_json JSON NOT NULL,
  idempotency_key CHAR(64) NOT NULL,
  progress_done INT NOT NULL DEFAULT 0,
  progress_total INT NOT NULL DEFAULT 0,
  stats_json JSON DEFAULT NULL,
  error_message TEXT DEFAULT NULL,
  attempt_count INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  worker_id VARCHAR(128) DEFAULT NULL,
  locked_at DATETIME DEFAULT NULL,
  heartbeat_at DATETIME DEFAULT NULL,
  next_run_at DATETIME DEFAULT NULL,
  started_at DATETIME DEFAULT NULL,
  finished_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_ingest_job_idempotency (tenant_id, idempotency_key),
  KEY idx_ingest_job_claim (status, next_run_at, created_at),
  KEY idx_ingest_job_tenant_created (tenant_id, created_at),
  KEY idx_ingest_job_heartbeat (status, heartbeat_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 告警处理时间线（2026-10-19）：告警详情 → 诊断 → 证据 → 人工确认 → 处理记录 → 恢复观察
-- 正式工作流记录：绑定告警、操作人、诊断 run_id；本地浏览器笔记不写入本表。
CREATE TABLE IF NOT EXISTS alert_handling_log (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id   VARCHAR(64) NOT NULL DEFAULT 'default',
  alarm_id    VARCHAR(64) NOT NULL,
  device_id   VARCHAR(64) DEFAULT NULL,
  action      VARCHAR(32) NOT NULL COMMENT 'confirm=人工确认 handle=处理记录 observe=恢复观察',
  actor       VARCHAR(64) NOT NULL,
  run_id      VARCHAR(80) DEFAULT NULL COMMENT '关联诊断 run_id（如有）',
  note        VARCHAR(500) DEFAULT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_handling_alarm (tenant_id, alarm_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 诊断任务记录（2026-10-20）：成功诊断保存为可重开任务（T6）；与告警/设备互链。
CREATE TABLE IF NOT EXISTS diagnosis_runs (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id   VARCHAR(64) NOT NULL DEFAULT 'default',
  run_id      VARCHAR(40) NOT NULL,
  alarm_id    VARCHAR(64) DEFAULT NULL,
  device_id   VARCHAR(64) DEFAULT NULL,
  query       VARCHAR(500) NOT NULL,
  status      VARCHAR(24) NOT NULL,
  mode        VARCHAR(16) NOT NULL DEFAULT 'agent' COMMENT 'agent/rule',
  kb_version  VARCHAR(32) DEFAULT NULL,
  summary     VARCHAR(800) DEFAULT NULL COMMENT '答案首段摘要（可重开显示）',
  created_by  VARCHAR(64) NOT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_run (run_id),
  KEY idx_runs_alarm (tenant_id, alarm_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 案例草稿（2026-10-20）：工单关闭后的案例沉淀，人工审核通过后仍需走资料入库工作区正式入库。
CREATE TABLE IF NOT EXISTS case_drafts (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id   VARCHAR(64) NOT NULL DEFAULT 'default',
  ticket_id   VARCHAR(40) NOT NULL,
  alarm_id    VARCHAR(64) DEFAULT NULL,
  title       VARCHAR(160) NOT NULL,
  content     MEDIUMTEXT NOT NULL,
  status      VARCHAR(16) NOT NULL DEFAULT 'submitted' COMMENT 'submitted/approved/rejected',
  created_by  VARCHAR(64) NOT NULL,
  reviewed_by VARCHAR(64) DEFAULT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  reviewed_at DATETIME DEFAULT NULL,
  KEY idx_case_status (tenant_id, status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 案例导出（2026-10-20）：审核通过后可导出稳定来源的案例原文，交给资料入库工作区走准入。
ALTER TABLE case_drafts ADD COLUMN source_id VARCHAR(160) DEFAULT NULL COMMENT '稳定来源ID：case:<ticket>:<id>';
ALTER TABLE case_drafts ADD COLUMN content_sha256 CHAR(64) DEFAULT NULL COMMENT '导出原文 sha256（与实际文件一致）';
ALTER TABLE case_drafts ADD COLUMN exported_at DATETIME DEFAULT NULL COMMENT '最近一次导出时间';
