# DATA_GOVERNANCE — 数据来源与治理

## 定位与声明

本项目是通信运维 AI Copilot 的企业级场景验证，不是客户生产系统。领域数据均为确定性生成的模拟数据；公开资料只保存来源索引，并据此编写项目自己的知识卡，不下载、不重新分发第三方规范或文档全文。

## 数据版本

- 当前原ACTIVE：`v4-foundation-r3`，生成器 `enterprise-dataset-v1`，随机种子 `20260919`。旧索引未携带完整注册身份，原入口完整引用仍未就绪；人工准入与定位准备见下方2026-10-07审核方案。
- 旧 `v3` 原始文件保留在原位置，并生成 `manifests/legacy_v3.json` 冻结清单；版本切换后只改变 ACTIVE/ARCHIVED 状态，不物理删除。
- `v4-foundation`、`r1`、`r2` 是未激活的工程候选。它们保留问题定位证据，不进入线上检索。
- 每次生成使用独立目录；生成器拒绝覆盖非空版本目录。

## 来源分级

| `origin_type` | 含义 | 使用方式 |
|---|---|---|
| `public_reference_derived` | 依据公开入口自编的知识卡 | 保存链接和引用编号，不保存规范原文 |
| `experiment` | 自建实验过程的模拟记录 | 明确 `SIMULATED`、生成器和随机种子 |
| `synthetic` | 模拟 FAQ、SOP、工单和领域快照 | 使用 `SIM-*` 标识，不冒充生产数据 |

来源目录包含 17 个官方入口，主要包括 3GPP 规范入口、ETSI 标准检索、Open5GS、free5GC 和 srsRAN 官方文档。示例入口：

- 3GPP TS 23.501：<https://portal.3gpp.org/desktopmodules/Specifications/SpecificationDetails.aspx?specificationId=3144>
- ETSI Standards：<https://www.etsi.org/standards/>
- Open5GS Documentation：<https://open5gs.org/open5gs/docs/>
- free5GC Quick Setup：<https://free5gc.org/guide/quick-setup/>
- srsRAN Project Documentation：<https://docs.srsran.com/projects/project/en/latest/>

## r3 数据规模

| 数据 | 数量 |
|---|---:|
| 自编参考文档 / 章节 | 20 / 300 |
| 模拟实验场景 | 40 |
| SOP 文档 / 步骤 chunks | 200 / 2000 |
| FAQ | 1000 |
| 模拟设备 / 告警 / 工单 | 500 / 5000 / 750 |
| 模拟变更 / 监控观测 | 350 / 1500 |
| 原始知识文件 / 预测 chunks | 276 / 5300 |

工单按每 50 单一个文件分片，避免单个 CPU 向量化批次过大；SOP 按步骤切分。数据量主要来自不同业务对象和流程步骤，不通过复制或同义改写制造重复文档。

## 元数据与生命周期

每个知识文件的 `meta.json` 至少记录：

- `tenant_id`、`dept_id`、`visibility`、`role_scope`、`security_level`；
- `status`、`effective_from`、`effective_to`、`source_version`；
- `origin_type`、`synthetic`、`generator_version`、`generation_seed`；
- `review_status`、`license_status`、`evidence_refs`。

检索在 Milvus 查询前下推租户、权限、ACTIVE 状态和有效期条件。完整来源与授权信息同时保存在版本 manifest 中，兼容旧 Collection 滚动升级。

## 质量门禁

`scripts/validate_enterprise_dataset.py` 在向量化前检查：

1. manifest 文件集合和 SHA-256；
2. 治理元数据完整性和来源引用；
3. 手机号、身份证等 PII 模式；
4. 解析切分后的精确重复；
5. CMDB、告警、工单、变更、审核人的引用完整性；
6. 与冻结评测集的查询文本泄漏；
7. 第三方全文是否被重新分发；
8. 各类数据和 chunks 是否达到目标区间。

向量化后的线上门禁再次检查告警召回、SOP 步骤连续性、元数据完备率、chunk 内容重复和脱敏结果。只有门禁报告存在且通过的版本才能激活。

## 复现

```powershell
python scripts/generate_enterprise_dataset.py `
  --output-root data --legacy-root data/raw `
  --version v4-foundation-r3 --seed 20260919

python scripts/validate_enterprise_dataset.py `
  --output-root data --version v4-foundation-r3 --eval-dir eval_sets
