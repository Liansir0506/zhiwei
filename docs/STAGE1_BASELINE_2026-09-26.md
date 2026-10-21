# V3 首版阶段 1：开发位置核对、缺口修复与基线

日期：2026-09-26。对应桌面《RAG项目-V3首个实现版开发执行方案》阶段 1。

状态：**有遗留**。阶段 1 的代码实现、隔离完整回归和当前 dev 基线记录已完成；检索质量门槛未达，真实生成尚未验收。阶段 2～5 未开始。

## 1. 开发位置决定与两处代码差异

继续开发：`F:\heima\myprojectall\.codex\worktrees\783e\myproject`。

主仓库：`F:\heima\myproject`，包含另一处源代码、数据、模型、评测集和报告，不是纯资源目录。两处均有大量既有未提交修改。本次未整树合并、覆盖、提交或清理 Git 历史。

| 范围 | 主仓库 | 此前工作树 | 本次决定 |
|---|---|---|---|
| 问答实现 | 门面与独立 Orchestrator | 单一 QaUseCase 编排 | 在工作树做最小修复，不搬运整套编排器 |
| 流式与会话 | StreamRuntime、SessionRepository | 现有 WebSocket 线程桥接、主体隔离 | 保留两套实现；阶段 5 前核对主仓库可复用能力 |
| 企业领域、权限、任务 | 缺少工作树相应模块 | domain、security、ingest、Adapter 与隔离环境齐全 | 保留第 0～6 步基础，选工作树继续 |
| 评测 | evaluation 模块化、dev/test 口径 | r3 多真值、hard negative、版本固定及 stateless | 在工作树补 split 纪律和审计信息，不改已有真值 |
| 规则与改写 | 与工作树不同 | 已有告警码、FAQ 编号与自包含保护 | 延续工作树，补 SQL 和明确设备保护 |
| 日志 | 独立 observability 模块 | 现有指标与健康接口 | 不为阶段 1 搬运日志架构 |
| 场景文件 | 有 | 有，SHA256 相同 | 本次未改场景阈值或同步另一套代码 |

主仓库可见基线为 `4040273`；工作树基线为 `bed0304`。共享 Git 元数据不意味着未提交代码自动同步，不能用任一 HEAD 代表当前全部改动。

## 2. 执行环境

- 工作目录：上述 F 盘工作树；解释器：该目录 `.venv\Scripts\python.exe`。
- 显式 `APP_ENV_FILE=.env.test`，通过 `validate_isolated_environment(settings, 'test')` 校验。
- MySQL：`rag_kb_test`、宿主端口 13306；Redis：DB 15、端口 16379。
- Milvus：端口 29530，`test_doc_collection` / `test_faq_collection`。
- 数据及运行材料：工作树 `.runtime/test/`；模型从主仓库 `models/` 只读加载。
- 只启动 test 的 MySQL、Redis、Milvus、etcd、MinIO；未启动或重建开发 API，也未动开发数据卷。
- test 版本：`v1` 为 ACTIVE；`v4-foundation-r3` 为 STAGED。评测显式固定 r3，不切换 ACTIVE。
- test 未配置 `LLM_API_KEY`；没有新增外部 DeepSeek 生成调用。

## 3. 本次行为修复

1. 规则优先处理索取／执行破坏性 SQL 和明确数据修改请求，即使同时带告警码也不进入检索、FAQ 或生成。正常重启／下电知识咨询和 SQL 含义咨询不一概拦截。
2. 同步和流式按本次实际加载的历史做意图判断；`persist=False` 不读取或继承已有历史。明确设备名、告警码、FAQ 编号不拼接上一轮的危险请求。
3. 覆盖检查开启时空文本不再判为有效证据；英文内容词匹配忽略大小写，保持原覆盖阈值不变。
4. 同步统一出口清空拒答引用，覆盖 FAQ、缓存和降级；流式低分返回空引用，structured lookup 流式也经过覆盖检查。

这是规则与一致性修复，不是完整 SQL 解析器、所有恶意输入的证明或生成事实性保障。证据覆盖仍是词汇启发式，不替代阶段 2 的统一证据契约与状态。

## 4. 意图模型：已核实的根因与修复

本次确认的代码问题：

- `_get_state()` 在超时计时范围外加载模型，冷启动可能长时间阻塞请求。
- 每请求创建线程池并用 `with` 退出，超时后仍 `shutdown(wait=True)`，并非真正的有界等待。
- 原预热调用 `_decide('预热')`，该问句规则低分，不触发灰区模型，不能证明 BERT 已预热。
- 原单一失败状态不区分模型路径和设备，错误路径失败可能影响后续正确路径。

