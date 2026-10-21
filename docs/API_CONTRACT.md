# API_CONTRACT — 接口契约

## 完整8010配置交接准备（2026-10-13）

`POST /admin/version-workspace/runtime-handoffs`仅接收`{"version":"版本"}`，202持久后台检查；`GET /runtime-handoff-jobs/{id}`查询任务，`GET /runtime-handoffs`读取最近10份交接及10个任务，`POST /runtime-handoffs/{id}/revalidate`重新核验。后面路径均沿用`/admin/version-workspace`前缀。管理员准备/重验、审计员只读；ops、跨租户及缩小范围管理员拒绝，不扩角色权限。

只准备当前/上一已批准完整配置、各自五项门禁与实时快照、进程归属和具体失败恢复矩阵；完整配置只展示不含DSN/Key的字段及完整摘要。15分钟有效，账号、版本/快照、配置/代码或进程变化409；重启旧任务interrupted。关联未完成或任何执行中任务阻断，其他旧候选pending仍隔离，不删除或领取。所有响应no-store、额外profile/approved等字段422、安全依赖失败503。没有此流程的执行接口，`execution_authorized=false`；准备不激活、回滚、取消历史任务、新准入或外发。

## 新增：POST /qa/sse（2026-09-28，阶段 5 第一批）

使用与 `POST /qa` 相同的 `X-API-Key`/开发模式认证头和 `{"query":"...","session_id":"..."}` 请求体，返回 `text/event-stream`，`Cache-Control: no-cache, no-store`。可选 `kb_version` 固定问答版本；省略时使用 ACTIVE，指定 STAGED 时仅管理员可预览，且必须通过与原文读取相同的版本状态/集合绑定校验。预览不激活版本，不支持在 WebSocket 兼容通道选择版本。现有同步 `POST /qa` 与 WebSocket `/qa/ws` 仍可使用；页面现在默认以 `fetch` 读取 SSE，顶部可手动选择 WebSocket 兼容通道。SSE 失败不会自动切通道重发，避免重复提交已受理的问题。

SSE 每帧为 `event: 名称` + `data: JSON`。既有流式事件映射为 `start→meta`、`delta→token`、`end→done`；`stage`、`decision`、`sources`、`error` 原名保留。`meta` 带知识版本并标注 `token_mode=provisional_until_done`；`token` 是未经最终核验的正文片段，客户端必须以 `done.data.answer/status` 为准；`sources` 是问答链路最终保留的引用，不能把阶段候选当最终引用。错误事件含 `status=FAILED` 与安全错误码，认证/请求格式错误仍使用普通 HTTP 状态码。

首版每进程最多 5 个并行 SSE 推理，超出返回 429；流式总处理上限 120 秒，超时发送 `error E504`。客户端断连会设置停止标志，阻止继续迭代及提交下一段生成；正在执行的同步模型/检索调用无法强制中断，直到其返回才释放并发名额。该上限是单进程边界，不是分布式容量或 5 并发性能结论。隔离开发环境已完成一次 DeepSeek→HTTP SSE→浏览器→真实引用重读整链；五路并发与断连仅以本机假模型验证控制边界，不代表真实模型吞吐能力。

`GET /kb/versions` 在原 `versions` 数组外新增 `can_preview_staged` 布尔值，由已认证角色计算，仅用于页面显示 STAGED 选项；服务端 `POST /qa/sse` 和证据接口始终独立复核权限。页面对 HTTP 401、404、429 和流内 `error` 明确显示失败，未完成的 `token` 不保存，且不自动重发；手动切换通道会清除未完成草稿。错误后顶部保留失败状态，直到新的请求或显式切换通道。

## 新增：只读证据接口（2026-09-27）

问答兼容字段补充：POST /qa 的 JSON 和 WebSocket end.data 新增 status（ANSWERED、NO_EVIDENCE、NEEDS_CLARIFICATION、OUT_OF_SCOPE、DEGRADED），应用错误为 FAILED；框架认证/参数错误仍按旧形状返回。开启流程功能后，已支持沙箱场景的明确缺适用条件/分支歧义可触发 NEEDS_CLARIFICATION，不调用模型且 sources 为空；权限或安全依据缺失仍为 NO_EVIDENCE，不伪装成缺条件。流程 sources 新增 child_evidence：每条是真实子分块的证据 ID/修订/查看地址，汇总自身没有假证据 ID；前端逐条重新鉴权，任一失效不展示残缺流程。

