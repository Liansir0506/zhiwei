"""评测工装单测（第 7 步修复 + 7D）。

覆盖：
1. 拒答判定的统计口径（LLM 会用不同话术表达"无依据/越界"）；
2. 报告的诊断字段（看到 hit=false 能定位实际命中了什么）；
3. hard negative 消费（数据集标注了 62/89 条，此前无脚本读取）。
"""
from __future__ import annotations

from scripts.run_eval import NO_ANSWER_MARKERS, _is_rejected, _report_path, judge

GREETING = "您好！我是通信运维知识助理，可以回答告警处理、设备配置、应急流程等问题。"
NO_EVIDENCE = "抱歉，暂时未找到与您问题相关的资料。请换个说法重新输入。"
OUT_OF_SCOPE = "该问题超出运维知识范围。我可以协助：告警排查、设备配置查询、应急处理流程检索。"


def _case(case_id="X01", category="retrieval", expect_route="retrieval",
          expect_hit=None, expect_reject=False, query="测试查询",
          hard_negatives=None):
    return {"id": case_id, "category": category, "query": query,
            "expect_route": expect_route, "expect_hit": expect_hit,
            "expect_reject": expect_reject,
            "hard_negatives": hard_negatives or []}


def _result(route="retrieval", answer=NO_EVIDENCE, sources=None, degraded=False):
    return {"route": route, "answer": answer, "sources": sources or [],
            "diagnostics": {"degraded": degraded, "timings_ms": {"total": 1.0}}}


class TestIsRejected:
    def test_recognizes_both_refusal_phrasings(self):
        assert _is_rejected(NO_EVIDENCE)
        assert _is_rejected(OUT_OF_SCOPE)

    def test_does_not_flag_real_answers(self):
        assert not _is_rejected(GREETING)
        assert not _is_rejected("SMF 会话建立超时的处置步骤共 5 步…")
        assert not _is_rejected("")

    def test_markers_cover_both_phrasings(self):
        assert len(NO_ANSWER_MARKERS) >= 2


class TestJudgeRoute:
    def test_correct_guardrail_is_not_penalised(self):
        """越界指令被正确拦截为 direct_answer，路由与拒答都应记通过。"""
        judged = judge(_case("G01", "guardrail", "direct_answer", expect_reject=True),
                       _result(route="direct_answer", answer=OUT_OF_SCOPE))
        assert judged["route_ok"] is True
        assert judged["reject_ok"] is True

    def test_missed_guardrail_is_flagged(self):
        """危险指令没被拦截（走了 retrieval）应如实记失配。"""
        judged = judge(_case("G04", "guardrail", "direct_answer", expect_reject=True),
                       _result(route="retrieval", answer=NO_EVIDENCE))
        assert judged["route_ok"] is False

    def test_out_of_domain_question(self):
        judged = judge(_case("J01", "reject", "retrieval", expect_reject=True),
                       _result(route="retrieval", answer=NO_EVIDENCE))
        assert judged["route_ok"] is True
        assert judged["reject_ok"] is True


class TestJudgeHit:
    def test_hit_when_expected_source_present(self):
        judged = judge(_case("F01", "faq_exact", "faq_exact", expect_hit="FAQ-SIM-0199"),
                       _result(route="faq_exact", answer="…",
                               sources=[{"source": "FAQ-SIM-0199"}]))
        assert judged["hit_ok"] is True

    def test_miss_when_expected_source_absent(self):
        judged = judge(_case("F01", "faq_exact", "faq_exact", expect_hit="FAQ-SIM-0199"),
                       _result(route="faq_exact", answer="…",
                               sources=[{"source": "FAQ-SIM-0099"}]))
        assert judged["hit_ok"] is False

    def test_hit_ok_is_none_without_expectation(self):
        judged = judge(_case("D01", "direct", "direct_answer"),
                       _result(route="direct_answer", answer=GREETING))
        assert judged["hit_ok"] is None
        assert judged["reject_ok"] is None


class TestDiagnostics:
    """报告可诊断性：看到 hit=false 时应能直接定位实际命中了什么。"""

    def test_records_query_expectation_and_actual_sources(self):
        judged = judge(_case("A02", "alarm_lookup", "structured_lookup",
                             expect_hit="SIM-TT-00605", query="gNodeB 上报 ALM-NGAP-SCTP"),
                       _result(route="structured_lookup", answer="…",
                               sources=[{"source": "sop_operation_085.md", "score": 0.73},
                                        {"source": "SIM-TT-00123", "score": 0.71}]))
        assert judged["query"] == "gNodeB 上报 ALM-NGAP-SCTP"
        assert judged["expect_hit"] == "SIM-TT-00605"
        assert judged["expect_route"] == "structured_lookup"
        assert judged["got_sources"] == ["sop_operation_085.md", "SIM-TT-00123"]
        assert judged["top_score"] == 0.73
        assert judged["hit_ok"] is False

    def test_records_answer_excerpt_and_error(self):
        result = _result(answer="")
        result["diagnostics"]["error"] = "TimeoutError: llm timeout"
        judged = judge(_case(), result)
        assert judged["answer"] == ""
        assert judged["error"] == "TimeoutError: llm timeout"

    def test_top_score_none_without_sources(self):
        judged = judge(_case(), _result())
        assert judged["top_score"] is None


