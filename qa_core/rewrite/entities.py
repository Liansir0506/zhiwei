"""查询侧实体提取：从问句里识别可用于精确匹配的实体标识。

目前只做 FAQ 编号。r3 的 FAQ 由模板批量生成，1000 条 question 结构几乎一致
（`X出现ALM-Y时，案例SIM-FAQ-Z的第N类上下文应先核对什么？`），唯一区分度就是
编号 token，而向量模型对它不敏感——实测问句里已经写了"案例SIM-FAQ-0199"，
向量检索仍会返回别的条目。因此编号必须单独提取出来做精确匹配。
"""
from __future__ import annotations

import re

# 问句里写作 "案例SIM-FAQ-0199"，数据里的 source 写作 "FAQ-SIM-0199"，两种都认
_FAQ_ID_RE = re.compile(r"(?:SIM-FAQ|FAQ-SIM)[-_]?(\d{3,6})", re.IGNORECASE)


def faq_ids(text: str | None) -> set[str]:
    """提取文本中的 FAQ 编号（返回数字串集合，保留前导零以便与 source 对齐）。"""
    return set(_FAQ_ID_RE.findall(text or ""))
