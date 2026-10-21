# 通信运维 RAG 详细设计大纲

> 历史详细设计参考：包含分块、检索、缓存等实现细节，部分模型/参数/性能目标不代表当前实测。现行分发范围见 `README.md` 与 `docs/REPOSITORY_AUDIT.md`，实际配置以代码为准。本文不作为新环境的逐章开发指令。

---

## 第 1 章 总体约束与非功能指标

### 1.1 技术栈（冻结项）

Python 3.12 / FastAPI / Milvus 2.5+ / BGE-M3（dense 1024 维）/ bge-reranker-v2-m3 / MySQL 8 / Redis 7 / Docker Compose / WebSocket。

### 1.2 非功能指标（可验收，不是口号）

| 指标                | 目标值           | 测量方式           |
| ----------------- | ------------- | -------------- |
| 在线 P95 端到端延迟（非流式） | ≤ 6 s         | Trace 阶段耗时聚合   |
| LLM 首 token       | ≤ 3 s         | 流式事件打点         |
| 检索 recall@5（评测集）  | ≥ 0.85        | 离线回归评测         |
| FAQ 直出准确率         | ≥ 0.95        | 评测集 + 线上采样     |
| 拒答准确率（无证据时不编造）    | ≥ 0.95        | 评测集越界/无证据子集    |
| 服务可用性             | 99.5%（单机部署口径） | /health + 熔断记录 |
| 告警码召回抽检命中率        | ≥ 0.90        | 质量门禁           |

### 1.3 安全边界（冻结项）

只读知识问答；不操作网元、不下发指令；API Key 仅环境变量；工单入库前必须脱敏（见 2.6.5）。

**本章产出物**：非功能指标写入 ACCEPTANCE_CHECKLIST.md。

---

## 第 2 章 离线入库链路详细设计

### 2.1 文件格式 × 解析器矩阵

| 知识源          | 格式         | 解析库               | 失败处理                                   |
| ------------ | ---------- | ----------------- | -------------------------------------- |
| 故障工单         | CSV / XLSX | pandas + openpyxl | 列映射失败 → 标记 failed，人工配模板                |
| 设备手册         | PDF        | PyMuPDF           | 扫描件（文本层字符数 < 100/页）→ OCR 分支            |
| 扫描件 PDF      | 图片         | PaddleOCR（可选依赖）   | OCR 置信度 < 0.85 的页标注 `quality=low`，门禁降权 |
| 运维指南 / SOP   | DOCX       | python-docx       | 表格转 markdown 表格；文本框内容丢弃并记警告            |
| 3GPP/IEEE 标准 | PDF        | PyMuPDF           | 双栏 PDF 按列坐标排序（x0 < 页宽/2 先出）            |
| 排查 FAQ       | CSV        | pandas            | Q 或 A 为空 → 跳过该行并计数                     |

通用版式清洗：去页眉页脚（每页首尾各一行，与正文重复率 > 80% 判定）、跨页段落合并（页尾字符非句末标点则与下页首段拼接）、乱码率 > 5% 的文件整份拒绝。

### 2.2 切分类型与参数（chunkers/ 六个实现）

| 切分器              | 策略        | 关键参数                                            | 输出保证            |
| ---------------- | --------- | ----------------------------------------------- | --------------- |
| ticket_chunker   | 一单一 chunk | 超长（> 1200 token）拆 现象/根因/处理 三块，共享 parent_id      | alarm_code 必填   |
| manual_chunker   | 标题递归切分    | chunk 300–500 token，overlap 50，按 `\n#{1,4} ` 递归 | chapter_path 完整 |
| standard_chunker | 条款切分      | 正则 `^\d+(\.\d+)+\s` 识别条款；孤立条款并入上一条              | clause 路径必填     |
| sop_chunker      | 整段一条      | 不 overlap；步骤正则 `步骤\s*\d+` 提取 step_index 列表      | 首尾步骤号连续         |
| faq_chunker      | Q-A 对     | question、answer 分字段存，dense 用 question 计算        | 非空校验            |
| table_chunker    | 整表一条      | 表格转 markdown，行数 > 30 按表头分组拆分                    | 表头随每块重复         |

### 2.3 向量化（转向量化）规范

- 模型：BGE-M3 本地加载；batch_size=32；输入截断 8192 token；输出 L2 归一化后入库（COSINE 等价 IP）。
- **embedding 内容决策**（易错点，必须写死在代码注释里）：
  - doc：dense 用 chunk 正文；
  - faq：dense 用 question（用户输入是问题，Q-Q 语义对齐优于 Q-A）；answer 只存不编码；
  - query 侧：检索时加 BGE 查询指令前缀 `"为这个句子生成表示以检索相关文章："`，入库侧不加——**前缀不对称是 BGE 的既定用法**，缓存 key 必须区分两侧。