定位补充：新上传文本资料可返回 `location.kind=source_spans`，含 format、matches（Markdown 行号、PDF 页码、DOCX 顶层段落号）、ambiguous、context_prefix 和 file_sha256。重复文字保留多处匹配，未定位的旧片段保持 legacy。只返回已入库脱敏片段，页码/段落仅用作来源定位，不开放原始文件路径。批量定位需 `record_locations=true` 或 CLI `--record-locations`；旧 done 文件不会因此自动回灌。

`POST /kb/search-evidence` 请求：`{"query":"AMF 检查依据","top_n":5}`。可选 `kb_version`（1～64 字符），`top_n` 为 1～10；禁止额外的租户/角色等请求字段，访问域只从认证上下文取得。默认固定请求开始时唯一 ACTIVE 版本；显式 STAGED 仅 admin 可预览，ARCHIVED 不提供。流程题复用完整 SOP 证据保护，可能返回超过 top_n 的完整步骤组，不截断组。

响应含 `status`（`EVIDENCE_FOUND` / `NO_EVIDENCE` / `NEEDS_CLARIFICATION`）、`kb_version`、`evidence` 数组。每条包含 `evidence_id`、`chunk_id`、`document_id`、`source_revision`、`text`、`source`、`location`、四类独立 `scores`、`source_url` 和 `synthetic`。本接口不生成答案、不执行业务动作；当前普通检索查 DOC 集合，FAQ 搜索合流未实现。

`GET /kb/evidence/{evidence_id}` 返回当前重新鉴权后的单条**已入库原文片段**，不是原始文件下载。引用绑定分块、资产、知识版本、来源修订及正文摘要；它不是访问凭证。查看时复验版本策略、完整 schema、租户/角色/部门/密级/设备域与有效期，并强一致读取，不使用答案缓存。撤回、正文变更、身份不匹配、无权限、无效 ID 均不能查看。成功响应 `Cache-Control: no-store`。

本组接口错误沿用 FastAPI `{"detail":...}`：401 认证失败、404 不存在或不可访问、422 请求校验失败、429 限流、503 存储/安全验证不可用。失败关闭，不回退本地文件。审计复用现有权限审计，审计存储故障仍按既有尽力记录策略处理。

`location` 仅返回真实已有步骤索引或章节；缺可靠定位时标记 `{"kind":"legacy","available":false}`，不编造 PDF 页码/DOCX 段落号。普通问答和 FAQ 引用兼容增加证据 ID/查看地址；缺资产/修订的旧引用不生成新地址，流程汇总保留子分块 ID，不生成指向不存在汇总分块的引用。三格式可靠定位、流程汇总引用的前端展开与完整统一状态仍待实施。

通用约定：Base URL `http://127.0.0.1:8010`；JSON UTF-8；开发模式（仅 `AUTH_REQUIRED=false`）支持 Header `X-User-Id`、`X-User-Roles`（如 `ops,admin`）、`X-Tenant-ID`、`X-Dept-IDs`、`X-Device-Types`、`X-Security-Levels`。生产模式必须提供 `X-API-Key`，租户、部门、角色、设备域和密级全部从 Key 解析；请求字段只能收窄角色密级上限，不能扩权。错误响应统一 `{"error": {"code": "E001", "message": "..."}}`。

错误码：E001 参数非法 / E002 会话不存在 / E003 权限不足 / E100 检索服务不可用 / E101 生成服务不可用 / E102 触发限流。

## 1. GET /health

响应的 `generation_enabled` 表示服务是否允许模型生成。`GENERATION_ENABLED=false` 时，同步、SSE、WebSocket 共用引用模式，保留鉴权、证据覆盖、来源撤回与流程安全检查；不读取生成语义缓存、不调用 LLM。同步 `diagnostics.answer_mode` 和流式终态 `answer_mode` 为 `citation`；默认开启时为 `generation`（表示服务模式，FAQ 直出和问候仍可能无需模型）。事件名称与终态协议保持兼容。

200 → `{"status":"ok|degraded", "components": {"mysql":"ok","redis":"ok","milvus":"ok","models":"ok"}, "version":"..."}`；任一组件失败 status=degraded 且组件值为 error 详情。

## 2. POST /qa（同步问答）

请求：
```json
{ "query": "5G基站传输中断排查步骤", "session_id": "uuid(可选,不传则新建)",
  "top_n": 5, "stream": false }
```
校验：query trim 后 1..500 字符，违反 → E001；用户级限流 5 QPS → E102。

