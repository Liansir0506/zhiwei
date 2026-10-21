"""答案质量评估：LLM-as-judge 抽样打分（补齐评测体系"答案质量"维度）。

分层抽样 20 条评测用例 → 真实全链路生成答案（含 LLM）→
DeepSeek 作为裁判按 faithfulness（忠实度）/ relevance（相关性）1-5 打分。
输出 reports/answer_quality.json。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_core.application.qa_usecase import QaUseCase
from qa_core.generation.llm import LLMClient
from qa_core.config.settings import get_settings

JUDGE_PROMPT = """你是 RAG 系统的答案质量评审员。给定【问题】【回答】【检索来源摘要】，请严格评分：

1. faithfulness（忠实度 1-5）：回答中的事实是否都被来源支撑，有没有编造告警码/参数/步骤；
2. relevance（相关性 1-5）：回答是否切题、是否实际解决了问题（"未找到"类回答若确属库外问题可得 4-5，库内可得问题才扣分）。

只输出 JSON：{{"faithfulness": n, "relevance": n, "reason": "一句话"}}"""

SAMPLE_PER_CAT = {"faq_exact": 7, "retrieval": 9, "alarm_lookup": 4}


def main() -> None:
    data = json.loads(Path("eval_sets/v3.json").read_text(encoding="utf-8"))
    sample = []
    for cat, n in SAMPLE_PER_CAT.items():
        pool = [c for c in data["cases"] if c["category"] == cat]
        sample += pool[:n]  # 取前 n 条（评测集已按领域排序，覆盖各设备域）

    uc = QaUseCase()
    settings = get_settings()
    judge = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)

    results = []
    for i, case in enumerate(sample, 1):
        t0 = time.perf_counter()
        r = uc.ask(case["query"], roles=["ops"], session_id=f"judge-{i}")
        answer = r["answer"] or ""
        # 按 doc_id 从 Milvus 拉真实 chunk 原文（裁判必须看到证据内容才能判忠实度）
        from qa_core.storage.milvus_client import query_with_retry

        def _chunk_text(doc_id: str) -> str:
            for coll, fields in ((settings.doc_collection, ["text", "source"]),
                                 (settings.faq_collection, ["question", "answer", "source"])):
                try:
                    rows = query_with_retry(settings.milvus_uri, coll,
                                            filter=f'id == "{doc_id}"',
                                            output_fields=fields, limit=1)
                    if rows:
                        r0 = rows[0]
                        return (r0.get("text")
                                or f"Q: {r0.get('question', '')}\nA: {r0.get('answer', '')}")
                except Exception:  # noqa: BLE001
                    pass
            return ""

        src_names = [s.get("source", "") for s in r["sources"]]
        evidence = "\n".join(
            f"[{k}] {s.get('source', '')}: {_chunk_text(s.get('doc_id', ''))[:350]}"
            for k, s in enumerate(r["sources"], 1)) or "（无证据——系统判定未找到相关内容）"
        try:
            raw = judge.chat([
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content":
                    f"【问题】{case['query']}\n\n【回答】\n{answer[:1500] or '（空回答）'}\n\n"
                    f"【检索来源】文件：{', '.join(src_names) or '无（系统判定未找到相关内容）'}\n\n"
                    f"【证据摘要】\n{evidence[:1200]}"}],
                max_tokens=500, retries=3)
            m = re.search(r"\{[^}]*\}", raw, re.S)
            scores = json.loads(m.group(0)) if m else {}
        except Exception as exc:  # noqa: BLE001 —— 裁判失败该条跳过不计
            print(f"[{i}/{len(sample)}] judge 失败: {exc}")
            continue
        results.append({
            "id": case["id"], "category": case["category"], "query": case["query"],
            "faithfulness": int(scores.get("faithfulness", 0)),
            "relevance": int(scores.get("relevance", 0)),
            "reason": str(scores.get("reason", ""))[:120],
            "route": r["route"], "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        })
        print(f"[{i}/{len(sample)}] {case['id']} {case['category']:<14} "
              f"F={results[-1]['faithfulness']} R={results[-1]['relevance']} "
              f"{results[-1]['reason'][:36]}")

    n = len(results)
    out = {
        "sample_size": n,
        "faithfulness_avg": round(sum(r["faithfulness"] for r in results) / n, 2) if n else 0,
        "relevance_avg": round(sum(r["relevance"] for r in results) / n, 2) if n else 0,
        "results": results,
    }
    Path("reports/answer_quality.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nfaithfulness 均分: {out['faithfulness_avg']} / 5")
    print(f"relevance 均分:   {out['relevance_avg']} / 5")
    print("report -> reports/answer_quality.json")


if __name__ == "__main__":
    main()