- embedding 缓存：`hash(model + text)` → vector，重建知识库时未变更 chunk 不重算。

### 2.4 Milvus 物理 schema（字段级）

```text
doc_collection:
  id            VarChar(64) PK
  text          VarChar(8192)
  dense         FloatVector(1024)   HNSW: M=16, efConstruction=200, metric=COSINE
  sparse        SparseFloatVector   AUTOINDEX + BM25
  kb_version    VarChar(32)   partition_key=False
  network_tech  VarChar(16)   partition_key=True   # 5G/4G/传输/核心网
  doc_type      VarChar(16)                          # fault_case/manual/standard/guide/sop
  equipment_type VarChar(32) / vendor VarChar(32)
  alarm_code    VarChar(32)  default ""
  clause        VarChar(128) default ""
  source        VarChar(256) / parent_id VarChar(64) default ""
  tenant_id / dataset_id VarChar(32)
  visibility    VarChar(16)   # public | role_restricted
  allowed_roles JSON
  quality       VarChar(8)    # high | low（OCR 降权）
faq_collection: 同构，去 clause/parent_id，加 question VarChar(512)、answer VarChar(4096)
检索参数：dense ef=64，候选 top_k_dense=50 / top_k_sparse=50
```

### 2.5 入库流水线与断点续跑

- MySQL 表 `ingest_records`：file_hash, filename, doc_type, status(pending/parsed/chunked/embedded/loaded/done/failed), error_msg, kb_version；
- 幂等：file_hash 已 done 的文件跳过；状态机逐步推进，任何一步失败停在该状态，重跑只续跑未完成文件；
- `scripts/rebuild_kb.py --mode full|incremental --kb-version vN`；增量写 STAGED，全量重建在影子 Collection 建好后原子切换别名（alias）。

### 2.6 质量门禁细则（quality/ 五项，全部可执行）

1. **告警码召回抽检**：从工单表随机抽 30 个 alarm_code 构造查询（码 + 一句自然语言），走真实检索链路，命中判定 = top10 内出现该码工单；输出 HTML 报告；
2. **SOP 完整性**：每条 sop chunk 校验 step_index 连续且首步为 1；
3. **元数据完备率**：equipment_type、network_tech 非空率 ≥ 95%，按 doc_type 分组出报告；
4. **重复检测**：alarm_code + root_cause 完全相同 → 保留最新一条，其余标记 duplicate 并排除入库；
5. **脱敏扫描**：正则扫描手机号、身份证、内网 IP 明细、密码字段；命中即替换为占位符并计数——**未通过脱敏的文件禁止 loaded**。

门禁失败 → 版本停在 STAGED，报告写入 `quality_reports/{kb_version}.html`。

---

## 第 3 章 在线问答链路详细设计

### 3.1 请求规范与输入校验

```json
POST /qa
{ "query": "string(1..500)", "session_id": "uuid", "stream": false, "top_n": 5 }
```

输入管道（rewrite/ 前置）：trim → 长度/空校验（违规返回 400 + 错误码）→ 全半角与标点归一 → prompt injection 规则扫描（"忽略以上指令/你现在是…"命中 → direct_answer + 标记）→ 缩略语展开（配置表 200+ 条）。

错误码表：`E001 参数非法 / E002 会话不存在 / E003 权限不足 / E100 检索不可用 / E101 生成不可用 / E102 限流`。

### 3.2 四路路由与模型兜底（intent/ + rewrite/）

路由集合：`direct_answer / faq_exact / structured_lookup / retrieval`。

- **规则通道**：告警码正则 `ALM-\d{4}|厂商码格式表`、网元名正则、意图关键词表（每类意图一个词表 + 权重），rule_score = 命中权重和归一到 [0,1]；
- **模型通道（兜底策略）**：仅当 rule_score ∈ [0.40, 0.70] 灰区时触发小分类模型；模型超时 200ms 或失败 → **直接取规则结果，灰区默认 retrieval**，记 `source=model_fallback`。绝不因模型故障阻塞主链路；
- **网关仲裁**：structured_lookup > faq_exact > direct_answer > retrieval 的优先级仅在同一请求多信号并存时生效；输出 `intent, route, source(rule|model|model_fallback), score, reason`；
- **多轮改写**：指代词命中（"它/这个/那"）→ 拼接最近 3 轮 history 做 query rewrite，rewrite 失败用原句，绝不因改写失败中断。

