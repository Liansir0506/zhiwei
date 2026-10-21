"""Redis 滑动窗口限流：按认证主体（user_id）限 QPS，Redis 故障时 fail-open。

答辩要点：固定窗口计数（INCR+EXPIRE）实现简单且原子；多副本部署时
计数在 Redis 天然共享，是会话内存方案做不到的。
"""
from __future__ import annotations

import time

import redis as redis_lib

from fastapi import HTTPException

from qa_core.config.settings import get_settings

_client: redis_lib.Redis | None = None
_client_failed = False


def _redis():
    global _client, _client_failed
    if _client is None and not _client_failed:
        try:
            _client = redis_lib.Redis.from_url(
                get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1)
            _client.ping()
        except Exception:  # noqa: BLE001 —— Redis 故障放行（fail-open）
            _client_failed = True
    return _client


def check_rate_limit(user_id: str, limit_qps: int) -> None:
    """固定窗口 1s 计数；超限抛 429。"""
    client = _redis()
    if client is None:
        return
    key = f"rl:{user_id}:{int(time.time())}"
    try:
        cnt = client.incr(key)
        if cnt == 1:
            client.expire(key, 2)
    except Exception:  # noqa: BLE001
        return
    if cnt > limit_qps:
        raise HTTPException(status_code=429, detail=f"请求过于频繁（限制 {limit_qps}/s），请稍后再试")
