# DATA_MODEL — 数据模型

## 1. MySQL 平台基础表

```sql
sessions        (session_id VARCHAR(36) PK, user_id VARCHAR(64), created_at DATETIME,
                 last_active_at DATETIME, INDEX(user_id))
messages        (message_id VARCHAR(36) PK, session_id VARCHAR(36), role ENUM('user','assistant'),
                 content MEDIUMTEXT, route VARCHAR(20), intent VARCHAR(32),
                 trace_id VARCHAR(36), kb_version VARCHAR(32), created_at DATETIME,
                 INDEX(session_id, created_at), INDEX(trace_id))
feedback        (id BIGINT AUTO_INCREMENT PK, message_id VARCHAR(36),
                 rating ENUM('up','down'), reason VARCHAR(32), comment TEXT,
                 fixed_kb_version VARCHAR(32) DEFAULT NULL, created_at DATETIME)
kb_versions     (version VARCHAR(32) PK, scenario VARCHAR(32) DEFAULT 'telecom_ops',
                 status ENUM('STAGED','ACTIVE','ARCHIVED'), published_at DATETIME NULL,
                 quality_report_path VARCHAR(256), activated_by VARCHAR(64),
                 updated_at DATETIME, UNIQUE KEY uniq_active(scenario, status))  -- ACTIVE 唯一性应用层保证
ingest_records  (id BIGINT AUTO_INCREMENT PK, file_hash VARCHAR(64), filename VARCHAR(256),
                 doc_type VARCHAR(16), kb_version VARCHAR(32),
                 status ENUM('pending','parsed','chunked','embedded','loaded','done','failed'),
                 error_msg TEXT, chunk_count INT DEFAULT 0,
                 created_at DATETIME, updated_at DATETIME,
                 UNIQUE KEY uniq_file(file_hash, kb_version))
ingest_jobs     (job_id VARCHAR(36) PK, job_type ENUM('rebuild','upload'),
                 tenant_id VARCHAR(64), actor VARCHAR(64), kb_version VARCHAR(32),
                 mode ENUM('incremental','full'),
                 status ENUM('pending','running','retry_wait','success','failed','cancelled'),
                 stage VARCHAR(32), payload_json JSON, idempotency_key CHAR(64),
                 progress_done INT, progress_total INT, stats_json JSON, error_message TEXT,
                 attempt_count INT, max_attempts INT, cancel_requested BOOL,
                 worker_id VARCHAR(128), locked_at DATETIME, heartbeat_at DATETIME,
                 next_run_at DATETIME, started_at DATETIME, finished_at DATETIME,
                 UNIQUE(tenant_id,idempotency_key), INDEX(status,next_run_at,created_at))
eval_runs       (run_id VARCHAR(36) PK, dataset VARCHAR(64), metrics_json JSON,
                 created_at DATETIME)
bad_cases       (id BIGINT AUTO_INCREMENT PK, query_text TEXT, answer_text MEDIUMTEXT,
                 cause VARCHAR(32), status ENUM('open','fixed'), fixed_version VARCHAR(32),
                 created_at DATETIME, INDEX(status))
audit_log       (id BIGINT AUTO_INCREMENT PK, action VARCHAR(32),   -- activate/rollback/rebuild
                 actor VARCHAR(64), payload_json JSON, created_at DATETIME)
api_keys        (id BIGINT AUTO_INCREMENT PK, key_hash CHAR(64), user_id VARCHAR(64),
                 tenant_id VARCHAR(64), dept_id VARCHAR(64), device_scope JSON,
                 security_levels JSON, roles JSON, enabled BOOL)
permission_audit(id BIGINT AUTO_INCREMENT PK, actor VARCHAR(64), tenant_id VARCHAR(64),
                 auth_source VARCHAR(16), action VARCHAR(64), resource VARCHAR(128),
                 decision ENUM('allow','deny'), reason VARCHAR(128), roles_json JSON,
                 scope_json JSON, query_hash CHAR(64), created_at DATETIME,
                 INDEX(tenant_id, created_at), INDEX(decision, created_at))
```

`sessions` 增加 `tenant_id/dept_id`，`ingest_records` 增加 `asset_id`，用于把既有问答、入库记录与企业领域对象关联；新增列都有兼容默认值，不影响历史数据。

## 2. 企业统一领域模型

```text
tenants ──< departments ──< user_departments >── users
   │              │
   │              └──< devices ──< monitoring_metrics
   │                       └──────< alarms ──< work_orders ──< change_records
   │                       │             │              │              │
   └──< knowledge_assets ──┴─────────────┴──────────────┴──────────────┘
                │
                └── knowledge_relations(relation_type, relation_id)
```

| 表 | 业务主键 | 关键关系与用途 |
|---|---|---|
| `tenants` | `tenant_id` | 所有企业数据的隔离边界 |
| `departments` | `dept_id` | 租户内组织树，支持父部门 |
| `users` / `user_departments` | `user_id` | 用户与部门多对多，标记主部门 |
| `devices` | `device_id` | 对接 CMDB，保留 `cmdb_id`、厂商、型号、版本、网络制式 |
| `monitoring_metrics` | `observation_id` | 对接监控平台；结构化保存设备指标、单位、采样时间和标签，不进入向量库 |
| `alarms` | `alarm_id` | 对接告警平台，关联设备、告警码、级别、发生/清除时间 |
| `work_orders` | `ticket_id` | 对接工单系统，关联告警、设备、部门和处理人 |
| `change_records` | `change_id` | 对接变更系统，关联设备、工单、窗口和操作人 |
| `knowledge_assets` | `asset_id` | 知识来源、版本、密级、可见范围、有效期和审核状态 |
| `knowledge_relations` | 复合主键 | 将知识与设备、告警、工单、变更建立多对多关系 |

