# 阶段 4 第六批：用户批准、来源注册与隔离开发基线

日期：2026-09-27。用户在阅读人工清单后明确回复“全部认可  ，下一步”。批准范围为本批四份项目协议摘要和八题，隔离开发用途；不包含生产发布、独立冻结成绩或全部旧资料批准。

## 审核和产物

- `docs/STAGE4_PROTOCOL_APPROVAL_2026-09-27.json`：保存用户原话、对应原包 SHA-256、审核清单和批准范围。审核人标识为 user_in_current_chat，不编造真实姓名或“独立专家”；这是聊天确认记录，不是系统实名或电子签名。
- 原 `stage4-protocol-notes-r1` 和全部旧审核包保留 pending，未覆写历史提案。
- `data/candidates/stage4-protocol-approved-r1/`：另存批准包、eval-dev.json、稳定身份 source-map.json、四原文及严格元数据。该技术草稿的未知有效期写成空字符串，在实际入库中失败，保留不覆盖。
- 当前使用 **stage4-protocol-approved-r2**：正文、题目、审批绑定不变，仅未知有效期表示为 null，保存独立产物哈希。不假造开始/结束日期；稳定 ID 为 project.protocol-note.*，系统 project_rfc_summary，租户 default。
- 批准后的包仍标明正文来源为项目中文概括，不把原文“待审核”草稿备注删除后冒充正式 RFC。实际审核状态由另存的审批契约和收据体现。

## 服务和实际入库

仅启动五个现有 test 容器（MySQL/Redis/etcd/MinIO/Milvus），没有启动生产 API/Worker，也没有消费后台任务队列。结束时五个测试依赖保持运行；不关闭用户可能后续使用的服务。

使用真实 MySQL + Milvus、本地 BGE、真实 tokenizer 限长和定位旁表：

- 新 doc/FAQ 集合：test_protocol_r1_doc、test_protocol_r1_faq；没有删除或重建旧集合。
- 新版本：v4-protocol-dev-r1，保持 **STAGED**，没有激活、回滚或通过发布门禁。
- 四个资产登记在知识资产表，稳定身份在实际片段中逐个核对。资产维持 draft，数据库 reviewed_by 未填，因为聊天用户不是已存在的系统用户，不为绕过外键伪造账号；审核依据在外部绑定凭据中。
- 四份文件完成入库，共 **4 chunks**，全部账本 done、tenant=default，实际来源/稳定 ID 与审核映射一致。
- 旧 v1、v4-foundation-r3、v4-quality-r1、v4-r3-quality-control 状态不变；v1 仍 ACTIVE。

本批类型解析补充：严格元数据可以给合法显式 doc_type，从而保留已审核但没有 manual_ 前缀的文件名。非严格模式仍要求旧前缀；无效类型或与既有前缀冲突则拒绝。没有通过改名改变八题真值，也没有放宽审批/权限。

执行工具 `scripts/ingest_protocol_dev.py` 限定上述 test 集合、版本及已批准包摘要。重试只允许对应 STAGED 版本的四条同摘要 failed/0chunks 账本、正确配置绑定和两个空集合，禁止删除重建或覆盖有数据的集合。

## 故障与修订记录

1. 首轮创建两个新集合后，知识资产 reviewed_by 的真实用户外键拒绝聊天标识。事务未写入资产；修订为不填系统账号，保留真实外部批准凭据。没有添加假的 users 行。
2. 后续入库因四个文件 effective_from/to 空字符串被严格模型拒绝，实际集合0片段，账本四条failed。保留 `.runtime/test/reports/stage4-protocol-ingest.json` 原失败报告和 r1包。
3. 新r2将未知日期改null，检查空集合和同来源失败账本后重试成功。验收报告 `.runtime/test/reports/stage4-protocol-ingest-fixed.json`，passed=true、4chunks、来源身份一致、旧版本状态不变。
4. 单元夹具一度把额外的完整入库侧字段直接传给禁止额外字段的 KnowledgeMetadata 模型，1项失败；改为合法模型字段投影，不放宽生产模型。原失败全单元报告保留。

## 八题开发基线

报告 `reports/eval_v4-protocol-dev-r1_20260927-201814-a0b28449744c.json`。

固定 dataset=批准r2 eval-dev.json、版本v4-protocol-dev-r1、上述新集合，真实本地向量/BM25检索与精排；**--no-llm**，不调用DeepSeek、不写历史会话、不调阈值。

- 路由：8/8。
- 精确预期来源命中：8/8（runner最多看前5条引用，属于该8题的source Hit@5口径）。
- 预期 ANSWERED 终态：8/8；同步引用模式，没有真实生成答案质量评分。
- 拒答题、条件负例：本包没有，因此对应指标为null，不记100%。
- 平均耗时3015.8ms，首题含冷启动12452.5ms；不是P95或并发压测。

**只有4份资料、8题，题目开发已暴露且无足量干扰来源；这是入库和链路跑通基线，不是独立质量验证，也不是整体准确率提升。** 原62/75=0.827、历史174题0.833不变，不用本轮1.0替换简历原指标。报告complete=false、generation_quality_assessed=false、frozen_independence=not_claimed。

## 回归与后续

新增5项单元覆盖聊天收据内容绑定、改摘要拒绝、严格类型/旧前缀兼容、类型冲突拒绝及未知日期合法性。最终全单元报告和数量下方补记，不把真实入库/八题基线重复加到单元数。

最终结果：669项单元通过，0失败/错误/跳过，24.903秒，报告 `.runtime/test/reports/stage4-protocol-approved-fixed-unit.xml`。原1项夹具失败报告stage4-protocol-approved-final-unit.xml保留。父候选、三批旧包和两个批准技术包哈希均有效；最后只读版本核对v1仍ACTIVE，其余四个版本均STAGED。

下一步应在保留八题/真值/阈值的前提下补 **有干扰资料的开发对照** 或真实存储状态/引用读取验证；不要在这个四文档小库上调参或声称检索质量已优化。混入新资料需保留合法来源、身份和权限，不自动批准旧108份未审来源；若需新增审核范围先说明。DeepSeek生成、独立frozen和部署仍未完成，候选不发布。
