"""BGE Reranker（CrossEncoder）精排封装。

- 输出 sigmoid(logit) ∈ 0-1（bge-reranker 原始输出是无界 logit）；
- 输入截断 512 token（reranker 上限）；
- 只在召回后执行，不参与入库、不替代 Milvus；
- rerank 分 < rerank_min 时由调用方走"资料不足"保护。
"""
from __future__ import annotations

import math
import threading

_lock = threading.Lock()
_singleton: "BGEReranker | None" = None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class BGEReranker:
    def __init__(self, model_path: str, device: str = "cpu", max_length: int = 512):
        # sentencepiece segfault 规避见 qa_core/__init__.py（stub 在包 import 时已注入）
        from sentence_transformers import CrossEncoder

        # bge-reranker-large 目录原本无 tokenizer.json，fast 转换依赖 sentencepiece（本机崩溃）。
        # 两模型同为 XLM-R 250k 词表，直接复用 bge-m3 的 tokenizer.json 绕开 sentencepiece。
        self.model = CrossEncoder(model_path, device=device, max_length=max_length)

    def rerank(self, query: str, docs: list, top_n: int = 5) -> list:
        """docs: list[RetrievedDoc]；返回按 rerank 分降序的前 top_n。"""
        if not docs:
            return []
        from qa_core.retrieval.evidence_text import rerank_text
        pairs = [(query, rerank_text(query, d)) for d in docs]
        logits = self.model.predict(pairs, show_progress_bar=False)
        import numpy as np

        for doc, logit in zip(docs, logits):
            doc.rerank_score = round(float(_sigmoid(float(logit))), 4)
        ranked = sorted(docs, key=lambda d: d.rerank_score, reverse=True)
        return ranked[:top_n]


def get_reranker(model_path: str, device: str = "cpu") -> BGEReranker:
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = BGEReranker(model_path, device)
    return _singleton


def reset_reranker() -> None:
    global _singleton
    with _lock:
        _singleton = None
