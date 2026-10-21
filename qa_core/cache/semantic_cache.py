"""语义缓存：embedding 相似度 ≥ 阈值时直接复用历史答案，省 LLM 调用。

答辩要点：精确缓存（同问句）命中率低，语义缓存按向量余弦相似度命中
"换了个说法的同一个问题"。只有**通过生成后核验**的答案才写入，避免错误
答案被缓存传播。kb_version 参与 key，版本切换天然失效。
"""
from __future__ import annotations

import json
import hashlib
import time

import numpy as np

from qa_core.cache.redis_cache import CacheLayer

THRESHOLD_HIT = 0.82   # 达到即直接复用（实测：同义问题对 ~0.86，无关问题 ~0.70）
DIM = 128              # 存储截断维度（近似相似度，降低存储与计算）


class SemanticCache:
    def __init__(self, cache_layer: CacheLayer, kb_version: str,
                 threshold: float = THRESHOLD_HIT, max_entries: int = 300,
                 scope: str = "default"):
        self.cl = cache_layer
        self.kb_version = kb_version
        self.threshold = threshold
        self.max_entries = max_entries
        self.scope = hashlib.sha256(scope.encode()).hexdigest()[:16]

    def _key(self) -> str:
        return f"sem:{self.cl.scenario}:{self.kb_version}:{self.scope}"

    def lookup(self, vec: list[float]) -> dict | None:
        try:
            raw = self.cl.client.get(self._key())
        except Exception:  # noqa: BLE001
            return None
        if not raw:
            return None
        entries = json.loads(raw)
        if not entries:
            return None
        q = np.asarray(vec[:DIM], dtype=np.float32)
        qn = q / (np.linalg.norm(q) + 1e-9)
        best, best_sim = None, 0.0
        for e in entries:
            v = np.asarray(e["vec"], dtype=np.float32)
            sim = float(np.dot(qn, v / (np.linalg.norm(v) + 1e-9)))
            if sim > best_sim:
                best, best_sim = e, sim
        if best and best_sim >= self.threshold:
            return {"answer": best["answer"], "sources": best["sources"],
                    "message_id": None, "similarity": round(best_sim, 4),
                    "route": best.get("route", "retrieval")}
        return None

    def store(self, vec: list[float], payload: dict) -> None:
        try:
            raw = self.cl.client.get(self._key())
            entries = json.loads(raw) if raw else []
            entries.append({"vec": [round(float(x), 5) for x in vec[:DIM]],
                            "ts": time.time(), **payload})
            entries = entries[-self.max_entries:]
            self.cl.client.set(self._key(), json.dumps(entries, ensure_ascii=False),
                               ex=self.cl.ttl.get("ret", 600) * 12)  # 约 2 小时
        except Exception:  # noqa: BLE001
            pass
