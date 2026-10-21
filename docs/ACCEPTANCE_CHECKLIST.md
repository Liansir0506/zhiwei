# ACCEPTANCE_CHECKLIST — 验收清单

## 每阶段通用门禁

- [ ] `git diff` 仅含本阶段允许修改的文件
- [ ] `python -m pytest tests -q` 全绿
- [ ] 新增代码有对应测试（纯逻辑测试不打真实服务）
- [ ] git commit 完成，提交信息说明本阶段范围

## 阶段 3 基础设施

- [ ] `docker compose config` 通过
- [ ] 六容器 Up 且 milvus/etcd/minio 无重启循环
- [ ] `curl /health`：mysql/redis/milvus/models 全 ok
- [ ] .env 不在 git 中，.env.example 完整

## 阶段 4 离线入库

- [ ] `scripts/init_collections.py` 建出 FAQ/DOC 两 Collection，字段与 DATA_MODEL 一致
- [ ] dense 索引 HNSW / sparse AUTOINDEX+BM25 可查（`describe_index`）
- [ ] 六类样例数据全部入库，ingest_records 无 failed
- [ ] 脱敏扫描：样例中的手机号/IP 已替换为占位符
- [ ] 质量门禁五项输出报告且 passed；人为破坏一项（删告警码）能 fail 并阻止激活
- [ ] 断点续跑：中断后重跑不重复入库（file_hash 幂等）

## 阶段 5 检索

- [ ] 同一 query 能取得 dense/sparse/hybrid/rerank 四种分数且量纲不同
- [ ] 权限过滤：role 不足的用户检索不到 role_restricted 文档
- [ ] kb_version 过滤：ARCHIVED 版本不可召回
- [ ] parent_id 去重：同一工单多块只出一条
- [ ] 空候选返回保护性结果，不抛异常

## 阶段 6 路由

- [ ] 问候/注入/越界 → direct_answer
- [ ] "5G基站传输中断排查步骤" → faq_exact（seed FAQ 命中）
- [ ] "ALM-1234 怎么处理" → structured_lookup；改成不存在的码 → 降级 retrieval
- [ ] 普通问题 → retrieval
- [ ] 意图模型进程不启动/超时 200ms → 规则结果兜底，路由不受影响

## 阶段 7 在线链路

- [ ] 同步与 WebSocket 两条路径答案一致
- [ ] WS 事件序列 start→route→delta*→sources→end 完整，end 必达
- [ ] 答案含 [n] 引用且 sources 可对应；答案中的告警码/条款号均出现在证据中
- [ ] 无证据问题返回固定拒答话术，sources=[]
- [ ] LLM_API_KEY 无效/断网 → 引用模式兜底 + degraded=true
- [ ] 三级缓存命中可从 diagnostics 观察；kb_version 切换后旧缓存不再命中

## 阶段 8 治理

- [ ] STAGED→ACTIVE→ARCHIVED 全流程 + ACTIVE 唯一性
- [ ] rollback 后答案指向旧版本且 L2 相关 key 已清理
- [ ] 评测 30 条跑完，recall@5 ≥ 0.85、拒答准确率 ≥ 0.95（不达标记录差距与原因）
- [ ] feedback down → bad_cases 出现 open 记录
- [ ] trace_id 贯穿日志，可按 message_id 反查整条链路

## 阶段 9 最终交付

- [ ] `requirements.txt` 全新 venv 安装成功
- [ ] README：新人按文档可独立完成 部署→入库→提问
- [ ] 故障排查手册覆盖 E100/E101/模型加载失败/门禁失败
- [ ] 最终输出：启动命令 / 入库命令 / 提问命令 / 已知风险清单
