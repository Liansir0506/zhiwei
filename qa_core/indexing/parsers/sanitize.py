"""入库前脱敏扫描（质量门禁第 5 项的前置）：手机号/身份证/IPv4/密码字段。

替换为占位符而非删除，保证上下文仍可读。规则是白名单式保守匹配，
误伤率优先让位于漏配率——运维工单里 IP 和账号常见。
"""
from __future__ import annotations

import re

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
SECRET_LINE = re.compile(r"(?i)(password|passwd|pwd|secret|token)\s*[:=]\s*(\S+)")

REPLACEMENTS = [
    (SECRET_LINE, r"\1:{SECRET}"),
    (PHONE, "{PHONE}"),
    (ID_CARD, "{ID}"),
    (IPV4, "{IP}"),
]

# 乱码判定：控制字符（除\n\t\r）与 U+FFFD
_SUSPECT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")


def sanitize(text: str) -> tuple[str, dict[str, int]]:
    """返回 (脱敏后文本, 各类命中计数)。"""
    counts: dict[str, int] = {}
    out = text
    for pattern, repl in REPLACEMENTS:
        out, n = pattern.subn(repl, out)
        key = pattern.pattern[:20]
        counts[key] = n
    return out, counts


def garbled_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_SUSPECT.findall(text)) / len(text)
