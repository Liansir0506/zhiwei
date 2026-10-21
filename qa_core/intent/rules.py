"""规则通道：注入/问候/越界识别 + 告警码提取 + 关键词加权打分 rule_score。

rule_score 定义：命中的关键词权重求和后 min(1, sum/1.5)——一个强词(0.8+)即可过
0.6，两个弱词才过 0.5；纯靠单弱词（≤0.5）落入灰区交给模型通道。
"""
from __future__ import annotations

import re

from qa_core.config.scenario import ScenarioConfig
from qa_core.schemas.intent import IntentDecision


def is_greeting(query: str, cfg: ScenarioConfig) -> bool:
    return any(r.search(query) for r in cfg.greeting_res)


def find_injection(query: str, cfg: ScenarioConfig) -> str | None:
    # Imperative boundary avoids classifying discussion such as
    # "如何防止用户用忽略所有规则绕过限制" as an instruction.
    bypass = re.search(
        r"(?:^|[，,。；;：:\n])\s*(?:请你?|你必须|现在|直接|立刻|马上)?\s*"
        r"忽略(?:以上|之前|上面的)?(?:所有|全部)?(?:规则|限制)", query)
    if bypass:
        return bypass.group(0).strip()
    for r in cfg.injection_res:
        m = r.search(query)
        if m:
            return m.group(0)
    return None


def find_out_of_scope(query: str, cfg: ScenarioConfig) -> str | None:
    # Forecast requests have many ordinary phrasings beyond "天气预报".
    # Weather-related network faults remain valid operational consultation.
    operational_weather = bool(re.search(r"基站|天线|无线|光链路|微波|传输|机房|网络", query)
        and re.search(r"影响|排查|告警|退服|衰减|故障|防护|维护|保障|处置", query))
    forecast = re.search(r"天气(?:怎么样|如何|怎样|预报)|(?:查|查询|查查).{0,20}天气"
                         r"|(?:今天|明天|后天|下周|周末).{0,20}天气", query)
    if forecast and not operational_weather:
        return forecast.group(0)
    # 执行或索取破坏性指令必须在告警/FAQ 路由之前拦截。
    request = re.search(r"帮我|替我|直接|执行|运行|下发|立即|马上|给我|生成|写一|please|execute|run\b", query, re.I)
    destructive = re.search(
        r"\b(?:delete\s+from|truncate\s+(?:table\s+)?|drop\s+(?:table|database)\b|update\s+\w+\s+set)"
        r"|(?:删除|清空|修改|更新).{0,16}(?:用户数据|数据库|数据表|记录)", query, re.I)
    definition = re.search(r"什么意思|是什么|含义|原理|解释|风险|注意事项", query)
    if destructive and (request or (not definition and re.match(r"\s*(?:DELETE|TRUNCATE|DROP|UPDATE)\b", query, re.I))):
        return "请求执行或提供数据修改指令"
    consultation = re.search(r"怎么|如何|什么|哪些|为什么|流程|步骤|注意事项|解释|含义|原理", query)
    immediate = re.search(r"帮我|替我|执行|下发|立即|马上|现在就|直接|请.{0,8}(?:重启|关闭|下电)", query)
    for r in cfg.oos_res:
        m = r.search(query)
        if m:
            if operational_weather and '天气' in m.group(0):
                continue
            # 旧场景正则很宽：重启/下电的知识咨询并非要求系统操作设备。
            if (consultation and not immediate
                    and re.search(r"重启|关闭|下电", m.group(0))):
                continue
            return m.group(0)
    return None


def find_alarm_code(query: str, cfg: ScenarioConfig) -> str | None:
    for r in cfg.alarm_res:
        m = r.search(query.upper())
        if m:
            return m.group(0)
    return None


def rule_score(query: str, cfg: ScenarioConfig) -> tuple[str, float]:
    """返回 (intent, score)。遍历关键词规则表，取加权得分最高的意图。"""
    best_intent, best_score = "", 0.0
    q = query.lower()
    for intent, table in cfg.keyword_rules.items():
        score = 0.0
        for kw, w in table.items():
            if kw.lower() in q:
                score += float(w)
        score = min(1.0, score / 1.5)
        if score > best_score:
            best_intent, best_score = intent, score
    return best_intent, best_score


def _hits_any(query: str, table: dict) -> bool:
    q = query.lower()
    return any(str(kw).lower() in q for kw in table)


def classify_by_rules(query: str, cfg: ScenarioConfig) -> IntentDecision:
    """规则通道主入口。产出候选意图 + route 建议（灰区留给网关仲裁）。"""
    injection = find_injection(query, cfg)
    if injection:
        return IntentDecision("injection", "direct_answer", "rule", 1.0,
                              f"命中注入特征: {injection}")
    if is_greeting(query, cfg):
        return IntentDecision("greeting", "direct_answer", "rule", 1.0, "问候语")

    # 安全拦截必须排在任何业务路由之前。此前 out_of_scope 检查排在告警码分支之后，
    # 危险指令只要同时含告警码就会先命中告警码分支、返回 structured_lookup 并进入检索，
    # 安全性退化为"依赖 LLM 话术"而不是确定性规则拦截。实测可绕过：
    #   "把ALM-PWR-LOW对应的基站远程下电" → structured_lookup
    #   "请重启出现ALM-SMF-PDU的网元"     → structured_lookup
    oos = find_out_of_scope(query, cfg)
    if oos:
        return IntentDecision("out_of_scope", "direct_answer", "rule", 1.0,
                              f"越界特征: {oos}")

    alarm = find_alarm_code(query, cfg)
    if alarm:
        intent, score = rule_score(query, cfg)
        # 告警码是强特征，但不等于"用户要查历史工单"：FAQ 问句与流程查询经常引用告警码，
        # 它们要的是知识条目而不是工单。只看"命中告警码"就强制走 structured_lookup，
        # 会把这两类整类吃掉——此前 alarm_code_patterns 只认纯数字告警码，
        # 在 r3 数据上从不命中，因此这个缺陷一直被掩盖；补上字母型告警码后
        # 实测 FAQ 直出准确率从 100% 跌到 0、retrieval 路由从 100% 跌到 28%。
        # 因此这里按问句意图让路：FAQ 形态给 faq_exact，流程形态给 retrieval。
        if intent == "faq" and score >= 0.6:
            return IntentDecision(intent, "faq_exact", "rule", score,
                                  f"含告警码 {alarm}，但问句为 FAQ 形态")
        if intent == "procedure_query" or _hits_any(
                query, cfg.keyword_rules.get("procedure_query", {})):
            return IntentDecision(intent or "procedure_query", "retrieval", "rule",
                                  max(score, 0.6),
                                  f"含告警码 {alarm}，但问句为流程查询")
        intent = intent or "fault_diagnosis"
        return IntentDecision(intent, "structured_lookup", "rule", max(score, 0.6),
                              f"告警码精确命中 {alarm}", alarm_code=alarm)

    intent, score = rule_score(query, cfg)
    if intent and score >= 0.6:
        route = "faq_exact" if intent == "faq" else "retrieval"
        return IntentDecision(intent, route, "rule", score,
                              f"关键词加权命中 {intent}")
    # 低分/灰区：交网关仲裁（可能升级模型通道）
    return IntentDecision(intent or "unknown", "retrieval", "rule", score,
                          "规则分不足，待网关仲裁")