```

同一生成器版本、版本名和随机种子会产生相同manifest与文件哈希。历史生成器治理报告位于`data/manifests/v4-foundation-r3/quality_report.json`，不代表当前独立质量验收。

## 2026-10-07：原ACTIVE身份与原文审核方案

当前可审核包为[`legacy-identity-review-r2`](../data/candidates/legacy-identity-review-r2/review-summary.json)，协议`legacy-identity-review-v2`。它冻结原ACTIVE的276个知识文件、原metadata/manifest/领域注册快照及5,300条片段追溯。仅复制本次审核输入；原数据库、原向量、旧版本和ACTIVE保持原状态。每份正文、完整FAQ答案、可用租户/访问字段及入库账本均核对；位置来自实际Markdown行或CSV记录，不由文件名猜测。

| 审核组 | 文件数 | 实际ACTIVE片段 | 身份处理 |
|---|---:|---:|---|
| 自编参考卡manual | 20 | 910 | 保留原MySQL已有asset/source身份，仍待真实人工准入 |
| 模拟实验guide | 40 | 440 | 分配新opaque身份草案，未注册 |
| 模拟SOP | 200 | 2,200 | 分配新opaque身份草案，未注册 |
| 模拟工单fault_case | 15 | 750 | 文件资产草案与行内工单ID分别保留，未注册 |
| 模拟FAQ | 1 | 1,000 | 文件资产草案与行内FAQ ID分别保留，未注册 |

5,300条均唯一对应批准前的当前原文，文件/账本/manifest哈希和片段数量一致。原库全版本5,477行及原向量指纹保持步骤5值。**这证明内容可追溯，不证明协议事实、故障根因、许可或生产适用性。** 历史原始来源有276个文件，不能把5,300个片段当5,300份独立来源；历史预测分块数量与实际索引分开使用。

原MySQL仅有20条领域资产记录，`reviewed_by=sim-user-quality-gate`为模拟生成声明，不能当真实人工批准。其中`3gpp-23501/23502/23503`各被两份不同参考卡共享：01/18、02/19、03/20。r2保留这些既有身份、独立asset与正文哈希，将三组共同撤回语义列为待核对，不擅自拆分或合并。其余256份分配UUID草案；草案ID并非从标题/文件名推断的权威身份，首次注册后才固定。r1未纳入领域注册快照，已被r2取代且未导入，保留初次审计记录。

额外领域权限只读核对发现20条记录的dept_id=NULL，而raw metadata声明public；tenant/security/visibility/role_scope一致。部门差异需在候选投影前统一，不能默认为已确认的部门注册；完整差异见`.runtime/test/reports/identity-20261007-164438/domain-permission-audit.json`。旧向量只有部分权限字段，本次匹配不能证明所有新增ABAC边界已治理。

审核从[`source-proposals.json`](../data/candidates/legacy-identity-review-r2/source-proposals.json)开始，逐份核对原文、来源系统/稳定ID、租户与适用范围、模拟声明、许可声明和撤回分组；[`row-trace.json`](../data/candidates/legacy-identity-review-r2/row-trace.json)可查实际chunk ID→文件/位置/内容哈希。`source-map.pending.json`及`raw/meta.json`全部pending/draft，不能导入；既有auto_validated或模拟审核记录不会转为人工批准。元数据访问范围、提案、正文、映射和原注册快照均受绑定检查，变更需重新审核。

CSV原文位置现在区分逻辑记录号与物理行范围，覆盖引号内多行。FAQ匹配问题和答案；工单由当前解析/切分器重建后匹配。重复记录标ambiguous，内容不一致不给位置。入库的`record_locations`已覆盖CSV，证据读取会加载FAQ定位；定位身份同时绑定FAQ问题与答案，旧FAQ定位若只绑定问题须重新记录，不静默沿用。

本轮真实存储验证包含三个真实旧库样本的pending引用拒绝（无Key401、admin404、伪装admin的ops404）、CSV FAQ/工单完整入库→位置保存→证据重读、答案篡改失效与跨租户拒绝；另回归Markdown/PDF/DOCX和已批准协议引用。CSV及三格式入库测试使用受控向量，不是新嵌入/检索质量评测。详情与结果见交接第17节。

复核包绑定，不写库、不调用模型：

```powershell
$env:APP_ENV_FILE='.env.test'
.\.venv\Scripts\python.exe -m scripts.build_legacy_identity_review `
  --validate-package data/candidates/legacy-identity-review-r2 `
  --raw-dir F:/heima/myproject/data/raw/v4-foundation-r3
