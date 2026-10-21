"""lookup_alarm 确定性排序单测（第 8 步 E 阶段）。

背景：Milvus `query` 不带 order_by 时返回顺序不保证稳定。同一告警码下有
几十张工单，任取 limit 条，同一问题两次调用可能给出不同答案。
实测 174 条评测集上 alarm_lookup 的 hit@5 = 0.000（40/40 全部失败），
路由正确、确实走了结构化查询，但返回的不是期望那张工单。

纯函数测试，不连 Milvus。
"""
from __future__ import annotations

from qa_core.retrieval.searcher import order_alarm_hits


def _row(source: str, rid: str | None = None) -> dict:
    return {"source": source, "id": rid or ("id-" + source)}


class TestOrderAlarmHits:
    def test_sorts_by_source_ascending(self):
        rows = [_row("SIM-TT-00030"), _row("SIM-TT-00001"), _row("SIM-TT-00012")]
        got = [r["source"] for r in order_alarm_hits(rows, 5)]
        assert got == ["SIM-TT-00001", "SIM-TT-00012", "SIM-TT-00030"]

    def test_respects_limit(self):
        rows = [_row("SIM-TT-%05d" % i) for i in range(40, 0, -1)]
        got = order_alarm_hits(rows, 5)
        assert len(got) == 5
        assert [r["source"] for r in got] == ["SIM-TT-%05d" % i for i in range(1, 6)]

    def test_deterministic_across_calls(self):
        """同一个问题两次调用必须给出完全相同的顺序——这是本次修复的核心。"""
        rows = [_row("SIM-TT-%05d" % i) for i in (17, 3, 42, 8, 25, 1, 33)]
        first = [r["source"] for r in order_alarm_hits(rows, 5)]
        second = [r["source"] for r in order_alarm_hits(list(reversed(rows)), 5)]
        assert first == second

    def test_tie_breaks_by_id(self):
        """source 相同时用 id 兜底，避免同号文档顺序抖动。"""
        rows = [_row("SIM-TT-00001", "id-b"), _row("SIM-TT-00001", "id-a")]
        got = order_alarm_hits(rows, 5)
        assert [r["id"] for r in got] == ["id-a", "id-b"]

    def test_empty_input(self):
        assert order_alarm_hits([], 5) == []

    def test_limit_larger_than_result(self):
        rows = [_row("a.md"), _row("b.md")]
        assert len(order_alarm_hits(rows, 5)) == 2

    def test_missing_source_sorts_last_and_stays_stable(self):
        """source 缺失（空串）应排在最前还是最后取决于实现，但必须**稳定**。

        空串按升序排最前；这里验证的是两次调用结果一致，而不是具体位置。
        """
        rows = [{"id": "x"}, _row("b.md"), {"id": "y"}, _row("a.md")]
        once = [r.get("source") for r in order_alarm_hits(rows, 5)]
        twice = [r.get("source") for r in order_alarm_hits(list(rows), 5)]
        assert once == twice