### 3.3 FAQ 路径（faq_exact）

FAQ 召回 top5 → 最高分 `faq_score ≥ 0.92` 且第二名的分数差 ≥ 0.05 → 直出 answer + source；分数差不足（歧义）→ 降级 retrieval 并把候选 FAQ 作为强先验；无候选 → retrieval。FAQ 直出结果同样带 kb_version 与权限过滤。

### 3.4 structured_lookup 路径

- 告警码归一化：厂商私有码 → 标准码映射表（scenario 配置），映射不到则按原文查；
- Milvus Query：`expr = 'alarm_code == "ALM-1234" and kb_version == "v3"' + 权限过滤`，partition 按 network_tech 裁剪；
- 命中 0 条 → 自动降级 retrieval（把告警码当文本检索）并在 diagnostics 标注 `lookup_fallback=true`。

### 3.5 检索子系统（retrieval/ + rerank/）

1. 多查询变体：原句 + 缩略语展开句（+ 多轮改写句），最多 3 变体并行检索；
2. 召回：Dense（ef=64）+ BM25，各取 50；
3. 融合：Weighted Hybrid，默认 dense 0.6 / sparse 0.4；`doc_type=fault_case` 时调整为 0.5/0.5（工单字段词匹配价值高）——权重进 scenario 配置，不写死；
4. 标量过滤顺序：**权限过滤（tenant/visibility/roles）→ kb_version → doc_type/network_tech**（软过滤，未命中可放宽重查一次）；
5. 去重：doc_id 级 + 跨 chunk parent_id 级（同一工单三个子块只留 rerank 最高）；
6. Reranker：bge-reranker-v2-m3，输入截断 512 token，批 16，输出 top_n=5；
7. 保护：候选全空 → 直接"资料不足"；rerank 最高分 < 0.30 → 同样拒绝生成（阈值进配置）。

### 3.6 上下文构建与生成（generation/）

- Token 预算：system 模板 500 + 证据 5 × 500 + query/answer 余量 1500 ≈ 总 8K 上限；
- 证据块格式：`[n] 来源:{source} | 设备:{equipment_type} | 告警码:{alarm_code} | 内容…`，按 rerank 分降序；
- System 提示词四要素：角色（通信运维助理）、边界（只用证据、无证据说资料不足、不给现网操作指令）、输出模板（结论/编号步骤/风险提示/引用 [n]）、拒答话术；
- LLM 参数：temperature 0.1，max_tokens 1024，connect 3s / read 30s，流式 WebSocket；
- **生成后核验**：抽取答案中的告警码、条款号、步骤号，逐个回查证据上下文；任一不存在 → 丢弃重生成一次 → 仍失败降级为"引用模式"（原文摘录 + 引用）。

### 3.7 结果组织与规范化（响应契约）

```json
{ "answer": "...", "sources": [{"doc_id":"", "source":"", "doc_type":"", "kb_version":"", "clause":"", "score":0.87}],
  "route": "retrieval", "intent": "fault_diagnosis", "requires_rewrite": true,
  "diagnostics": { "timings": {"rewrite":0, "retrieval":0, "rerank":0, "llm":0},
                   "retrieval": {"variants":3, "candidates":96, "top_score":0.87},
                   "cache": {"l1":false, "l2_embedding":true, "l2_candidates":false} } }
```

前端规则：`sources` 按 score 降序；diagnostics 仅 debug 权限可见。

### 3.8 三级缓存设计（cache/）

| 层                    | 内容                           | Key 设计                                                            | TTL    | 失效方式                            |
| -------------------- | ---------------------------- | ----------------------------------------------------------------- | ------ | ------------------------------- |
| L1 进程内               | scenario 配置、意图规则表、告警码映射、缩略语表 | 配置文件 mtime + 版本戳                                                  | 进程生命周期 | 配置热加载（SIGHUP / 管理接口）            |
| L2 Redis · embedding | query 向量                     | `emb:{model}:{hash(prefix+query)}`                                | 24h    | 随 TTL 自然过期                      |
| L2 Redis · 检索候选      | hybrid 去重后候选 id+分数           | `ret:{scenario}:{kb_version}:{perm_hash}:{hash(rewritten_query)}` | 10 min | kb_version 切换 → 按 version 段精准删除 |
| L2 Redis · FAQ 直出    | FAQ 命中结论                     | `faq:{scenario}:{kb_version}:{perm_hash}:{hash(query)}`           | 1 h    | 同上                              |
| L3                   | **最终 LLM 答案：不缓存**            | —                                                                 | —      | —                               |

