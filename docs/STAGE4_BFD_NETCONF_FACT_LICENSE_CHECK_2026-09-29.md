# BFD / NETCONF 来源事实与许可证据核查（AI 辅助，非最终审批）

日期：2026-09-29。核查对象为 `data/candidates/stage4-holdout-source-review-r1/source-review.json`，SHA-256 `322bd50578a252fbc6a4f8ae6bce0e422af4e4fdda1e3c5633213f274b9d85f1`；两张卡的内容确认凭据只覆盖当前哈希。用户限定用途为仅本地面试演示。本记录只把项目中文表述映射到官方条款，不复制 RFC 正文，不等于独立网络专家、版权权利人或法务批准。

## 逐项事实核对

| 卡片位置 | 项目主张 | 官方证据 | 本轮判断与边界 |
| --- | --- | --- | --- |
| BFD 第 5 行 | 异步模式检测时间使用**收到的对端** Detect Mult，乘以取本端 Required Min RX 与对端 Desired Min TX 较大值的发送间隔 | [RFC 5880 §6.8.4](https://www.rfc-editor.org/rfc/rfc5880.html#section-6.8.4) | 与规范公式一致；不能误用本端 Detect Mult。 |
| BFD 第 6 行前半 | 接收方向分别计算，可能不同；非 Demand 模式下，RFC 正文为 Init/Up 会话规定 Control 报文超时置 Down | [RFC 5880 §6.8.4](https://www.rfc-editor.org/rfc/rfc5880.html#section-6.8.4) | 与正文一致；不推广到 Demand/Echo。 |
| BFD 第 6 行后半 | 已处于 Down 时的超时诊断码存在额外讨论 | [RFC 5880 勘误 5205](https://www.rfc-editor.org/errata/rfc5880) | 勘误状态为“Held for Document Update”，**不是**已验证正文修改；卡片仅提示该边界，不把建议当正式规范。 |
| NETCONF 第 5 行 | `<lock>` 对指定 datastore 生效，其他会话和非 NETCONF 管理操作不得修改被锁资源；释放锁或会话关闭结束锁 | [RFC 6241 §7.5](https://www.rfc-editor.org/rfc/rfc6241.html#section-7.5) | 与规范对应；不能把锁当成整机所有数据的永久独占，也不能推定锁一定获取成功。 |
| NETCONF 第 6 行 | `:candidate` 是设备宣告的能力；候选配置可编辑，`<commit>` 将其请求成为 running；无 `:candidate` 时 `<commit>` 不可用 | [RFC 6241 §8.3.1、§8.3.4.1](https://www.rfc-editor.org/rfc/rfc6241.html#section-8.3) | 与规范对应；具体设备是否宣告该能力仍需设备证据。 |
| 两卡第 7 行 | 不以协议通用语义推断单次故障根因或授权生产操作 | [RFC 5880 §1](https://www.rfc-editor.org/rfc/rfc5880.html#section-1)、[RFC 6241 §1](https://www.rfc-editor.org/rfc/rfc6241.html#section-1) | 属项目安全使用边界，不是 RFC 对具体事故的事实证明。 |

结论：在所列官方章节内，**未发现需要再改卡片正文的明确事实错误**。这是 AI 辅助的文字—原文对照，不构成独立专家事实审批；用户的“内容认可”也未明确声称做过逐条协议核验。两卡仍保持 `approval.status=pending`。

## 许可与展示范围

两份 RFC 的首页均声明适用其**发布时**的 IETF Trust Legal Provisions。IETF Trust 的 [TLP 4.0 公告](https://mailarchive.ietf.org/arch/msg/ietf-announce/aSCEB2AGDRpJhqg-_mhySUMO0yU/)载明版本 4.0 于 2009-12-28 生效，并提供当时稳定的 PDF 地址；RFC 5880（2010-06）和 RFC 6241（2011-06）均在此之后。官方旧版 PDF 链接本轮未能读取全文，因此**未完成**对 TLP 4.0 具体授权条款的逐项核验，不能拿当前 TLP 5.0 替代当时版本。

当前 [IETF Trust FAQ](https://trustee.ietf.org/about/faq/)提示标准制定流程外的修改/衍生作品权利通常不自动授予。本项目只保存短篇项目自编中文事实说明与指向 RFC 的链接，未复制 RFC 长段文字或代码组件；但这**不能自动得出**“许可通过”的法律结论。尤其“本地面试演示”仍可能向面试官展示内容，不能仅因没有上线就视为完全私人使用。

保守做法：在许可未核清前，两卡仅保留于本地待审资料；不推送公开仓库、不在线上页面分发、不注册入库。若只需要讲解设计思路，可以展示官方 RFC 链接、架构和来源审核流程，不必展示或分发两张卡全文。若未来需要公开或实际入库，应先由有权判断者核对发布时许可和具体呈现方式，必要时更换来源或取得额外许可。本记录不是法律意见。

## 待完成的独立审核

1. 协议熟悉者核对上表每项事实、BFD 勘误状态和设备适用边界，并留下真实审阅证据；AI 自查不得填入 `reviewer`。
2. 核对 TLP 4.0 原文、两份 RFC 的版权声明及本地演示方式，记录许可/使用决定；在此之前 `license_review=pending`。
3. 与旧知识库及未来题目做人工语义独立性审核；精确术语未碰撞不足以生成可信 frozen。

本轮不修改来源卡哈希、审批字段、知识库、评测题或质量指标。
