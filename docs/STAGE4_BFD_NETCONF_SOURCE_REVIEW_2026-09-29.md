# 阶段 4：BFD / NETCONF 待审来源卡

日期：2026-09-29。用户本轮仅同意选用 BFD、NETCONF 作为下一批来源方向；这不是对下列摘要、版权/许可、独立性或后续入库的批准。两卡均为项目 AI 改写摘要，不是 RFC 原文副本。待审包：`data/candidates/stage4-holdout-source-review-r1/source-review.json`；其 `approval.status` 均为 `pending`，绑定文件 SHA-256 与元数据审核摘要。包内无 `cases`、无查询、无指标。

## 来源与逐条核对点

| 卡片 | 官方依据 | 本地待核对行 | 人工应确认 |
| --- | --- | --- | --- |
| `raw/bfd_detection_note.md` | [RFC 5880 §6.8.4](https://www.rfc-editor.org/rfc/rfc5880.html#section-6.8.4)、[勘误 5205](https://errata.rfc-editor.org/search/?rfc_number=5880&presentation=records) | 5–7 | 异步模式使用**对端** Detect Mult；协商发送间隔取本端 Required Min RX 与对端 Desired Min TX 较大值；不同方向可不同。第 6 行不把 RFC 正文的 Init/Up 条件误当成对 Down 状态的排除结论；勘误目前是“留待文档更新”，不是已验证勘误。Demand/Echo 另行处理。 |
| `raw/netconf_lock_candidate_note.md` | [RFC 6241 §7.5](https://www.rfc-editor.org/rfc/rfc6241.html#section-7.5)、[§8.3.1](https://www.rfc-editor.org/rfc/rfc6241.html#section-8.3.1)、[§8.3.4.1](https://www.rfc-editor.org/rfc/rfc6241.html#section-8.3.4.1) | 5–7 | 锁目标、其他会话/非 NETCONF 操作的限制、锁释放条件是否准确；`:candidate` 必须由设备宣告，`<commit>` 不能被当成所有设备的基本能力。 |

## 人工审核清单（仅内容表述获得用户认可）

用户回复“内容认可”，已用 `docs/STAGE4_BFD_NETCONF_CONTENT_ACK_2026-09-29.json` 绑定当前包及两张卡的 SHA-256。此记录仅表示认可当前文字表述，不是独立专家事实核验、许可授权或入库批准；任一卡内容或包元数据更改后，应重新确认。

用途补充：用户随后明确答复“仅本地面试演示”。据此按本地候选制定 `docs/STAGE4_LOCAL_DEMO_EVAL_GATE_2026-09-29.md`；不推定允许公开仓库、线上演示或生产使用，也不因此将 `license_review` 改为通过。

- [ ] 对照上表 RFC 原文检查逐句事实、术语、条件及误导性省略；如修改卡片，重新计算文件 SHA-256、审核绑定，再重新审核。
- [ ] 检查是否与旧 r3、v3 和现有知识库在正文、主题、同义链上重复；此前仅做关键词排除，**不能**据此宣称是独立来源或冻结评测。
- [ ] 核对 RFC 引用方式与 IETF Trust 许可约束；确认项目改写摘要可用于预定展示及分发方式，不复制规范长段原文。[IETF Trust FAQ](https://trustee.ietf.org/about/faq/)说明 RFC 的衍生作品权利通常不自动授予标准制定流程外使用；本项目只能暂按“自编事实摘要”待审，不能由程序自动判定许可已通过。两份 RFC 均早于 2015 年，具体适用条款须按发布时版本核对。
- [ ] 明确实际用途和权限元数据；此包只有来源提案 ID，没有正式注册、租户权限、索引或版本发布。
- [ ] 指定与提案者不同的审核人，留下具体审核证据与日期；审批须绑定最终版本，不接受口头“方向同意”替代内容审核。
- [ ] 来源通过后另行设计独立评测协议和题目；在冻结前完成与开发集的语义去重、权限检查和隔离，严禁用开发调参结果冒充 holdout。

## 本轮审核准备记录

后续逐条官方事实与许可证据核查见 `docs/STAGE4_BFD_NETCONF_FACT_LICENSE_CHECK_2026-09-29.md`。核查为 AI 辅助，不替代独立人工或许可决定；来源状态保持 pending。

对 `v4-quality-r1-final/raw`、旧 `eval_sets/v3.json`、主仓库历史 `v4-r3-tiered.json` 和现有四张协议卡做定向术语搜索：BFD、NETCONF、双向转发检测、检测倍数、候选配置、配置锁、配置提交等未命中旧材料。旧题中有 `ALM-PTP-LOCK`，但那是时钟锁定告警，不是 NETCONF 配置数据存储锁；两者不能按英文 `LOCK` 简单合并。旧模拟故障材料存在链路/会话/配置等宽泛主题，故**只能报告未发现上述精确概念碰撞，不能认定语义独立**。仍需人工看旧文档正文、同义表述及未来出题方式。

RFC 勘误复核发现 BFD §6.8.4 的 [勘误 5205](https://errata.rfc-editor.org/search/?rfc_number=5880&presentation=records) 为“留待文档更新”，涉及原已 Down 状态下的超时诊断码。已据此修订 BFD 待审卡并重新绑定 SHA-256；未擅自把该勘误标为已验证或把卡片标为已批准。NETCONF 卡引用的锁与候选配置段落仍按 [RFC 6241](https://www.rfc-editor.org/rfc/rfc6241.html) 待人工逐句复核。

许可方面，[IETF Trust FAQ](https://trustee.ietf.org/about/faq/) 与 [TLP](https://trustee.ietf.org/documents/trust-legal-provisions/tlp-5/)提示：标准制定流程外的修改/衍生作品使用权不能当然推定；而两份 RFC 发布于 2010/2011 年，须按当时适用版本判断。本项目卡片未复制整段原文，但“自编事实摘要”是否符合预定展示/分发用途仍待有权限的人审核，不能据此宣布许可通过。本段只是风险提示，不是法律结论。

只读自检：`\.venv\Scripts\python.exe -m scripts.validate_holdout_source_review`。该命令检验包结构、哈希、待审状态和用户内容确认凭据的版本绑定，**不能**代替事实/许可/独立性人工审核；校验脚本也不会生成题目、入库或发布。

验证记录：新增 5 项单元测试通过；设置 `APP_ENV_FILE=.env.test` 后，增补后的全量 `tests/unit` 回归通过。首次未指定测试环境时，旧的本地 tokenizer 测试尝试读取容器路径 `/app/models/bge-m3` 而失败；与本轮来源卡无关。

当前闸门：两卡的**当前文字表述已获用户认可**，但来源仍为 `approval.status=pending`。下一步仍须完成事实、许可、语义独立性、用途和权限审核；之后才能另行讨论题目和入库，且仍需单独授权。
