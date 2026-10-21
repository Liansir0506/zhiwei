CREATE TABLE IF NOT EXISTS knowledge_source_withdrawals (
  identity_hash CHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL,
  source_system VARCHAR(64) NOT NULL,
  source_id VARCHAR(128) NOT NULL,
  reason VARCHAR(500) NOT NULL,
  actor VARCHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_withdrawals_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