```

`--require-approved`对原冻结包仍应失败，它保留批准前全部pending状态，真实批准另记凭据并形成独立投影。候选采用新STAGED版本、完整schema、strict metadata、reviewed source map和record locations，重新跑门禁再按实际授权发布。不得重建原ACTIVE、将pending并行迁移集合启用，或直接采用旧质量报告。原库整体引用闭环与可信质量验收仍未完成；BFD/NETCONF、Lab资料及独立frozen边界继续保留。

### 分组批准与本地候选投影

当前可审阅入口为[分组审核页](../data/candidates/legacy-group-review-r2/review.html)，完整原文与身份/访问/来源声明逐份展开；[review-plan.json](../data/candidates/legacy-group-review-r2/review-plan.json)绑定原r2冻结包、完整领域权限快照、20处部门差异及三组共同撤回关系。分组r1页面被r2替代，未据r1批准或投影。页面及原提案显示批准前pending快照，实际决定以独立凭据为准。

用户2026-10-07明确回复“批准256份模拟资料，仅限隔离本地演示（推荐）”。[真实批准凭据](../data/candidates/legacy-group-review-r2.receipt.approved.json)绑定审核计划/产物哈希和明确的256个文件名，保留原问题及确认项标识。批准仅覆盖模拟FAQ1、工单15、实验40、SOP200文件，共4,390实际片段；20份自编参考卡/910片段、部门冲突和三组撤回关系继续pending。未扩大到原ACTIVE、生产操作、许可结论、独立frozen、BFD/NETCONF或Lab资料；没有再请求此前已耗尽的付费预算。

[legacy-approved-demo-r1](../data/candidates/legacy-approved-demo-r1/candidate-intent.json)已生成本地待入库候选：256个原字节文件、完整已审source map、严格metadata、原来源修订号及真实批准凭据。保留原租户/部门/角色/可见性/生效时间/模拟/许可声明，只补明确批准的稳定source/asset身份、doc_type和局部准入状态。`status=active`表示来源生命周期可用于候选，与知识库版本ACTIVE不同；候选意图版本`test-legacy-demo-r1`、目标test、意图STAGED。**尚未创建数据库版本、写入向量或发布。** 256条身份尚未落库注册，不影响原20条领域记录。

`scripts/prepare_legacy_projection.py`不连接数据库、不调用模型。批准凭据验证是内容绑定声明检查，不认证审核人；真实用户聊天确认是本次凭据依据。它拒绝pending/自审、范围扩大、未知/重复文件、原文或权限篡改、未确认的部门差异/共同撤回关系，并拒绝覆盖或嵌套审核输出；部分批准不会自动批准共享来源的其他文件。后续读取候选时必须复核：

```powershell
$env:APP_ENV_FILE='.env.test'
.\.venv\Scripts\python.exe -m scripts.prepare_legacy_projection `
  --package data/candidates/legacy-identity-review-r2 `
  --review data/candidates/legacy-group-review-r2 `
  --validate-projection data/candidates/legacy-approved-demo-r1
```

相关单元61通过（新增投影21项，审批仅为合成测试夹具，不伪造真实来源批准）。实际候选256文件逐份重解析、原字节/权限保留及4,390个无歧义位置全部通过；实际Edge审核页5组/276份/FAQ1,000记录展开验证通过。当前20条领域权限快照与原库仍一致，原ACTIVE仍v4-foundation-r3。原始报告`.runtime/test/reports/projection-20261007-173336/`；无DB/向量写入、无付费调用。本步不是嵌入性能或检索质量验收。

上述是17.4准备时点的冻结投影记录，执行进度见下文；不可变意图与批准产物不因实际入库而回写。不得直接用原集合、默认test集合或旧质量报告替代新候选验证；发布原入口仍须单独满足门禁和实际授权。

### 独立STAGED执行结果（交接17.5）

256份已批准模拟资料已在独立`test_legacy_stage_fb36d947ab67`及同名`_doc`/`_faq`完整schema集合注册并严格入库，`test-legacy-demo-r1`保持STAGED、不发布。实际4,390片段均为本地真实BGE-M3新嵌入，完整原文/FAQ问答、身份/权限、无歧义位置及manifest逐项核对通过。40份实验guide存储类型为manual，不包含pending的20份参考卡。五项新普通门禁、四组鉴权引用、真实HTTP/Edge桌面和手机预览通过；815单元及3项相关真实存储集成通过。本步付费请求0、独立frozen仍0，无独立质量结论。

发现并修复文件默认network_tech覆盖CSV记录分类：记录有效标签优先，缺失才采用文件默认；权限声明仍严格覆盖。仅此新STAGED的400条FAQ标签修正，原文/ID/修订/权限/真实向量逐行验证不变；新门禁与引用检查重新通过。原报告保留，最终报告`.runtime/test/reports/legacy-staged-20261007-181025/delivery-summary.json`，修正结果和新门禁分别在`network-label-repair/`及`recheck-20261007-111003/`。不可变候选原字节、批准凭据和哈希保持不变；领域身份仅新库注册，不改原20条记录。

