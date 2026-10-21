-- 第2步：企业统一领域模型。
-- 该文件仅包含幂等 CREATE/INSERT；已有环境通过 scripts/migrate_domain_model.py 应用。

CREATE TABLE IF NOT EXISTS schema_migrations (
  version      VARCHAR(32) PRIMARY KEY,
  description VARCHAR(256) NOT NULL,
  applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tenants (
  tenant_id  VARCHAR(64) PRIMARY KEY,
  name       VARCHAR(128) NOT NULL,
  status     ENUM('active','disabled') NOT NULL DEFAULT 'active',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS departments (
  dept_id        VARCHAR(64) PRIMARY KEY,
  tenant_id      VARCHAR(64) NOT NULL,
  parent_dept_id VARCHAR(64) DEFAULT NULL,
  name           VARCHAR(128) NOT NULL,
  status         ENUM('active','disabled') NOT NULL DEFAULT 'active',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_departments_tenant (tenant_id),
  KEY idx_departments_parent (parent_dept_id),
  CONSTRAINT fk_departments_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_departments_parent FOREIGN KEY (parent_dept_id) REFERENCES departments (dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS users (
  user_id      VARCHAR(64) PRIMARY KEY,
  tenant_id    VARCHAR(64) NOT NULL,
  username     VARCHAR(128) NOT NULL,
  display_name VARCHAR(128) DEFAULT NULL,
  status       ENUM('active','disabled') NOT NULL DEFAULT 'active',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_users_tenant_username (tenant_id, username),
  KEY idx_users_tenant (tenant_id),
  CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_departments (
  user_id    VARCHAR(64) NOT NULL,
  dept_id    VARCHAR(64) NOT NULL,
  is_primary TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, dept_id),
  KEY idx_user_departments_dept (dept_id),
  CONSTRAINT fk_user_departments_user FOREIGN KEY (user_id) REFERENCES users (user_id),
  CONSTRAINT fk_user_departments_dept FOREIGN KEY (dept_id) REFERENCES departments (dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS devices (
  device_id        VARCHAR(64) PRIMARY KEY,
  tenant_id        VARCHAR(64) NOT NULL,
  dept_id          VARCHAR(64) DEFAULT NULL,
  cmdb_id          VARCHAR(64) DEFAULT NULL,
  name             VARCHAR(128) NOT NULL,
  device_type      VARCHAR(64) NOT NULL,
  vendor           VARCHAR(64) DEFAULT NULL,
  model            VARCHAR(64) DEFAULT NULL,
  network_tech     VARCHAR(32) DEFAULT NULL,
  software_version VARCHAR(64) DEFAULT NULL,
  status           ENUM('online','offline','maintenance','retired') NOT NULL DEFAULT 'online',
  attributes_json  JSON DEFAULT NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_devices_tenant_cmdb (tenant_id, cmdb_id),
  KEY idx_devices_tenant_dept (tenant_id, dept_id),
  CONSTRAINT fk_devices_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_devices_dept FOREIGN KEY (dept_id) REFERENCES departments (dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS monitoring_metrics (
  observation_id VARCHAR(64) PRIMARY KEY,
  tenant_id      VARCHAR(64) NOT NULL,
  device_id      VARCHAR(64) NOT NULL,
  metric_name    VARCHAR(128) NOT NULL,
  metric_value   DOUBLE NOT NULL,
  unit           VARCHAR(32) DEFAULT NULL,
  sampled_at     DATETIME NOT NULL,
  source_system  VARCHAR(64) NOT NULL,
  labels_json    JSON DEFAULT NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_metrics_device_time (device_id, sampled_at),
  KEY idx_metrics_tenant_name (tenant_id, metric_name),
  CONSTRAINT fk_metrics_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_metrics_device FOREIGN KEY (device_id) REFERENCES devices (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS alarms (
  alarm_id      VARCHAR(64) PRIMARY KEY,
  tenant_id     VARCHAR(64) NOT NULL,
  device_id     VARCHAR(64) DEFAULT NULL,
  alarm_code    VARCHAR(32) NOT NULL,
  severity      ENUM('critical','major','minor','warning','info') NOT NULL DEFAULT 'major',
  status        ENUM('open','acknowledged','cleared','closed') NOT NULL DEFAULT 'open',
  source_system VARCHAR(64) NOT NULL,
  occurred_at   DATETIME NOT NULL,
  cleared_at    DATETIME DEFAULT NULL,
  payload_json  JSON DEFAULT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_alarms_tenant_code (tenant_id, alarm_code),
  KEY idx_alarms_device_status (device_id, status),
  CONSTRAINT fk_alarms_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_alarms_device FOREIGN KEY (device_id) REFERENCES devices (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS work_orders (
  ticket_id        VARCHAR(64) PRIMARY KEY,
  tenant_id        VARCHAR(64) NOT NULL,
  dept_id          VARCHAR(64) DEFAULT NULL,
  device_id        VARCHAR(64) DEFAULT NULL,
  alarm_id         VARCHAR(64) DEFAULT NULL,
  title            VARCHAR(256) NOT NULL,
  priority         ENUM('P1','P2','P3','P4') NOT NULL DEFAULT 'P3',
  status           ENUM('open','processing','resolved','closed','cancelled') NOT NULL DEFAULT 'open',
  assignee_user_id VARCHAR(64) DEFAULT NULL,
  source_system    VARCHAR(64) NOT NULL,
  opened_at        DATETIME NOT NULL,
  closed_at        DATETIME DEFAULT NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_work_orders_tenant_status (tenant_id, status),
  KEY idx_work_orders_alarm (alarm_id),
  KEY idx_work_orders_device (device_id),
  CONSTRAINT fk_work_orders_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_work_orders_dept FOREIGN KEY (dept_id) REFERENCES departments (dept_id),
  CONSTRAINT fk_work_orders_device FOREIGN KEY (device_id) REFERENCES devices (device_id),
  CONSTRAINT fk_work_orders_alarm FOREIGN KEY (alarm_id) REFERENCES alarms (alarm_id),
  CONSTRAINT fk_work_orders_assignee FOREIGN KEY (assignee_user_id) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS change_records (
  change_id        VARCHAR(64) PRIMARY KEY,
  tenant_id        VARCHAR(64) NOT NULL,
  dept_id          VARCHAR(64) DEFAULT NULL,
  device_id        VARCHAR(64) DEFAULT NULL,
  ticket_id        VARCHAR(64) DEFAULT NULL,
  title            VARCHAR(256) NOT NULL,
  change_type      VARCHAR(64) NOT NULL,
  status           ENUM('planned','approved','executing','completed','failed','cancelled') NOT NULL DEFAULT 'planned',
  window_start     DATETIME DEFAULT NULL,
  window_end       DATETIME DEFAULT NULL,
  operator_user_id VARCHAR(64) DEFAULT NULL,
  source_system    VARCHAR(64) NOT NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_changes_tenant_status (tenant_id, status),
  KEY idx_changes_device (device_id),
  KEY idx_changes_ticket (ticket_id),
  CONSTRAINT fk_changes_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_changes_dept FOREIGN KEY (dept_id) REFERENCES departments (dept_id),
  CONSTRAINT fk_changes_device FOREIGN KEY (device_id) REFERENCES devices (device_id),
  CONSTRAINT fk_changes_ticket FOREIGN KEY (ticket_id) REFERENCES work_orders (ticket_id),
  CONSTRAINT fk_changes_operator FOREIGN KEY (operator_user_id) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS knowledge_assets (
  asset_id        VARCHAR(64) PRIMARY KEY,
  tenant_id       VARCHAR(64) NOT NULL,
  dept_id         VARCHAR(64) DEFAULT NULL,
  filename        VARCHAR(256) NOT NULL,
  doc_type        VARCHAR(32) NOT NULL,
  source_system   VARCHAR(64) NOT NULL,
  source_id       VARCHAR(128) DEFAULT NULL,
  source_version  VARCHAR(64) DEFAULT NULL,
  security_level  ENUM('public','internal','confidential','restricted') NOT NULL DEFAULT 'internal',
  visibility      ENUM('public','role_restricted','department_restricted') NOT NULL DEFAULT 'public',
  role_scope      JSON DEFAULT NULL,
  status          ENUM('draft','reviewing','active','retired') NOT NULL DEFAULT 'draft',
  effective_from  DATETIME DEFAULT NULL,
  effective_to    DATETIME DEFAULT NULL,
  kb_version      VARCHAR(32) DEFAULT NULL,
  checksum        CHAR(64) DEFAULT NULL,
  reviewed_by     VARCHAR(64) DEFAULT NULL,
  approved_at     DATETIME DEFAULT NULL,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_assets_tenant_status (tenant_id, status),
  KEY idx_assets_source (source_system, source_id),
  KEY idx_assets_effective (effective_from, effective_to),
  CONSTRAINT fk_assets_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id),
  CONSTRAINT fk_assets_dept FOREIGN KEY (dept_id) REFERENCES departments (dept_id),
  CONSTRAINT fk_assets_reviewer FOREIGN KEY (reviewed_by) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS knowledge_relations (
  asset_id      VARCHAR(64) NOT NULL,
  relation_type ENUM('device','alarm','work_order','change') NOT NULL,
  relation_id   VARCHAR(64) NOT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (asset_id, relation_type, relation_id),
  KEY idx_knowledge_relations_entity (relation_type, relation_id),
  CONSTRAINT fk_knowledge_relations_asset FOREIGN KEY (asset_id)
    REFERENCES knowledge_assets (asset_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Adapter 同步基础设施状态，不属于业务领域表；保存增量游标和降级原因。
CREATE TABLE IF NOT EXISTS adapter_sync_state (
  adapter_name      VARCHAR(64) NOT NULL,
  tenant_id         VARCHAR(64) NOT NULL,
  cursor_value      VARCHAR(256) DEFAULT NULL,
  sync_status       ENUM('idle','running','success','degraded') NOT NULL DEFAULT 'idle',
  last_error_code   VARCHAR(64) DEFAULT NULL,
  last_error_message VARCHAR(512) DEFAULT NULL,
  simulation        TINYINT(1) NOT NULL DEFAULT 0,
  items_synced      INT NOT NULL DEFAULT 0,
  last_synced_at    DATETIME DEFAULT NULL,
  updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (adapter_name, tenant_id),
  KEY idx_adapter_sync_tenant (tenant_id, sync_status),
  CONSTRAINT fk_adapter_sync_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO tenants (tenant_id, name, status)
VALUES ('default', '默认租户', 'active');

INSERT IGNORE INTO departments (dept_id, tenant_id, parent_dept_id, name, status)
VALUES ('public', 'default', NULL, '公共知识域', 'active');

INSERT IGNORE INTO schema_migrations (version, description)
VALUES ('002_enterprise_domain', '企业统一领域模型与知识资产关系');
