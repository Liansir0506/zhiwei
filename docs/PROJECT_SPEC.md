# PROJECT_SPEC — 通信运维 RAG 知识问答系统

## 1. 项目定位

面向通信网络运维团队的 Docker 化企业级 RAG 知识问答系统，覆盖两类场景：

1. **智能故障诊断**：基于历史故障工单、设备手册、3GPP/IEEE 标准、厂商运维指南，按告警/故障检索历史案例与排查步骤；
2. **运维知识快速检索**：设备配置手册、网络拓扑规范、应急处理流程（SOP）的自然语言问答，如"5G基站传输中断排查步骤"。

## 2. 系统能力（业务行为级）

用户（运维工程师）输入一个业务问题，系统：

1. 校验并规范化输入（长度、注入扫描、缩略语展开、全半角归一）；
2. 通过规则+模型双通道判定意图，网关仲裁出四路之一：
   - `direct_answer`：问候、越界（操作现网/变更类）、注入命中 → 固定话术或转人工说明；
   - `faq_exact`：高置信 FAQ（faq_score ≥ 0.92 且与第二名分差 ≥ 0.05）→ 直出 FAQ 答案与来源；
   - `structured_lookup`：命中告警码/网元结构 → Milvus 标量精确查工单，0 命中降级 retrieval；
   - `retrieval`：完整检索链路（改写 → Hybrid → 过滤 → 去重 → Reranker → 生成）；
3. 生成答案带编号排查步骤、风险提示与引用 `[n]`；无证据明确回答"资料不足"，禁止编造；
4. 返回 `answer, sources, route, intent, diagnostics(timings/cache/retrieval)`；
5. 支持 WebSocket 流式与 HTTP 同步两种接口；
6. 知识库支持 STAGED/ACTIVE/ARCHIVED 版本状态机、质量门禁、发布与回滚审计；
7. 支持 tenant/dataset/visibility/allowed_roles 数据隔离。

## 3. 非功能指标（验收口径）

| 指标 | 目标 | 方式 |
|---|---|---|
| 在线 P95 延迟（非流式） | ≤ 6 s | trace 聚合 |
| LLM 首 token | ≤ 3 s | 流式打点 |
| recall@5（评测集 v1） | ≥ 0.85 | 回归评测 |
| FAQ 直出准确率 | ≥ 0.95 | 评测集 |
| 拒答准确率 | ≥ 0.95 | 越界/无证据子集 |
| 告警码召回抽检 | ≥ 0.90 | 质量门禁 |
| 可用性 | 99.5% | /health + 熔断记录 |

## 4. 边界（明确不做）

- 不操作现网网元、不下发工单、不执行变更指令（只读知识问答）；
- 不使用 Agent、不引入 GraphRAG；
- 不缓存最终 LLM 答案；
- API Key 仅从环境变量读取；模型本地 volume 挂载，不在镜像构建期下载。

## 5. 技术栈（冻结）

Python 3.12 / FastAPI / Milvus 2.5+ / BGE-M3(dense 1024, HNSW) / Milvus BM25(AUTOINDEX) / bge-reranker-v2-m3 / bert_intent_classifier_v1(灰区兜底) / MySQL 8 / Redis 7 / OpenAI 兼容 API(LLM) / Docker Compose / WebSocket。

## 6. 部署形态

单机 Docker Compose：api、mysql、redis、etcd、minio、milvus 六容器；模型目录 `./models` volume 挂载；`.env` 提供 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL` 等。