17.5历史时点SOP单片段可读、整来源展开受修订号相等校验阻塞，自动审批审核拒绝代码修改。该历史阻塞已由2026-10-08用户明确“允许”解除，具体授权与执行见交接17.6及`.runtime/test/reports/sop-revision-20261008/authorization.json`。现只改为非空来源修订，保留KB/租户/来源/修订/asset/权限/生命周期精确过滤和权威账本数量检查；200份/2,200片段完整展开及11条鉴权引用、822单元与1真实存储集成通过。旧SOP缺审批/回滚，200份仍missing_safety_controls，不自动修改或批准正文。候选STAGED、不发布、无新嵌入/付费，知识向量未变；20参考卡/部门差异/共同撤回、BFD/NETCONF/Lab继续pending。下一项准备限定隔离本地演示的发布/撤回/回滚方案，发布仍需实际授权；原库完整引用尚未恢复，原存储本步未访问。

### 限定首次发布准备（交接17.7）

已生成readiness/review.html及内容绑定release-plan.json，位于.runtime/test/reports/legacy-release-readiness-20261008。仅读复核256份批准投影、实际4,390原文/向量/权限指纹、单一STAGED、无任务/Key/撤回和publication门禁快照，与持久报告一致；真实候选未激活/撤回。61相关单元+1真实临时沙箱集成通过，沙箱发布/撤回/回滚只对自身fixture，不当实际候选结果。

拟仅在test_legacy_stage_fb36d947ab67激活test-legacy-demo-r1；当前无上一ACTIVE/ARCHIVED，首次发布没有现成版本回滚，异常停止临时127.0.0.1演示入口并清理Key，不直接改状态/删撤回账本。旧SOP缺审批/回滚仍流程拒绝，20参考卡等pending和独立frozen=0保持；不扩批准正文、原库/共享ACTIVE或生产范围。此前用户批准是准入和SOP代码修正，本次首次激活仍待明确确认；批准后即时重验计划/报告/快照及权限，验收后关闭临时服务/Key，付费0、候选不重嵌入。

### 限定隔离首次发布实际批准与执行（交接17.8）

用户对审核页明确“批准”，授权记录绑定原release-plan SHA448782b14ba1663e0b20b55c9fe95a28ca932d0fa5dc781a851fd8c953882e54，位于.runtime/test/reports/legacy-release-executed-20261008。旧准备报告中的STAGED/未授权为冻结历史时点，最新以该执行报告为准。执行前重新核对整个计划、冻结批准投影、持久门禁和实际快照，真实认证admin一次激活test_legacy_stage_fb36d947ab67内test-legacy-demo-r1成为唯一ACTIVE；不涉及原库/共享test或真实来源撤回。

四类原文ops/admin200、无Key401、外租户404；发布审计认证actor/批准快照、真实缓存失效及两宽度Edge通过。原文/向量/身份/权限、共享test默认知识均未变，77相关单元通过；SOP实际证据搜索仍NO_EVIDENCE/missing_safety_controls，不补写声明或放宽流程护栏。临时服务/三把Key/浏览器profile已清理，无常驻入口；独立ACTIVE持久，无上一版本可回滚，关闭入口不称回滚。本步付费0，候选无新passage嵌入，无独立质量结论。

此ACTIVE仍只表示此前256模拟资料在隔离本地演示的发布状态，不扩大原生产范围、20参考卡/部门差异/共同撤回、BFD/NETCONF/Lab pending或内容准入。原库存储未访问/未启动。下一项固化同一已发布隔离库的运行配置与启动/停止交接，不需重问已有批准、不重复激活，不覆盖原环境；新增内容/付费生成另有明确范围。

### 已发布隔离库的运行固化（交接17.9）

同一已获准256文件/4,390片段及唯一ACTIVE的独立运行入口已完成：start/stop默认127.0.0.1:18011，ops强制鉴权和后端同步/SSE/WS引用模式，生成关闭。883项全量单元及真实鉴权引用/流程拒绝/浏览器/启停Key更换通过；候选与共享test默认知识指纹不变，未再发布、重嵌入或改冻结正文。报告legacy-demo-runtime-20261008，运行方法见ENVIRONMENT_ISOLATION；验证后入口/Key/profile清理，ACTIVE保留。此运行配置不扩大既有资料用途或pending准入，独立frozen仍0。下一项修复流式追问上下文一致性并核对旧审核声明的页面呈现，默认引用模式开发回归。

### 追问上下文与审核声明呈现（交接17.10）

已统一同步/SSE/WS追问上下文，并将当前版本/权限访问校验与正文历史审核声明分开展示；来源原文、修订、准入范围与ACTIVE不变。932全量单元及真实引用模式跨通道/澄清/权限/拒绝/Edge通过，实际存储指纹不变；旧声明提示另以controlled_ui_probe验证，不当实际内容审核。验证后服务/临时Key/profile清理，付费0。20参考卡等pending与独立frozen=0仍未解锁；下一项只读Agent闭环规划不扩大资料范围，已暴露追问题继续dev/regression。

