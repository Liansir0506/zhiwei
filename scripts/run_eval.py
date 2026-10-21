#!/usr/bin/env python
"""回归评测：跑 eval_sets/v1.json 全部用例，输出指标与逐例结果。

指标（docs/telecom-ops-rag-design-outline.md 第5章）：
- 路由准确率：expect_route == 实际 route
- FAQ 直出准确率：faq_exact 类命中比例
- 命中率（recall@1）：retrieval/lookup 类，expect_hit 出现在 topN sources.source
- 拒答准确率：reject 类 answer 含"未找到"
- degraded 比率：引用模式/降级比例
- hard negative 违例率：hard_negatives 中的文档出现在 topN sources 的比例

用法：python scripts/run_eval.py --dataset <path> --split dev [--no-llm]
旧无 split 诊断集显式选择 regression；frozen/test/all 含冻结题需 --final-acceptance。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.application.qa_usecase import QaUseCase, REFUSAL_MARKERS, is_refusal_answer


# 拒答话术判定改为引用 qa_usecase 的唯一入口（7E）。
# 保留常量名以兼容既有测试；此前本地维护一份列表，与业务侧口径会悄悄分叉
# （业务侧改了话术、评测侧不知道，拒答准确率就会无声地变化）。
NO_ANSWER_MARKERS = REFUSAL_MARKERS


def select_cases(payload: dict, split: str = "dev", *, final_acceptance: bool = False) -> list[dict]:
    """默认只跑 dev；冻结集或含冻结集的 all 必须显式授权最终验收。"""
    if payload.get("metrics_eligible") is False:
        raise ValueError("待审候选不能进入正式评测")
    if payload.get("protocol") in {"eval-review-candidate-v1", "reviewed-eval-v1"}:
        from qa_core.quality.eval_review import validate_export
        validate_export(payload)
    cases = payload["cases"]
    if not cases or any(not c.get("id") or not str(c.get("query", "")).strip() for c in cases):
        raise ValueError("评测集为空或存在缺失 id/query 的用例")
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("评测集 case id 重复")
    labeled = [c.get("split") for c in cases]
    if not any(labeled):
        if split not in {"regression", "all"}:
            raise ValueError("旧无 split 诊断集需显式使用 --split regression")
        return list(cases)
    if not all(labeled):
        raise ValueError("评测集混有未标 split 的用例")
    selected = list(cases) if split == "all" else [c for c in cases if c["split"] == split]
    if not selected:
        raise ValueError(f"评测集不存在 split={split}")
    if any(c["split"] in {"frozen", "test"} for c in selected) and not final_acceptance:
        raise ValueError("包含 frozen/test，用 --final-acceptance 显式选择最终验收；旧 frozen 已使用，不再独立")
    return selected


def _git_metadata() -> dict:
    root = Path(__file__).resolve().parents[1]
    def run(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        status = run("status", "--porcelain")
        digest = hashlib.sha256()
        files = [root / "app.py"]
        for directory in ("qa_core", "scripts", "scenarios"):
            files.extend(p for p in (root / directory).rglob("*")
                         if p.is_file() and p.suffix in {".py", ".yaml", ".yml"})
        for path in sorted(files):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        return {"commit": run("rev-parse", "HEAD"), "dirty": bool(status),
                "status_sha256": hashlib.sha256(status.encode()).hexdigest(),
                "code_sha256": digest.hexdigest()}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None, "status_sha256": None, "code_sha256": None}


def _config_metadata(usecase) -> dict:
    # 白名单：不把 API Key、数据库密码或完整 .env 写进报告。
    names = ("app_env", "scenario", "doc_collection", "faq_collection", "fusion_method",
             "rerank_min", "evidence_min_coverage", "top_n_final", "top_k_dense",
             "top_k_sparse", "embedding_model_path", "reranker_model_path",
             "intent_model_path", "embedding_device", "rerank_device", "llm_model",
             "semantic_cache_enabled")
    scenario = {f.name: getattr(usecase.cfg, f.name) for f in fields(usecase.cfg)
                if not f.name.startswith("_")}
    config = {"settings": {n: getattr(usecase.settings, n) for n in names}, "scenario": scenario}
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"sha256": hashlib.sha256(canonical.encode()).hexdigest(), **config}


def _is_rejected(answer: str) -> bool:
    """评测侧的拒答判定，直接复用业务侧唯一入口，保证口径一致。"""
    return is_refusal_answer(answer)


def _match_source(expected: str, got_sources: list[str]) -> bool:
    """命中的判定口径：先精确相等，再退化为子串包含。

    与 expect_hit 保持同一口径，避免 hard negative 与正例用两套标准。
    """
    return expected in got_sources or any(expected in s for s in got_sources)


def judge(case: dict, result: dict, top_k: int = 5) -> dict:
    """判定单条用例，并保留足以定位 bad case 的现场信息。

    此前只记录 ok 布尔值，看到 hit=false 时无法知道实际命中了什么，
    排查必须重跑单条；这里补上 query、期望值、实际 sources、top 分数与答案摘要。

    hard negative 消费（第 7 步 D 阶段补）：数据集里 62/89 条标了
    hard_negatives，但此前没有任何脚本读取，等于标注白做。这里计算
    "该文档是否混进了 topN"——它代表"看似相关但结论不同"的误召回。

    **多真值**（7D 补）：`expect_hit` 允许是列表，命中任意一个即算通过。
    这不是宽松，而是表达"多篇文档同样正确"这个事实：r3 的 FAQ 同一主题下有
    50 条内容相同、只差案例编号的副本，问句不带编号时这 50 条都是正确答案。
    旧评测集正是因为没有多真值表达能力，才把同一句问话写成多条**互斥**真值
    （详见 docs/STEP7D_DIAGNOSIS.md 第 3.3 节）。
    """
    route_ok = result["route"] == case["expect_route"]
    sources = list(result.get("sources") or [])[:top_k]
    got_sources = [s.get("source", "") for s in sources]
    expected = case.get("expect_hit")
    wanted: list[str] = []
    if isinstance(expected, list):
        wanted = [w for w in expected if w]
    elif expected:
        wanted = [expected]
    hit_ok, hit_which = None, None
    if wanted:
        hit_which = next((w for w in wanted if (w in got_sources if case.get("source_match") == "exact"
                                              else _match_source(w, got_sources))), None)
        hit_ok = hit_which is not None
    reject_ok = None
    if case.get("expect_reject"):
        reject_ok = _is_rejected(result["answer"])
    hard_negatives = [h for h in (case.get("hard_negatives") or []) if h]
    hn_hits = [h for h in hard_negatives if _match_source(h, got_sources)]
    scores = [s.get("score") for s in sources if s.get("score") is not None]
    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "expect_route": case["expect_route"],
        "expect_hit": expected,
        "expect_hit_n": len(wanted),
        "hit_which": hit_which,
        "route": result["route"],
        "route_ok": route_ok,
        "hit_ok": hit_ok,
        "reject_ok": reject_ok,
        "expect_status": case.get("expect_status"),
        "status": result.get("status"),
        "status_ok": (result.get("status") == case["expect_status"] if case.get("expect_status") else None),
        # 7G 可观测性：意图仲裁走了哪条通道。此前评测报告不含该字段，
        # "模型通道是否生效 / 有没有退化成 rule_default"只能靠猜。
        "intent_source": (result.get("decision") or {}).get("source"),
        "intent": (result.get("decision") or {}).get("intent"),
        "hn_total": len(hard_negatives),
        "hn_hits": hn_hits,
        "hn_violation": bool(hn_hits),
        "got_sources": got_sources,
        "top_score": round(max(scores), 4) if scores else None,
        "answer": (result.get("answer") or "")[:200],
        "degraded": result["diagnostics"]["degraded"],
        "elapsed_ms": result["diagnostics"]["timings_ms"]["total"],
        "error": result["diagnostics"].get("error"),
    }


def _report_path(kb_version: str, run_id: str) -> Path:
    """报告文件名必须带 run_id，否则重跑会覆盖上一份。

    此前固定为 `eval_<kb_version>.json`，本次实测踩到：
    dev 与 test 两个环境跑同一个版本，第二份把第一份覆盖掉，
    只剩最后一份，无法并排比较。
    脚本里早就担心过"结果不可比"这件事，但当时只给 session 加了 run_id，
    漏了报告文件本身。
    """
    return Path("reports") / f"eval_{kb_version}_{run_id}.json"


def _assert_version_registered(kb_version: str) -> None:
    """确认版本已在 kb_versions 登记，避免版本号拼错后跑出一份全是空结果的报告。"""
    import sqlalchemy as sa

    from qa_core.config.settings import get_settings
    from qa_core.storage.kb_dao import KbDao

    dao = KbDao(get_settings().mysql_dsn)
    with dao.engine.begin() as conn:
        row = conn.execute(sa.text(
            "SELECT status FROM kb_versions WHERE version=:v"), {"v": kb_version}).first()
    if row is None:
        raise SystemExit("知识版本 %r 未在 kb_versions 中登记，拒绝执行" % kb_version)
    print("版本状态: %s" % row[0])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="F:/heima/myproject/eval_sets/v4-r3-tiered.json")
    parser.add_argument("--split", choices=("dev", "regression", "frozen", "test", "all"), default="dev")
    parser.add_argument("--final-acceptance", action="store_true")
    parser.add_argument("--kb-version", default=None,
                        help="显式固定评测的知识版本；默认取数据集声明的 kb_version")
    parser.add_argument("--no-llm", action="store_true",
                        help="retrieval 类走引用模式（不调 LLM），用于快速评测检索质量")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    try:
        cases = select_cases(payload, args.split, final_acceptance=args.final_acceptance)
    except ValueError as exc:
        parser.error(str(exc))

    # 版本必须真正固定：此前 --kb-version 只参与报告文件名，实际检索走数据库里的 ACTIVE，
    # 导致报告名与实际评测内容可能不符（写 v4-r3 却测了别的版本），
    # 且 ACTIVE 变更后重跑会覆盖同名报告、结果不可比。
    # v4-r3.json 用 kb_version 字段；v1~v3.json 用的是 version 字段，这里都认
    declared = payload.get("kb_version") or payload.get("version")
    kb_version = args.kb_version or declared
    if declared and args.kb_version and args.kb_version != declared:
        raise SystemExit("评测版本冲突：--kb-version=%s 与数据集声明的 %s 不一致，拒绝执行"
                         % (args.kb_version, declared))
    if not kb_version:
        raise SystemExit("无法确定评测版本：数据集未声明 kb_version，请显式传 --kb-version")
    _assert_version_registered(kb_version)
    # 每次运行一个 run_id：会话键带 run_id 后，本轮评测不会读到上一轮的历史
    # （此前 session_id 固定为 eval-<id>，跨次运行会复用 Redis 里的旧会话）。
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]
    print("评测数据集: %s（%d 条）" % (args.dataset, len(cases)))
    print("固定知识版本: %s" % kb_version)
    print("运行标识: %s" % run_id)

    usecase = QaUseCase()
    config_snapshot = _config_metadata(usecase)
    code_snapshot = _git_metadata()

    results = []
    for case in cases:
        t0 = time.perf_counter()
        try:
            # no-llm 严格禁止生成；该模式的拒答只衡量确定性护栏。
            use_llm = not args.no_llm
            # persist=False + run_id 前缀：评测应保持单轮可重复，既不继承上一次运行的历史，
            # 也不把批量会话写进开发环境的 sessions / messages 表。
            result = usecase.ask(case["query"], roles=["ops"],
                                 session_id="eval-%s-%s" % (run_id, case["id"]),
                                 use_llm=use_llm, kb_version=kb_version,
                                 persist=False)
        except Exception as exc:  # noqa: BLE001
            result = {"route": "error", "answer": "", "sources": [],
                      "diagnostics": {"degraded": True, "timings_ms": {"total": 0},
                                      "error": str(exc)[:120]}}
        judged = judge(case, result)
        results.append(judged)
        extras = []
        if judged["hit_ok"] is not None:
            extras.append("hit_ok=%s" % judged["hit_ok"])
        if judged["reject_ok"] is not None:
            extras.append("reject_ok=%s" % judged["reject_ok"])
        if judged["hn_total"]:
            extras.append("hn=%d/%d" % (len(judged["hn_hits"]), judged["hn_total"]))
        print(f"[{judged['id']}] route={judged['route']} "
              f"route_ok={judged['route_ok']} {' '.join(extras)} "
              f"{judged['elapsed_ms']}ms")

    n = len(results)
    hit_cases = [r for r in results if r["hit_ok"] is not None]
    reject_cases = [r for r in results if r["reject_ok"] is not None]
    hn_cases = [r for r in results if r["hn_total"] > 0]
    status_cases = [r for r in results if r["status_ok"] is not None]
    metrics = {
        "total": n,
        "status_evaluated": len(status_cases),
        "status_accuracy": (round(sum(r["status_ok"] for r in status_cases) / len(status_cases), 3)
                            if status_cases else None),
        # 此前写成 sum(... for r in routed) / len(routed)，routed 本身已按 route_ok 过滤，
        # 导致该比值恒等于 1.0 —— 无论路由实际错多少，route_accuracy 都显示 100%。
        "route_accuracy": round(sum(1 for r in results if r["route_ok"]) / n, 3) if n else None,
        "hit_accuracy": round(sum(1 for r in hit_cases if r["hit_ok"]) / len(hit_cases), 3) if hit_cases else None,
        "reject_accuracy": round(sum(1 for r in reject_cases if r["reject_ok"]) / len(reject_cases), 3) if reject_cases else None,
        # hard negative 违例率：标了 hard_negatives 的用例里，有多少条让
        # "看似相关但结论不同"的文档混进了 topN。数据集 62/89 条带标注，
        # 此前无脚本消费；不达标不代表系统坏，但它是消融实验分辨检索质量的关键指标。
        "hn_evaluated": len(hn_cases),
        "hard_negative_violation_rate": (
            round(sum(1 for r in hn_cases if r["hn_violation"]) / len(hn_cases), 3)
            if hn_cases else None),
        "degraded_ratio": round(sum(1 for r in results if r["degraded"]) / n, 3),
        "avg_elapsed_ms": round(sum(r["elapsed_ms"] for r in results) / n, 1),
    }
    # 按 category 给出细分，避免只看到聚合值而忽略某一类整体失效
    # （实测 faq_exact / alarm_lookup 两类路由全错，聚合值却因上面的 bug 显示 100%）
    metrics["by_category"] = {}
    for cat in dict.fromkeys(r["category"] for r in results):
        group = [r for r in results if r["category"] == cat]
        hits = [r for r in group if r["hit_ok"] is not None]
        rejs = [r for r in group if r["reject_ok"] is not None]
        hns = [r for r in group if r["hn_total"] > 0]
        metrics["by_category"][cat] = {
            "n": len(group),
            "route_accuracy": round(sum(1 for r in group if r["route_ok"]) / len(group), 3),
            "hit_accuracy": (round(sum(1 for r in hits if r["hit_ok"]) / len(hits), 3)
                             if hits else None),
            "reject_accuracy": (round(sum(1 for r in rejs if r["reject_ok"]) / len(rejs), 3)
                                if rejs else None),
            "hn_evaluated": len(hns),
            "hard_negative_violation_rate": (
                round(sum(1 for r in hns if r["hn_violation"]) / len(hns), 3)
                if hns else None),
        }

    report_path = _report_path(kb_version, run_id)
    report_path.parent.mkdir(exist_ok=True)
    ok = (metrics["route_accuracy"] or 0) >= 0.9 \
        and (metrics["hit_accuracy"] is None or metrics["hit_accuracy"] >= 0.85) \
        and (metrics["reject_accuracy"] is None or metrics["reject_accuracy"] >= 0.95) \
        and all(r["status_ok"] for r in status_cases)
    report_path.write_text(json.dumps({"run": {
                                       "run_id": run_id,
                                       "created_at": datetime.now(timezone.utc).isoformat(),
                                       "dataset_sha256": hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest(),
                                       "split": args.split, "case_count": len(cases),
                                       "final_acceptance": args.final_acceptance,
                                       "evaluation_role": "historical_diagnostic_regression",
                                       "frozen_independence": "not_claimed",
                                       "llm_mode": "no_llm" if args.no_llm else "deepseek_generation",
                                       "generation_quality_assessed": not args.no_llm,
                                       "session_policy": "isolated_no_persistence",
                                       "config": config_snapshot,
                                       "code": code_snapshot,
                                       "code_changed_during_run": code_snapshot.get("code_sha256") != _git_metadata().get("code_sha256")},
                                       "kb_version": kb_version,
                                       "dataset": str(args.dataset),
                                       "run_id": run_id,
                                       "passed": ok,
                                       "complete": not args.no_llm,
                                       "thresholds": {"route_accuracy": 0.9, "hit_accuracy": 0.85,
                                                      "reject_accuracy": 0.95},
                                       "metrics": metrics, "results": results},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"report -> {report_path}")

    # 验收阈值：路由准确率 ≥ 0.9，命中 ≥ 0.85，拒答 ≥ 0.95
    # hard_negative_violation_rate 只统计不设阈值：当前数据集本身不可信
    # （见 docs/STEP7D_DIAGNOSIS.md，存在 5 组同一问话多个互斥真值），
    # 在没有可信基线之前给它设阈值，等于用一个坏尺子去卡另一个坏尺子。
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