领域数据可通过 `EnterpriseSnapshot` 一次校验并事务化幂等写入。`DomainDao.get_alarm_context()` 和 `get_device_context()` 会按 `tenant_id` 联查完整业务上下文，只返回处于有效期内的 active 知识。

17.14补充：上述Pydantic形状与单列外键不保证跨实体的同租户/部门/设备关系，历史生成快照发现742处工单—告警及348处变更—工单设备冲突。新离线prepare_agent_domain_review在候选准备时补typed闭包/归属/身份重复/业务时间校验，不改变通用DAO写入语义，也不意味着通用sync/import已具备同等准入护栏。16条最小候选保持pending，明确批准后应走专用insert-only事务与既有tenant/asset前提核对；源UTC时间显式转SQL UTC-naive，不能把SQL created_at当现场观测时间。详情见交接17.14和DATA_GOVERNANCE。

第 6 步治理字段包括 `origin_type`、`synthetic`、`generator_version`、`generation_seed`、`review_status`、`license_status` 和 `evidence_refs`。新建 Milvus Collection 原生保存这些字段；旧 Collection 滚动升级期间以 `meta.json`、版本 manifest 和 `knowledge_assets` 作为完整治理旁路，入库代码只写目标 Collection 已存在的字段。检索必须同时限制 `status=active` 及 `effective_from/effective_to` 有效期。

## 3. Milvus Collection

### doc_collection
```text
id             VarChar(64)  PrimaryKey
text           VarChar(8192)
dense          FloatVector(1024)  Index: HNSW(M=16, efConstruction=200), metric=COSINE
sparse         SparseFloatVector  Index: AUTOINDEX(BM25)
kb_version     VarChar(32)
network_tech   VarChar(16)  -- partition_key: 5G/4G/传输/核心网
doc_type       VarChar(16)  -- fault_case/manual/standard/guide/sop
equipment_type VarChar(32)  / vendor VarChar(32)
alarm_code     VarChar(32) DEFAULT ""
clause         VarChar(128) DEFAULT ""      -- standard 类必填
source         VarChar(256) / parent_id VarChar(64) DEFAULT ""
tenant_id      VarChar(32) / dataset_id VarChar(32)
dept_id        VarChar(64)
visibility     VarChar(16)                  -- public / role_restricted
allowed_roles  JSON
role_scope     JSON / security_level VarChar(16)
device_type    VarChar(64) / source_system VarChar(64) / source_id VarChar(128)
source_version VarChar(64) / effective_from VarChar(32) / effective_to VarChar(32)
status         VarChar(16)                  -- draft/reviewing/active/retired
ticket_id      VarChar(64) / cmdb_id VarChar(64) / change_id VarChar(64) / asset_id VarChar(64)
quality        VarChar(8)                   -- high / low(OCR)
```

### faq_collection
同构，去掉 clause/parent_id；增加：
```text
question VarChar(512)   -- dense 只由 question 计算
answer   VarChar(4096)
```

索引与检索参数：dense 检索 ef=64；召回各取 top 50；BM25 由 Milvus 服务端参数。

## 4. Redis Key 规范

```text
emb:{model}:{sha256(query_instruction+query)}        TTL 24h   value: float32 向量
ret:{scenario}:{kb_version}:{perm_hash}:{sha256(rewritten_query)}
                                                      TTL 10min value: 候选 id+score 列表
faq:{scenario}:{kb_version}:{perm_hash}:{sha256(query)}  TTL 1h   value: FAQ 命中结果
lock:{任意上述key}                                     TTL 5s    防击穿互斥
```
`perm_hash` 是以下规范化权限域 JSON 的 SHA-256 前 20 位：`tenant_id + roles + dept_ids + device_types + security_levels`。候选缓存、FAQ 缓存和语义缓存使用同一权限摘要，因此任一 ABAC 维度不同都不会共享命中。失效：版本 activate/rollback 时对 `ret/faq/sem` 执行 `SCAN` 精准删除。TTL 一律加 ±10% 抖动；空结果也缓存 60s（防穿透）。

## 5. scenario 配置（scenarios/telecom_ops.yaml）

```yaml
name: telecom_ops
routes: [direct_answer, faq_exact, structured_lookup, retrieval]
thresholds: {faq_direct: 0.80, faq_gap: 0.05  # 0.92 为初始假设，BGE-M3 实测校准 0.80, rerank_min: 0.30,
             model_gray_zone: [0.40, 0.70], model_timeout_ms: 200}
hybrid_weights: {default: {dense: 0.6, sparse: 0.4},
                 fault_case: {dense: 0.5, sparse: 0.5}}
filters_by_intent:
  fault_diagnosis: {doc_type_priority: [fault_case, guide]}
  procedure_query: {doc_type_priority: [sop, faq]}
  config_query:    {doc_type_priority: [manual, standard]}
limits: {query_max_chars: 500, user_qps: 5, global_concurrency: 50}
timeouts_ms: {rewrite: 300, retrieval: 800, rerank: 500, llm_first_token: 3000, llm_total: 30000}
```

## 6. 评测集（eval_sets/v1.json）

30 条：faq_exact 5 / 告警诊断(含码) 10 / 配置查询 5 / 流程查询 5 / 越界+无证据拒答 5；每条含 query、期望 route、期望命中 doc_id（可空）、期望行为（answer|reject|direct）。
