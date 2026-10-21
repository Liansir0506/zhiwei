"""混合负载压测：模拟真实运维问答流量分布。

- 问题池来自评测集 70 条（真实分布：FAQ 直出/工单速查/检索生成/拒答）
- 每虚拟用户独立 X-User-Id（绕过单用户 5 QPS 限流，模拟多用户场景）
- 语义缓存命中率会被真实触发并统计（生产预期行为）
- 用法：python -m locust -f tests/loadtest/locustfile.py --host http://127.0.0.1:8010
       --headless -u 20 -r 5 -t 2m --csv reports/loadtest_20u --html reports/loadtest.html
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from locust import HttpUser, between, events, task

_questions: list[str] = []
_route_counter: Counter = Counter()
_cache_hits = {"hit": 0, "total": 0}


@events.init.add_listener
def _load_questions(environment, **kwargs):
    data = json.loads(Path("eval_sets/v3.json").read_text(encoding="utf-8"))
    _questions.extend(c["query"] for c in data["cases"])
    print(f"[loadtest] 问题池 {len(_questions)} 条")


@events.quitting.add_listener
def _summary(environment, **kwargs):
    total = _cache_hits["total"]
    if total:
        print(f"[loadtest] 语义缓存命中率: {_cache_hits['hit']}/{total} = "
              f"{_cache_hits['hit'] / total:.1%}")
    print("[loadtest] 路由分布:", dict(_route_counter))


class OpsUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.uid = f"load-{random.randint(1, 100000)}"

    @task(1)
    def ask(self):
        q = random.choice(_questions)
        with self.client.post(
            "/qa",
            json={"query": q, "session_id": f"load-{self.uid}"},
            headers={"X-User-Id": self.uid, "X-User-Roles": "ops"},
            name="/qa [mixed]",
            catch_response=True,
        ) as resp:
            _cache_hits["total"] += 1
            if resp.status_code != 200:
                resp.failure(f"http-{resp.status_code}")
                return
            d = resp.json()
            _route_counter[d.get("route", "?")] += 1
            ret = d.get("diagnostics", {}).get("retrieval", {})
            if ret.get("semantic_cache_hit"):
                _cache_hits["hit"] += 1
            if not d.get("answer"):
                resp.failure("empty-answer")
            else:
                resp.success()
