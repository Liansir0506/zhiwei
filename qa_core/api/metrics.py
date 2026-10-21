"""Prometheus 指标记录器（qa_usecase 埋点用；端点在 route_metrics.py）。

prometheus_client 为可选依赖：镜像未装时降级为 no-op 计数（不影响问答主流程），
容器内 `pip install prometheus_client` 后重启即恢复完整指标。
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram
except ImportError:  # pragma: no cover - 镜像精简场景
    Counter = Histogram = None

if Counter is not None:
    QA_TOTAL = Counter("qa_requests_total", "问答请求总数", ["route", "intent"])
    QA_LATENCY = Histogram("qa_latency_seconds", "问答端到端延迟",
                           ["route"], buckets=(0.5, 1, 2, 5, 10, 30, 60))
    ALERT_TOTAL = Counter("alert_webhooks_total", "告警 webhook 总数")
    LLM_CALLS = Counter("qa_llm_calls_total", "LLM 生成调用总数（含重生成）")
    SEM_CACHE_HITS = Counter("qa_semantic_cache_hits_total", "语义缓存命中次数")
else:
    class _Noop:
        def labels(self, *a, **k):
            return self
        def inc(self, *a, **k):
            pass
        def observe(self, *a, **k):
            pass

    QA_TOTAL = QA_LATENCY = ALERT_TOTAL = LLM_CALLS = SEM_CACHE_HITS = _Noop()


def record_qa(route: str, intent: str, latency_s: float) -> None:
    QA_TOTAL.labels(route=route, intent=intent).inc()
    QA_LATENCY.labels(route=route).observe(latency_s)