L3 不缓存的理由（写进设计说明）：答案受权限上下文影响存在泄露风险；kb_version 切换需全量失效成本高；流式回答缓存收益低。三防：穿透（空结果缓存 60s）、击穿（热点 key 互斥锁）、雪崩（TTL 加 ±10% 抖动）。Redis 故障 → 缓存旁路直连 Milvus，降级计数进 metrics。

### 3.9 降级与稳定性策略（分级熔断表）

| 故障           | 第一动作         | 兜底动作                      | 用户感知        |
| ------------ | ------------ | ------------------------- | ----------- |
| 意图模型超时/失败    | 用规则结果        | 灰区默认 retrieval            | 无感          |
| Reranker 失败  | 用 hybrid 分排序 | —                         | 排序略降        |
| LLM 超时       | 换 key 重试 1 次 | 返回"生成服务暂不可用"+ top3 检索片段摘要 | 可用但非生成      |
| Milvus 不可用   | 熔断 30s       | 全部请求返回 E100               | 问答不可用，健康页可见 |
| Redis 不可用    | 缓存旁路         | —                         | 延迟略增        |
| WebSocket 断连 | 心跳 15s×3     | HTTP 同步接口兜底               | 需重发问题       |

超时预算：改写 300ms / 检索 800ms / rerank 500ms / LLM 首 token 3s。限流：用户级 5 QPS、全局并发 50（信号量），超限返回 E102。

---

## 第 4 章 数据与治理

- **MySQL 表清单（字段级）**：`sessions(session_id, user, created_at)`、`messages(session_id, role, content, route, intent, trace_id)`、`feedback(message_id, rating, reason, fixed_kb_version)`、`kb_versions(version, status, published_at, quality_report_path)`、`ingest_records(2.5)`、`eval_runs(run_id, dataset_ver, metrics_json)`、`bad_cases(id, query, answer, cause, status_open|fixed)`；
- **权限模型**：visibility=public 直接放行；role_restricted 时判定 `user.roles ∩ allowed_roles ≠ ∅`；`perm_hash = md5(sorted(user.roles))` 进缓存 key；
- **版本发布**：STAGED →（门禁五项通过）→ ACTIVE（同 scenario 只有一个 ACTIVE）→ ARCHIVED；回滚 = 旧版本重新置 ACTIVE + 清理 L2 该 version 相关 key，全程写审计表。

## 第 5 章 测试与评测

- **测试分层**：纯逻辑（切分器、归一化、网关仲裁、perm_hash，不打真实服务）/ 组件（Milvus/MySQL/Redis 真连接）/ 集成（入库→问答端到端）/ Smoke（compose 起全套 + 5 条样例）；
- **评测指标公式**：recall@5、MRR、FAQ 直出准确率、structured_lookup 命中率、拒答准确率（无证据子集答"资料不足"的比例）、端到端 BLEU 不适用 → 用人工抽检 20 条/版；
- 评测集版本化（eval_dataset_v1.json），每次发版跑回归并落 eval_runs。

## 第 6 章 可观测与运维

- 结构化 JSON 日志，`trace_id` 从请求入口生成贯穿离线在线所有打点；
- Trace 阶段：rewrite / intent / lookup / retrieval / rerank / generation / verify，各记 ms 与结果计数；
- Metrics（Prometheus 口径）：qps、各阶段延迟直方图、各降级路径触发计数、缓存命中率（按 key 前缀）、门禁通过率；
- 告警规则：E100 连续 5 分钟、LLM 失败率 > 5%、P95 > 10s；
- 故障排查手册结构：症状 → 检查命令（docker compose ps / /health / 门禁报告 / trace 查询）→ 处置。

## 第 7 章 风险清单与开放问题

1. 厂商手册版权与保密：allowed_roles 粒度到"厂商文档仅厂商接口人可见"，需业务侧提供分级清单（开放问题）；
2. 厂商告警码格式差异大，映射表维护成本高 → 先支持 2 家主力厂商；
3. 增量入库与 ACTIVE 版本的一致性：增量改动走下一个版本，不在 ACTIVE 上原地改；
4. 模型灰区阈值 0.40/0.70 为经验值，上线后按 bad case 分布回调。

---

## 章节依赖与写作顺序

```text
第1章 约束 → 第2章 离线 → 第4章 数据治理 → 第3章 在线（依赖2的schema）→ 第5章 测试 → 第6章 观测 → 第7章 风险
每章产出物：第2章 = Collection DDL + 切分器测试；第3章 = API_CONTRACT + 缓存key表 + 降级表；第4章 = 建表SQL；第5章 = 评测集v1；第6章 = trace打点清单
```

