-- 第4步：RBAC + ABAC 访问域及权限决策审计。
-- 已有库的列由 scripts/migrate_security_model.py 非破坏性补齐。

CREATE TABLE IF NOT EXISTS permission_audit (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor       VARCHAR(64) NOT NULL,
  tenant_id   VARCHAR(64) NOT NULL DEFAULT 'default',
  auth_source VARCHAR(16) NOT NULL,
  action      VARCHAR(64) NOT NULL,
  resource    VARCHAR(128) NOT NULL,
  decision    ENUM('allow','deny') NOT NULL,
  reason      VARCHAR(256) DEFAULT NULL,
  roles_json  JSON DEFAULT NULL,
  scope_json  JSON DEFAULT NULL,
  query_hash  CHAR(64) DEFAULT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_permission_audit_tenant_created (tenant_id, created_at),
  KEY idx_permission_audit_decision_created (decision, created_at),
  KEY idx_permission_audit_actor_created (actor, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO schema_migrations (version, description)
VALUES ('003_security_governance', 'RBAC ABAC 访问域与权限决策审计');