### 只读诊断领域候选（17.14，pending）

[agent-domain-review-r1审核页](../data/candidates/agent-domain-review-r1/review.html)包含12条原样历史模拟领域/身份行及4条待审typed知识关系，目标仅test_legacy_stage_fb36d947ab67本地演示。原快照1090处设备关联冲突另留审计，不整包同步；候选关系/租户/部门/用户/时间检查通过。candidate.json、artifact-hashes、source-audit和planned_not_executed验收方案绑定源快照、原行及已批准工单CSV记录/资产；生成不代表准入。

此前256知识文件批准不包含新增领域行或这4条关系。候选review_status=pending不得导入；明确确认后须另记内容绑定批准凭据，保留原候选不回写批准。既有default租户及已批准知识资产仅验证不更新，新行事务insert-only、冲突拒绝；不写原20参考资产/关系，不重嵌入/重新发布或扩大其他pending。SQL业务时间明确采用UTC，模拟快照/观测不称实时现场或独立根因评测；域数据不在原知识publication快照中，新增领域准入与指纹必须单独记录。详情见交接17.14。


### 领域候选准入执行（17.15）

[准入回执](../data/candidates/agent-domain-review-r1.receipt.approved.json)是用户对17.14同一候选的独立明确批准；候选源包仍维持冻结pending状态，回执限制于16条、隔离本地演示与指定数据库。它不是其它pending材料的通行准入。执行脚本逐项验证回执/候选/清单与隔离目标，先重核原ACTIVE、既有租户/批准知识资产身份与权限及空表/无冲突，然后以SERIALIZABLE insert-only单事务写12领域/身份行和4 typed关系；相同事务在audit_log记录domain_admission。已提交的admission.json用于禁止重复导入。

最终test库新增清单与精确数量、领域读取、API/权限结果、完整知识/向量/manifest/撤回/共享存储对照和停机清理记录见[17.15报告](../.runtime/test/reports/agent-domain-executed-20261009/summary.json)及[交接](HANDOFF_2026-10-07.md)。获批领域行留存于此隔离数据库；原20参考卡及其关联、部门差异、共同撤回、Lab/BFD/NETCONF和独立frozen状态未改变。知识检索未保证命中关系指定的CSV行，不将快照或摘录解释成实时事实/根因。

### 关联检索设计与开发基线（17.16）

[方案包](../.runtime/test/reports/agent-relation-plan-20261009/review.html)仅使用17.15获批领域候选/回执、已批准CSV及历史最终报告；不是新内容候选或准入决定。typed资产关系只证明文件关联，工单记录范围须再来自本轮可见work_order边，不能批准同文件其它工单身份或用任意query ID扩范围。缓存/召回/终检均须保留授权范围，关系与权限变化停止发布。

历史两次实体请求6引用关联文件内0只是开发关系对齐基线；50条源记录及12纯测试不证明在线索引或根因质量。23类案例均planned_not_executed，已暴露资料继续dev/regression，独立frozen=0。下一项实施在既有批准隔离库及临时受控夹具验证；不重复16条准入、不整快照同步、不变更原文/向量/ACL/ACTIVE，不扩大其它pending。17.16未连接数据库或调用模型。

### 关联检索实现与收口（17.17）

本轮只实现既有批准范围的读取约束，不新增资料准入。文件边仅限定文件/修订，记录级原文必须来自本轮授权work_order边；任意query身份、同文件未关联工单、多候选/截断列表不会扩大授权。缓存/软重试和最终原文均保留范围，变化清掉事实及引用。完整工单按原内容绑定token/位置账本核对，不合成正文或伪造定位，不把片段/摘要冒充完整原文。

23类开发预期逐类绑定验收证据，受控向量故障和1个浏览器传输探针标注，已暴露模拟资料继续dev/regression，独立frozen0。19真实API/9临时MySQL/1080单元与Edge通过，批准16行和知识/向量/权限/ACTIVE/共享test相同；0重复导入/新嵌入/发布/付费生成，API/Key/临时Edge清理、13容器停。17.16历史方案清单/原输入保持冻结，6个实现文件代码漂移单列在17.17delivery-summary，不能改旧哈希将设计复核伪称当前代码验证。其它pending不因读取功能完成而获准，根因与独立质量仍未证明。

### 原运行库完整引用准入准备（17.18）

