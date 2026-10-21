# 文档索引

2026-10-07已清理46份重复旧计划、旧交接和逐批过程Markdown，清理时保留25份Markdown及接口/示例/审批JSON。新步骤6仅新增一份必要的首版总验收；保留业务数据、知识候选和原始测试报告。

## 开发入口：先读这两份

- [当前交接17.69](HANDOFF_2026-10-07.md)：2026-10-15 AMF流程训练已接入业务工作区：16单元、39真实HTTP、28项Edge检查通过，八场景及实际导出/权限/沙箱/退出清理验收完成；资料与预算保持，付费/入库/发布均0。[业务登录](../.runtime/readonly-workspace/open-workspace.html) → 流程训练；[实际验收](../.runtime/test/reports/amf-workspace-integration-20261015/review.html)，计划5.47。

- [当前交接17.68](HANDOFF_2026-10-07.md)：2026-10-15依赖与入口恢复完成：8010、业务18021、指南18024/18025均已恢复，10个既有存储健康；26单元、68真实HTTP、20/20开发主题逐条引用、4项检索Edge及启动状态页检查通过，四入口3轮采样及后台观察通过。11组历史数据/配置指纹匹配，本轮只读验收前后一致；付费、入库和发布均0。计划5.46，见[实际恢复验收](../.runtime/test/reports/entry-recovery-20261015/review.html)。

- [当前交接17.67](HANDOFF_2026-10-07.md)：AMF完整流程本地样板已可演练，8场景/31行为/18Edge通过，计划5.45。
- [本地演练入口](../data/candidates/amf-sop-exercise-r1/exercise.html)与[实际离线验收](../.runtime/test/reports/amf-sop-exercise-20261015/review.html)：不依赖后台，来源和真实权限界限保留；新SOP仍pending未入库。原Milvus19530本次不可连接，在线状态未复验，下项先恢复依赖与入口。

- [当前交接17.66](HANDOFF_2026-10-07.md)：新版独立只读预览/109取证项/200步骤对齐可使用，229单元及20/20开发题每条来源正确，主题混入修正，计划5.44。
- [私有新版入口](../.runtime/sop-record-preview/open-preview.html)与[最终真实验收](../.runtime/test/reports/sop-record-preview-topic-20261014/review.html)：选择“SOP支持性对齐”查看新增项与原文。当前控制器sop_record_topic_runtime，后台无窗口，已实际停启/轮换Key/预热检索，原8010保持、新版STAGED未发布，付费0。

- [当前交接17.65](HANDOFF_2026-10-07.md)：具体批准后20份draft-r2已在新隔离库完成STAGED，180真实向量/109合同，106单元、67HTTP和20/20公开开发题检索引用通过，计划5.43。
- [实际新STAGED验收报告](../.runtime/test/reports/sop-record-staged-20261014/review.html)：4临时Key清理，原库/业务/旧指南保持，付费0；普通门禁未过，生成/Agent关闭。当前18024仍为r1，新版只读预览与支持性对齐为下一项。

- [当前交接17.64](HANDOFF_2026-10-07.md)：20份draft-r2具体准入及独立新目标配置/候选构建器准备完成，106相关单元和8无窗口Edge通过，计划5.42。
- [正确全文审核页](../.runtime/test/reports/sop-record-admission-20261014/full-content-review.html)与[准备验收报告](../.runtime/test/reports/sop-record-admission-20261014/review.html)：原库/业务/指南及Key/PID保持，准入/入库/新STAGED/付费0，待具体准入决定。旧封存HTML换行问题另出正确视图，精确计划及回执未改。

- [当前交接17.63](HANDOFF_2026-10-07.md)：按训练项目定位，业务列表/详情/诊断引用去掉反复模拟标注，后台来源与权限保留；第一步20份待审修订新增49项具体记录，60→109，180个本地分块/位置通过。计划5.41。
- [第一步验收报告](../.runtime/test/reports/training-step1-20261014/review.html)、[修订稿集中审核](../data/candidates/sop-record-revision-r2/review.html)与[业务入口](../.runtime/readonly-workspace/open-workspace.html)：26单元、15真实HTTP、10无窗口Edge及新隐藏启动通过，原8010未改，付费0。修订未准入/入库；业务后续启动用start_business_workspace.ps1，接真实外部系统不列本训练项目必做项。

