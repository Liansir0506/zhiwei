# 开发、测试与演示环境隔离

项目使用三套独立的 Compose project、MySQL 数据库、Redis DB、Milvus collection、宿主机端口和文件目录。测试默认不能写入开发或演示环境。

2026-10-07步骤5后的当前主入口为既有`telecom-ops-rag`项目8010，使用工作树`.env.runtime`和新代码/CPU镜像；主目录模型、场景、数据、报告继续挂载。下方`.env.dev`等是环境模板，不替代已验证的`.env.runtime`。实际版本、并行schema迁移与旧库引用限制见[交接第15节](HANDOFF_2026-10-07.md)。重启使用`docker compose --env-file .env.runtime -p telecom-ops-rag up -d`；不重新初始化或清卷。

| 环境 | API | MySQL 数据库 | Redis DB | Milvus collection 前缀 | 文件目录 |
|---|---:|---|---:|---|---|
| dev | 8010 | `rag_kb` | 0 | 无 | `data/` |
| test | 18010 | `rag_kb_test` | 15 | `test_` | `.runtime/test/` |
| demo | 28010 | `rag_kb_demo` | 1 | `demo_` | `.runtime/demo/` |

## 首次准备

```powershell
Copy-Item config/environments/dev.env.example .env.dev
Copy-Item config/environments/test.env.example .env.test
Copy-Item config/environments/demo.env.example .env.demo
```

需要调用真实模型时，只在对应文件中补充 `LLM_API_KEY`。三个文件都已被 Git 忽略，不提交密钥。

## 启动环境

```powershell
# 开发
docker compose --env-file .env.dev up -d

# 测试（独立端口和数据卷）
docker compose --env-file .env.test up -d

# 面试演示
docker compose --env-file .env.demo up -d
```

三个环境可同时运行。页面右上角会显示 `DEV`、`TEST` 或 `DEMO`，`GET /health` 也返回 `environment`。

## 安全运行测试

纯单元测试不连接外部服务：

```powershell
python -m pytest tests/unit -q
```

完整测试必须显式加载 test 配置；否则 component、integration、smoke 会跳过：

```powershell
$env:APP_ENV_FILE = ".env.test"
python -m pytest tests -q
python tests/api_matrix.py
Remove-Item Env:APP_ENV_FILE
```

bash 等价写法：`APP_ENV_FILE=.env.test .venv/Scripts/python.exe -m pytest tests`

### 宿主机跑测试的前置条件（第 7 步 F 阶段补充）

测试全部走 `TestClient(create_app())` **进程内**调用，因此：

1. **只需要基础设施容器**，不需要 api / ingest-worker：
   `docker compose --env-file .env.test up -d mysql redis milvus etcd minio`
2. **模型路径必须用绝对路径。** `worktree` 下的 `./models` 是空的（模型不入库），
   模型实体在数据目录。`.env.test` 里若沿用示例的 `MODELS_DIR=./models`，
   组件/集成/冒烟会全线报 `Path ./models/bge-m3 not found`。
3. **心跳等时间字段一律用数据库侧 `NOW()`**，不要在测试里用宿主 `datetime.now()`。
   容器 MySQL 是 UTC，宿主是 UTC+8，相差 8 小时：
   用宿主时间写入的"1 小时前"在数据库看来是未来时间，租约恢复类断言必然失败。
4. **不要给 pytest 再加 `-q`**：`pytest.ini` 的 `addopts` 已含 `-q`，
   再追加会变成 `-qq`，把汇总行一起吞掉。
5. `--basetemp=.test-tmp` 会随运行累积（一次全量约 500+ 文件）。
   在带批量删除保护的环境里，累积过多会导致下一次会话开始时清理被拒。
   此时可指定独立目录：`--basetemp=.runtime/test/tmp-<n>`，
   或分批运行（`tests/unit tests/component` 与 `tests/integration tests/smoke` 分开）。

