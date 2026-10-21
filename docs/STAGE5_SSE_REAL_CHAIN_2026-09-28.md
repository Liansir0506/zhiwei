# 阶段 5 第三批：真实 SSE 生成与引用整链

## 范围与结果

- 在原有 `POST /qa/sse` 上增加可选 `kb_version`。未指定时照旧走 ACTIVE；指定 STAGED 时复用证据 API 的管理员权限、版本状态与真实集合绑定校验。页面仅在 SSE 模式提供版本选择，WebSocket 兼容通道不可选。没有激活候选、修改共享 `v1` 或扩大资料审批。
- 单次受控浏览器端到端：隔离本机 API、临时管理员 API Key、Edge 独立用户目录，提问已批准八道开发题之一。真实 `deepseek-chat` 经 HTTP SSE 返回 `ANSWERED`，浏览器收到并显示四个最终来源；点击回答里的 `[1]` 后由 `/kb/evidence/{id}` 重新鉴权读取同版本的已批准 `http_503_note.md` 原文。`v1` 保持 ACTIVE，`v4-protocol-dev-r1` 保持 STAGED。
- 本次页面端观测：一个请求、首个正文 `token` 约 24,043 ms、浏览器直到终态约 25,533 ms，来源 4 条。截图 `.runtime/test/reports/stage5-real-sse-browser-b067b2ab476e.png` 已人工查看，结构化记录 `.runtime/test/reports/stage5-real-sse-browser-r1.json`。时间包含冷启动/检索/网络/渲染，**不是** DeepSeek 的模型首 Token 或稳定 P95，也不构成正式质量指标。没有记录 Token 用量或成本。
- 另用本机测试专用假模型和真实 HTTP 连接，五条流占满名额时第六条返回 429；主动断开其中一条后，同步工作线程未返回前名额保持占用，释放工作线程后五个名额全部恢复。见 `.runtime/test/reports/stage5-sse-transport.xml`。这是控制行为测试，不是五路真实 LLM 并发性能测试。
- 回归：全量单元 677/677 通过（`.runtime/test/reports/stage5-third-full-unit.xml`）；相关隔离集成 3/3 通过（`.runtime/test/reports/stage5-third-integration.xml`）；真实模型整链 1/1 通过（`.runtime/test/reports/stage5-real-sse-browser.xml`）。均为 0 失败、0 错误、0 跳过。真实模型测试只调用一次，没有在全量回归中重复调用。

## 复现与限制

真实模型测试位于 `tests/integration/test_qa_sse_deepseek_browser.py`，须显式设置 `RUN_STAGE5_DEEPSEEK_E2E=1`、隔离 `.env.test`、批准测试集合与 `LLM_API_KEY` 才执行；普通回归自动跳过，避免重复计费。测试创建的 API Key 按哈希清理，独立 API/浏览器进程退出；密钥不写入报告。五路控制测试在 `tests/integration/test_qa_sse_transport.py`，假模型不接触业务存储或真实 LLM。

开发题已暴露且知识库只有四个批准片段；本次成功不能推导 Hit@5 提升、泛化能力或生产 SLA。旧基线 0.827 不变。下一步应先检查 STAGED 预览权限负例/回归，再针对真实并发与长连接建立受控、低成本压测；独立来源 frozen 评测与候选发布仍需另行审批，不能由单题截图替代。