- [当前交接17.62](HANDOFF_2026-10-07.md)：SOP/指南逐步支持性对齐已接入同一指南预览，20家族/200步骤、60取证项、支持与相反条件及现场缺项可查；95单元、19真实HTTP、10Edge、382行引用/100片段通过，计划5.40。
- [逐项对齐验收报告](../.runtime/test/reports/sop-support-alignment-20261014/review.html)与[私有指南入口](../.runtime/sop-guide-preview/open-preview.html)：选择“SOP支持性对齐”。仅指南入口升级并重发本机技术凭据，旧封存代码与报告不改；原8010/只读角色、Key、PID与知识保持，付费0、STAGED未发布。新入口控制器为sop_guide_support_runtime。

- [当前交接17.61](HANDOFF_2026-10-07.md)：20指南候选有真实管理员只读预览，8项实时读取完整性、45单元、24网络HTTP、9无窗口Edge及自有进程停启通过；普通发布门禁仍失败，候选保持STAGED。计划5.39。
- [指南预览实际验收](../.runtime/test/reports/sop-guide-preview-20261014/review.html)与[本机私有预览入口](../.runtime/sop-guide-preview/open-preview.html)：127.0.0.1:18024，目录/检索/原文与缺口；仅新隔离库技术管理员，原角色/Key/PID不变、付费0。预览入口独立启停，不切8010，不声明完整SOP、独立质量或长期可用。

- [当前交接17.60](HANDOFF_2026-10-07.md)：用户20指南准确准入已执行，独立test-sop-evidence-r1保持STAGED，20身份/160真实向量与位置、67HTTP/20主题检索通过；普通发布门禁未过，未发布/切8010。计划5.38。
- [实际STAGED验收报告](../.runtime/test/reports/sop-evidence-staged-20261014/review.html)：真实聊天凭据、存储/权限/位置、20公开开发检索、门禁失败范围及4临时Key清理。真实HTTP处理器用TestClient核验新SQL/Milvus，未启动网络预览页或生成模型。

- [当前交接17.59](HANDOFF_2026-10-07.md)：20份具体正文/身份/内部范围与全新隔离STAGED目标准备完成，31单元/8实际页面/160分块定位；准入决定仍pending，未写存储。计划5.37。
- [本次准确准入审核页](../data/candidates/sop-evidence-admission-review-r1/review.html)与[准备验收报告](../.runtime/test/reports/sop-evidence-admission-20261014/review.html)：仅20新指南，拟test_sop_evidence_r1数据库/独立集合，不复制旧资料，不切8010/发布或新增DeepSeek外发；真实批准另记凭据，不改旧pending包。

- [当前交接17.58](HANDOFF_2026-10-07.md)：20家族SOP落实为具体取证/支持及相反分支的待审guide；142单元、120模拟、9项实际页面检查通过，不批准现场操作或独立质量。计划位置5.36。
- [20份新正文集中审核页](../data/candidates/sop-evidence-drafts-r1/review.html)与[本步验收报告](../.runtime/test/reports/sop-evidence-drafts-20261014/review.html)：200原件身份绑定、60具体记录组、pending正文/身份/default租户ops-admin内部范围提案。今日依赖/入口恢复并重发本机凭据，使用本机登录页重新进入；未导入新内容或发布新版本。

- [当前交接17.57](HANDOFF_2026-10-07.md)：由助手拟定的公开开发自检与两处规则修复；43/43组件、19/19HTTP、125单元及9项实际页面检查。8010隐藏重载，版本/配置/Key保持，付费0；不是独立人工评测。计划位置5.35。
- [开发自检报告](../.runtime/test/reports/development-selfcheck-20261013/review.html)：原文逐片段核对、真实页面截图、完整失败历史和保护快照。后续哈希筛查使用 `python -m scripts.screen_question_holdout_v2`，新增曝光阻断，题集仍未冻结。

- [当前交接17.56](HANDOFF_2026-10-07.md)：用户确认沿用现有语料做新问题留出；A/B使用匿名职责占位，不编造身份或审核凭据。允许来源/正文/主题复用，问题/模板曝光仍阻断；不是来源独立。计划位置5.34。
- [新问题留出交接页](../data/candidates/known-corpus-question-holdout-r1/review.html)：306份精确现库资产、空白哈希模板、A/B任务单、真值证据要求与冻结/评分条件；真实题目和真值仍0。
- [第四步工程准备报告](../.runtime/test/reports/question-holdout-step4-20261013/review.html)：56单元、8项无窗口Edge、5类实际清单元数据控制、真实CLI空模板阻断及3次有界健康观察；零模型，不是独立质量分数。填好的哈希副本使用screen_question_holdout离线入口，不能向开发助手提交问句/答案。