旧逐批验收Markdown已清理；当前运行入口、隔离目标和接手命令见 `docs/HANDOFF_2026-10-07.md` 第4、8节。原始测试产物保留，执行前须核对当前状态。

### 停止 test 环境

```powershell
docker compose --env-file .env.test down
```

**不要加 `-v`**，否则会删除 test 数据卷。

接口矩阵会先检查目标 `/health`。只要目标不是 `test`，脚本就会在第一次写操作前退出。每次执行使用唯一文档名、会话和 API Key，并在结束或异常退出时尽力清理。

## 初始化与恢复 demo

当前已批准的256份模拟资料使用独立 test 数据库，启动方式见交接17.9和下节“已发布隔离模拟库”。下述 `.env.demo/reset_demo.py` 是另一套可重建环境的历史操作，不用于该已发布库。

先将用于演示的公开/模拟语料放入 `.runtime/demo/data/raw/`，文件名遵守 `manual_`、`standard_`、`sop_`、`faq_`、`fault_case_` 等前缀。

```powershell
$env:APP_ENV_FILE = ".env.demo"

# 只清理历史自动化测试污染和缓存
python scripts/reset_demo.py --yes

# 从 raw 语料完整重建 collection、版本和质量门禁
python scripts/reset_demo.py --yes --full --kb-version demo-v1

Remove-Item Env:APP_ENV_FILE
```

重置脚本具有四重保护：`APP_ENV=demo`、MySQL 库名含 `demo`、两个 collection 均以 `demo_` 开头、Redis 不使用 DB 0。任何一项不满足都会拒绝执行。`--full` 会清空并重建 demo 的业务数据和向量集合，只应用于隔离的演示环境。

## 停止

```powershell
docker compose --env-file .env.test down
docker compose --env-file .env.demo down
```

不要给 `down` 添加 `-v`，除非明确需要删除该环境全部数据卷。

## 已发布隔离模拟库：独立启动与停止