200 响应：
```json
{ "answer": "…排查步骤…", "sources": [
    {"doc_id":"…","source":"工单#2024-0817","doc_type":"fault_case",
     "kb_version":"v3","clause":"","score":0.87}],
  "route":"retrieval","intent":"fault_diagnosis","requires_rewrite":false,
  "session_id":"…","message_id":"…",
  "diagnostics":{"timings":{"rewrite":12,"intent":5,"retrieval":210,"rerank":95,"llm":1830},
                 "retrieval":{"variants":2,"candidates":96,"top_score":0.87},
                 "cache":{"l2_embedding":true,"l2_candidates":false}} }
```
规则：direct_answer/faq_exact 响应同构，`sources` 可为 FAQ 来源；无证据时 answer 为固定拒答话术且 sources=[]；Milvus 不可用 → E100；LLM 失败且兜底 → answer 为引用模式片段、`degraded=true` 附加字段。

## 3. WebSocket /qa/ws（流式）

鉴权与 HTTP 使用同一策略。非浏览器客户端可发送相同 Header；浏览器因 WebSocket API 不能自定义 Header，可使用 `/qa/ws?key=<API_KEY>`。开发模式还可使用 `user_id/roles/tenant_id/dept_ids/device_types/security_levels` 查询参数。生产模式无 Key 或 Key 无效时在 `accept` 前以 1008 关闭连接。

客户端发 `{"type":"query","query":"...","session_id":"..."}`；服务端事件序列：
```text
{"type":"start","message_id":"…"}            # 必有
{"type":"route","route":"retrieval","intent":"fault_diagnosis"}
{"type":"delta","content":"部分答案"}         # ≥0 次，retrieval 路径
{"type":"sources","sources":[...]}            # 在 delta 之后 / direct 直出时紧跟 start
{"type":"end","diagnostics":{...}}            # 必有，终止事件
{"type":"error","code":"E101","message":"…"}  # 失败终止
```
心跳：服务端 15s `{"type":"ping"}`×3 无响应断开；客户端断线需重发问题。

## 4. 知识库管理

- `POST /kb/rebuild` `{"mode":"incremental|full","kb_version":"v4","data_dir":"data/raw","run_quality_gate":true}` → 202，返回持久化 `job`；相同租户、版本、模式和目录快照重复提交返回原任务。`data_dir` 必须位于 `DATA_DIR/raw` 内。
- `GET /kb/rebuild?limit=50` → 当前租户最近任务；`GET /kb/rebuild/{job_id}` → 状态、阶段、进度、统计、重试次数和错误。
- `POST /kb/rebuild/{job_id}/cancel` → pending 立即取消，running 在安全文件边界停止；`POST /kb/rebuild/{job_id}/retry` → failed/cancelled 手动重试。写操作仅 admin，读取允许 ops/auditor/admin。
- 状态为 `pending/running/retry_wait/success/failed/cancelled`；Worker 使用 MySQL 行锁领取，超出租约的 running 任务在重启后恢复。
- `GET /kb/versions` → `[{"version":"v3","status":"ACTIVE","published_at":"...","quality_report":"..."}]`。
- `POST /kb/versions/{version}/activate` → 门禁重验通过后置 ACTIVE，旧 ACTIVE→ARCHIVED，写审计；失败返回门禁报告路径。
- `POST /kb/versions/{version}/rollback` → 旧版本置 ACTIVE + 清理对应 L2 缓存 key，写审计。

## 5. 质量门禁与评测

- `POST /quality/gate` `{"kb_version":"v4"}` → `{"passed":true,"report_path":"reports/quality_v4.html","checks":[{...五项结果}]}`。
- `POST /eval/run` `{"dataset":"eval_sets/v1.json"}` → `{"run_id":"…"}`；`GET /eval/runs/{id}` → 指标（recall@5、MRR、faq_acc、lookup_acc、reject_acc）。

## 6. 反馈

`POST /feedback` `{"message_id":"…","rating":"up|down","reason":"wrong_alarm_code|irrelevant|hallucination|other","comment":""}` → `{"ok":true}`。仅 `ops/admin`；非 admin 只能反馈同租户且属于自己的回答；接口执行用户级限流。down 自动写 bad_cases(status=open)。

文档上传：`POST /admin/documents/upload` 使用原始字节 body 与 `X-Filename`，成功立即返回 202、`job_id` 和 `poll_url`；文件进入隔离暂存目录，由独立 Worker 增量入库，不在 HTTP 请求内加载模型。

## 7. 场景配置（只读）

`GET /scenario` → 当前 scenario 摘要（意图词表版本、阈值、hybrid 权重、LLM 模型名），供诊断。

## 8. 企业领域上下文