- [最新交接17.55](HANDOFF_2026-10-07.md)：剩余第三步完成公开曝光登记、来源/正文/主题/模板隔离和空白人工交接；独立题/真值/冻结仍为0，下一项需要新适用来源与独立命题/审核人员。计划位置5.33。
- [独立质量准备报告](../.runtime/test/reports/independent-quality-step3-20261013/review.html)：24单元、8项无窗口Edge、真实空模板/旧冻结题/公开来源卡复用阻断，保护快照保持；零模型，不是独立质量分数。
- [曝光清单与人工交接](../data/candidates/independent-quality-preparation-r2/review.html)：2287输入、8588问句出现记录/1380归一问句；SHA、空模板、真值/保管和冻结流程，完整曝光/语义独立仍待人工核验。

- [最新交接17.54](HANDOFF_2026-10-07.md)：剩余第二步完成200份SOP逐字支持性审查、20家族修订规格和8份pending设备记录单；完整现场SOP与型号手册仍缺，独立分母0。计划位置5.32；原入口未切换。
- [剩余第二步报告](../.runtime/test/reports/sop-device-support-step2-20261013/review.html)：19单元、200份真实存储SOP组装/分块核对、8份FAQ绑定、8项无窗口Edge及3次短窗口观察；零模型请求、原快照保持，不等于独立质量或完整SOP通过。
- [SOP/设备集中待审页](../data/candidates/sop-device-support-review-r2/review.html)：逐文件SHA/行号、20组相反分支、8份具体待审正文与16项设备资料清单；未准入/未发布/未新增外发。

- [执行计划](DEVELOPMENT_PLAN_2026-10-07.md)：5.24为六项完善顺序，5.25～5.30记录工程实现与限定验收。第6项发布/回滚真实闭环限临时隔离库，8010未切换；当前上传增量不足完整门禁，SOP/设备依据、独立质量和原完整配置交接仍有缺口。
> **当前质量状态（17.27）：** 17.27已恢复获准的DeepSeek文档生成，保留17.26推荐绑定当前可读FAQ、原句精确匹配和明确主题保护。AMF真实浏览器SSE、SMF同步生成均通过，答案包含结论、排查步骤和引用，原文可读；本轮验证额度使用3/4（含1次TLS连接失败），生成成功2次，后续用户主动提问生成已获准。乒乓切换仍缺对应资料，明确NO_EVIDENCE且不调用模型。1239全量、71相关单元通过。最终路由标签及历史记录已修复，使用真实答案回放验证，无新增模型请求。原.env.runtime、ACTIVE/ARCHIVED、知识原文及向量未改，Agent关闭；整体资料覆盖和独立质量验收仍未完成。

- [开发交接](HANDOFF_2026-10-07.md)：最新17.53补齐原8010当前/上一完整配置准备、持久检查/重新核验与具体恢复阶段。17.52的真实发布/回滚仍仅隔离库；原8010未新切换。修正Docker冷启动空版本误判，恢复原入口与批准16条资料的只读入口；Worker绑定及旧pending隔离保持。SOP/设备依据、独立质量和合格新候选实际切换继续有缺口，恢复时Key轮换，需重新打开私有登录页。
- [剩余第一步：完整配置交接准备](../.runtime/test/reports/runtime-handoff-step1-20261013/review.html)：70相关单元、14真实接口及8真实Edge检查；原/上一完整配置各自核验、不扩大权限或准入，不实际切换8010、不付费，具体存活与保护范围见报告。
- [第6项验收报告](../.runtime/test/reports/version-workspace-p2-20261012/review.html)：真实隔离SQL/Milvus与Edge发布/回滚、原入口审核/完整封包/失败门禁及拒绝切换、后台窗口和存活检查；零付费、新准入0，仍有具体质量缺口。
- [第5项验收报告](../.runtime/test/reports/readonly-business-p2-20261012/review.html)：149单元、37真实HTTP、23真实Edge、实际隔离MySQL故障/恢复及有界存活观察；零付费调用，无新增准入或业务写操作。
- [关联检索方案](../.runtime/test/reports/agent-relation-plan-20261009/review.html)：关联文件范围、授权工单精确原文、缓存/终检/安全约束和历史基线；设计完成不代表运行功能或质量验收已通过。
- [首版总验收](FIRST_RELEASE_ACCEPTANCE_2026-10-07.md)：工程与隔离演示限定通过，原库完整引用和可信质量仍未完成；去重结果、截图、复核条件与限制。

## 仍需使用的设计与运行文档