在工作树根目录执行，默认 `http://127.0.0.1:18011/`。脚本固定使用该工作树 `.venv`；准备依赖时执行 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`。需要既有 test 五个存储服务健康，模型路径沿用 `.env.test`。

```powershell
# 存储若已停止，只恢复已有 test 存储；不启动 Worker/API 或清理卷
docker compose --env-file .env.test -p telecom-ops-rag-test up -d mysql redis etcd minio milvus
.\scripts\start_legacy_demo.ps1
.\.venv\Scripts\python.exe -m scripts.legacy_demo_runtime status
.\scripts\stop_legacy_demo.ps1
```

启动校验已批准门禁、版本绑定与实时快照，仅读取既有 `test_legacy_stage_fb36d947ab67`、同名 `_doc/_faq` 及唯一 ACTIVE `test-legacy-demo-r1`。默认端口18011，可通过 `start_legacy_demo.ps1 -Port 18012` 选择独立端口；原8010、共享test18010和mock18020受保护，占用端口会拒绝启动。启动不会激活版本、入库或重嵌入。

启动生成的 `.runtime/test/legacy-demo/profile.json` 为独立运行配置；继承的shell生产/付费变量不能覆盖固定配置。`APP_ENV=test`、Redis DB15、CPU模型、强制鉴权、流程安全护栏及引用模式固定启用，生成与生成语义缓存关闭。网页显示“引用模式”；FAQ可直出，其他问答只输出通过护栏的原文片段，旧SOP缺审批/回滚仍拒绝。该入口没有运行Worker。

启动输出 Key 文件路径，不输出 Key；从 `.runtime/test/legacy-demo/api-key.txt` 复制当前 ops Key，在网页左下角“设置 API Key”输入。该文件只在服务运行时保留，处于 Git 忽略目录，不应提交或分享。每次重启 Key 更换，浏览器中的旧 Key 需更新。停止核对 PID/创建时间/命令中的实例标识和配置路径，清理同一实例的 Windows 子进程及自身 Key，保留库、向量、ACTIVE和报告；重复启动/停止可重入。

运行状态/归属记录在 `state.json`，日志在 `server.log`。失败时先查看状态、日志、test服务健康与端口；失效实例可执行 stop 清理后再 start。Key 删除失败会保留恢复记录；PID/配置归属不符会拒绝操作，不能按端口或未知PID强杀。数据/门禁绑定变化时启动拒绝，需要处理实际变更原因，不能重新首次发布或清卷来绕过。


17.12：上述已发布隔离启动配置显式AGENT_ENABLED=true，仍GENERATION_ENABLED=false；默认Settings的Agent开关为false，其他环境未启用。真实Key可POST /agent/diagnose（例query="ALM-AMF-REG 常见原因"）；首版同步、单轮、无LLM与业务动作，接口最长90秒/单工具30秒，冷模型初始化可能超时。验收时先调用无LLM的/kb/search-evidence预热本地模型，热态结果不代表冷启动保证。17.15已在唯一隔离ACTIVE库写入批准范围内12条领域/身份原行和4条typed知识关系（部门3、user2、设备2、监控2、告警2、工单1、变更1、关系4）；API/权限/原文与Edge回归后服务关闭，密钥清理。数据为模拟历史快照，不自动或实时造现场观测。17.13已接工作台“只读诊断”，按真实终态展示引用、工具记录、澄清/停止与分离的实体快照，不沿用QA历史或预览版本。页面等待上限105秒、取消不自动重发，换Key/离页/新会话清空结果；原文每次重新鉴权。当前入口/Key验收后已清理，重新start获取本次Key。复验条件见交接17.13。


17.13浏览器验收可在test五组件健康且当前演示停止时执行`.\.venv\Scripts\python.exe scripts/verify_readonly_agent_ui.py`。脚本仅Windows/本地Edge，持有API与浏览器生命周期，本地证据接口预热后验证真实知识/引用与原SSE追问；受控UI/传输探针单列，finally停自有API、清临时Key/Edge profile。脚本不会启动或停止Docker存储。本轮曾因组件停止/MySQL拒连及Milvus重启恢复未完成而未就绪，最终等待恢复通过；验收后本轮恢复的test五组件已停回起始状态，原八容器仍停，数据库/数据卷未删除。下次复验先按上方命令恢复test存储，不重新发布或清卷。

17.16只生成关联检索设计与离线基线，不启动上述入口/存储、不连接数据库、不初始化模型。复核原八和test五容器exited，API state=stopped且api-key.txt不存在；批准领域行仍按17.15留存，本轮没有重新查询存储指纹。离线复核命令`.\.venv\Scripts\python.exe -m scripts.prepare_agent_relation_plan --validate-only`，报告agent-relation-plan-20261009；23类在线/夹具场景待执行。下一项先恢复test存储并核对实际DOC标量与根记录，再实现与隔离开发验收；复用唯一ACTIVE，不重复准入/发布/嵌入或扩大付费预算，最后清Key停自有入口并恢复组件原状态。详见交接17.16。

17.17本轮关联检索实现和验收已收口。test五组件从exited恢复，仅使用已批准隔离库及随机临时MySQL夹具；原八组件未启动。真实19 API、Edge1280/390以及9项临时SQL集成通过，全量单元1080使用APP_ENV_FILE=.env.test与现存本地模型，无下载/付费生成/passage嵌入。结束自有API/临时Key/Edge目录清理，原八/test五容器均exited；保留运行profile.json和批准域数据，不清卷。

复验：先按本文件命令恢复test存储至健康，再运行`.\.venv\Scripts\python.exe scripts/verify_agent_relation_demo.py`，脚本拒绝已有运行入口，只核对已提交16行而没有导入分支，持有并finally清理自有API/Key/Edge；不会自行启动/停Docker。`--browser-only`要求已有通过的19项API报告并保留它，只复验浏览器。最后恢复存储起始状态。严格17.16方案复核因实现代码哈希变化会拒绝，历史包不回写；当前证据见agent-relation-implemented-20261009/delivery-summary.json和交接17.17。

17.18仅使用原.env.runtime明确dev/3306 rag_kb/Milvus19530读审计，提供所有Settings字段，shell变量不能换目标；SQL快照服务端READ ONLY。原四存储两次启动/停止用于首次与表名更正重审；原API/Worker/mock/Redis与test五存储均未启动。当前13容器exited，8010/18011关闭、独立演示state=stopped、Key不存在，原数据卷保留。

完整引用拟新local-citation-r1 STAGED及local_citation_r1_doc/faq，尚待原库276资料范围/20卡/部门/共同撤回明确准入。没有创建版本/集合/定位表、启动API或模型、嵌入/付费/发布；原运行配置不改，禁止compose全量启动消费四个遗留任务。离线复核`.\.venv\Scripts\python.exe -m scripts.prepare_original_citation_review --validate`，默认使用sealed original-citation-readiness-20261009-r2；原无r2报告的简称表名清单无效，保留且由r2替代。实际manifest/撤回表存在且空，只有定位表缺失。后续候选验收使用独立预览配置与临时自有Key；原8010切换另按实际结果审核，不借18011批准绕过原端口保护。

17.19执行原库新STAGED仅使用显式dev/3306 rag_kb/19530、local_citation_r1_doc/faq及CPU宿主路径；强鉴权/GENERATION_ENABLED=false/AGENT_ENABLED=false/空LLM Key、不可用本地LLM URL和禁模型联网下载。临时预览127.0.0.1:18013/Redis14与原8010和隔离18011分开，不写.env.runtime或原运行profile。新增定位表仅候选，新版本local-citation-r1始终STAGED；原API/Worker/mock未启动、4旧任务不消费。

本轮启动原/test各五存储；为BGE内存曾在test保护before后暂时停止test五存储，final前恢复读取，知识/16领域指纹不变。最后10存储全部停回起始状态，原八/test五共13容器exited；临时18013 API/五Key/Edge profile清理，8010/18011/18013关闭，持久隔离profile.json保留。测试使用独立basetemp；首轮共用.test-tmp产生setup错误及查询百分号错误报告保留，最终4临时SQL与1120unit通过。

离线复核：`.\.venv\Scripts\python.exe -m scripts.original_citation_candidate`。不要重跑stage_original_citation_candidate --execute或原库资产准入；它拒绝已有报告/候选。引用复核应新建验证输出，只读使用已完成STAGED和同一URI/模型路径的manifest。候选绑定http://localhost:19530与F:/heima/myproject/models，Docker http://milvus:19530或/app/models不相等；下一项准备原入口发布/匹配宿主绑定的启停方案，不默认改原Docker或8010入口。

17.20原入口方案仅准备宿主.venv/127.0.0.1:8010、原3306 rag_kb/localhost19530及候选集合，Redis6379/0，原宿主模型路径保持manifest字面绑定。强鉴权，生成/Agent/语义缓存关闭，workflow安全拒绝保留；所有Settings显式构造，APP_ENV_FILE设devnull并覆盖APP_SCENARIOS_DIR/模型离线开关。审核配置不含DSN/LLM Key，原.env.runtime不改。

真实准备只恢复原五存储，API/Worker/mock/test保持停。容器healthy之后须再检查六集合Strong可读，总180秒，仅106恢复通道重试，query RPC最多5秒；不自动load/release或改原文向量。verify_publication关闭同URI SDK alias，故探针→发布验证→旧向量比对必须分客户端生命周期。最终91相关/真实快照和旧SQL/向量核对通过，离线审核页Edge1280/390及profile清理通过，13容器停/三个入口关闭。本轮0API/Key/模型/付费。`python -m scripts.prepare_original_citation_release --validate-only`可离线复核；真正发布或宿主8010启用尚未授权，标准回滚旧ACTIVE不可用。

### 两步发布目标与真实隔离演练（17.21）

17.21已建成合格回滚基线local-rollback-v4-r1（5300）与后续local-release-r1（5900），两版仍STAGED，11200条原文/真实旧向量/位置逐条复核；新276×2账本与manifest，0新资产准入、0付费。1178全量单元和完整真实数据临时库标准激活/回滚、五组引用/权限、默认检索、SOP拒绝及篡改/撤回负例通过。原唯一ACTIVE仍v4-foundation-r3，8010未启用，13容器停；原资料/6受保护集合/旧SQL及共享和隔离知识/16领域行保持不变。 新原库集合local_release_r1_doc/faq只含两新STAGED；测试演练用随机自有test_rollback_pair命名空间，完成后删除DB/两集合/授权/临时Key。原与test共13容器已停，8010/18011/18013/18014关闭；共享test、已批准隔离知识及领域指纹相同。拟宿主原8010、Milvus http://127.0.0.1:19530、原Redis6379/0、CPU模型/强鉴权/生成和Agent关闭，原.env.runtime不写。下一项仅待审两步发布与8010方案：先激活并验收合格基线，再激活新版；此后标准rollback可回基线。第一次从旧版到基线仍不能标准回滚旧版，提交后失败须停入口/自有Key并报告实际指针。原激活与入口未获批准，不能把本轮“解决下一步”当发布批准，不重复准入/导入。

### 原8010合格基线发布与可重启宿主入口（17.22）

17.22原库两步本地发布与8010入口已完成：local-release-r1为唯一ACTIVE，local-rollback-v4-r1为ARCHIVED且标准rollback已在原库真实成功；v4-foundation-r3归档，旧local-citation-r1仍STAGED。1211全量单元、102相关子集、实际默认检索/SSE、五组原文/权限/SOP拒绝、Edge1280/390真实页面提问和停止→重启通过。 生成和Agent关闭、强鉴权、付费0；原276资料/身份/ACL/20关系/4任务/账本/manifest/位置及八集合字段/向量不变，仅预期三版本状态/发布字段/自动updated_at、接口审计和自有Key/问答日志变化。原五存储与宿主8010保持运行，原Docker API/Worker/mock和test五组件停；只保留一个自有ops Key。 宿主入口使用.runtime/original-local无凭据profile与原.env.runtime进程内凭据，新集合local_release_r1_doc/faq、Milvus127.0.0.1:19530、原Redis6379/0和宿主模型路径。start_original_local.ps1等待五存储健康及Strong可读再验已发布ACTIVE，stop_original_local.ps1仅停自有API/撤销自有Key；实际重启版本/其它Key不变。旧Docker API仍停止，原配置文件不替换，不按容器名推断已更新入口。本发布任务已收口，不重复准入、入库或发布。启动/停止用scripts/start_original_local.ps1与scripts/stop_original_local.ps1；停止只关闭自有宿主入口并撤销自有Key，存储保留。其它pending与独立frozen0不扩大，可信独立质量仍待新来源和独立核对；原跨版本Agent关系未迁移。

### localhost与IPv4入口统一（17.23）

17.23已修复“页面能开但问答报错”：旧Docker API复起占用localhost/IPv6，旧集合/模型URI与新版manifest不符导致503，而127.0.0.1的新入口正常。已停止原Docker API/Worker/mock，宿主同一服务同时绑定127.0.0.1和::1；localhost、127.0.0.1及[::1]三个地址的真实检索/原文/SSE均通过，localhost页面1280/390实测通过，1217全量单元通过。 原ops Key保持不变，local-release-r1仍唯一ACTIVE，回滚基线仍ARCHIVED；本次未发布、入库或改原文/向量/权限/原.env.runtime，SQL保护快照完全相同，生成和Agent关闭、付费0。 宿主loopback双地址绑定，同一实例/同一Key/同一manifest。start原脚本先停三个旧冲突服务；restart仅重启自有API并保留ops Key，stop依然撤销自有Key。两个地址都仅本地，不改manifest URI或允许旧Docker的milvus内部路径越过绑定检查。

### 私有本地登录文件（17.24）

打开.runtime/original-local/open-platform.html并点击“进入平台”，导入当前已有ops Key后进入localhost:8010；fragment在应用初始化前立即移除，凭据沿用浏览器既有api_key存储。文件只在被忽略的本地运行目录，不能复制到公开静态目录。start/restart核对现有自有Key后生成文件；成功stop撤销自有Key再删除，撤销失败保留恢复资料。stop/start轮换Key后应重新打开当前文件。仅loopback接受此导入，远端/格式错误不覆盖Key；HTTP服务鉴权始终开启。本次实际仅更新静态代码与私有文件，后台进程保持运行，没有创建Key或重新发布。

### 用户实际问答质量诊断（17.25，未修复）

17.25用户截图触发的真实问答质量诊断：当前认证和服务可用，但推荐/检索内容存在未修复问题。真实复现11个侧栏推荐：8个NO_EVIDENCE，3个返回片段；BBU掉电返回NGAP连接中断等工单，乒乓切换返回NR切换失败率升高，不能把ANSWERED标签当作答对。当前1000条FAQ抽样是带模拟案例编号的模板内容，1条库内原句实际faq_exact直出成功。推荐问题仍写死且未绑定当前库，generation_enabled=false且模型地址为disabled，通用15%词汇覆盖不足以判定故障主题；流程保护也会拒绝缺审批/回滚的既有SOP。17.22～17.24只证明限定链路/治理/鉴权恢复，未证明自然问题质量。下一开发重点转为推荐与知识范围一致、错题检索保护、FAQ/文档内容质量和带引用的生成回答；本轮仅诊断，不声称已修复，不改库/发布/模型设置，付费0。

### 问答质量代码修复与剩余范围（17.26）

17.26已部署无需付费的问答质量修复：推荐改从当前版本/权限可读FAQ加载，并标识模拟案例；FAQ原句先做Strong精确匹配，再退到语义检索；明确主题在精排前和引用前检查，阻止乒乓切换引用NR切换失败率、BBU掉电引用NGAP工单。12个动态推荐真实SSE均faq_exact且精确对应来源，两道原错题现在NO_EVIDENCE/零引用；实际浏览器点击动态推荐和原文、1280/390布局通过。1231全量/114相关单元通过。当前仍关闭生成，恢复DeepSeek的具体配置方案已准备但待追加付费授权；缺失主题资料仍待补齐，不把改推荐或拒答宣称为原题已可回答。无入库/发布/原文或向量变更，Agent关闭、付费0。

旧三个应用容器再次占用入口导致首次重启失败，自有Key按失败清理撤销；既有启动脚本已恢复自有入口并生成新Key/登录文件。重新打开当前私有登录文件进入平台，不能沿用旧浏览器凭据。

### 17.27 2026-10-09 获准文档生成恢复与实际验收

17.27已恢复获准的DeepSeek文档生成，保留17.26推荐绑定当前可读FAQ、原句精确匹配和明确主题保护。AMF真实浏览器SSE、SMF同步生成均通过，答案包含结论、排查步骤和引用，原文可读；本轮验证额度使用3/4（含1次TLS连接失败），生成成功2次，后续用户主动提问生成已获准。乒乓切换仍缺对应资料，明确NO_EVIDENCE且不调用模型。1239全量、71相关单元通过。最终路由标签及历史记录已修复，使用真实答案回放验证，无新增模型请求。原.env.runtime、ACTIVE/ARCHIVED、知识原文及向量未改，Agent关闭；整体资料覆盖和独立质量验收仍未完成。

本轮用户明确批准恢复生成及最多4次真实验证，随后批准payload-review.html中AMF/SMF题目和检索片段发送至https://api.deepseek.com/v1/chat/completions，并允许后续主动提问的必要会话上下文、同一本地演示库可读片段用于生成。首次外发被自动审批拦截；具体数据/目标授权补齐后同一验证已通过，不再处于待批准状态。批准记录仅存公共配置和摘要，不记录API Key、数据库凭据或权限配置。

生成配置通过单独generation-approval.json覆盖已冻结基础配置的LLM字段；基础host-profile和原.env.runtime不修改。启动检查基础配置、原配置全文摘要及提供商/型号/凭据摘要，漂移时拒绝启动。验证预算在每次真实HTTP尝试前保留额度，包括失败与重试；当前used=3、limit=4、mode=interactive_approved，表示验证结束后开放已批准的用户手动生成，不是后续日常问答永久只有1次余额。

第一次TLS连接超时没有生成答案，失败日志保留。连接超时改为10秒，流式网络异常转换为既有LLMError以走降级处理；已配置deepseek-flash按官方参数关闭thinking，避免面向用户的普通排查回答只等待推理。官方依据：https://api-docs.deepseek.com/api/create-chat-completion/ 和 https://api-docs.deepseek.com/guides/thinking_mode/ 。实际AMF流式和SMF同步各有5条引用，未降级；原乒乓题无证据、零引用、计数不变。

浏览器实际请求报告与路由显示回放报告分开保留：前者是真实生成；后者重用该真实答案检查最终retrieval标签和历史记录、1280/390布局以及引用查看，没有追加模型请求。全部截图已人工查看。browser.json旧模板paid_llm_calls=0字段不代表本轮免费；实际计数以paid-validation-count.json及本次汇总为准。

失败重启清理曾撤销自有Key，随后恢复服务并生成新私有登录页；不声称Key全程保持，也不猜测未记录的启动失败原因。目前原五存储与宿主入口运行，旧API/Worker/mock停止。本轮没有入库、发布、重写模拟原文或重建向量。历史17.26报告继续记录当时关闭生成的状态，不覆盖历史验收。

推荐和FAQ原句验证沿用17.26的12条真实SSE结果；本轮两道已暴露开发题只算生成链路回归，不算独立质量评测。BFD/NETCONF等其余pending、独立frozen=0及旧SOP缺安全依据边界保留。模拟资料仅限隔离本地演示，不能宣称原静态11题均可回答或生产知识质量已合格。

## draft-r2指南独立只读预览（2026-10-14，交接17.66）

现用控制器：`.venv/Scripts/python.exe -m scripts.sop_record_topic_runtime start|status|stop`。入口127.0.0.1:18025，私有登录文件`.runtime/sop-record-preview/open-preview.html`，仅default未限域技术admin；不要分享登录文件或把Key加入报告/静态目录。状态命令不占变更锁，背景服务使用pythonw和CREATE_NO_WINDOW，不弹空终端。

固定test_sop_records_r2/13306、Milvus29530/test_sop_records_r2_doc和_faq、Redis16379/13；20指南/180片段/唯一test-sop-records-r2 STAGED。生成、Agent、语义缓存、角色头覆盖关闭，强鉴权；原8010、业务18021和旧指南18024保持。指南目录、检索、109项对齐和原文重读是真实新SQL/Milvus。明确告警/设备先限定已准入asset与draft-r2，未知告警/冲突不放宽；其他无指定主题的问题是探索性证据检索。

旧sop_record_preview_runtime及sop-guide-preview控制器不管理这一topic适配器。新版Key仅存在此新隔离库，停止会删除自有Key/私有入口，重启生成新Key；请重开私有登录文件。最终实际验收与失败历史见`.runtime/test/reports/sop-record-preview-topic-20261014/review.html`。STAGED与普通失败门禁不改，未发布、并库、切8010或授权新模型外发；本次短时观察不代表长期稳定。
