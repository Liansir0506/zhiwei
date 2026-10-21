# 四份协议摘要与八道题：人工审核清单

当前状态：等待用户审阅，未批准。整理此清单不改变来源或题目的审批字段。

## 审核范围

只审核 `data/candidates/stage4-protocol-notes-r1/` 的四份项目摘要和八题，用于后续隔离开发评测。不是批准生产动作、发布知识版本、删除旧数据或声明独立冻结成绩。

四份资料均为 AI 根据官方章节写出的短中文概括，不是规范原文；原文中的限制句同样属于必要审核内容。请选择通过、修改或暂缓，不能只确认代码测试通过就认定事实和题目真值已审核。

## 原文入口

- [HTTP 503 项目摘要](../data/candidates/stage4-protocol-notes-r1/raw/http_503_note.md)：核对 [RFC 9110 §15.6.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.6.4) 与 [§10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3)。
- [HTTP 429 项目摘要](../data/candidates/stage4-protocol-notes-r1/raw/http_429_note.md)：核对 [RFC 6585 §4](https://www.rfc-editor.org/rfc/rfc6585.html#section-4)。
- [DNS 负缓存项目摘要](../data/candidates/stage4-protocol-notes-r1/raw/dns_negative_cache_note.md)：核对 [RFC 2308 §3](https://www.rfc-editor.org/rfc/rfc2308.html#section-3) 与 [§5](https://www.rfc-editor.org/rfc/rfc2308.html#section-5)。
- [TLS 早期数据项目摘要](../data/candidates/stage4-protocol-notes-r1/raw/tls_early_data_note.md)：核对 [RFC 8446 E.5](https://www.rfc-editor.org/rfc/rfc8446.html#appendix-E.5)。

这里列出的答案是待审预期，不是实际模型输出。行号均指上面相应本地摘要，不是英文 RFC 行号。

## 逐题核对

| 题目 ID | 原问题 | 待审预期答案要点 | 已绑定本地证据 |
|---|---|---|---|
| PN-http-503-1 | 监控接口返回 HTTP 503，能否仅据此断定是过载？响应一定有 Retry-After 吗？ | 不能只认过载，也可能计划维护；Retry-After 可选，不保证有 | 503 摘要第5、7行 |
| PN-http-503-2 | HTTP 响应的 Retry-After 写为 120 是什么时间单位？除了数字还能采用哪种格式？ | 非负整数秒，120即120秒；也可用 HTTP 日期 | 503 摘要第6、7行 |
| PN-http-429-1 | 运维 API 返回 HTTP 429，协议表达了什么？是否必须给出等待多久？ | 一定时间内请求过多；说明限流情况，等待字段可选 | 429 摘要第5、7行 |
| PN-http-429-2 | 客户端收到 HTTP 429，能直接认定服务端按 IP 计数吗？规范是否统一限定了计数范围？ | 不能认定按IP；规范不固定识别和计数方式 | 429 摘要第6、7行 |
| PN-dns-negative-ttl-1 | 按 RFC 2308 基础规则，SOA TTL=600 秒而 MINIMUM=180 秒，否定回答的起始缓存寿命如何计算？ | min(600,180)=180秒；讨论起始值，不是当前剩余TTL | DNS 摘要第5、7行；数字例子另见第6行 |
| PN-dns-negative-ttl-2 | 排查 DNS 时发现负缓存 TTL 已降到零，还能复用原来的否定回答吗？这是否保证新查询成功？ | 不能复用该缓存的否定回答；新查询仍可能否定，不能保证成功 | DNS 摘要第6、7行 |
| PN-tls-early-replay-1 | 请求通过 TLS 1.3 的 0-RTT 发送，已经加密是否就能排除重放导致的重复处理？ | 不能；0-RTT存在重放风险，需应用明确具备重放安全 | TLS 摘要第5、7行；加密边界另见第6行 |
| PN-tls-early-replay-2 | 评审接口能否安全接受 TLS 早期数据时，幂等是否已经构成充分保证？ | 幂等至少必要，但不自动构成充分保证 | TLS 摘要第6、7行 |

若认为某题证据绑定还需额外行（例如DNS数值例子或TLS加密边界），请指出题号；修改证据要生成新修订再审，不静默覆盖已有包。

## 统一判断标准

1. 四个知识点符合项目的网络/运维问答补充范围，且题目不要求执行配置变更。
2. 预期答案由资料支持，没有把“可以”误写成“必须”、把必要条件误当充分条件。
3. 所有题当前期望 retrieval / ANSWERED，精确命中对应摘要；这只是待审契约，不是已测成绩。降级为 DEGRADED 会按当前严格预期记失配，不改标签掩盖结果。
4. default 租户、ops 角色；没有当前租户数据、配额、设备配置或真实故障根因假设。
5. 全部 dev、已开发暴露，不声称独立 frozen；DNS 与旧资料存在领域重叠。
6. 来源和真值批准不等于入库成功。后续还需隔离来源注册、版本和实际片段核验，且不能切共享 ACTIVE。

## 如何给出审核结论

- 若已阅读原文和题目，并认可全部内容，可明确回复：“已阅读，批准本批4份摘要和8道题，仅用于隔离开发评测。”
- 若有疑问，回复题号、需要修改的点或暂缓的来源即可；可以部分通过，不要求一次全部批准。
- 若没有时间实际核对，请说明暂缓审核。可以继续做不需要批准的工程测试，但不能把未审资料转为正式评测或生产知识。

不得根据“下一步”“继续”或AI自己的复核把状态改 approved。只有实际审核结论到达后才另存审阅记录；不编造审核姓名、审批依据、生产验证或新指标。