| 文档 | 用途 |
|---|---|
| [PROJECT_SPEC](PROJECT_SPEC.md) | 业务范围与原始行为约定 |
| [ARCHITECTURE](ARCHITECTURE.md) | 架构、模块职责与调用链 |
| [API_CONTRACT](API_CONTRACT.md) | API契约与权限边界 |
| [DATA_MODEL](DATA_MODEL.md) | 数据库、向量字段及缓存约定 |
| [DATA_GOVERNANCE](DATA_GOVERNANCE.md) | 来源属性、元数据与版本治理 |
| [ENVIRONMENT_ISOLATION](ENVIRONMENT_ISOLATION.md) | dev/test/demo运行与隔离方式 |
| [ACCEPTANCE_CHECKLIST](ACCEPTANCE_CHECKLIST.md) | 原始验收检查项，结合新计划使用 |
| [详细设计参考](telecom-ops-rag-design-outline.md) | 分块、检索、缓存等细节，代码注释仍有引用 |

部分设计材料中的模型名、阈值、性能目标和部署范围属于历史设计，并非当前实测结论；以代码配置、新计划和有日期的验收记录为准。

## 保留的关键验收与诊断

| 文档 | 保留原因 |
|---|---|
| [当前旧dev基线](STAGE1_BASELINE_2026-09-26.md) | Hit@5=0.827的分母、条件和原始报告位置 |
| [检索失败诊断](RETRIEVAL_FAILURE_DIAGNOSIS_2026-09-26.md) | 模板同质性、召回/截断/精排失败依据 |
| [评测集缺陷诊断](STEP7D_DIAGNOSIS.md) | 同源改写、互斥标签与开发泄漏；现有代码注释引用 |
| [证据与浏览器验收](STAGE2_RUNTIME_ACCEPTANCE_2026-09-27.md) | 追问、原文定位、权限和浏览器集成范围 |
| [发布工程总体验收](STAGE3_OVERALL_ACCEPTANCE_2026-09-27.md) | 发布/撤回/安全回滚与P1/P2修复结果 |
| [协议开发库基线](STAGE4_APPROVED_PROTOCOL_BASELINE_2026-09-27.md) | 实际批准包、集合、版本和入库结果 |
| [独立评测现状](STAGE4_HOLDOUT_READINESS_AUDIT_2026-09-29.md) | 已暴露题与独立frozen=0的依据 |
| [页面首版验收](STAGE5_FIRST_ACCEPTANCE_2026-09-29.md) | SSE/UI权限与失败状态验证 |
| [真实DeepSeek整链](STAGE5_SSE_REAL_CHAIN_2026-09-28.md) | 单次真实模型→SSE→浏览器→引用重读证据 |

以上是历史实测记录，不是当前运行状态，也不是新的开发顺序；过程文件已清理，原始产物仍在 `.runtime/test/reports/`、`reports/`、`eval_sets/` 和 `data/candidates/`。

## 审核关联材料：不可当普通过程日志删除

- [四摘要/八题审核清单](STAGE4_HUMAN_REVIEW_CHECKLIST_2026-09-27.md)及 [批准凭据](STAGE4_PROTOCOL_APPROVAL_2026-09-27.json)：候选包内approval-receipt仍指向该清单。
- [BFD/NETCONF来源清单](STAGE4_BFD_NETCONF_SOURCE_REVIEW_2026-09-29.md)及 [文字认可凭据](STAGE4_BFD_NETCONF_CONTENT_ACK_2026-09-29.json)：绑定当前两张卡，不能据清理改变pending状态。
- [事实/许可核对](STAGE4_BFD_NETCONF_FACT_LICENSE_CHECK_2026-09-29.md)、[近邻筛查](STAGE4_BFD_NETCONF_OVERLAP_SCREEN_2026-09-29.md)、[本地演示范围](STAGE4_LOCAL_DEMO_EVAL_GATE_2026-09-29.md)：保留具体待确认项与用户用途边界，减少接手重复检查。

其他非Markdown材料：`openapi/mock-enterprise.yaml` 是模拟系统接口契约，`examples/domain_snapshot.example.json` 是领域导入示例；`baseline/`只保留旧API/指标JSON快照作为历史参考，不当作当前基线。

## 后续文档约定

开发进度优先更新交接和计划；有新结论时更新对应模块总报告。只有必要的阶段总验收才新增文档，不再每轮产生first-batch/second-batch等重复过程Markdown。

本轮收口结果：[17.17交付摘要](../.runtime/test/reports/agent-relation-implemented-20261009/delivery-summary.json)、[23类开发验收证据](../.runtime/test/reports/agent-relation-implemented-20261009/acceptance-results.json)。17.16方案继续作为冻结历史设计，不改写其中planned状态。

- [旧11题来源补齐审核草案](../data/candidates/coverage-source-review-r2/review.html)：官方来源、中文摘要、设备范围与缺口；全部pending，尚未入库，不能作为在线覆盖通过。

- [三项完整FAQ审核候选](../data/candidates/protocol-faq-review-r1/review.html)：完整答案、适用限制、来源身份/范围及12条待审反例；尚未入库或执行问答评测。
