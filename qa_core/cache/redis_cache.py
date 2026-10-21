"""L2 Redis 缓存层：emb/ret/faq 三类 key + perm_hash + 三防（穿透/击穿/雪崩）。

Key 规范（docs/DATA_MODEL.md §3）：
  emb:{model}:{sha256(prefix+query)}                                 TTL 24h
  ret:{scenario}:{kb_version}:{perm_hash}:{sha256(query_signature)}  TTL 10min
  faq:{scenario}:{kb_version}:{perm_hash}:{sha256(query)}            TTL 1h
Redis 不可用时全部降级为直连（缓存旁路），只记 misses，不抛异常。
"""
from __future__ import annotations

import hashlib
import json
import random
import time

import redis as redis_lib


def perm_hash(roles: list[str]) -> str:
    return hashlib.md5(",".join(sorted(roles or [])).encode()).hexdigest()[:12]


def _sig(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


class CacheLayer:
    def __init__(self, redis_url: str, scenario: str = "telecom_ops",
                 emb_ttl: int = 86400, ret_ttl: int = 600, faq_ttl: int = 3600):
        self.scenario = scenario
        self.ttl = {"emb": emb_ttl, "ret": ret_ttl, "faq": faq_ttl}
        self.hits = 0
        self.misses = 0
        self.bypass = False
        try:
            self.client = redis_lib.Redis.from_url(
                redis_url, socket_connect_timeout=2, socket_timeout=2)
            self.client.ping()
        except Exception:  # noqa: BLE001 —— Redis 故障 = 缓存旁路
            self.client = None
            self.bypass = True

    # ---- 基础 ----

    def _key(self, kind: str, kb_version: str, ph: str, sig: str) -> str:
        return f"{kind}:{self.scenario}:{kb_version}:{ph}:{sig}"

    def _ttl(self, kind: str) -> int:
        base = self.ttl[kind]
        return int(base * (0.9 + random.random() * 0.2))  # 雪崩防护：TTL ±10% 抖动

    def get(self, kind: str, kb_version: str, ph: str, sig: str):
        if self.bypass or self.client is None:
            self.misses += 1
            return None
        try:
            raw = self.client.get(self._key(kind, kb_version, ph, sig))
        except Exception:  # noqa: BLE001
            self.misses += 1
            return None
        if raw is None:
            self.misses += 1
            return None
        self.hits += 1
        return json.loads(raw)

    def set(self, kind: str, kb_version: str, ph: str, sig: str, value) -> None:
        if self.bypass or self.client is None:
            return
        try:
            self.client.set(self._key(kind, kb_version, ph, sig),
                            json.dumps(value, ensure_ascii=False), ex=self._ttl(kind))
        except Exception:  # noqa: BLE001
            pass

    def set_empty(self, kind: str, kb_version: str, ph: str, sig: str) -> None:
        """空结果缓存 60s（穿透防护）。"""
        if self.bypass or self.client is None:
            return
        try:
            self.client.set(self._key(kind, kb_version, ph, sig),
                            json.dumps({"__empty__": True}), ex=60)
        except Exception:  # noqa: BLE001
            pass

    def get_with_lock(self, kind: str, kb_version: str, ph: str, sig: str):
        """命中返回数据；未命中尝试拿互斥锁（击穿防护），未抢到锁返回 LOCKED。"""
        data = self.get(kind, kb_version, ph, sig)
        if data is not None:
            if isinstance(data, dict) and data.get("__empty__"):
                return []
            return data
        if self.bypass or self.client is None:
            return None
        try:
            lock_key = f"lock:{self._key(kind, kb_version, ph, sig)}"
            acquired = self.client.set(lock_key, "1", nx=True, ex=5)
            return "LOCKED" if not acquired else None
        except Exception:  # noqa: BLE001
            return None

    def invalidate_version(self, kb_version: str, *, strict: bool = False) -> int:
        """版本切换/回滚时按 version 段精准清理 ret:/faq:（SCAN 而非 KEYS）。"""
        if self.bypass or self.client is None:
            if strict:
                raise RuntimeError("cache invalidation unavailable")
            return 0
        deleted = 0
        try:
            for kind in ("ret", "faq", "sem"):
                pattern = f"{kind}:{self.scenario}:{kb_version}:*"
                cursor = 0
                while True:
                    cursor, keys = self.client.scan(cursor, match=pattern, count=200)
                    if keys:
                        deleted += self.client.delete(*keys)
                    if cursor == 0:
                        break
        except Exception:  # noqa: BLE001
            if strict:
                raise
        return deleted

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "bypass": self.bypass}


def now_ms() -> float:
    return time.perf_counter() * 1000
