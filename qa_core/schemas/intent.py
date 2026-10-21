"""意图判定结果 schema（网关输出的统一契约）。"""
from __future__ import annotations

from dataclasses import dataclass

ROUTES = ("direct_answer", "faq_exact", "structured_lookup", "retrieval")


@dataclass
class IntentDecision:
    intent: str                 # greeting/out_of_scope/injection/faq/fault_diagnosis/config_query/procedure_query
    route: str                  # ROUTES 之一
    source: str                 # rule | model | model_fallback | rule_default
    score: float                # 通道分数（规则分或模型置信度）
    reason: str
    requires_rewrite: bool = False
    alarm_code: str = ""        # structured_lookup 时非空

    def to_dict(self) -> dict:
        return {
            "intent": self.intent, "route": self.route, "source": self.source,
            "score": round(self.score, 4), "reason": self.reason,
            "requires_rewrite": self.requires_rewrite, "alarm_code": self.alarm_code,
        }