原库276文件/5300 ACTIVE片段已实际重新唯一定位，20原资产及权限/共同撤回关系与冻结r2一致，旧全版本5477向量/正文和SQL账本未变。待审方案[review.html](../.runtime/test/reports/original-citation-readiness-20261009-r2/review.html)绑定完整proposal/live audit/旧审核包与隔离receipt；此前未带-r2的报告有三处简称表名清单错误，保留历史，以本次r2为准。实际manifest/撤回表存在且空，定位表不存在，没有执行迁移。

本次一次待审：256模拟资料的原rag_kb本地STAGED范围，20项目自编参考卡身份/正文/范围，原空部门映射及三组共同撤回。20登记维持SQL NULL、既有asset/source/checksum/kb_version/reviewed_by与20关系不变；新候选索引dept_id=""，准入另记审计；其它ACL声明保留。拟仅new local-citation-r1 STAGED及两新集合，256缺失身份insert-only、新真实BGE/严格source map/定位/门禁/QA引用预览。旧原文和5300计数不等于新tokenizer分块数量；原Agent跨版本关联不自动迁移。全部此目标仍pending，准入/发布均未授权；不回写旧批准范围或将sim-user审核人当真人批准。

49离线相关测试及Edge1280/390审核页通过，审批夹具不是真实批准、页面不是在线引用验收。原库/ACTIVE/向量未写、0模型/付费，13容器停止。真正候选入库必须有本次内容绑定明确准入；发布/切原入口在实测后审核具体结果。此次本地自编摘要演示批准不会证明规范事实、外部许可、根因或生产质量；BFD/NETCONF/Lab pending、200不安全SOP拒绝和独立frozen0继续。

### 原库STAGED完整引用验收（17.19）

用户“确认，接着干”明确批准17.18集中问题中的276文件原rag_kb本地STAGED范围、20参考卡正文/身份/原空部门和三组共同撤回。外部receipt SHA0539ec3f2181a2ee38ee881aa046431d24c97c9eab93370ac3f8ebda31a1db5d、source projection manifest SHA3069d2d17de8201cf7ef63a86e76cd05f37608fa6dc34e8dd54c833e2ee96224；原pending封包不回写、旧隔离批准不扩大。仅本地新候选，外部事实/许可和生产质量不宣称已核验；BFD/NETCONF/Lab及其它待审范围继续pending。

实际只insert256缺失知识身份与准入审计，旧20注册/NULL部门/身份/checksum/kb_version/reviewed_by和20typed关系不更新。新256登记reviewed_by=NULL，聊天准入由绑定receipt/audit佐证，不伪造users审核人；UTC业务时间明确保存。new local-citation-r1及两完整schema集合始终STAGED，新定位仅5900候选片段、不回填旧原文。原276字节不变；参考卡按实际450token拆分使旧910变1510，其他4390不变，DOC4900/FAQ1000。全部严格metadata/reviewed source map、真实本地1024D BGE与可绑定定位验证通过。

五项新普通门禁/1120单元/4临时SQL事务、五组鉴权与引用篡改拒绝、20卡实际HTTP重读、200整个SOP、Edge1280/390通过；页面回答为受控展示探针，HTTP原文为真实，不称付费生成或独立质量。SOP仍200份missing_safety_controls，旧/共享/隔离存储与16域行不变，临时Keys/API/Edge清理，13容器停。原ACTIVE v4-foundation-r3及8010未切；实际发布/运行绑定另按具体方案审核，原20资产旧kb_version不代表新版本Agent关系已经迁移。

### 原入口发布准备与回滚限制（17.20）

“下一步”与“那怎么解决”授权方案准备和核验故障修复，不等于17.19范围外的原ACTIVE发布/8010启用。readiness的release-plan和review仅待审方案，publication_authorized=false；原276准入回执及所有历史封包保留。旧ACTIVE缺完整schema/manifest，标准rollback不能作为发布安全保证，不伪造旧门禁或手动恢复状态。当前建议保持STAGED，先明确合格回滚基线迁移范围；若明确接受无标准回滚的本地首发，需要另审具体方案及提交后停止入口/Key而指针可能已改变的限制。

本轮仅原库Strong读取与SQL快照，无准入、入库、发布、Key或业务写入。六集合恢复探针只重试明确106恢复错误且总180秒；永久错误失败，发布完整快照不因探针通过而省略。修复SDK同URI共享alias后分生命周期重建客户端。91相关测试及真实门禁报告/manifest/5900行快照、原文和旧SQL/向量比对通过，20旧登记/关系不迁移，200旧SOP安全拒绝保持，模型/付费0，独立frozen0。

### 原库合格回滚基线（17.21）

