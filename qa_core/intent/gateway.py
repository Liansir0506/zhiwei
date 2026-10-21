"""apply_intent_decision_gateway：四路路由的最终仲裁（对接第 05 章 rule+model 双通道思路）。

仲裁规则（优先级从高到低）：
1. 规则强信号（score ≥ 0.6 或结构性命中：注入/问候/越界/告警码）→ 直接采信规则；
2. 规则分落在灰区 [gray_lo, gray_hi] → 升级模型通道：
   - 模型判定 FAQ → faq_exact；
   - 模型判定多轮 → retrieval + requires_rewrite；
   - 模型超时/失败 → model_fallback，灰区默认 retrieval；
3. 规则低分（< gray_lo）→ retrieval（source=rule_default），不唤醒模型（省钱省时延）。

网关只做仲裁，不做检索——faq_exact 是否真直出由后续 FAQ 检索分数决定。
"""
from __future__ import annotations

from qa_core.config.scenario import ScenarioConfig
from qa_core.intent import model_classifier
from qa_core.intent.rules import classify_by_rules
from qa_core.schemas.intent import IntentDecision


def apply_intent_decision_gateway(query: str, cfg: ScenarioConfig,
                                  model_path: str | None = None,
                                  device: str = "cpu",
                                  use_model: bool = True) -> IntentDecision:
    rule = classify_by_rules(query, cfg)

    # 强信号直接采信
    if rule.source == "rule" and (rule.score >= 0.6 or rule.route != "retrieval"):
        return rule

    gray_lo, gray_hi = cfg.thresholds["rule_gray_zone"]
    if gray_lo <= rule.score <= gray_hi and use_model and model_path:
        model = model_classifier.classify(
            query, model_path, device,
            timeout_ms=cfg.thresholds["model_timeout_ms"],
        )
        if model is None:  # 模型禁用/加载失败
            return IntentDecision(rule.intent, "retrieval", "model_fallback",
                                  rule.score, "模型不可用，灰区默认 retrieval")
        if model.route == "faq_exact":
            return model  # 模型高置信 FAQ：faq_exact 候选
        return IntentDecision(
            model.intent, "retrieval", model.source, model.score,
            model.reason, requires_rewrite=model.requires_rewrite,
        )

    # 低分默认 retrieval，不唤醒模型
    return IntentDecision(rule.intent or "knowledge", "retrieval", "rule_default",
                          rule.score, "规则分低于灰区下限，默认检索")