需要 `ops`、`auditor` 或 `admin` 角色；查询条件强制包含当前认证上下文的 `tenant_id`。

- `GET /domain/alarms/{alarm_id}/context`：返回告警、设备、关联工单、变更和有效知识资产；
- `GET /domain/devices/{device_id}/context`：返回设备、最近告警、工单、变更和有效知识资产；
- 当前租户中不存在实体返回 404；领域迁移未执行或数据库不可用返回 503。
## 9. 模拟企业系统 Adapter API

以下接口仅用于技术验证，响应固定包含 `simulation: true`：

- `GET /health`：模拟服务健康状态；
- `GET /mock/v1/{resource}?cursor=&limit=`：读取 `cmdb/alarms/monitoring/work-orders/changes/wiki`；
- `GET /admin/integrations/status`：ops、auditor、admin 查看同步游标与降级状态；
- `POST /admin/integrations/sync`：仅 admin 触发同步，可指定 `sources/reset_cursor/page_size`。

分页契约、字段映射与错误响应见 `docs/openapi/mock-enterprise.yaml`。上游超时、网络错误、429/502/503/504 会转为 `UPSTREAM_UNAVAILABLE` 并记录 `degraded`；契约异常转为 `CONTRACT_INVALID`。

## 10. 权限审计

`GET /admin/audit/access?decision=allow|deny&limit=100`：仅 `admin/auditor`；强制按当前租户查询，`limit` 范围 1..500。返回认证来源、动作、资源、权限决策、原因、角色和有效访问域；问题仅保存并返回 SHA-256，不保存原文。

## 会话追问的一致性（2026-10-08）

同步、SSE与WebSocket复用同一有效问题准备逻辑和服务端隔离会话历史。相同认证主体、请求权限域、KB版本与读取绑定下，相同session_id可跨通道追问；版本/权限/撤回账本变化会重置有效上下文。自带告警、FAQ、设备或HTTP状态的新问题独立处理；指代/省略追问继承已明确对象，缺历史或多个候选对象时返回NEEDS_CLARIFICATION且sources为空，继续保持危险请求和流程护栏优先。

同步diagnostics.conversation和流式done/end.data.conversation包含reason与effective_query；effective_query是本次用于检索/流程判定的补全问题，MySQL仍保存原始用户输入。事件名称、认证与终态词汇保持兼容。GENERATION_ENABLED=false仍不调用LLM；开启时同步/流式共用同一有预算限制的生成消息构造，历史不是事实证据。原文面板中的访问校验与正文审核声明分别呈现，未改变内容绑定原文或资料准入范围。

## 只读 Agent 诊断接口（17.12后端、17.13工作台）

