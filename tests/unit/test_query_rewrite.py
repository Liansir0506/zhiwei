"""查询改写单测（第 7 步修复）。

覆盖三个缺陷：
1. 指代判定用单字 "该" 做子串匹配，"应该" 被当成指代词，
   导致任何"…应该怎么处理"的追问在有会话历史时被拼上上一轮问题；
2. FAQ 编号在问句与 source 里的写法不同，需要统一提取；
3. 句内已自带实体（告警码/FAQ 编号）的问句仍被拼上上一轮问题，
   导致同一句问话的 route 取决于上一句问了什么。
"""
from __future__ import annotations

from qa_core.application.qa_usecase import QaUseCase
from qa_core.rewrite.entities import faq_ids
from qa_core.rewrite.normalizer import resolve_coreference

HISTORY = [
    {"role": "user", "content": "UDM 设备上报 ALM-UDM-AUTH 告警，应该怎么处理？"},
    {"role": "assistant", "content": "..."},
]


class TestResolveCoreference:
    def test_real_pronoun_triggers_rewrite(self):
        for q in ["它怎么处理？", "该设备如何排查？", "这个告警的根因是什么？",
                  "上述流程要多久？"]:
            assert resolve_coreference(q, HISTORY) is not None, q

    def test_modal_word_should_not_trigger(self):
        """'应该' 里的 '该' 不是指代词——这是会话串扰的根因。"""
        for q in ["gNodeB 设备上报 ALM-NGAP-SCTP 告警，应该怎么处理？",
                  "ALM-SMF-PDU 告警的可能原因有哪些？",
                  "SMF 会话建立超时应该先查什么？"]:
            assert resolve_coreference(q, HISTORY) is None, q

    def test_other_word_not_treated_as_pronoun(self):
        assert resolve_coreference("其他网元有没有类似问题？", HISTORY) is None

    def test_no_history_no_rewrite(self):
        assert resolve_coreference("它怎么处理？", []) is None


class TestFaqIds:
    def test_question_form(self):
        assert faq_ids("案例SIM-FAQ-0199的第9类上下文应先核对什么？") == {"0199"}

    def test_source_form(self):
        assert faq_ids("FAQ-SIM-0002 是什么") == {"0002"}

    def test_keeps_leading_zeros(self):
        """前导零必须保留，否则无法与 source 里的编号对齐。"""
        assert faq_ids("案例SIM-FAQ-0002") == {"0002"}

    def test_no_id_returns_empty(self):
        for text in ["SMF 会话建立超时怎么处理", "ALM-SMF-PDU 告警", "", None]:
            assert faq_ids(text) == set()

    def test_multiple_ids(self):
        assert faq_ids("对比 SIM-FAQ-0199 与 FAQ-SIM-0002") == {"0199", "0002"}


class TestSelfContainedQueryGuard:
    """句内已含实体时不应用上一轮做指代改写。

    缺陷背景：/qa 会拿会话历史做多轮指代改写，而 "ALM-1201 这个告警怎么处理"
    里的"这个"指向的是**句内**已经给出的告警码，不是上一轮话题。共用 session
    连问时，同一句问话的 route 会随上一句改变。实测（test 环境）：
      "5G基站传输中断排查步骤" → "ALM-1201 这个告警怎么处理"
        alarm_code 变空、route 从 structured_lookup 变 retrieval；
      若上一句是"帮我重启基站"，则直接命中"越界特征: 重启基站"→ direct_answer。
    """

    HISTORY_ALARM = [
        {"role": "user", "content": "UDM 设备上报 ALM-UDM-AUTH 告警，应该怎么处理？"},
        {"role": "assistant", "content": "..."},
    ]
    HISTORY_OUT_OF_SCOPE = [
        {"role": "user", "content": "帮我重启基站"},
        {"role": "assistant", "content": "该问题超出运维知识范围。"},
    ]
    QUERY = "ALM-1201 这个告警怎么处理"

    @staticmethod
    def _decide(query: str, history: list[dict]):
        usecase = QaUseCase(searcher=object())
        usecase._sessions["s"] = history
        return usecase._decide(query, "s")

    def test_alarm_query_keeps_its_own_alarm_code(self):
        d = self._decide(self.QUERY, self.HISTORY_ALARM)
        assert d.alarm_code == "ALM-1201"
        assert d.route == "structured_lookup"

    def test_previous_out_of_scope_cannot_leak_into_alarm_query(self):
        d = self._decide(self.QUERY, self.HISTORY_OUT_OF_SCOPE)
        assert d.intent != "out_of_scope"
        assert d.route == "structured_lookup"

    def test_route_is_history_independent(self):
        """有/无历史必须同一条 route。"""
        assert (self._decide(self.QUERY, []).route
                == self._decide(self.QUERY, self.HISTORY_ALARM).route)

    def test_pronoun_followup_still_rewritten(self, monkeypatch):
        """真正的追问仍要借上一轮补全，不能被这个守卫误伤。"""
        from qa_core.application import qa_usecase as mod

        seen: list[str] = []
        real = mod.resolve_coreference
        monkeypatch.setattr(mod, "resolve_coreference",
                            lambda q, h: seen.append(q) or real(q, h))

        self._decide("这个告警怎么处理", self.HISTORY_ALARM)
        assert seen == ["这个告警怎么处理"]

    def test_faq_id_query_is_self_contained(self, monkeypatch):
        from qa_core.application import qa_usecase as mod

        seen: list[str] = []
        real = mod.resolve_coreference
        monkeypatch.setattr(mod, "resolve_coreference",
                            lambda q, h: seen.append(q) or real(q, h))

        self._decide("FAQ-SIM-0002 这个条目讲了什么", self.HISTORY_ALARM)
        assert seen == []