改为单一执行槽和共享 worker：加载及推理都计入等待时限；超时立刻降级，任务仍执行时新请求返回 busy，不无限排队。状态按路径／设备隔离，明确显示 not_loaded、loading、loaded、warming、ready、failed 与错误类型。预热显式加载并实际预测，`/health` 增加 `intent_model` 状态。

底层 Torch 任务无法强制中断；本次保证的是请求等待和提交数量有界，不宣称线程已取消或进程退出绝不会等待。非 test 自动后台预热保留，test 不启动自动 Torch 预热线程。真实模型可用性测试使用 2s 推理限额，不把它冒充配置默认 200ms 的性能验收。

历史模型文件／路径问题见旧交接；本轮 test 模型路径实际有效。不能仅凭本次实现推断每次历史 fallback 的唯一原因。

## 5. 评测工具与冻结集纪律

- `--split` 默认 dev，支持 regression、frozen、test、all。
- frozen/test 或包含冻结题的 all 必须显式 `--final-acceptance`；在访问存储前拒绝未经选择的冻结集。
- 无 split 的旧诊断集必须明确选择 regression 或 all，不静默当 dev 使用。
- `--no-llm` 对所有类别严格不生成；此模式拒答指标只验证确定性护栏，不代表 DeepSeek 生成质量。
- 报告带 UUID 后缀 run_id、数据集 SHA256、选定 split、知识版本、LLM 模式、配置哈希、Git 提交／dirty 状态、实际代码指纹；最终版本在运行开始记录配置／代码，记录运行期间代码是否变化。
- 保留旧逐例字段、分类统计、多真值和 hard negative；未改题目、真值或阈值。报告不包含 API Key 或数据库密码。
- 旧 51 条 frozen 已参与历史开发，只能作为历史回归，不能再称未见验收。本次不运行 frozen 或 all。

## 6. 回归记录

| 运行 | 结果 | 保存材料 |
|---|---|---|
| 修改前单元测试 | 160 passed | 本轮执行输出 |
| 首次新增单元回归 | 199 passed、1 failed；SQL 含义咨询误拦截，已修复 | `.runtime/test/reports/stage1-unit.xml` |
| 首次完整回归 | 241 passed、1 skipped、1 failed；旧限流测试时序假设不成立 | `.runtime/test/reports/stage1-full.xml` |
| 中间补测 | 201 passed、1 failed；报告完成标记所在层级不一致，已修复 | `.runtime/test/reports/stage1-confirm.xml` |
| 最终完整回归 | **246 passed、1 skipped、exit=0**，130.28s | `.runtime/test/reports/stage1-final.xml` |

跳过项为需要 DeepSeek 密钥的语义缓存测试。其余既有端到端测试在无密钥下通过，也不能因此声称真实生成验收通过。当前依赖仍有 13 条弃用告警，本阶段未升级依赖。

限流诊断实测五次完整 API 调用约 2047、1109、1219、1297、1172ms，计数进入不同秒窗口，Redis 可用。修复测试而非服务实现：固定限流模块窗口，真实 Redis 允许两次计数，再由真实 HTTP 验证第三次 429；唯一测试键结束删除。

新增验收涵盖 HTTP/WebSocket 危险 SQL、真实 Redis 历史读取、真实 BERT 预热／灰区路由／健康状态，及同步／流式低分、空证据、拒答清引用和 stateless 纪律。

## 7. r3 诊断基线与未解决事项

数据集：`F:\heima\myproject\eval_sets\v4-r3-tiered.json`；SHA256：`CC0D210AFA913E30D4B5782DE58CA728FC1680AB8366B79BC2A2BA72FD94374F`。未修改文件。

第一轮 dev 诊断：89 条，路由准确率 0.944、Hit@5 0.827、确定性拒答准确率 1.000、hard negative 违例率 0.108。报告：`reports/eval_v4-foundation-r3_20260926-202450-d0537930343a.json`。当时报告完成标记在 run 内，之后仅完善报告结构／元数据与测试；保留原文件，不覆盖。

最终固定代码 dev 报告：`reports/eval_v4-foundation-r3_20260926-203420-b0dc8b3bd40f.json`。原阈值未变，**passed=false、complete=false、原生 exit=2**；no_llm 下 complete=false 表示没有生成验收。