已注册`POST /agent/diagnose`同步响应接口，默认AGENT_ENABLED=false；仅APP_ENV=test、AUTH_REQUIRED=true、有效API Key与开启开关可用。独立18011启动脚本显式开启，仍GENERATION_ENABLED=false。设计与验收见[架构第10节](ARCHITECTURE.md#10-只读-agent-诊断闭环方案与首个实现2026-10-08)。

请求JSON：`{"query":"核对注册失败可能原因","alarm_id":"alarm-demo-001","device_id":null}`。query严格字符串1..500字符，alarm_id/device_id可选严格字符串1..64，标识字符集`[A-Za-z0-9_.:-]+`，extra=forbid。所有字段均不接受类型强制转换；至少query非空。不接收session_id、版本、tenant/roles、工具列表、URL或预算字段。无实体参数为知识模式；若要求现场关联但只给alarm_code或不明确ID，先澄清，不选择任意告警。只给device_id可查询设备；同时给两个ID须校验确实关联。

沿用API Key与AuthContext、ops/auditor/admin RBAC和请求限流。功能禁用返回503；无Key401、角色不足403、schema错误422、并发/限流429；租户或权限外实体与不存在实体均不披露记录，返回NO_EVIDENCE/entity_unavailable。首版只绑定唯一ACTIVE，admin也不能通过该接口预览STAGED。

响应字段：`run_id/status/stop_reason/mode/kb_version/answer/observations/sources/limitations/trace/budget`。mode为knowledge或domain；observations含observation_ref、授权字段、source_system、recorded_at（缺失null）、retrieved_at、freshness与synthetic，不能冒充知识引用；sources沿用内容绑定引用与定位。trace仅展示本轮工具事件摘要，不展示凭据、原始工具payload或思维链。budget返回工具调用数、剩余次数与耗时；当前无模型token统计。示例事件：`{"phase":"search","tool":"search_evidence","status":"ok","elapsed_ms":12,"calls_remaining":8}`，其中耗时仅示意。

终态使用ANSWERED/NEEDS_CLARIFICATION/NO_EVIDENCE/OUT_OF_SCOPE/DEGRADED/FAILED，详细映射见架构10.4；FAILED和拒绝/澄清均不展示半成品回答或引用。明确实体的必需领域读取失败不能以知识模式掩盖。deadline、预算、审计与生命周期重验不通过立即停止；断连只记录cancelled，不增加QA终态。该接口不自动读取旧QA历史、不缓存Agent答案、不提供run读取/恢复、SSE或WS接口。2026-10-09起，仅有经单独批准的12条隔离历史模拟领域行与4条资产关系供test_legacy_stage_fb36d947ab67只读演示；其他test/原库不导入。五类权限/澄清拒绝与真实快照+当前ACTIVE引用验收见交接17.15。模拟时态数据不能作为实时现场或根因结论，关系不保证检索命中指定CSV记录。


当前并发2个工作槽位；10工具调用、90秒总预算、单工具30秒、最终3来源，原文终检计入预算。普通原因诊断仅fault_case/manual有正文证据，标题/适用标签不能独立回答；现场关联缺ID先澄清，明确ID但缺记录NO_EVIDENCE。业务快照默认internal，公开密级无权读取；输出剔除自由payload及人员ID。当前实现一次搜索，无第二轮模型规划。

请求超时返回HTTP200/FAILED/timeout，同一run_id及mode/kb_version（尚未绑定则null）、预算快照、空answer/observations/sources/trace和退出限制说明；工作实际退出前继续占槽，内部最终审计由worker完成。客户端断连无结果，不自动重发。引用结果仅反映读取校验时点，详情再次鉴权；不提供思维链、任务持久恢复或根因置信度。17.12验证时知识模式可用而实际领域五类表为空；17.15已按上述独立回执写入最小模拟行。历史API验证与临时夹具严格分开，见交接17.12/17.15。

17.13：`GET /health`新增布尔`agent_enabled`，当且仅当Agent开关、APP_ENV=test和AUTH_REQUIRED同时满足才为true；这是服务能力提示，不代表当前Key已获准，提交仍受认证/RBAC约束。工作台“只读诊断”提交上述严格JSON，仅问题及可选实体ID；不带QA会话、预览版本、租户或工具参数。服务仍同步返回，页面显示真实等待时间，结束后才显示工具事件、终态、停止代码、调用预算、知识摘录及分离的实体快照出处。

浏览器等待上限105秒为90秒服务预算留传输余量；取消通过AbortController断连，不自动重试、不表示工具已经退出。离页、更换Key、新建/切换QA会话和预览版本变化清空结果并丢弃晚响应；原文逐次经`GET /kb/evidence/{id}`重新鉴权，读取失败先清空该引用旧正文。结果不持久化，失败/拒绝/澄清不展示旧摘录、引用或观测，澄清问题单独呈现。所有返回文本使用textContent。受控UI探针不作为真实领域或质量证据，详细结果见交接17.13。

17.16后续设计，尚未实现：公共AgentRequest继续只有query/alarm_id/device_id，mode仍knowledge/domain。实体普通查询拟限定授权关联文件及修订；显式“核对关联工单原文”拟强读授权工单完整记录，例query="请核对关联工单 SIM-TT-00001 的原文"加alarm_id="SIM-ALM-000001"。查询ID不能绕过typed授权；多个候选未指定合法ID则NEEDS_CLARIFICATION，空关系/记录不完整NO_EVIDENCE，关系变化不发布事实或引用。内部策略不作为新公开mode或调用者过滤参数；可添加服务端生成的范围说明，但不改原文与引用身份。该候选无关联SOP，未来domain SOP预期related_evidence_unavailable；knowledge的不安全SOP继续missing_safety_controls。当前接口仍原query-based行为，23类场景是待执行预期，见架构10.10/交接17.16。

17.17已实现上述设计：请求仍严格query/alarm_id/device_id，mode仍knowledge/domain；响应新增retrieval_scope（失败/拒绝/澄清和knowledge为null）。实体成功时包含strategy=related_assets或linked_work_order、asset_count、可空ticket_id及服务端description；它解释检索范围，不是根因置信度或新公开工具参数。

普通domain限定授权关联资产及修订；query="请核对关联工单 SIM-TT-00001 的原文"加alarm_id="SIM-ALM-000001"实际通过，返回完整原文/CSV记录1行2/1引用/4工具。无关联资料related_evidence_unavailable，未授权工单linked_record_unavailable，多候选或不支持的选择ambiguous_work_order，截断范围relationship_scope_incomplete，关系变化relation_changed，记录缺片段record_incomplete、身份缺失linked_record_unavailable、位置缺失/歧义record_location_unavailable；这些无证据终态均不发布观测/来源/范围。domain没有关联SOP时related_evidence_unavailable；knowledge与相关不安全SOP仍missing_safety_controls。原Key/ACTIVE/manifest/撤回/正文hash及预算终态不变。19真实API和两宽度浏览器验收见交接17.17。
## 知识清单补充（P3，2026-10-10）

`GET /kb/capabilities` 使用真实鉴权角色返回知识页上传、任务读取/重试、候选预览能力，响应 `Cache-Control: no-store`。客户端能力提示不替代写接口鉴权与候选门禁。

`GET /kb/catalog` 为普通用户可用的只读清单，不调用管理清单或生成模型。支持 `query`（≤200字符）、`kind=all|faq|document`、`doc_type`、`origin=all|synthetic|non_synthetic`、`kb_version`、`page`（1～1000）、`page_size`（1～30，默认12）。默认读取唯一 ACTIVE，只有管理员能指定 STAGED；归档版本不可读。读取保留租户、部门、角色、设备、安全等级、生效日期及撤回约束，先过滤权限再搜索、归组、计数和分页。

响应包含 `kb_version/items/total/page/page_size/pages/scope_note`。FAQ一条对应一个问题；文档按来源身份、修订、来源名和类型归组，不等同原始文件总数。条目包含标题、资料属性、来源、修订、匹配可见片段数、摘要、代表片段 `evidence_id` 和 `fragments` 引用列表。查看各片段仍调用 `/kb/evidence/{evidence_id}` 重新校验权限与内容；引用ID不授予访问权。摘要和标题在返回前复核，状态变化409，验证不可用503，无认证401，限流429；不会返回部分页面或旧清单作成功结果。

清单每个集合最多读取9999条授权片段，达到10000时拒绝返回503，避免把不完整统计当作完整结果；关键词和资料属性筛选在这一有界授权快照上执行。该接口用于当前本地演示规模，超出此规模需实现存储层游标与分组分页。

页面上传沿用 `POST /admin/documents/upload`：实际管理员权限、显式 STAGED候选、稳定 `source_id`、单文件非空且≤20MB、支持md/csv/txt/docx/pdf/xlsx，`X-Filename` 英文文件名及二进制请求体。页面明确要求类型前缀。202只表示后台排队，`created=false` 为已有幂等任务；入库完成仍不代表激活。任务列表及重试沿用租户限定 `/kb/rebuild` 接口，重试失败/取消任务仍须候选门禁，全局重建重试另需全局管理员。

自2026-10-12封存运行配置改用独立入库工作区：GET /admin/ingest-workspace返回STAGED兼容状态、最近50条上传及专用Worker心跳；POST /admin/ingest-workspace/candidates只收label（非空≤80字符），生成绑定当前集合/模型/处理代码/准入包的local-work-*候选。POST /admin/ingest-workspace/uploads使用kb_version、source_id、X-Filename及二进制正文，20MB上限，文件名类型前缀及英文字符检查；不接受路径/集合/权限/身份映射。未登记/封存/配置失配候选在创建文件前拒绝。封存配置的旧/admin/documents/upload分流至此协议，非封存配置保留原兼容协议。

202现在表示接收暂存而非总是排队：新内容/身份没有具体准入时admission_status=pending、state=awaiting_review、job_id=null；不向量化/检索/生成。只有当前已批准包的完全相同文件名、字节、source_id、租户与元数据/准入映射原文复本能inherited_approved并排队，保持原权限、稳定资产、来源与修订，不能批准新正文。created=false返回已有幂等上传/任务；成功不激活。GET /admin/ingest-workspace/uploads/{id}返回脱敏任务、SHA及该候选SQL账本；POST同路径/retry仅失败/取消、STAGED且当前匹配并重新验证正文/具体准入，POST /cancel仅未终止的已排队任务；待审核无执行任务。409说明具体缺口，依赖/工作区记录不可用503，错误响应也no-store。

写操作仅default且未缩小部门/设备/密级范围的管理员；ops/auditor只读，guest、其他租户或受限凭据不能读取全局工作区。专用Worker只领取自己清单中获准、配置匹配、同协议的指定任务；max_attempts=1，人工重试增加一次。重启遗留执行任务标failed/interrupted，须人工重试；不运行旧Worker的全局恢复。公开状态不包含凭据、文件系统路径、来源审批payload或原错误堆栈。原/kb/rebuild保留历史兼容读取；本页新任务使用工作区重试边界。当前只完成入库工作区，审核/完整门禁/封存发布/回滚仍待后续流程。

## 治理页面补充（P4，2026-10-10）

GET /admin/capabilities 对已认证账号返回真实roles、tenant_id及运行/评测/凭据读取与同租户管理能力。GET /admin/ops、/overview、/overview/raw 为全局数据库历史口径，仅默认租户的ops/auditor/admin可读；GET /admin/eval仅默认租户auditor/admin可读。非默认租户返回403，不使用全局统计作为租户数据。JSON治理响应均no-store。snapshot包含UTC读取时间、全局历史范围、最近24小时数据库时钟范围及API进程重启重置说明。

评测页自2026-10-12改用版本索引：GET /admin/eval/reports读取配置reports_dir、项目reports及独立.runtime/evaluations/reports中的eval_*.json与quality_*.json，返回ACTIVE、版本状态、报告来源/摘要/运行时间/划分/范围/分母、最近20个任务及格式错误提示。GET /admin/eval/reports/{id}仅按索引中的不透明内容ID查详情，文件变化后旧ID返回404；客户端不能指定路径。仅报告显式kb_version归属，缺失或冲突单列，不从文件名追认。结构门禁单独展示原sample/chunks/sop_sources等口径，不转换为问答分母或独立质量通过；历史执行绑定也不追认。无独立冻结证明时independent_verified=false，当前缺口明确。兼容GET /admin/eval仅选择当前ACTIVE的开发回归，不能回退为别的版本最新报告。

checks中的route_ok/hit_ok/reject_ok/status_ok/body_ok/degraded仅统计明确布尔值，空分母ratio=null。分类联合检查也区分有效分母与总记录；P95仅包含有限非负数字（排除布尔值），0样本未测量。正文检查显示具体判据；新FAQ原句回归仅将〔原文注n〕还原为[n]后精确比较完整正文，不忽略空白、段落或其他文字。历史报告不补改新判据。

POST /admin/eval/plans仅default租户、未收窄访问范围的管理员可用，准备当前唯一ACTIVE中1～40条ops可读FAQ的15分钟计划，绑定账号/访问范围、版本、代码/读取配置及原文数据摘要。POST /admin/eval/jobs只接受plan_id，额外模型模式/数据路径字段返回422；执行前再校验计划字节摘要、有效期、来源、绑定和账号，失效或已执行返回409。单进程只允许一个任务，强制no_llm并使用禁止模型访问的客户端，persist=False，不加载/写入问答会话；进度queued/running/completed/failed写独立可写目录，绝不写封存候选/门禁目录。GET /admin/eval/jobs/{id}供default管理员/审计员只读追踪，应用重启后的旧running标为interrupted，不自动重跑，失败需重新准备计划。Windows原子更新采用本进程读写锁及有界共享冲突重试。执行不代表发布、生成质量或独立质量验收；样本及真值来自同库原文，仅开发自回归。问答计数/缓存及正常访问审计仍会更新，不声明数据库零写入。

GET /admin/permissions仍为同租户审计员/管理员可读，旧安全表结构错误返回503而不回退跨租户查询。POST /admin/keys仍需同租户管理员，roles仅非空guest/ops/auditor/admin列表。页面采用新增DELETE /admin/key-records/{key_id}精确记录接口，WHERE id及tenant_id及enabled=1限定，一次吊销一条；不存在/已吊销/不在租户范围返回409。原哈希前缀接口保留兼容，页面不再使用；能力提示不替代写接口逐次鉴权。

## 页面联合验收补充（P5，2026-10-10）

反馈POST /feedback仍校验消息归属、租户及ops/admin角色，拒绝可写安全审计，不能产生反馈或Bad Case。页面同一回答提交期间防重复，成功后不重复提交；成功提示仅在接口成功且会话/凭据/节点仍有效时更新。此为页面防重复，不宣称服务端跨刷新幂等。复制按钮等待剪贴板写入成功，失败显示手动选择正文提示。P5终态/HTTP故障/反馈的浏览器发送为受控夹具；真实资料覆盖回归显式use_llm=False与persist=False，不代表文档新生成或独立质量验收。

## 隔离只读业务工作区（第5项，2026-10-12）

GET /admin/capabilities增加can_read_business，仅ops/auditor/admin且有效范围含internal为真；用于导航，不替代逐次鉴权。GET /business/availability需要相同读取角色，公开当前服务是否为已准入隔离入口、仅公开独立进程状态与固定回环URL。8010未启用此领域准入，业务列表返回503，不以原库历史模拟快照兜底。私有Key及文件路径不由接口返回。

独立服务固定127.0.0.1:18021、test_legacy_stage_fb36d947ab67及已发布test-legacy-demo-r1；强API Key、Agent开、生成关。每次/business、/domain、/agent、/kb核心读取前后核对精确16条领域准入回执与账本/全表范围/具体字段、关联资产身份与权限、唯一ACTIVE及运行代码绑定，变化返回503/no-store并丢弃结果。接口只能查询、重新读取原文或提交确定的只读诊断；同步、上传、生成、发布、回滚、凭据写及业务POST均403，包括admin。

GET /business/{alarms|devices|tickets|changes}接受query（字面ID或标题子串，最长100）、after（ID游标，最长64）、limit（1～50，默认20）。返回items、next_cursor、page_size、synthetic、scope及retrieved_at，不返回跨范围total；SQL在分页前按租户、设备部门/类型、工单/变更部门及类型化关联闭合过滤。guest/public-only403；其他租户/不匹配部门空列表。GET /business/{kind}/{identity}使用相同主对象条件，无权限/不存在404；返回授权entity、共享过滤的context、synthetic、读取时间与diagnostic_target。关联最多20条并显示truncated。原payload/attributes/labels、assignee/operator/reviewed_by不公开。

GET /business/integrations只读当前租户账本，部门/设备范围受限403。六类无账本项observed=false、sync_status=never_synced、items_synced/last_synced_at=null；不称为零或成功。只展示结构错误代码，不展示cursor、原错误消息、URL或凭据；live_connection_checked=false，不调用外部系统。页面不提供同步/重试操作。

隔离浏览器Key只保留于当前标签页sessionStorage；私有文件片段登录后立刻清理URL。列表请求15秒上限，手动刷新，不自动重发；凭据变化/离开清空结果、取消等待并拒绝旧响应。详情带入明确alarm_id/device_id及关联工单问题，必须用户手动提交。/agent/diagnose沿用既有严格单轮协议、工具与时间预算；引用仍经GET /kb/evidence/{token}重新鉴权，不读取管理下载路径。正常访问审计与自有凭据生命周期写入允许，不声明数据库零写入。
# 版本审核与发布工作区（2026-10-12）

前缀`/admin/version-workspace`，成功与失败均no-store。强鉴权；全局default未缩小范围admin可写，auditor只读状态；ops、其他租户与带部门/设备/安全等级约束账号不能读全局工作区。`GET /capabilities`仅返回当前账号能力，无凭据/存储路径。

| 方法/路径 | 行为 |
| --- | --- |
| GET / | 当前库版本、执行范围与内容/质量缺口 |
| GET /versions/{version} | 实时来源身份/ACL、台账、快照、pending隔离状态、持久门禁任务与封存原件包；其他配置409 |
| GET /versions/{version}/sources/{filename} | 仅全局管理员，逐次鉴权的获准原件字节文本；64,000字符上限明确截断，二进制不假装解析 |
| GET /versions/{version}/gate | 版本归属的原门禁报告及SHA；失败结果如实保留，不代表独立质量 |
| POST /reviews | `{version}`，工程绑定审核；`new_admission=false`，不批准pending |
| POST /packages | `{review_id}`，核验来源子目录、汇总完整获准原件、封存独立PREPARED包；无增量不伪造新STAGED |
| POST /gate-jobs | `{review_id}`，有界单任务、零模型五项门禁，返回任务ID；共享发布锁，失败需手动重新审核 |
| GET /gate-jobs/{id} | queued/running/passed/blocked/failed/interrupted、0～5进度；服务重启不自动执行 |
| POST /plans | `{version, action: publish或rollback}`，仅固定自有一次性测试配置支持指针计划；原8010需要完整配置交接，409拒绝 |
| POST /executions | `{plan_id, confirmation}`，本人10分钟具体计划、完整操作字符串、唯一ACTIVE与实时准入/快照重验；事务审计、缓存与文件回执状态分别返回 |
| GET /history | 当前库最近30次已提交publish/activate/rollback；保留真实操作者与历史目标，隐藏旧payload中的路径/配置 |

不接受调用方自填批准标记、模型/DSN/目录或执行模式。原8010不切换ACTIVE、不调用DeepSeek、不批准新资料；只有其既有STAGED增量候选的门禁路径/快照可以按手动审核更新。一次性演练源于既有具体准入模拟资料，测试初始化、真实发布与回滚分别记录；不能当成整库/生产验收。
