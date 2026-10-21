"""在线输入管道：归一化 → 注入扫描 → 缩略语展开 → 多查询变体（检索用）。"""
from __future__ import annotations

import re
import unicodedata


def normalize_query(query: str) -> str:
    """全半角/空白归一（与离线入库侧 normalize_text 同款逻辑）。"""
    query = unicodedata.normalize("NFKC", query)
    import re

    return re.sub(r"[ \t]+", " ", query).strip()


def expand_abbreviations(query: str, abbreviations: dict[str, str]) -> tuple[str, list[str]]:
    """返回 (原句, 缩略语展开句列表)。命中的缩略语各生成一句展开查询。"""
    variants = []
    for abbr, full in abbreviations.items():
        if abbr in query:
            variants.append(query.replace(abbr, f"{abbr}({full})"))
    return query, variants


def build_query_variants(query: str, abbreviations: dict[str, str],
                         rewritten: str | None = None, max_variants: int = 3) -> list[str]:
    """多查询变体：改写句 > 缩略语展开句 > 原句，去重限 3。"""
    variants: list[str] = []
    if rewritten and rewritten != query:
        variants.append(rewritten)
    _, expanded = expand_abbreviations(query, abbreviations)
    variants.extend(expanded)
    variants.append(query)
    out: list[str] = []
    for v in variants:
        if v and v not in out:
            out.append(v)
        if len(out) >= max_variants:
            break
    return out


_PRONOUN_TAILS = ("设备", "告警", "网元", "问题", "小区", "站点", "版本", "故障",
                  "参数", "流程", "情况", "型号", "对象", "工单", "指标", "网管")


def _has_pronoun(query: str) -> bool:
    """是否含指代用法。

    不能拿单字 "该" 做子串匹配：它会被"应该 / 不该 / 活该"这类词命中，
    于是任何"…应该怎么处理"的追问在有会话历史时都会被拼上上一轮问题，
    实测会造成 alarm_code 被上一轮覆盖。同理"它"要排除"其他"。
    """
    if any(p in query for p in ("这个", "那个", "这种", "上述")):
        return True
    if "它" in query and "其他" not in query:
        return True
    if re.match(r"^(?:那(?:么)?|接下来|接着|还(?:需要|要|能|可以|有))", query):
        return True
    return bool(re.search(r"该(?:%s)" % "|".join(_PRONOUN_TAILS), query))


def resolve_coreference(query: str, history: list[dict]) -> str | None:
    """多轮指代消解（轻量版）：query 含指代词且有历史时，拼接最近一轮用户问题。"""
    if not _has_pronoun(query) or not history:
        return None
    last_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
    if last_user:
        return f"{last_user} {query}"
    return None
