"""Deterministic conversational context; past answers are never retrieval evidence."""
from dataclasses import dataclass, field
import re
from qa_core.intent.rules import classify_by_rules, find_alarm_code
from qa_core.rewrite.entities import faq_ids
from qa_core.rewrite.normalizer import _has_pronoun, normalize_query, resolve_coreference


def devices(cfg):
    return set(cfg.abbreviations) | {"AMF", "SMF", "UPF", "UDM", "UDR", "AUSF", "PCF", "NRF", "gNodeB", "eNodeB"}


def explicit_devices(query, cfg):
    return sorted(d for d in devices(cfg) if re.search(
        r"(?<![A-Za-z0-9])" + re.escape(d) + r"(?![A-Za-z0-9])", query, re.I))


def self_contained(query, cfg):
    return bool(find_alarm_code(query, cfg) or faq_ids(query) or explicit_devices(query, cfg)
                or re.search(r"(?<![A-Za-z0-9])HTTP[ /-]*[1-5]\d{2}(?![0-9])", query, re.I))


@dataclass
class TurnContext:
    query: str
    history: list[dict] = field(default_factory=list)
    reason: str = "self_contained"
    clarification: str | None = None


def prepare_context(query, history, cfg):
    # Current input guards take precedence over any previous safe topic.
    if classify_by_rules(query, cfg).route == "direct_answer":
        return TurnContext(query)
    history = [m for m in history if isinstance(m, dict) and m.get("role") in {"user", "assistant"}
               and isinstance(m.get("content"), str)][-6:]
    last_answer = next((m for m in reversed(history) if m["role"] == "assistant"), {})
    if last_answer.get("status") in {"REFUSED", "OUT_OF_SCOPE", "FAILED"}:
        history = []
        last_answer = {}
    pending = bool(last_answer.get("needs_clarification"))
    last_user = next((m for m in reversed(history) if m["role"] == "user"), {})
    previous = last_user.get("context_query") or last_user.get("content", "")
    # A condition reply can repeat the same device. A complete question or a new
    # explicit target remains independent of an earlier clarification.
    condition_reply = (pending and bool(explicit_devices(query, cfg))
        and set(explicit_devices(query, cfg)) == set(explicit_devices(previous, cfg))
        and not re.search(r"什么|怎么|如何|怎样|为什么|含义|SOP|流程|步骤", query, re.I))
    if self_contained(query, cfg) and not condition_reply:
        return TurnContext(query)
    if not _has_pronoun(query) and not pending:
        return TurnContext(query, reason="independent")
    if not previous or classify_by_rules(previous, cfg).route == "direct_answer":
        return TurnContext(query, reason="missing_context",
            clarification="请补充您指的是哪个告警、FAQ条目或设备，以及当前版本和症状。")
    alarms = {m.group(0).upper() for pattern in cfg.alarm_res for m in pattern.finditer(previous.upper())}
    http_codes = set(re.findall(r"(?<![A-Za-z0-9])HTTP[ /-]*([1-5]\d{2})(?![0-9])", previous, re.I))
    if len(alarms) > 1 or len(faq_ids(previous)) > 1 or len(explicit_devices(previous, cfg)) > 1 or len(http_codes) > 1:
        return TurnContext(query, reason="ambiguous_target",
            clarification="上一轮涉及多个对象。请明确本次要查询的告警码、FAQ编号、设备或HTTP状态。")
    if pending:
        # Preserve identity/version/purpose, replacing unresolved alternatives with
        # the user's supplied conditions instead of perpetually repeating them.
        anchors = [*sorted(alarms), *["FAQ-SIM-" + i for i in sorted(faq_ids(previous))],
                   *explicit_devices(previous, cfg), *["HTTP " + code for code in sorted(http_codes)]]
        anchors += re.findall(r"\bLab-\d{4}\.\d{2}\b|\bV\d+(?:\.\d+)+\b", previous, re.I)
        if re.search(r"SOP|流程|步骤", previous, re.I):
            anchors.append("SOP 流程")
        previous = " ".join(dict.fromkeys(anchors)) or previous
    rewritten = (previous + "；" + query if pending else
                 resolve_coreference(query, [{"role": "user", "content": previous}]))
    if not rewritten or len(rewritten) > cfg.limits["query_max_chars"] * 2:
        return TurnContext(query, reason="context_limit",
            clarification="请用一条完整问题重新说明对象、版本、症状和希望核对的内容。")
    return TurnContext(normalize_query(rewritten), history[-4:], "resolved")
