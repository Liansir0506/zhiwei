"""切分器公共类型与 token 粗估。

token 估算用 len(text)//2（中文为主语料的工程粗估），仅用于控制 chunk 大小，
不追求与 BGE tokenizer 一致；入库前的真实截断由 embedder 负责（8192 上限）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str
    doc_type: str
    metadata: dict = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def make_chunk(text: str, kind: str | None = None, **meta) -> Chunk:
    """doc_type 可由第二个位置参数或 meta['doc_type'] 提供，二者不可同时缺失。"""
    doc_type = kind or meta.pop("doc_type", None)
    if not doc_type:
        raise ValueError("doc_type is required")
    return Chunk(text=text.strip(), doc_type=doc_type, metadata=meta)


def split_by_window(text: str, max_tokens: int = 450, overlap_tokens: int = 50) -> list[str]:
    """句子边界优先的滑动窗口切分（手册类通用兜底）。"""
    max_chars = max_tokens * 2
    overlap_chars = overlap_tokens * 2
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    # 先按句子切
    import re

    sentences = [s for s in re.split(r"(?<=[。！？.!?\n])", text) if s.strip()]
    pieces: list[str] = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) > max_chars and buf:
            pieces.append(buf)
            buf = buf[-overlap_chars:] + s if overlap_chars else s
        else:
            buf += s
    if buf.strip():
        pieces.append(buf)
    return pieces
