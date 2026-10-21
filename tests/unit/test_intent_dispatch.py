"""意图分流单测（第 7 步修复）。

覆盖两个曾导致整类路由失效的缺陷：
1. 告警码命中后一律走 structured_lookup，会把 FAQ 问句与流程查询整类拦走；
2. out_of_scope 只覆盖"动作在前"语序，对象在前的危险指令漏拦截。
"""
from __future__ import annotations

from qa_core.config.scenario import get_scenario
from qa_core.intent.rules import classify_by_rules, find_alarm_code


def _cfg():
    return get_scenario("telecom_ops")


class TestAlarmCodeExtraction:
    """告警码正则：r3 使用字母分段码，旧模式只认纯数字。"""

    def test_r3_letter_alarm_codes(self):
        cfg = _cfg()
        for text, expect in [
            ("UDM 设备上报 ALM-UDM-AUTH 告警", "ALM-UDM-AUTH"),
            ("SMF 出现 ALM-SMF-PDU 时如何排查", "ALM-SMF-PDU"),
            ("ALM-NGAP-SCTP 告警怎么处理", "ALM-NGAP-SCTP"),
            ("ALM-NR-HO 对应的流程", "ALM-NR-HO"),
        ]:
            assert find_alarm_code(text, cfg) == expect

    def test_legacy_numeric_alarm_codes_still_work(self):
        cfg = _cfg()
        assert find_alarm_code("设备报 ALM-1234 告警", cfg) == "ALM-1234"
        assert find_alarm_code("ALM1234 是什么", cfg) == "ALM1234"

    def test_case_insensitive(self):
        assert find_alarm_code("alm-smf-pdu 小写输入", _cfg()) == "ALM-SMF-PDU"

    def test_no_false_positive(self):
        cfg = _cfg()
        for text in ["SMF 会话建立超时怎么处理", "ALM-配置说明", "请介绍 5G 核心网架构"]:
            assert find_alarm_code(text, cfg) is None


class TestAlarmAwareDispatch:
    """命中告警码后按问句意图让路，不能一律走结构化查询。"""

    def test_diagnosis_goes_structured_lookup(self):
        cfg = _cfg()
        for q in ["UDM 设备上报 ALM-UDM-AUTH 告警，应该怎么处理？",
                  "ALM-SMF-PDU 告警的可能原因有哪些？",
                  "ALM-NR-HO 告警如何排查？"]:
            assert classify_by_rules(q, cfg).route == "structured_lookup"

    def test_faq_shaped_query_keeps_faq_exact(self):
        cfg = _cfg()
        q = "SGW出现ALM-GTP-CREATE时，案例SIM-FAQ-0199的第9类上下文应先核对什么？"
        decision = classify_by_rules(q, cfg)
        assert decision.route == "faq_exact"

    def test_procedure_query_goes_retrieval(self):
        cfg = _cfg()
        for q in ["ALM-NR-HO 告警对应的标准处置流程包含哪些步骤？",
                  "ALM-SMF-PDU 对应的 SOP 流程是什么？"]:
            assert classify_by_rules(q, cfg).route == "retrieval"

    def test_plain_query_unaffected(self):
        cfg = _cfg()
        assert classify_by_rules("SMF 会话建立超时怎么处理", cfg).route != "structured_lookup"


class TestOutOfScopeGuardrail:
    """越界指令拦截：两种语序都要覆盖，且不能误伤知识咨询。"""

    def test_action_first_order_blocked(self):
        cfg = _cfg()
        for q in ["重启一下核心网网元", "帮我执行一下重启核心网网元的命令"]:
            d = classify_by_rules(q, cfg)
            assert (d.route, d.intent) == ("direct_answer", "out_of_scope")

    def test_object_first_order_blocked(self):
        """'把上海基站的电源远程下电' —— 评测集 G04 暴露的漏拦截。"""
        cfg = _cfg()
        d = classify_by_rules("把上海基站的电源远程下电", cfg)
        assert (d.route, d.intent) == ("direct_answer", "out_of_scope")

    def test_non_device_requests_blocked(self):
        cfg = _cfg()
        for q in ["帮我写一段 Python 脚本批量重命名文件", "给我推荐几只值得买的股票基金"]:
            assert classify_by_rules(q, cfg).intent == "out_of_scope"

    def test_knowledge_question_not_blocked(self):
        """祈使标记约束的意义：'基站下电告警怎么处理'是知识咨询，不该判越界。"""
        cfg = _cfg()
        d = classify_by_rules("基站下电告警怎么处理", cfg)
        assert d.intent != "out_of_scope"

    def test_dangerous_action_with_alarm_code_is_blocked(self):
        """带告警码的危险指令同样必须拦截。

        此前 out_of_scope 检查排在告警码分支之后，含告警码的危险指令会提前命中
        告警码分支、返回 structured_lookup 并进入检索——安全性退化为"依赖 LLM 话术"
        而不是确定性规则拦截。同时正则窗口太小（.{0,8}）也覆盖不了告警码占位后的距离。
        """
        cfg = _cfg()
        for q in ["把ALM-PWR-LOW对应的基站远程下电",
                  "请重启出现ALM-SMF-PDU的网元"]:
            # 先确认这条确实含告警码，避免测试因"压根没匹配到告警码"而假通过
            assert find_alarm_code(q, cfg) is not None, q
            d = classify_by_rules(q, cfg)
            assert (d.route, d.intent) == ("direct_answer", "out_of_scope"), q

    def test_alarm_queries_still_not_blocked_after_window_widened(self):
        """窗口放宽后，含告警码的正常问句仍不该被判越界。"""
        cfg = _cfg()
        for q in ["ALM-SMF-PDU 告警的可能原因有哪些？",
                  "ALM-NR-HO 告警对应的标准处置流程包含哪些步骤？",
                  "基站下电告警怎么处理"]:
            assert classify_by_rules(q, cfg).intent != "out_of_scope", q
