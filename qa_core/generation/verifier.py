"""生成后核验：答案中出现的告警码/条款号必须能在证据上下文中找到，否则降级。"""
from __future__ import annotations

import re

ALARM_RE = re.compile(r"ALM[-\s]?\d{4}", re.IGNORECASE)
CLAUSE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{1,2})+\b")


def _normalize_code(s: str) -> str:
    return re.sub(r"[-\s]", "", s.upper())


def verify_answer(answer: str, evidence_text: str) -> tuple[bool, list[str]]:
    """返回 (是否通过, 问题清单)。"""
    problems: list[str] = []
    evidence_norm = _normalize_code(evidence_text)
    for code in {m.group(0) for m in ALARM_RE.finditer(answer)}:
        if _normalize_code(code) not in evidence_norm:
            problems.append(f"答案中的告警码 {code} 未出现在证据中")
    for clause in {m.group(0) for m in CLAUSE_RE.finditer(answer)}:
        # 排除明显的序号误报：证据中条款级数字串才算
        if clause not in evidence_text:
            problems.append(f"答案中的条款号 {clause} 未出现在证据中")
    return not problems, problems
