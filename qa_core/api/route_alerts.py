"""告警接入接口（演进路线阶段二）：网管 webhook → 相似案例 → 处理建议卡片。

POST /alerts/webhook
  body: {"alarm_code": "ALM-1201", "source": "网管系统名", "severity": "critical"}

复用 structured_lookup 精确查；未命中降级语义检索；LLM 生成处理建议。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from qa_core.api.rate_limit import check_rate_limit
from qa_core.config.settings import get_settings
from qa_core.generation.context import build_evidence, context_budget_ok
from qa_core.generation.llm import LLMClient, LLMError
from qa_core.retrieval.searcher import Searcher
from qa_core.security.audit import audit_access

router = APIRouter(prefix="/alerts", tags=["alerts"])

_searcher_instance: Searcher | None = None

ADVICE_PROMPT = """你是通信运维值班助理。根据以下检索证据，为该告警生成处理建议。

输出格式：
【可能原因】依据证据列出 1-3 个最可能的原因
【建议动作】编号步骤（只给排查思路，不提供现网操作指令）
【参考案例】对应工单号/文档
若证据与告警无关，回复"知识库中暂无该告警的处理记录，建议转人工值班处理"。

【告警信息】
告警码：{alarm_code}
来源：{source}
严重级别：{severity}

【检索证据】
{evidence}"""


class AlarmWebhook(BaseModel):
    alarm_code: str = Field(min_length=4, max_length=32)
    source: str = ""
    severity: str = "major"


def _get_searcher() -> Searcher:
    global _searcher_instance
    if _searcher_instance is None:
        _searcher_instance = Searcher()
    return _searcher_instance


def _active_kb_version() -> str:
    import sqlalchemy as sa

    from qa_core.storage.kb_dao import KbDao

    dao = KbDao(get_settings().mysql_dsn)
    with dao.engine.begin() as conn:
        row = conn.execute(sa.text(
            "SELECT version FROM kb_versions WHERE status='ACTIVE' LIMIT 1")).first()
    return row[0] if row else "v2"


@router.post("/webhook")
def alarm_webhook(body: AlarmWebhook, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("ops", "admin"))
    check_rate_limit(ctx.user_id, 60 if "admin" in ctx.roles else get_settings().rate_limit_qps)
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="alarm_webhook", resource="/alerts/webhook",
                 decision="allow", roles=ctx.roles, scope=ctx.scope.to_dict(), query=body.alarm_code)
    t0 = time.perf_counter()
    kb_version = _active_kb_version()
    searcher = _get_searcher()

    hits = searcher.lookup_alarm(body.alarm_code, ctx.roles, kb_version,
                                 tenant_id=ctx.tenant_id, scope=ctx.scope)
    if not hits:
        # 精确未命中 → 语义相似检索兜底
        result = searcher.search_docs(
            [f"{body.alarm_code} 告警处理 {body.severity}"], ctx.roles, kb_version,
            tenant_id=ctx.tenant_id, scope=ctx.scope)
        hits = result.docs[:5]

    advice = None
    if hits:
        settings = get_settings()
        llm = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
        evidence = build_evidence(hits)
        messages = [{"role": "system",
                     "content": ADVICE_PROMPT.format(alarm_code=body.alarm_code,
                                                     source=body.source or "网管",
                                                     severity=body.severity,
                                                     evidence=evidence)},
                    {"role": "user", "content": body.alarm_code}]
        if context_budget_ok(messages):
            try:
                advice = llm.chat(messages)
            except LLMError:
                advice = None  # 降级到下面的兜底文案
        if not advice or len(advice.strip()) < 20:
            advice = "（生成服务暂不可用，以下为最相关案例）" + \
                     "；".join(h.source or h.id for h in hits[:3])

    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "alarm_code": body.alarm_code,
        "kb_version": kb_version,
        "similar_cases": [{"source": h.source, "doc_type": h.doc_type,
                           "alarm_code": h.alarm_code,
                           "summary": (h.text or h.question)[:200]} for h in hits[:5]],
        "advice": advice or "知识库中暂无该告警的处理记录，建议转人工值班处理",
        "elapsed_ms": elapsed,
    }