| 指标 | 分母及结果 |
|---|---|
| 路由准确率 | 84/89 = 0.944 |
| Hit@5（并非证据 Recall） | 62/75 = 0.827，低于 0.85 |
| 确定性拒答准确率 | 9/9 = 1.000，不代表生成拒答质量 |
| hard negative 违例率 | 7/65 = 0.108 |
| 普通检索 Hit@5 | 20/30 = 0.667 |
| 平均请求耗时 | 2805.4ms，含模型冷加载及缓存状态，不作为容量或性能提升证明 |

配置 SHA256：`971edcb92b1a8993d5d63eb1130fcfe3294212595ad7ba15076e465f8f5be1ad`；代码 SHA256：`93670d6cd0fbca32747a40da18a9d942425bc21342ebd6c452d3fe01fe35e74b`。报告记录 `code_changed_during_run=false`。

意图来源：rule 73、rule_default 15、model_fallback 1（M15）。因此本轮 dev 报告不支持“模型通道零降级”声明；模型已加载、已预热、能经灰区路由的证据来自专门真实模型测试，不能混成所有查询默认 200ms 均成功的结论。

可复跑命令（从本工作树运行）：

```powershell
$env:APP_ENV_FILE = '.env.test'
.\.venv\Scripts\python.exe -B -m pytest tests --basetemp=.runtime/test/stage1-next-tmp -o cache_dir=.runtime/test/stage1-next-cache
.\.venv\Scripts\python.exe -B scripts/run_eval.py --dataset F:/heima/myproject/eval_sets/v4-r3-tiered.json --split dev --kb-version v4-foundation-r3 --no-llm
```

重复测试应选新临时目录／报告名称，避免覆盖本次失败和最终通过材料。对已达到完成条件的第 7 步代码项记录通过，但不把 Hit@5 未达门槛改记为通过。

仍有遗留：

1. Hit@5 未达到 0.85，保留失败，不调阈值。后续阶段 4 先补区分度资料与人工评测，再做检索对照；不在阶段 1 提前调融合或精排参数。
2. 无编号 FAQ、缺设备条件的告警查询和同质 SOP 干扰仍有 bad case；J07 路由期望与实际安全拒答的历史口径仍不一致。不为了结果好看修改真值。
3. 旧 frozen 不独立，新独立冻结验收集待阶段 4。
4. DeepSeek 生成、真实语义缓存、完整生成引用支持性仍待配置密钥后验收。
5. 主仓库流式／会话／日志能力尚未迁入工作树；阶段 5 按兼容范围再评估，不默认整套重构。

## 8. 历史后续记录（不作为当前执行指令）

2026-09-27历史补充：实验/知识卡二十条范围已复核，累计AI检查三十条；当时四条“知识卡依据是什么”仍需确认目的，尚未复核五十九条。该批全单元280 passed，原0.827保留，不代表生成/冻结集/新指标验收。对应逐批Markdown已清理，最新状态见 `docs/HANDOFF_2026-10-07.md`。

历史复核：十条SOP的AI语义标注处理完成；当时test_doc_collection缺部门、安全级别和生命周期等七字段，完整索引权限未验收、等价别名未批准，未进行迁移。该批全单元254 passed，旧0.827保留。对应逐批Markdown已清理，不将当时的待做状态当作现在的执行顺序。

历史安排：先校准评测尺子，再做阶段2。该批完成89条dev离线审计、75条正例的265个证据项草案、主题/正文分组与失败证据探针；发现10条SOP的20个hard negative正文冲突。该批全部单元236 passed，候选待语义审核，没有批准别名或新frozen，不代表效果改善。校准过程Markdown已清理；问题依据继续见 `docs/RETRIEVAL_FAILURE_DIAGNOSIS_2026-09-26.md`，当前顺序见 `docs/DEVELOPMENT_PLAN_2026-10-07.md`。

补充诊断（2026-09-26）：13 条 dev 未命中已逐阶段跟踪，首次丢失为未召回 2、候选截断 3、精排退出前五 8；全部最终来源与基线复现一致。发现 200 份 SOP 只有 20 种正文、7 条失败 SOP 的 14 个 hard negative 与目标正文等价，以及标题块/FAQ 问题块抢占实际步骤证据。保留原 0.827 与原真值，不据此改算通过。新增诊断脚本和 6 条测试，当前全部单元测试 207 passed；并非重新运行完整集成套件。具体证据和执行顺序见 `docs/RETRIEVAL_FAILURE_DIAGNOSIS_2026-09-26.md`。

最终 dev 报告已核对，桌面方案同步本轮状态；可按用户指令进入阶段 2 的原文定位和统一证据接口。阶段 1 的代码回归通过不等同于整个 RAG 效果达标，所有遗留继续保留。本次未重建或发布开发 API 镜像，test 存储栈保持运行以便下一阶段回归。
