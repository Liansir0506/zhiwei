# 开发、测试与演示环境隔离

新环境使用独立 Compose project、数据卷、数据库、Redis DB、Milvus 集合及文件目录。
仓库不包含演示机器的发布凭据、批准产物或运行数据；不要照搬旧机器的发布状态。

| 环境 | API | MySQL | Redis DB | 集合前缀 |
| --- | ---: | --- | ---: | --- |
| dev | 8010 | rag_kb | 0 | 无 |
| test | 18010 | rag_kb_test | 15 | test_ |
| demo | 28010 | rag_kb_demo | 1 | demo_ |

```powershell
python scripts/init_local_env.py --environment dev
$env:APP_ENV_FILE = '.env.dev'
docker compose --env-file .env.dev up -d mysql redis etcd minio milvus
```

初始化器生成随机数据库及对象存储口令并写入被 Git 忽略的配置文件，拒绝覆盖已有文件。
同样可使用 `--environment test` 或 `demo`。本机命令应先设置对应的 `APP_ENV_FILE`。
模板默认要求鉴权、关闭生成及 Agent；需要模型生成时在私有配置中填写自己的 Key。
Compose 的宿主端口仅绑定 `127.0.0.1`，不能视为面向公网的部署方案。

模型不在仓库内。启动应用前准备模型路径、初始化空集合并创建自己的 API Key，见主 README。
旧资料需要新一轮来源准入和候选流程；模板、历史批准示例及训练生成器不等于新环境已获准发布。

纯单元测试不需要外部服务：`python -m pytest tests/unit`。
有状态测试仅在 `APP_ENV=test` 且隔离校验通过后执行；还需要真实 test 存储和模型。
任何真实模型付费测试均需按脚本要求显式开启，不属于仓库完整性检查。

停止环境使用 `docker compose --env-file .env.test down`，保留卷；不要随意添加 `-v`。
已有数据库的口令不会随修改 `.env` 自动轮换，应单独制定轮换和恢复方案。