17.21已建成合格回滚基线local-rollback-v4-r1（5300）与后续local-release-r1（5900），两版仍STAGED，11200条原文/真实旧向量/位置逐条复核；新276×2账本与manifest，0新资产准入、0付费。1178全量单元和完整真实数据临时库标准激活/回滚、五组引用/权限、默认检索、SOP拒绝及篡改/撤回负例通过。原唯一ACTIVE仍v4-foundation-r3，8010未启用，13容器停；原资料/6受保护集合/旧SQL及共享和隔离知识/16领域行保持不变。 仅沿用17.19明确已批准的276身份/正文与原5300结构，新版本绑定ID/逐向量SHA/原文位置可追溯，不新增准入或伪造审核人。各276账本/manifest、两版门禁与实时发布核对完成。临时库实际标准rollback通过，临时撤回不写原库；三组共同撤回关系仍依来源身份保留。下一项仅待审两步发布与8010方案：先激活并验收合格基线，再激活新版；此后标准rollback可回基线。第一次从旧版到基线仍不能标准回滚旧版，提交后失败须停入口/自有Key并报告实际指针。原激活与入口未获批准，不能把本轮“解决下一步”当发布批准，不重复准入/导入。 实际URI为http://127.0.0.1:19530，旧localhost描述有误，历史封包不回写。

### 原库本地发布范围已执行（17.22）

用户在已展示两步发布/8010范围后指示“下一步”，本项按该具体范围执行；authorization记录真实指令及上下文，既有pending封包与资料准入回执不回写。17.22原库两步本地发布与8010入口已完成：local-release-r1为唯一ACTIVE，local-rollback-v4-r1为ARCHIVED且标准rollback已在原库真实成功；v4-foundation-r3归档，旧local-citation-r1仍STAGED。1211全量单元、102相关子集、实际默认检索/SSE、五组原文/权限/SOP拒绝、Edge1280/390真实页面提问和停止→重启通过。 生成和Agent关闭、强鉴权、付费0；原276资料/身份/ACL/20关系/4任务/账本/manifest/位置及八集合字段/向量不变，仅预期三版本状态/发布字段/自动updated_at、接口审计和自有Key/问答日志变化。原五存储与宿主8010保持运行，原Docker API/Worker/mock和test五组件停；只保留一个自有ops Key。 首次版本差异检查误判自动updated_at后标准rollback基线成功，再通过标准rollback已归档新版恢复目标；没有改门禁、直接SQL恢复状态或伪造旧schema/manifest。运行Key只留ops，临时admin/foreign及前次自有Key已删除。本发布任务已收口，不重复准入、入库或发布。启动/停止用scripts/start_original_local.ps1与scripts/stop_original_local.ps1；停止只关闭自有宿主入口并撤销自有Key，存储保留。其它pending与独立frozen0不扩大，可信独立质量仍待新来源和独立核对；原跨版本Agent关系未迁移。

### 入口故障修复不改变资料与发布状态（17.23）

17.23已修复“页面能开但问答报错”：旧Docker API复起占用localhost/IPv6，旧集合/模型URI与新版manifest不符导致503，而127.0.0.1的新入口正常。已停止原Docker API/Worker/mock，宿主同一服务同时绑定127.0.0.1和::1；localhost、127.0.0.1及[::1]三个地址的真实检索/原文/SSE均通过，localhost页面1280/390实测通过，1217全量单元通过。 原ops Key保持不变，local-release-r1仍唯一ACTIVE，回滚基线仍ARCHIVED；本次未发布、入库或改原文/向量/权限/原.env.runtime，SQL保护快照完全相同，生成和Agent关闭、付费0。 不为消除503而改manifest、放松绑定、重入库或发新版本；修复冲突监听和生命周期。原Key与认证角色保留，原v4不成为合格回滚目标，17.22实际标准回滚历史证明保留。

### 本地凭据恢复与流程证据边界（17.24）

17.24使用现有自有ops Key生成被忽略的私有登录文件；不新增用户、Key、权限或资料准入。文件不进入HTTP静态目录、候选包或共享报告，后端强鉴权保留。用户截图原题已通过认证，但流程证据不足，实际NO_EVIDENCE；不为消除拒答改已批准原文或虚构审批/回滚字段。原ACTIVE/ARCHIVED状态、所有其他pending、独立frozen0和历史审批范围不扩大，生成/Agent关闭、付费0。

### 用户实际问答质量诊断（17.25，未修复）

