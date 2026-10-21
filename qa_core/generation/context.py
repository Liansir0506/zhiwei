"""提示词模板（system 四要素：角色、边界、输出模板、拒答）与上下文构建（8K 预算）。"""
from __future__ import annotations

from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.retrieval.evidence_text import source_annotation_text

SYSTEM_PROMPT = """你是通信网络运维知识助理，只依据下方【检索证据】回答问题。

边界（必须遵守）：
1. 只使用证据中出现的信息，禁止编造告警码、参数值、条款号；
2. 证据不足以回答时，不要使用输出模板，只回复这一句话："抱歉，暂时未找到与您问题相关的资料。请换个说法重新输入，或从推荐问题中选择咨询；紧急故障请联系值班工程师。"，不要猜测、不要编造排查步骤；
3. 不提供任何现网操作指令（重启/下电/配置下发），只给排查思路并建议按 SOP 执行；
4. 引用证据时在句尾标注编号 [n]。
5. 会话历史只用于理解追问的对象与条件，不作为事实证据；事实和引用以本轮检索证据为准。
6. “〔原文注n〕”是原文脚注，不是本轮知识库来源编号；引用只使用证据块头部的 [n]。

输出模板：
【结论】一句话结论
【排查步骤】编号步骤（如证据为 SOP/工单，按其步骤顺序）
【风险提示】注意事项（无则省略）
【引用】[n] ...

【检索证据】
{evidence}"""


def build_evidence(docs: list[RetrievedDoc], max_chars_per_doc: int = 1000) -> str:
    """证据块格式：[n] 来源|设备|告警码|条款 | 内容。按传入顺序（rerank 降序）。"""
    blocks = []
    for i, d in enumerate(docs, 1):
        head = f"[{i}] 来源:{d.source or d.id}"
        if d.doc_type:
            head += f" | 类型:{d.doc_type}"
        if d.equipment_type:
            head += f" | 设备:{d.equipment_type}"
        if d.alarm_code:
            head += f" | 告警码:{d.alarm_code}"
        if d.clause:
            head += f" | 条款:{d.clause}"
        text = source_annotation_text(d.answer if d.doc_type == "faq" and d.answer else d.text)
        if d.extra.get("workflow_evidence"):
            from qa_core.retrieval.workflow import MAX_WORKFLOW_CHARS
            if len(text) > MAX_WORKFLOW_CHARS or not d.extra.get("workflow_ready"):
                raise ValueError("unsafe or oversized workflow evidence")
            blocks.append(f"{head}\n{text}")
        else:
            blocks.append(f"{head}\n{text[:max_chars_per_doc]}")
    return "\n\n".join(blocks)


def build_messages(query: str, docs: list[RetrievedDoc],
                   chat_history: list[dict] | None = None) -> list[dict]:
    evidence = build_evidence(docs)
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(evidence=evidence)}]
    if any(d.extra.get("workflow_evidence") for d in docs):
        messages[0]["content"] += ("\n流程仅适用于证据声明的设备、版本和症状；保持步骤顺序、审批、回滚与停止条件。"
                                   "不将排除/不适用分支作为当前动作；沙箱记录不用于现网操作，不推断根因已确认。")
    # 多轮历史注入（最近 4 条，截断防超预算）
    for m in (chat_history or [])[-4:]:
        content = str(m.get("content", ""))[:500]
        if content and m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": content})
    messages.append({"role": "user", "content": query})
    return messages


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def context_budget_ok(messages: list[dict], budget_tokens: int = 8000) -> bool:
    total = sum(estimate_tokens(m["content"]) for m in messages)
    return total <= budget_tokens
