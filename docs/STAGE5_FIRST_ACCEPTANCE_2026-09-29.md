# 阶段 5 首版入口与页面验收（范围限定）

日期：2026-09-29。此报告只收口 SSE/页面/引用/权限/失败展示的首个实现版；**不等于整个 V3 项目正式发布或生产验收**。

## 本批变更

- `GET /kb/versions` 增加由真实认证角色计算的 `can_preview_staged`，页面只向管理员展示 STAGED 选择；服务端 `/qa/sse` 和证据接口仍独立鉴权，UI 提示不作为安全边界。换 Key 时重置版本选择并刷新列表。
- HTTP 401/404/429、流内 `error` 和异常断流后，页面显示明确失败并保留“未完成、手动重试”状态；不保存暂态生成正文、不自动跨通道重发。手动切换通道/会话时取消本地显示，移除草稿并提示服务端可能仍在处理。
- 1280px 浏览器顶部不再挤压标题；窄屏隐藏重复路由图例，保留问答通道、知识版本和状态。
- README 改为“企业级场景验证项目”及当前可核实口径，增加隔离演示步骤；旧生产 SLA/满分表述不再作为当前成果展示。

## 验证与证据

| 范围 | 结果 |
|---|---|
| 全量单元 | `.runtime/test/reports/stage5-first-acceptance-unit.xml`：678/678 通过，0 跳过/失败/错误 |
| 相关隔离集成 | `.runtime/test/reports/stage5-first-acceptance-integration.xml`：6 项中 4 通过、2 项显式付费 opt-in 跳过，0 失败/错误 |
| 浏览器与解析器 | 隔离 Edge/本机 HTTP、真实 API Key 直答；控制流验证最终覆盖、真实引用重读、断流、401/404/429、流内 error、主动切换清草稿；`node tests/browser/sse_client_contract.js` 通过。最终截图 `.runtime/test/reports/stage5-sse-browser-743ee1ec4b10.png` 已人工查看 |
| 真实模型整链 | 上批一次 DeepSeek→HTTP SSE→Edge→引用重读通过，记录与截图见 `docs/STAGE5_SSE_REAL_CHAIN_2026-09-28.md`；本批没有再次调用模型 |
| 小规模并发 | 上批两路真实模型单次观察见 `.runtime/test/reports/stage5-real-dual-sse-r1.json` 及 `stage5-real-dual-sse.xml`；五路假模型控制说明见 `docs/STAGE5_SSE_REAL_CHAIN_2026-09-28.md`。逐批Markdown已清理，不宣称容量或P95 |

首次目标测试在 MySQL 13306 拒绝连接时失败，原报告 `.runtime/test/reports/stage5-ui-failure-targeted.xml` 保留；核对后只恢复五个 `telecom-ops-rag-test` 基础设施容器，未启动生产 API/Worker。重试报告 `stage5-ui-failure-targeted-r2.xml` 为 2/2 通过。临时测试 Key 由夹具清理；测试断言 `v1` 仍 ACTIVE、协议版本仍 STAGED，未激活候选、未业务回滚或删除知识。

## 未完成与下一阶段

独立来源 frozen 评测、候选发布、旧 SOP 安全依据和实际生产部署均未完成；旧 Hit@5 0.827 保留。DeepSeek 双路单次耗时不是模型首 Token、P95、QPS 或 SLA；四片段开发库不能替代全局质量证明。阶段 5 的**入口与页面首版可演示**，但开发方案中“新增资料可发布并被检索”的总验收条件尚未达成，不应标为整个阶段完成。

下一阶段优先补可信独立评测与资料发布所需的审核证据，再决定是否激活版本；若用户只是演示当前原型，应保持 STAGED 管理员预览并按 README 的隔离步骤操作。不要靠重复跑相同八题或提高并发数字制造成果。