17.25用户截图触发的真实问答质量诊断：当前认证和服务可用，但推荐/检索内容存在未修复问题。真实复现11个侧栏推荐：8个NO_EVIDENCE，3个返回片段；BBU掉电返回NGAP连接中断等工单，乒乓切换返回NR切换失败率升高，不能把ANSWERED标签当作答对。当前1000条FAQ抽样是带模拟案例编号的模板内容，1条库内原句实际faq_exact直出成功。推荐问题仍写死且未绑定当前库，generation_enabled=false且模型地址为disabled，通用15%词汇覆盖不足以判定故障主题；流程保护也会拒绝缺审批/回滚的既有SOP。17.22～17.24只证明限定链路/治理/鉴权恢复，未证明自然问题质量。下一开发重点转为推荐与知识范围一致、错题检索保护、FAQ/文档内容质量和带引用的生成回答；本轮仅诊断，不声称已修复，不改库/发布/模型设置，付费0。

### 问答质量代码修复与剩余范围（17.26）

17.26已部署无需付费的问答质量修复：推荐改从当前版本/权限可读FAQ加载，并标识模拟案例；FAQ原句先做Strong精确匹配，再退到语义检索；明确主题在精排前和引用前检查，阻止乒乓切换引用NR切换失败率、BBU掉电引用NGAP工单。12个动态推荐真实SSE均faq_exact且精确对应来源，两道原错题现在NO_EVIDENCE/零引用；实际浏览器点击动态推荐和原文、1280/390布局通过。1231全量/114相关单元通过。当前仍关闭生成，恢复DeepSeek的具体配置方案已准备但待追加付费授权；缺失主题资料仍待补齐，不把改推荐或拒答宣称为原题已可回答。无入库/发布/原文或向量变更，Agent关闭、付费0。

### 17.27 2026-10-09 获准文档生成恢复与实际验收

17.27已恢复获准的DeepSeek文档生成，保留17.26推荐绑定当前可读FAQ、原句精确匹配和明确主题保护。AMF真实浏览器SSE、SMF同步生成均通过，答案包含结论、排查步骤和引用，原文可读；本轮验证额度使用3/4（含1次TLS连接失败），生成成功2次，后续用户主动提问生成已获准。乒乓切换仍缺对应资料，明确NO_EVIDENCE且不调用模型。1239全量、71相关单元通过。最终路由标签及历史记录已修复，使用真实答案回放验证，无新增模型请求。原.env.runtime、ACTIVE/ARCHIVED、知识原文及向量未改，Agent关闭；整体资料覆盖和独立质量验收仍未完成。

本轮用户明确批准恢复生成及最多4次真实验证，随后批准payload-review.html中AMF/SMF题目和检索片段发送至https://api.deepseek.com/v1/chat/completions，并允许后续主动提问的必要会话上下文、同一本地演示库可读片段用于生成。首次外发被自动审批拦截；具体数据/目标授权补齐后同一验证已通过，不再处于待批准状态。批准记录仅存公共配置和摘要，不记录API Key、数据库凭据或权限配置。

生成配置通过单独generation-approval.json覆盖已冻结基础配置的LLM字段；基础host-profile和原.env.runtime不修改。启动检查基础配置、原配置全文摘要及提供商/型号/凭据摘要，漂移时拒绝启动。验证预算在每次真实HTTP尝试前保留额度，包括失败与重试；当前used=3、limit=4、mode=interactive_approved，表示验证结束后开放已批准的用户手动生成，不是后续日常问答永久只有1次余额。

第一次TLS连接超时没有生成答案，失败日志保留。连接超时改为10秒，流式网络异常转换为既有LLMError以走降级处理；已配置deepseek-flash按官方参数关闭thinking，避免面向用户的普通排查回答只等待推理。官方依据：https://api-docs.deepseek.com/api/create-chat-completion/ 和 https://api-docs.deepseek.com/guides/thinking_mode/ 。实际AMF流式和SMF同步各有5条引用，未降级；原乒乓题无证据、零引用、计数不变。

浏览器实际请求报告与路由显示回放报告分开保留：前者是真实生成；后者重用该真实答案检查最终retrieval标签和历史记录、1280/390布局以及引用查看，没有追加模型请求。全部截图已人工查看。browser.json旧模板paid_llm_calls=0字段不代表本轮免费；实际计数以paid-validation-count.json及本次汇总为准。

失败重启清理曾撤销自有Key，随后恢复服务并生成新私有登录页；不声称Key全程保持，也不猜测未记录的启动失败原因。目前原五存储与宿主入口运行，旧API/Worker/mock停止。本轮没有入库、发布、重写模拟原文或重建向量。历史17.26报告继续记录当时关闭生成的状态，不覆盖历史验收。

推荐和FAQ原句验证沿用17.26的12条真实SSE结果；本轮两道已暴露开发题只算生成链路回归，不算独立质量评测。BFD/NETCONF等其余pending、独立frozen=0及旧SOP缺安全依据边界保留。模拟资料仅限隔离本地演示，不能宣称原静态11题均可回答或生产知识质量已合格。
