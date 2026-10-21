# BFD / NETCONF 旧资料近邻筛查（只读诊断）

日期：2026-09-29。目标是给人工语义复核缩小查看范围，**不是**证明来源独立或生成 frozen 题。离线脚本 `scripts/screen_holdout_source_overlap.py` 使用字符 2–4 gram TF-IDF 余弦相似度，按 420 字符窗口/300 字符步长比较；不调用 DeepSeek、embedding 模型、数据库或检索服务，不修改知识和题集。新卡只取事实正文，旧四张协议卡去掉标题、引用和结尾安全样板句，以减少元数据假相似。

## 可复现输入

| 输入 | 范围与哈希 |
| --- | --- |
| 新卡 | BFD `cbe9e3832698a1e1fa5a554d337d6b8b1ae98fcf0cff2ec6757148564b525d7d`；NETCONF `80ddb482d6927a813d2bade821e7e5568bd10ff11acc99607c4ef7629a391841` |
| 现阶段候选源文 | `v4-quality-r1-final/raw` 中 108 份 MD/CSV，按文件名和单文件 SHA-256 排序后的清单摘要 `c3493ccaa82ffa99d7cf7d0f02ab38950ba42987a99435f8f18a7231bcd78ca6`；不含 `meta.json` |
| 已有协议卡 | 4 份，清单摘要 `a8e2c6745e3b4207ad4f29e3fca064f2057d9464e50c81fb8a913cb0b2131207` |
| 旧题 | 主仓库 r3 174 题 SHA-256 `cc0d210afa913e30d4b5782de58ca728fc1680ab8366b79bc2a2ba72fd94374f`；工作树 v3 70 题 SHA-256 `0c4fb028271a372047594d4f5e7ae7cff7ad0e31d950d05c76ec040cb387647b` |

共 6392 个历史文本窗口/问句单位。复现：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m scripts.screen_holdout_source_overlap --legacy F:/heima/myproject/eval_sets/v4-r3-tiered.json --top-k 5
```

## 人工优先查看的近邻

- BFD：旧 v3 `J12`“确定性网络 DetNet 转发机制”（余弦 0.0342）；旧 DNS 负缓存卡（0.0310）因时间/间隔描述被拉近。两者可人工判定是否只是宽泛的“转发/时间”共享词，不要按分数自动判等价。
- NETCONF：旧 r3 `A13` 的 `ALM-PTP-LOCK` 告警（0.0238），旧 FAQ `faq_foundation_simulated.csv` 第 9 行、实验卡 `guide_experiment_028.md`也与 PTP 锁定相关。这里的 `LOCK` 指时钟失锁，不是 NETCONF 配置数据存储锁；必须人工看语义，不能凭英文词面认定同源。

结论仅为“本筛查的最高近邻未显示可直接等同的新卡协议主张”，**不等于**无语义泄漏。分数未经校准；两个协议方向也不足以支撑平台总体质量估计。

## 未覆盖与下一闸门

1. 字符近邻不能识别远距离同义改写、跨文档组合、图表或设备运行知识。旧 r3 这里只比了题干；没有宣称审遍当时全部原始语料。按文件取最高一个窗口用于展示，也可能掩盖同文件其他相似段落。
2. 两张卡尚未完成独立人工事实/许可审批，且仅获当前文字内容认可和“本地演示”用途确认；`approval.status` 仍为 `pending`。
3. 需要人工逐项查看上述候选、现有知识源及未来题目的来源/正文族/主题/模板关联。若用于演示答案或调参，相关题整组只能作 dev；不得凭本报告产生 frozen 或新 Hit@5。

新增 4 项脚本单元测试通过，覆盖规范化、窗口、清单摘要对正文变化敏感及直接复用文本可被排到首位；设置 `APP_ENV_FILE=.env.test` 后，全量 `tests/unit` 回归通过。
