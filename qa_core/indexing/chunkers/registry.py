"""六类切分器实现与注册表（策略见 docs/telecom-ops-rag-design-outline.md 2.2）。"""
from __future__ import annotations

import re

from qa_core.indexing.chunkers.base import Chunk, estimate_tokens, make_chunk, split_by_window

# ---------------- 故障工单：一单一 chunk，超长拆现象/根因/处理三块 ----------------

TICKET_MAX_TOKENS = 1200
TICKET_PARTS = ("phenomenon", "root_cause", "resolution")


def chunk_ticket(rows: list[dict]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for row in rows:
        shared = {
            "doc_type": "fault_case",
            "alarm_code": (row.get("alarm_code") or "").strip(),
            "equipment_type": row.get("equipment_type") or "",
            "vendor": row.get("vendor") or "",
            "network_tech": row.get("network_tech") or "",
            "source": row.get("source") or row.get("ticket_id") or "",
        }
        body = " | ".join(
            f"{label}:{row.get(key, '')}" for key, label in
            (("title", "标题"), ("phenomenon", "现象"), ("root_cause", "根因"), ("resolution", "处理"))
            if row.get(key)
        )
        if estimate_tokens(body) <= TICKET_MAX_TOKENS:
            chunks.append(make_chunk(body, "fault_case", **shared))
            continue
        # 超长：按三段拆，共享 parent_id（检索时同 parent 只留最高分）
        parent = shared["source"] or shared["alarm_code"]
        for key, label in (("phenomenon", "现象"), ("root_cause", "根因"), ("resolution", "处理")):
            part = (row.get(key) or "").strip()
            if part:
                part_meta = {**shared, "parent_id": parent, "part": key}
                part_meta["source"] = part_meta["source"] or parent
                chunks.append(make_chunk(f"{label}:{part}", "fault_case", **part_meta))
    return chunks


# ---------------- 设备手册：标题递归切分 + 窗口兜底，章节路径入 metadata ----------------

MANUAL_MAX_TOKENS = 450
MANUAL_OVERLAP = 50
_HEADING = re.compile(r"^(#{1,4})\s+(.+)$", re.M)


def _heading_path(matches: list[re.Match], i: int) -> str:
    """标题 i 的祖先链路径：从后向前取层级递减的最近标题。"""
    parts = [matches[i].group(2).strip()]
    level = len(matches[i].group(1))
    for j in range(i - 1, -1, -1):
        if len(matches[j].group(1)) < level:
            parts.append(matches[j].group(2).strip())
            level = len(matches[j].group(1))
            if level == 1:
                break
    return "/".join(reversed(parts))


def chunk_manual(text: str, source: str = "", equipment_type: str = "", vendor: str = "") -> list[Chunk]:
    matches = list(_HEADING.finditer(text))
    sections: list[tuple[str, str]] = []  # (章节路径, 正文)
    if not matches:
        sections.append(("", text))
    else:
        # 文档开头到第一个标题
        preface = text[: matches[0].start()]
        if preface.strip():
            sections.append(("", preface))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append((_heading_path(matches, i), text[m.end(): end]))

    chunks: list[Chunk] = []
    for path, body in sections:
        for piece in split_by_window(body, MANUAL_MAX_TOKENS, MANUAL_OVERLAP):
            if piece.strip():
                chunks.append(make_chunk(
                    piece, "manual",
                    doc_type="manual", source=source, equipment_type=equipment_type,
                    vendor=vendor, chapter_path=path,
                ))
    return chunks


# ---------------- 标准：条款切分，保留条款号 ----------------

_CLAUSE = re.compile(r"^(?P<no>\d+(?:\.\d+)+)\s+(?P<body>.*)$", re.M)


def chunk_standard(text: str, source: str = "", standard_no: str = "", version: str = "") -> list[Chunk]:
    matches = list(_CLAUSE.finditer(text))
    if not matches:
        return [make_chunk(text, "standard", doc_type="standard", source=source,
                           standard_no=standard_no, version=version, clause="preface")]

    chunks: list[Chunk] = []
    preface = text[: matches[0].start()]
    if preface.strip():
        chunks.append(make_chunk(preface, "standard", doc_type="standard", source=source,
                                 standard_no=standard_no, version=version, clause="preface"))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        clause = m.group("no").strip()
        body = (m.group("body") + text[m.end(): end]).strip()
        for piece in split_by_window(body, MANUAL_MAX_TOKENS, MANUAL_OVERLAP):
            if piece.strip():
                chunks.append(make_chunk(piece, "standard", doc_type="standard", source=source,
                                         standard_no=standard_no, version=version, clause=clause))
    return chunks


# ---------------- SOP：步骤级切分（设计报告 §4.2.3：告警 SOP 每步一个 chunk） ----------------

_STEP = re.compile(r"步骤\s*(\d+)")
_STEP_SPLIT = re.compile(r"(?=步骤\s*\d+)")


def chunk_sop(text: str, source: str = "", scene: str = "", network_tech: str = "") -> list[Chunk]:
    """SOP 按步骤切分：标题/适用范围作为导语块，每个"步骤 N"独立成块。

    步骤块 text 携带 SOP 标题上下文（检索命中单步也能知道属于哪个流程）；
    步骤少于 2 个时退回整段一条。step_index 记录步骤号供前端展示。
    """
    steps = [int(n) for n in _STEP.findall(text)]
    if len(steps) < 2:
        return [make_chunk(text, "sop", doc_type="sop", source=source, scene=scene,
                           network_tech=network_tech, step_index=steps)]

    lines = text.split("\n")
    title = lines[0].strip().lstrip("# ").strip() if lines else "SOP"
    header_end = next((i for i, ln in enumerate(lines) if _STEP.search(ln)), len(lines))
    header = "\n".join(lines[:header_end]).strip()

    parts = [s.strip() for s in _STEP_SPLIT.split(text[sum(len(l) + 1 for l in lines[:header_end]):])
             if s.strip()] if header_end < len(lines) else []
    if not parts:
        body_start = text.find("步骤")
        parts = [s.strip() for s in _STEP_SPLIT.split(text[body_start:]) if s.strip()]

    chunks = [make_chunk(header or title, "sop", doc_type="sop", source=source, scene=scene,
                         network_tech=network_tech, step_index=[0])]
    for part in parts:
        m = _STEP.search(part)
        if not m:
            continue
        step_no = int(m.group(1))
        chunks.append(make_chunk(f"{title}\n{part}", "sop", doc_type="sop", source=source,
                                 scene=scene, network_tech=network_tech,
                                 step_index=[step_no]))
    return chunks


# ---------------- FAQ：Q-A 对，dense 只用 question ----------------

def chunk_faq(rows: list[dict]) -> list[Chunk]:
    chunks = []
    for row in rows:
        q = (row.get("question") or "").strip()
        a = (row.get("answer") or "").strip()
        if not q or not a:
            continue
        chunks.append(make_chunk(q, "faq", doc_type="faq", question=q, answer=a,
                                 source=row.get("source") or "",
                                 network_tech=row.get("network_tech") or ""))
    return chunks


# ---------------- 表格：整表一条，行数超限按表头分组拆，表头随块重复 ----------------

TABLE_MAX_ROWS = 30


def chunk_table(markdown_table: str, source: str = "", doc_type: str = "manual") -> list[Chunk]:
    lines = [ln for ln in markdown_table.strip().splitlines() if ln.strip()]
    if len(lines) <= 2:  # 只有表头/分隔行
        return []
    header, sep, body = lines[0], lines[1], lines[2:]
    if not set(sep.replace("|", "").replace(" ", "")) <= {"-", ":"}:
        body = lines[1:]
        header_row = []
    else:
        header_row = [header]

    if len(body) <= TABLE_MAX_ROWS:
        rows = header_row + body if header_row else [header] + body
        return [make_chunk("\n".join(rows), doc_type, doc_type=doc_type, source=source, is_table=True)]

    chunks = []
    group_size = TABLE_MAX_ROWS
    group_no = 0
    for i in range(0, len(body), group_size):
        group_no += 1
        rows = (header_row + body[i: i + group_size]) if header_row else [header] + body[i: i + group_size]
        chunks.append(make_chunk("\n".join(rows), doc_type, doc_type=doc_type, source=source,
                                 is_table=True, table_part=group_no))
    return chunks


# ---------------- 注册表 ----------------

_REGISTRY = {
    "fault_case": chunk_ticket,
    "manual": None,  # 签名不同（按整篇文本），调用方直接用
    "standard": None,
    "sop": None,
    "faq": chunk_faq,
}


def chunk_rows(doc_type: str, rows: list[dict]) -> list[Chunk]:
    """表格型知识源（工单/FAQ）统一入口。"""
    if doc_type == "fault_case":
        return chunk_ticket(rows)
    if doc_type == "faq":
        return chunk_faq(rows)
    raise ValueError(f"doc_type {doc_type} 不支持 rows 入口，请使用整篇文本切分器")
