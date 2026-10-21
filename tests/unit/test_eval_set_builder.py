"""评测集生成器的结构自检（第 7 步 D 阶段）。

`validate()` 是 7D 的护栏：它拦住的每一条都对应旧评测集真实发生过的缺陷。
这些测试保证护栏本身不会悄悄失效——护栏失效比数据出错更危险，
因为下游会以为数据已经检查过了。

不依赖 Milvus / MySQL：validate() 只吃一个 payload dict。
"""
from __future__ import annotations

import pytest

from scripts.build_eval_set_r5 import validate


def _case(cid="R01", template="sop_by_alarm", category="retrieval",
          query="查询", route="retrieval", hit="sop_operation_001.md",
          difficulty="medium", split="dev", hn=None):
    return {"id": cid, "template_id": template, "category": category,
            "query": query, "expect_route": route, "expect_hit": hit,
            "expect_reject": False, "difficulty": difficulty, "split": split,
            "hard_negatives": hn or [], "note": ""}


def _payload(cases):
    return {"version": "test", "kb_version": "v4-foundation-r3", "cases": cases}


class TestValidPayloadPasses:
    def test_minimal_ok(self):
        validate(_payload([_case()]))

    def test_multi_truth_allowed_on_faq_no_case_id(self):
        validate(_payload([_case("FH01", "faq_no_case_id", "faq_exact",
                                 route="faq_exact", hit=["a.md", "b.md"])]))

    def test_synthetic_case_has_no_truth(self):
        validate(_payload([_case("G01", "dangerous_instruction", "guardrail",
                                 route="direct_answer", hit=None)]))


class TestDuplicateQueryRejected:
    def test_same_query_twice_fails(self):
        # 旧评测集正是这样产出 5 组重复用例、每组真值还互斥
        with pytest.raises(SystemExit) as e:
            validate(_payload([_case("R01", query="同一句话"),
                               _case("R02", query="同一句话",
                                     hit="sop_operation_002.md")]))
        assert e.value.code == 2

    def test_whitespace_only_difference_still_counts_as_duplicate(self):
        with pytest.raises(SystemExit):
            validate(_payload([_case("R01", query="A B C"),
                               _case("R02", query="A  B C",
                                     hit="sop_operation_002.md")]))


class TestSourceCrossingSplitsRejected:
    def test_same_truth_in_two_splits_fails(self):
        # 同一源文档既在 dev 又在 frozen，等于把答案的难度信息泄漏给了调参
        with pytest.raises(SystemExit) as e:
            validate(_payload([
                _case("R01", query="问题一", hit="sop_operation_001.md", split="dev"),
                _case("R02", query="问题二", hit="sop_operation_001.md", split="frozen"),
            ]))
        assert e.value.code == 2

    def test_multi_truth_list_is_expanded_when_checking(self):
        with pytest.raises(SystemExit):
            validate(_payload([
                _case("FH01", "faq_no_case_id", "faq_exact", route="faq_exact",
                      query="问一", hit=["a.md", "b.md"], split="dev"),
                _case("FV01", "faq_verbatim", "faq_exact", route="faq_exact",
                      query="问二", hit="b.md", split="frozen"),
            ]))


class TestHardNegativeRejected:
    def test_hard_negative_equal_to_truth_fails(self):
        with pytest.raises(SystemExit) as e:
            validate(_payload([_case(hn=["sop_operation_001.md"])]))
        assert e.value.code == 2

    def test_hard_negative_inside_multi_truth_fails(self):
        with pytest.raises(SystemExit):
            validate(_payload([_case("FH01", "faq_no_case_id", "faq_exact",
                                     route="faq_exact", hit=["a.md", "b.md"],
                                     hn=["b.md"])]))


class TestExpectedShapeRejected:
    def test_multi_truth_only_for_faq_no_case_id(self):
        with pytest.raises(SystemExit) as e:
            validate(_payload([_case(hit=["a.md", "b.md"])]))
        assert e.value.code == 2

    def test_missing_truth_on_a_retrieval_case_fails(self):
        with pytest.raises(SystemExit):
            validate(_payload([_case(hit=None)]))

    def test_synthetic_category_must_not_carry_truth(self):
        with pytest.raises(SystemExit):
            validate(_payload([_case("G01", "dangerous_instruction", "guardrail",
                                     route="direct_answer", hit="a.md")]))

    def test_missing_split_or_template_fails(self):
        bad = _case()
        bad["split"] = ""
        with pytest.raises(SystemExit):
            validate(_payload([bad]))
        bad2 = _case()
        bad2["template_id"] = ""
        with pytest.raises(SystemExit):
            validate(_payload([bad2]))