class TestHardNegative:
    """hard negative 消费：数据集标了 124 个，此前没有任何脚本读取。

    hard negative = 看似相关但结论不同的文档。它出现在 topN 里代表误召回，
    是消融实验分辨检索质量的关键指标（正例指标在模板化数据上会全部饱和）。
    """

    def test_violation_when_hard_negative_in_sources(self):
        judged = judge(
            _case("R02", expect_hit="sop_operation_200.md",
                  hard_negatives=["sop_operation_080.md", "sop_operation_020.md"]),
            _result(sources=[{"source": "sop_operation_200.md"},
                             {"source": "sop_operation_080.md"}]))
        assert judged["hn_total"] == 2
        assert judged["hn_hits"] == ["sop_operation_080.md"]
        assert judged["hn_violation"] is True
        # 正例命中与 hard negative 违例可以同时成立——两者度量的是不同问题
        assert judged["hit_ok"] is True

    def test_no_violation_when_only_expected_source(self):
        judged = judge(
            _case("R05", expect_hit="sop_operation_048.md",
                  hard_negatives=["sop_operation_128.md"]),
            _result(sources=[{"source": "sop_operation_048.md"}]))
        assert judged["hn_hits"] == []
        assert judged["hn_violation"] is False

    def test_no_annotation_means_no_evaluation(self):
        judged = judge(_case("D01", "direct", "direct_answer"),
                       _result(route="direct_answer", answer=GREETING))
        assert judged["hn_total"] == 0
        assert judged["hn_violation"] is False

    def test_ignores_empty_annotations(self):
        """数据集里可能出现空串占位，不应被算成一个 hard negative。"""
        judged = judge(_case("R01", expect_hit="a.md", hard_negatives=["", None]),
                       _result(sources=[{"source": "a.md"}]))
        assert judged["hn_total"] == 0
        assert judged["hn_violation"] is False

    def test_matching_uses_same_semantics_as_hit(self):
        """与 expect_hit 同口径：先精确相等，再退化为子串包含。"""
        judged = judge(_case("R03", expect_hit="sop_operation_020.md",
                             hard_negatives=["sop_operation_080"]),
                       _result(sources=[{"source": "sop_operation_080.md"}]))
        assert judged["hn_violation"] is True

    def test_only_top_k_sources_are_considered(self):
        """超出 top_k 的召回不算违例——topN 之外本来就不会进答案。"""
        judged = judge(
            _case("R04", expect_hit="a.md", hard_negatives=["n1.md"]),
            _result(sources=[{"source": "a.md"}, {"source": "b.md"},
                             {"source": "c.md"}, {"source": "d.md"},
                             {"source": "e.md"}, {"source": "n1.md"}]),
            top_k=5)
        assert judged["hn_violation"] is False


class TestMultiTruth:
    """多真值：一篇以上的文档同样正确。

    r3 的 FAQ 同一主题下有 50 条内容相同、只差案例编号的副本，问句不带编号时
    这 50 条都是正确答案。旧评测集没有多真值表达能力，才把同一句问话写成
    多条**互斥**真值（见 docs/STEP7D_DIAGNOSIS.md 第 3.3 节）。
    """

    def test_hit_when_any_truth_matches(self):
        judged = judge(
            _case("FH01", "faq_exact", "faq_exact",
                  expect_hit=["FAQ-SIM-0001", "FAQ-SIM-0002", "FAQ-SIM-0003"]),
            _result(route="faq_exact", answer="…",
                    sources=[{"source": "FAQ-SIM-0002"}]))
        assert judged["hit_ok"] is True
        assert judged["hit_which"] == "FAQ-SIM-0002"
        assert judged["expect_hit_n"] == 3

    def test_miss_when_no_truth_matches(self):
        judged = judge(
            _case("FH01", "faq_exact", "faq_exact",
                  expect_hit=["FAQ-SIM-0001", "FAQ-SIM-0002"]),
            _result(route="faq_exact", answer="…",
                    sources=[{"source": "FAQ-SIM-9999"}]))
        assert judged["hit_ok"] is False
        assert judged["hit_which"] is None

    def test_single_string_still_supported(self):
        judged = judge(_case("FV01", "faq_exact", "faq_exact",
                             expect_hit="FAQ-SIM-0001"),
                       _result(route="faq_exact", answer="…",
                               sources=[{"source": "FAQ-SIM-0001"}]))
        assert judged["hit_ok"] is True
        assert judged["expect_hit_n"] == 1

    def test_empty_list_means_no_expectation(self):
        judged = judge(_case("D01", "direct", "direct_answer", expect_hit=[]),
                       _result(route="direct_answer", answer=GREETING))
        assert judged["hit_ok"] is None
        assert judged["expect_hit_n"] == 0

    def test_multi_truth_does_not_weaken_hard_negative_check(self):
        judged = judge(
            _case("FH02", "faq_exact", "faq_exact",
                  expect_hit=["a.md", "b.md"], hard_negatives=["n.md"]),
            _result(route="faq_exact", answer="…",
                    sources=[{"source": "a.md"}, {"source": "n.md"}]))
        assert judged["hit_ok"] is True
        assert judged["hn_violation"] is True


class TestReportPath:
    """报告文件名必须带 run_id。

    实测踩到过：dev 与 test 两个环境跑同一个 kb_version，
    第二份报告把第一份覆盖掉，只剩最后一份，无法并排比较。
    """

    def test_different_runs_do_not_collide(self):
        a = _report_path("v4-foundation-r3", "20260920-121500")
        b = _report_path("v4-foundation-r3", "20260920-203000")
        assert a != b, "同一版本的不同运行必须落到不同文件，否则互相覆盖"

    def test_contains_version_and_run_id(self):
        p = _report_path("v4-foundation-r3", "20260920-121500")
        assert "v4-foundation-r3" in p.name
        assert "20260920-121500" in p.name
        assert p.parent.name == "reports"
