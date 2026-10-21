"""在线问答主编排（在线链路顺序的唯一实现处）。

流式语义：真流式转发 LLM delta 并聚合，流完后做生成后核验；核验失败在 end
事件中给出修正后的引用模式答案（degraded=True）。所有降级路径显式处理。
"""
from __future__ import annotations

import json
import re
import time

import sqlalchemy as sa

from qa_core.config.scenario import ScenarioConfig, get_scenario
from qa_core.generation.context import build_evidence, build_messages, context_budget_ok
from qa_core.generation.llm import LLMClient, LLMError
from qa_core.generation.verifier import verify_answer
from qa_core.intent.gateway import apply_intent_decision_gateway
from qa_core.intent.rules import find_alarm_code
from qa_core.retrieval.searcher import Searcher
from qa_core.retrieval.relevance import relevant_docs
from qa_core.retrieval.evidence_text import evidence_text, source_annotation_text
from qa_core.retrieval.workflow import is_workflow_query, retrieve_workflow, MAX_WORKFLOW_CHARS
from qa_core.rewrite.entities import faq_ids
from qa_core.rewrite.normalizer import build_query_variants, normalize_query, resolve_coreference
from qa_core.rewrite.conversation import prepare_context, self_contained
from qa_core.security.policy import AccessScope
from qa_core.application.request_view import request_scoped
from qa_core.ingest.binding import version_binding, digest
from qa_core.security.withdrawals import WithdrawalPolicy

DIRECT_REPLIES = {
    "greeting": "您好！我是通信运维知识助理，可以回答告警处理、设备配置、应急流程等问题。请问有什么可以帮您？",
    "injection": "检测到不当输入。本助理只回答通信网络运维知识问题。",
    "out_of_scope": "该问题超出运维知识范围。我可以协助：告警排查、设备配置查询、应急处理流程检索。",
}
NO_EVIDENCE = "抱歉，暂时未找到与您问题相关的资料。请换个说法重新输入，或从推荐问题中选择咨询；紧急故障请联系值班工程师。"
FLOW_NO_EVIDENCE = NO_EVIDENCE + "\n流程取证提示：请补充设备、版本与当前症状；缺少审批、回滚依据或无法完成权限验证时，不提供处置流程。"

# 拒答/无证据话术的**唯一**判定入口（7E）。
# 此前 "暂时未找到" in answer 的子串匹配散落在同步、流式、评测三处，
# LLM 表达"库中无依据"的话术不固定，漏一种就漏清一次引用。
REFUSAL_MARKERS = (
    "暂时未找到",
    "超出运维知识范围",
    "超出通信网络运维知识范围",
    "检测到不当输入",
)


def is_refusal_answer(answer: str | None) -> bool:
    """答案是否为"无证据/拒答"类话术。判定通过就必须清空 sources。"""
    return any(m in (answer or "") for m in REFUSAL_MARKERS)


# 证据覆盖检查用的停用词：这些双字组合太泛，命中了也不能说明"问题与证据相关"
_COVER_STOP = {"什么", "怎么", "如何", "应该", "可以", "哪些", "为什么", "怎样",
               "还是", "或者", "以及", "这个", "那个", "一下", "请问", "帮我",
               "告诉", "需要", "是否", "出现", "进行", "样的", "的话",
               "常见", "原因", "可能", "情况", "方式", "时候", "哪里", "哪种",
               "多少", "多久", "主要", "一般", "通常", "相关", "麻烦", "介绍",
               "说明", "怎么样", "什么样", "怎样才", "如何才能"}


def _content_tokens(query: str) -> list[str]:
    """抽取可用于判断"问题是否落在证据上"的内容词。

    ASCII 词（告警码/设备名/编号等，长度 >=2）+ 中文双字组。
    停用词先从中文串中移除再做滑窗：避免"常见+原因 -> 见原"这类跨词伪组合
    稀释覆盖率（长问句里虚词越多、伪组合越多，正常问题越容易在阈值边缘翻转）。
    双字组对同词共现足够敏感，不引入分词依赖。
    """
    import re

    q = query or ""
    tokens = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}", q)]
    han = ''.join(re.findall(r"[\u4e00-\u9fff]", q))
    for stop in sorted(_COVER_STOP, key=len, reverse=True):
        han = han.replace(stop, '\u0000')
    for segment in han.split('\u0000'):
        tokens.extend(a + b for a, b in zip(segment, segment[1:]))
    return [t for t in tokens if t not in _COVER_STOP]


def evidence_covers(query: str, docs, min_coverage: float) -> tuple[bool, float]:
    """独立的证据覆盖检查（7E）：问题内容词有多少落在证据文本上。

    为什么要单独做这一层：CrossEncoder 的分数是**候选集内**的相对相关性，
    不是跨查询可校准的绝对分。域外问题（"今天天气怎么样"）照样能捞出 3~5 条
    分数高于 `rerank_min` 的文档，于是低分保护形同虚设——
    实测 6 条域外问题全部召回了无关引用。词汇覆盖是确定性信号，
    不依赖 LLM，也不依赖分数校准。

    返回 (是否覆盖, 覆盖率)。无内容词时返回 True（无法判断时不拦截）。
    """
    if min_coverage <= 0:
        return True, 1.0
    tokens = _content_tokens(query)
    if not tokens:
        return True, 1.0
    text = "\n".join(evidence_text(d) if getattr(d, "doc_type", "") == "faq" else (d.text or "")[:1500]
                     for d in list(docs)[:2]).casefold()
    if not text:
        return False, 0.0
    total = 0.0
    covered = 0.0
    for token in tokens:
        weight = 2.0 if token.isascii() else 1.0       # 实体（告警码/协议名/编号）证据指向性更高
        total += weight
        if token.casefold() in text:
            covered += weight
    ratio = covered / total
    return ratio >= min_coverage, round(ratio, 4)


def _citation_answer(docs, *, generation_disabled: bool = False, refusal: bool = False) -> str:
    """引用模式：LLM 不可用/核验失败/判定证据不足时的兜底——原始片段+引用。"""
    lines = ["【相关片段】现有资料不足以生成整合回答；以下为检索到的最相关资料原文，供人工核对：" if refusal
             else "【引用模式】以下为检索到的最相关原始片段：" if generation_disabled
             else "【结论】生成服务暂不可用，以下为检索到的最相关原始片段："]
    for i, d in enumerate(docs, 1):
        text = d.answer if d.doc_type == "faq" and d.answer else d.text
        limit = MAX_WORKFLOW_CHARS if d.extra.get("workflow_evidence") else (1000 if d.extra.get("lab_scope_verified") else 300)
        excerpt = text if d.doc_type == "faq" else text[:limit]
        lines.append(f"[{i}] {source_annotation_text(excerpt)}")
    return "\n\n".join(lines)


class QaUseCase:
    def __init__(self, cfg: ScenarioConfig | None = None,
                 searcher: Searcher | None = None, llm: LLMClient | None = None,
                 intent_model_path: str | None = None):
        from qa_core.config.settings import get_settings

        self.cfg = cfg or get_scenario()
        self.settings = get_settings()
        # 检索器包含 Milvus 客户端和 embedding 模型，按需初始化。
        # direct_answer/拒答路径无需为此承担模型冷启动或外部服务依赖。
        self._searcher = searcher
        self.llm = llm or LLMClient(self.settings.llm_base_url, self.settings.llm_api_key,
                                    self.settings.llm_model)
        self.intent_model_path = intent_model_path or self.settings.intent_model_path
        self._sessions: dict[str, list[dict]] = {}   # Redis 故障时的内存兜底

    @property
    def searcher(self) -> Searcher:
        if self._searcher is None:
            self._searcher = Searcher(settings=self.settings)
        return self._searcher

    def _redis(self):
        """会话 Redis（fail-open：故障时回退内存 dict，多副本仍可用消息表追溯）。"""
        if not hasattr(self, "_rc"):
            try:
                import redis as redis_lib

                self._rc = redis_lib.Redis.from_url(
                    self.settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
                self._rc.ping()
            except Exception:  # noqa: BLE001
                self._rc = None
        return self._rc

    def _load_session(self, session_id: str) -> list[dict]:
        rc = self._redis()
        if rc is not None:
            try:
                raw = rc.get(f"sess:{session_id}")
                if raw:
                    return json.loads(raw)
            except Exception:  # noqa: BLE001
                pass
        return self._sessions.get(session_id, [])

    def _save_session(self, session_id: str, hist: list[dict]) -> None:
        self._sessions[session_id] = hist
        rc = self._redis()
        if rc is not None:
            try:
                rc.set(f"sess:{session_id}", json.dumps(hist[-6:], ensure_ascii=False), ex=1800)
            except Exception:  # noqa: BLE001
                pass

    def _history_key(self, session_id, user_id, access, kb_version):
        # Old unscoped sess keys remain historical records and are never read here.
        # A permission/version/binding/withdrawal change starts a fresh context.
        ledger = sorted(WithdrawalPolicy(self.settings).entries(access.tenant_id))
        return "ctx-v2-" + digest({"session": session_id, "user": user_id,
            "scope": access.fingerprint, "version": kb_version,
            "read_binding": version_binding(self.settings),
            "database_sha256": digest(self.settings.mysql_dsn), "withdrawals": ledger})

    # ================= 同步主入口 =================

    @request_scoped
    def ask(self, query: str, user_id: str = "anon", roles: list[str] | None = None,
            session_id: str = "default", use_llm: bool = True,
            tenant_id: str = "default", scope: AccessScope | None = None,
            kb_version: str | None = None, persist: bool = True) -> dict:
        """kb_version 显式指定时按该版本检索，否则取当前 ACTIVE 版本。

        评测必须能固定版本：否则 ACTIVE 一变，同一份评测集的结果就不可比，
        报告还会覆盖同名文件（详见 scripts/run_eval.py）。

        persist=False 为评测模式：不加载历史、也不写会话与消息表。评测应保持单轮、
        可重复，且不应把批量会话灌进开发环境的业务数据。
        """
        t0 = time.perf_counter()
        use_llm = use_llm and self.settings.generation_enabled
        query = normalize_query(query)
        if not query or len(query) > self.cfg.limits["query_max_chars"]:
            return self._error("E001", "query 为空或超长")

        kb_version = kb_version or self._active_kb_version()
        t_dec = time.perf_counter()
        original_query = query
        access = scope or AccessScope.build(tenant_id=tenant_id, roles=roles or [])
        history_key = self._history_key(session_id, user_id, access, kb_version) if persist else None
        context = prepare_context(query, self._load_session(history_key) if persist else [], self.cfg)
        query, history = context.query, context.history
        decision = self._decide(query, session_id, history=[])
        self._workflow_route(query, decision)
        intent_ms = round((time.perf_counter() - t_dec) * 1000, 1)
        answer, sources, degraded, retrieval_info = None, [], False, {}

        if context.clarification:
            answer = context.clarification
            retrieval_info["needs_clarification"] = True
        elif decision.route == "direct_answer":
            answer = DIRECT_REPLIES.get(decision.intent, DIRECT_REPLIES["out_of_scope"])
        elif decision.route == "structured_lookup":
            hits = self.searcher.lookup_alarm(decision.alarm_code, roles, kb_version,
                                              tenant_id=tenant_id, scope=access)
            related = (self.searcher.related_alarms(
                decision.alarm_code, hits[0].equipment_type, roles, kb_version,
                tenant_id=tenant_id, scope=access) if hits else [])
            retrieval_info["related_alarms"] = [
                {"alarm_code": r.alarm_code, "source": r.source,
                 "summary": r.text[:120]} for r in related]
            if hits:
                answer, sources, degraded, retrieval_info = self._retrieval_path(
                    query, roles, kb_version, decision, preset_docs=hits, use_llm=use_llm,
                    history=history, tenant_id=tenant_id, scope=access)
            else:
                decision.reason += " → lookup 未命中，降级 retrieval"
                decision.route = "retrieval"

        # FAQ-first：faq_exact 与 retrieval 统一先试 FAQ 直出（路由误判兜底，开销 <100ms）
        if answer is None and decision.route in ("faq_exact", "retrieval"):
            answer, sources, degraded, retrieval_info = self._faq_path(
                query, roles, kb_version, decision, use_llm=use_llm, history=history,
                tenant_id=tenant_id, scope=access)

        # 统一出口覆盖 FAQ、缓存和降级路径，拒答绝不携带无关引用。
        if sources and not WithdrawalPolicy(self.settings).sources_allowed(sources, tenant_id):
            answer, sources = NO_EVIDENCE, []
            retrieval_info["withdrawal_guard"] = "source_withdrawn_or_unidentified"
        if is_refusal_answer(answer):
            sources = []
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        message_id = (self._remember(session_id, original_query, answer or "",
                                     route=decision.route, intent=decision.intent,
                                     user_id=user_id, tenant_id=tenant_id, history_key=history_key,
                                     context_query=query, needs_clarification=bool(retrieval_info.get("needs_clarification")))
                      if persist else None)
        try:
            from qa_core.api.metrics import record_qa

            record_qa(decision.route, decision.intent, elapsed / 1000)
        except Exception:  # noqa: BLE001 —— 监控埋点失败不影响主流程
            pass
        from qa_core.schemas.qa_status import outcome
        return {"status": outcome(answer, refusal=is_refusal_answer(answer), degraded=degraded, intent=decision.intent,
                                  needs_clarification=bool(retrieval_info.get("needs_clarification"))),
                "message_id": message_id, "answer": answer, "sources": sources,
                "route": decision.route, "intent": decision.intent,
                "decision": decision.to_dict(),
                "diagnostics": {"kb_version": kb_version,
                                "answer_mode": "generation" if use_llm else "citation",
                                "conversation": {"reason": context.reason, "effective_query": query},
                                "read_config_sha256": digest(version_binding(self.settings)),
                                "timings_ms": {"total": elapsed, "intent": intent_ms},
                                "retrieval": retrieval_info, "degraded": degraded}}

    # ================= 流式入口（真流式 + 事后核验） =================

    @request_scoped
    def ask_stream(self, query: str, roles: list[str] | None = None,
                   session_id: str = "default", tenant_id: str = "default",
                   user_id: str = "anon", scope: AccessScope | None = None,
                   kb_version: str | None = None, persist: bool = True):
        """yield 事件：start → decision → stage* → delta* → sources → end。

        stage 事件实时汇报链路进度（意图判定/检索/生成/核验，含分步耗时），
        驱动前端右侧时间线与聊天气泡的阶段提示实时刷新。
        """
        t0 = time.perf_counter()
        query = normalize_query(query)
        if not query or len(query) > self.cfg.limits["query_max_chars"]:
            yield {"event": "error", "data": {"status": "FAILED", "code": "E001", "message": "query 为空或超长"}}
            return
        kb_version = kb_version or self._active_kb_version()
        # Pin before the first event and before intent processing.
        yield {"event": "start", "data": {"session_id": session_id, "kb_version": kb_version}}
        yield {"event": "stage", "data": {"step": "intent", "status": "running",
                                          "label": "理解问题"}}
        t_dec = time.perf_counter()
        original_query = query
        access = scope or AccessScope.build(tenant_id=tenant_id, roles=roles or [])
        history_key = self._history_key(session_id, user_id, access, kb_version) if persist else None
        context = prepare_context(query, self._load_session(history_key) if persist else [], self.cfg)
        query, history = context.query, context.history
        decision = self._decide(query, session_id, history=[])
        self._workflow_route(query, decision)
        intent_ms = round((time.perf_counter() - t_dec) * 1000, 1)
        yield {"event": "decision", "data": decision.to_dict()}
        yield {"event": "stage", "data": {"step": "intent", "status": "done", "ms": intent_ms}}

        answer, sources, degraded = None, [], False
        chunks = None
        docs = None
        related_alarms = []
        ret_ms = 0.0
        gen_ms = 0.0

        if context.clarification:
            answer = context.clarification
        elif decision.route == "direct_answer":
            answer = DIRECT_REPLIES.get(decision.intent, DIRECT_REPLIES["out_of_scope"])
        elif decision.route == "structured_lookup":
            yield {"event": "stage", "data": {"step": "retrieve", "status": "running",
                                              "label": f"查询工单 {decision.alarm_code}"}}
            t_ret = time.perf_counter()
            hits = self.searcher.lookup_alarm(decision.alarm_code, roles or [], kb_version,
                                              tenant_id=tenant_id, scope=access)
            related_alarms = (self.searcher.related_alarms(
                decision.alarm_code, hits[0].equipment_type, roles or [], kb_version,
                tenant_id=tenant_id, scope=access)
                if hits else [])
            if hits:
                yield {"event": "stage", "data": {"step": "retrieve", "status": "done",
                        "ms": round((time.perf_counter() - t_ret) * 1000, 1)}}
                yield {"event": "stage", "data": {"step": "generate", "status": "running"}}
                chunks, docs, sources, degraded = self._stream_generate(query, hits, decision, history=history)
            else:
                decision.route = "retrieval"
        faq_docs = None  # FAQ 兜底候选，供后续 retrieval 降级时并入精排
        if (answer is None and chunks is None and decision.route in ("faq_exact", "retrieval")
                and not self._workflow_requested(query)):
            # FAQ-first 兜底：路由误判为 retrieval 的 FAQ 问句也能直出
            yield {"event": "stage", "data": {"step": "retrieve", "status": "running",
                                              "label": "FAQ 匹配"}}
            t_ret = time.perf_counter()
            # 单编号才直出；多编号时 faq_docs 只作为 extra_docs 交给检索路径，
            # 让"对比 A 与 B"能拿到全部被点名的条目（与非流式 _faq_path 保持一致）。
            faq_ids_found = faq_ids(query)
            exact = self.searcher.lookup_faq_by_source(
                ["FAQ-SIM-%s" % i for i in sorted(faq_ids_found)],
                roles or [], kb_version, tenant_id=tenant_id, scope=access) if faq_ids_found else []
            faq_docs = exact or self.searcher.search_faq(query, roles or [], kb_version,
                                                         tenant_id=tenant_id, scope=access)
            if (faq_docs and len(faq_ids_found) <= 1
                    and (bool(exact) or self._faq_direct_ok(faq_docs, query))):
                yield {"event": "stage", "data": {"step": "retrieve", "status": "done",
                        "ms": round((time.perf_counter() - t_ret) * 1000, 1)}}
                top = faq_docs[0]
                answer = f"【结论】依据标准排查 FAQ：\n{source_annotation_text(top.answer)}\n【引用】[1] {top.source or top.id}"
                from qa_core.schemas.evidence import citation_fields
                sources = [{"doc_id": top.id, "source": top.source, "doc_type": "faq",
                            "tenant_id": top.tenant_id, "source_system": top.source_system, "source_id": top.source_id,
                            "kb_version": top.kb_version, "clause": "", "score": top.dense_score,
                            **citation_fields(top)}]
                decision.route = "faq_exact"
            else:
                decision.route = "retrieval"
                decision.intent = "knowledge"  # 清除 faq 过滤偏好
        if answer is None and chunks is None and decision.route == "retrieval":
            yield {"event": "stage", "data": {"step": "retrieve", "status": "running",
                                              "label": "向量+BM25 检索"}}
            t_ret = time.perf_counter()
            chunks, docs, sources, degraded = self._stream_retrieval(query, roles or [],
                                                                     kb_version, decision,
                                                                     tenant_id=tenant_id,
                                                                     scope=access,
                                                                     extra_docs=faq_docs, history=history)
            ret_ms = round((time.perf_counter() - t_ret) * 1000, 1)
            yield {"event": "stage", "data": {"step": "retrieve", "status": "done", "ms": ret_ms}}
            if chunks is not None:
                yield {"event": "stage", "data": {"step": "generate", "status": "running"}}

        if chunks is not None:
            t_gen = time.perf_counter()
            collected = []
            for piece in chunks:
                if not WithdrawalPolicy(self.settings).sources_allowed(sources, tenant_id):
                    collected = []
                    docs, sources = [], []
                    break
                collected.append(piece)
                yield {"event": "delta", "data": {"text": piece}}
            gen_ms = round((time.perf_counter() - t_gen) * 1000, 1)
            answer = "".join(collected)
            if not collected:
                answer = NO_EVIDENCE
            evidence_text = build_evidence(docs)  # 含元数据头（告警码/条款）
            # 引用模式的文本直接来自已校验的证据，不经过模型生成核验。
            ok, _problems = (verify_answer(answer, evidence_text)
                             if self.settings.generation_enabled else (True, []))
            if not ok and docs:
                degraded = True
                answer = _citation_answer(docs)
                yield {"event": "delta", "data": {"text": "\n\n" + answer}}
            yield {"event": "stage", "data": {"step": "verify",
                    "status": "done" if ok else "warn"}}

        if any(d.extra.get("needs_clarification") for d in (docs or [])):
            answer = docs[0].text
        if answer and is_refusal_answer(answer):
            if "暂时未找到" in answer and docs:
                # 7E 升级（r2）：与非流式链路一致——拒答降级展示已过防护的候选片段与出处；
                # end.answer 为定稿的降级全文（不含拒答话术，避免被再次判为拒答而清空引用）。
                excerpt = _citation_answer(docs, refusal=True)
                yield {"event": "delta", "data": {"text": "\n\n" + excerpt}}
                answer = excerpt
                degraded = True
            else:
                sources = []  # 非"无证据"类拒答：隐藏低分来源
        elif answer is None and chunks is None:
            answer = FLOW_NO_EVIDENCE if self._workflow_requested(query) else NO_EVIDENCE
            sources = []

        if sources and not WithdrawalPolicy(self.settings).sources_allowed(sources, tenant_id):
            answer, sources = NO_EVIDENCE, []
        related_alarms = WithdrawalPolicy(self.settings).filter_docs(related_alarms, tenant_id)
        yield {"event": "sources", "data": {"sources": sources,
               "related_alarms": [{"alarm_code": r.alarm_code, "source": r.source,
                                   "summary": r.text[:120]} for r in related_alarms]}}
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        needs_clarification = bool(context.clarification) or any(d.extra.get("needs_clarification") for d in (docs or []))
        message_id = (self._remember(session_id, original_query, answer or "",
                                     route=decision.route, intent=decision.intent,
                                     user_id=user_id, tenant_id=tenant_id, history_key=history_key,
                                     context_query=query, needs_clarification=needs_clarification)
                      if persist else None)
        from qa_core.schemas.qa_status import outcome
        yield {"event": "end", "data": {"status": outcome(answer, refusal=is_refusal_answer(answer), degraded=degraded, intent=decision.intent,
                                         needs_clarification=needs_clarification),
                                        "answer": answer, "degraded": degraded,
                                        "message_id": message_id, "timings_ms": elapsed,
                                        "intent_ms": intent_ms,
                                        "retrieval": {"retrieval_rerank_ms": ret_ms,
                                                      "generation_ms": gen_ms},
                                        "kb_version": kb_version, "route": decision.route,
                                        "answer_mode": "generation" if self.settings.generation_enabled else "citation",
                                        "conversation": {"reason": context.reason, "effective_query": query},
                                        "read_config_sha256": digest(version_binding(self.settings))}}

    # ================= 路径实现 =================

    def _faq_path(self, query, roles, kb_version, decision, use_llm: bool = True,
                  history: list[dict] | None = None, tenant_id: str = "default",
                  scope: AccessScope | None = None):
        if self._workflow_requested(query):
            return self._retrieval_path(query, roles, kb_version, decision, use_llm=use_llm,
                                        history=history, tenant_id=tenant_id, scope=scope)
        # 问句里明确给出 FAQ 编号时，直接按 source 精确取，不走向量检索：
        # 编号是 r3 同质 FAQ 的唯一区分 token，向量模型对它不敏感，实测写了
        # "案例SIM-FAQ-0199"仍会返回别的条目（如 FAQ-SIM-0099）且 route 仍是 faq_exact，
        # 属于"自信地答错"。精确命中时 dense_score 置 1.0，直出判定直接通过。
        # 但只对**单个**编号做直出：多个编号是并列要求（如"对比 A 与 B"），
        # 直出 faq_docs[0] 只会回答其中一个，且返回顺序没有业务保证。
        # 多编号时改为把这些条目作为额外证据并入检索路径，由多文档证据回答。
        ids = faq_ids(query)
        exact = self.searcher.lookup_faq_by_source(
            ["FAQ-SIM-%s" % i for i in sorted(ids)],
            roles, kb_version, tenant_id=tenant_id, scope=scope) if ids else []
        if len(ids) > 1:
            decision.reason += (" → 问句含多个 FAQ 编号，改走检索路径（并入 %d 条 FAQ 证据）"
                                % len(exact))
            decision.route = "retrieval"
            decision.intent = "knowledge"
            return self._retrieval_path(query, roles, kb_version, decision,
                                        use_llm=use_llm, history=history,
                                        extra_docs=exact,
                                        tenant_id=tenant_id, scope=scope)
        faq_docs = exact or self.searcher.search_faq(
            query, roles, kb_version, tenant_id=tenant_id, scope=scope)
        if faq_docs and (bool(exact) or self._faq_direct_ok(faq_docs, query)):
            decision.route = "faq_exact"  # FAQ-first 直出成功，路由回写
            top = faq_docs[0]
            answer = f"【结论】依据标准排查 FAQ：\n{source_annotation_text(top.answer)}\n【引用】[1] {top.source or top.id}"
            from qa_core.schemas.evidence import citation_fields
            sources = [{"doc_id": top.id, "source": top.source, "doc_type": "faq",
                        "tenant_id": top.tenant_id, "source_system": top.source_system, "source_id": top.source_id,
                        "kb_version": top.kb_version, "clause": "", "score": top.dense_score,
                        **citation_fields(top)}]
            return answer, sources, False, {"faq_candidates": len(faq_docs)}
        decision.reason += " → FAQ 未达直出阈值，降级 retrieval（FAQ 候选并入精排）"
        decision.route = "retrieval"
        # 降级后清除 faq 偏好，避免 doc_type_priority=[faq,sop] 导致检索面过窄
        decision.intent = "knowledge"
        return self._retrieval_path(query, roles, kb_version, decision, use_llm=use_llm,
                                    history=history, extra_docs=faq_docs,
                                    tenant_id=tenant_id, scope=scope)

    def _retrieval_path(self, query, roles, kb_version, decision, preset_docs=None,
                        use_llm: bool = True, history: list[dict] | None = None,
                        extra_docs=None,
                        tenant_id: str = "default", scope: AccessScope | None = None):
        """检索 → 精排 → 低分保护 → LLM → 核验 → 兜底链（非流式）。use_llm=False 时引用模式（评测/离线模式）。"""
        t_ret = time.perf_counter()
        stage_ms = {}
        use_llm = use_llm and self.settings.generation_enabled

        # 语义缓存：相似问题直接复用已核验答案（省检索+精排+LLM 三段开销）
        if (preset_docs is None and self.settings.semantic_cache_enabled and use_llm and not history
                and not self._workflow_requested(query)):
            try:
                from qa_core.cache.semantic_cache import SemanticCache

                vec = self.searcher._embed_query_cached(query)
                sem = SemanticCache(self.searcher.cache, kb_version,
                                    scope=(scope.fingerprint if scope else
                                           AccessScope.build(tenant_id=tenant_id, roles=roles or []).fingerprint))
                hit = sem.lookup(vec)
                if hit and WithdrawalPolicy(self.settings).sources_allowed(hit["sources"], tenant_id):
                    try:
                        from qa_core.api.metrics import SEM_CACHE_HITS

                        SEM_CACHE_HITS.inc()
                    except Exception:  # noqa: BLE001
                        pass
                    return hit["answer"], hit["sources"], False, {
                        "semantic_cache_hit": hit["similarity"], "candidates": 0}
            except Exception:  # noqa: BLE001 —— 语义缓存故障不影响主流程
                pass

        docs = self._retrieve_and_rerank(query, roles, kb_version, decision, preset_docs,
                                         extra_docs=extra_docs,
                                         tenant_id=tenant_id, scope=scope)
        docs = relevant_docs(query, WithdrawalPolicy(self.settings).filter_docs(docs, tenant_id))
        stage_ms["retrieval_rerank_ms"] = round((time.perf_counter() - t_ret) * 1000, 1)
        clarification = next((d for d in docs if d.extra.get("needs_clarification")), None)
        if clarification:
            return clarification.text, [], False, {"needs_clarification": True,
                "workflow_guard": clarification.extra["reason"], **stage_ms}
        if any(d.extra.get("workflow_evidence") and not d.extra.get("workflow_ready") for d in docs):
            return FLOW_NO_EVIDENCE, [], False, {"workflow_guard": "missing_safety_controls", **stage_ms}
        if preset_docs is not None:
            # lookup 精确命中的文档无 rerank 分，不适用低分保护
            if not docs:
                return NO_EVIDENCE, [], False, {"candidates": 0}
        elif not docs or (docs[0].rerank_score or 0) < self.settings.rerank_min:
            return (FLOW_NO_EVIDENCE if self._workflow_requested(query) else NO_EVIDENCE), [], False, {"candidates": 0,
                                            "top_rerank": docs[0].rerank_score if docs else None}
        # 证据覆盖检查（7E）：分数挡不住域外问题，用词汇覆盖补一层确定性护栏
        covered, coverage = evidence_covers(query, docs, self.settings.evidence_min_coverage)
        if not covered:
            return (FLOW_NO_EVIDENCE if self._workflow_requested(query) else NO_EVIDENCE), [], False, {"candidates": len(docs),
                                            "top_rerank": docs[0].rerank_score,
                                            "evidence_coverage": coverage, **stage_ms}
        sources = self._sources_of(docs)
        if not use_llm:
            return _citation_answer(docs, generation_disabled=not self.settings.generation_enabled), sources, False, {
                "mode": "no_llm", "top_rerank": docs[0].rerank_score, "candidates": len(docs), **stage_ms}
        try:
            t_gen = time.perf_counter()
            answer = self._generate_with_verify(query, docs, history=history)
            stage_ms["generation_ms"] = round((time.perf_counter() - t_gen) * 1000, 1)
            degraded = False
            try:
                from qa_core.api.metrics import LLM_CALLS

                LLM_CALLS.inc()
            except Exception:  # noqa: BLE001
                pass
            if is_refusal_answer(answer):
                if "暂时未找到" in answer and docs:
                    # 7E 升级（r2）：LLM 判定证据不足以回答时，降级展示已过防护（低分保护+覆盖度+
                    # 撤回过滤）的候选片段与出处，供人工核对；不生成结论，防护语义不变。
                    answer = _citation_answer(docs, refusal=True)
                    degraded = True
                else:
                    sources = []  # 非"无证据"类拒答：隐藏低分来源，避免误导
            # 核验通过且非空的答案才写入语义缓存（空/过短答案入缓存会命中扩散）
            if (self.settings.semantic_cache_enabled and not history and answer and len(answer) > 20
                    and not self._workflow_requested(query)):
                try:
                    from qa_core.cache.semantic_cache import SemanticCache

                    vec = self.searcher._embed_query_cached(query)
                    SemanticCache(
                        self.searcher.cache, kb_version,
                        scope=(scope.fingerprint if scope else
                               AccessScope.build(tenant_id=tenant_id, roles=roles or []).fingerprint)
                    ).store(
                        vec, {"answer": answer, "sources": sources,
                              "route": decision.route})
                except Exception:  # noqa: BLE001
                    pass
        except _VerifyFailed as exc:
            answer, degraded = exc.fallback, True
        except LLMError as exc:
            answer, degraded = _citation_answer(docs), True
            if is_refusal_answer(answer):
                sources = []  # 兜底答案也是拒答话术：同样不能带引用
            return answer, sources, degraded, {"llm_error": str(exc)[:120], **stage_ms}
        return answer, sources, degraded, {"top_rerank": docs[0].rerank_score,
                                           "candidates": len(docs), **stage_ms}

    def _retrieve_and_rerank(self, query, roles, kb_version, decision, preset_docs=None,
                             extra_docs=None, tenant_id: str = "default",
                             scope: AccessScope | None = None):
        if preset_docs is not None:
            return preset_docs
        if self._workflow_requested(query):
            from qa_core.rerank.reranker import get_reranker
            result = retrieve_workflow(self.searcher,
                get_reranker(self.settings.reranker_model_path, self.settings.rerank_device),
                query, roles, kb_version, tenant_id=tenant_id, scope=scope,
                min_score=self.settings.rerank_min)
            if result.question:
                from qa_core.schemas.retrieval import RetrievedDoc
                return [RetrievedDoc("", result.question, 0, extra={"needs_clarification": True, "reason": result.reason})]
            return [result.packet] if result.packet is not None else []
        variants = build_query_variants(query, self.cfg.abbreviations)
        result = self.searcher.search_docs(
            variants, roles, kb_version,
            doc_types=self.cfg.doc_type_priority(decision.intent), tenant_id=tenant_id, scope=scope)
        from qa_core.rerank.reranker import get_reranker

        reranker = get_reranker(self.settings.reranker_model_path, self.settings.rerank_device)
        # Lab evidence requires an explicit compatible scope. This remains behind
        # the existing workflow opt-in; general production retrieval is unchanged.
        if self.settings.workflow_evidence_enabled:
            from qa_core.retrieval.workflow import declared_lab_versions, lab_source_applicable
            result.docs = [d for d in result.docs if lab_source_applicable(query, d.text)]
            for doc in result.docs:
                if declared_lab_versions(doc.text):
                    doc.extra["lab_scope_verified"] = True
        # 精排只处理融合分头部候选（top10：rerank 耗时与候选数线性，评测证明正确答案
        # 恒在融合前 5 内，10 个候选兼顾精度与 CPU 耗时；此前 top20 占 13s 拖垮端到端 P95）
        candidates = list(result.docs[:10])
        # FAQ 降级候选并入：faq_collection 与 doc_collection 是两个集合，search_docs 只查后者，
        # 未达直出阈值的 FAQ 问句若不并入，永远召不回 FAQ 条目（证据里就没有 answer）。
        if extra_docs:
            seen = {d.id for d in candidates}
            for d in relevant_docs(query, list(extra_docs))[:5]:
                if d.id not in seen and (not self.settings.workflow_evidence_enabled
                        or lab_source_applicable(query, d.text)):
                    candidates.append(d)
                    if self.settings.workflow_evidence_enabled and declared_lab_versions(d.text):
                        d.extra["lab_scope_verified"] = True
                    seen.add(d.id)
        candidates = relevant_docs(query, candidates)
        docs = reranker.rerank(variants[0], candidates, top_n=self.settings.top_n_final) if candidates else []
        return docs

    def _generate_with_verify(self, query: str, docs, history: list[dict] | None = None) -> str:
        messages = self._generation_messages(query, docs, history)
        answer = self.llm.chat(messages)
        # 核验文本用完整证据串（含头部元数据行），工单的告警码/标准的条款号
        # 存在 metadata 头部而不在正文 text 里
        evidence_text = build_evidence(docs)
        ok, _problems = verify_answer(answer, evidence_text)
        if not ok:
            retry = messages[:-1] + [{"role": "user", "content": (
                query + "\n（注意：上次回答包含证据中不存在的告警码/条款号，"
                        "请严格只用证据内容回答）")}]
            answer = self.llm.chat(retry)
            ok, _ = verify_answer(answer, evidence_text)
            if not ok:
                raise _VerifyFailed(_citation_answer(docs))
        return answer

    @staticmethod
    def _generation_messages(query, docs, history=None):
        messages = build_messages(query, docs, chat_history=history)
        return messages if context_budget_ok(messages) else build_messages(query, docs[:3])

    def _stream_generate(self, query, docs, decision, history=None):
        """已有文档（lookup 命中）→ 流式生成。"""
        docs = relevant_docs(query, WithdrawalPolicy(self.settings).filter_docs(docs, docs[0].tenant_id if docs else "default"))
        if not docs:
            return None, [], [], False
        if any(d.extra.get("workflow_evidence") and not d.extra.get("workflow_ready") for d in docs):
            return None, [], [], False
        covered, _ = evidence_covers(query, docs, self.settings.evidence_min_coverage)
        if not covered:
            return None, [], [], False
        chunks = (self.llm.chat_stream(self._generation_messages(query, docs, history))
                  if self.settings.generation_enabled else iter([_citation_answer(docs, generation_disabled=True)]))
        return chunks, docs, self._sources_of(docs), False

    def _stream_retrieval(self, query, roles, kb_version, decision,
                          tenant_id: str = "default", scope: AccessScope | None = None,
                          extra_docs=None, history=None):
        """检索 + 精排 → 低分保护 → 流式生成。返回 (chunks|None, docs, sources, degraded)。"""
        docs = self._retrieve_and_rerank(query, roles, kb_version, decision,
                                         extra_docs=extra_docs,
                                         tenant_id=tenant_id, scope=scope)
        docs = relevant_docs(query, WithdrawalPolicy(self.settings).filter_docs(docs, tenant_id))
        if any(d.extra.get("needs_clarification") for d in docs):
            return None, docs, [], False
        if any(d.extra.get("workflow_evidence") and not d.extra.get("workflow_ready") for d in docs):
            return None, [], [], False
        if not docs or (docs[0].rerank_score or 0) < self.settings.rerank_min:
            return None, docs, [], False
        # 证据覆盖检查（7E）：与非流式链路同一层护栏，域外问题不允许流出引用
        covered, coverage = evidence_covers(query, docs, self.settings.evidence_min_coverage)
        if not covered:
            return None, [], [], False
        chunks = (self.llm.chat_stream(self._generation_messages(query, docs, history))
                  if self.settings.generation_enabled else iter([_citation_answer(docs, generation_disabled=True)]))
        return chunks, docs, self._sources_of(docs), False

    # ================= 工具 =================

    @staticmethod
    def _sources_of(docs) -> list[dict]:
        from qa_core.schemas.evidence import citation_fields
        return [{"doc_id": d.id, "source": d.source, "doc_type": d.doc_type,
                 "kb_version": d.kb_version, "clause": d.clause,
                 "asset_id": d.asset_id, "source_system": d.source_system,
                 "source_id": d.source_id, "tenant_id": d.tenant_id,
                 "source_version": d.source_version, "security_level": d.security_level,
                 "ticket_id": d.ticket_id, "cmdb_id": d.cmdb_id,
                 "score": d.rerank_score if d.rerank_score is not None else d.fusion_score,
                 **citation_fields(d),
                 **({"workflow_steps": d.extra["workflow_steps"],
                     "child_chunk_ids": d.extra["child_chunk_ids"], "workflow_evidence": True,
                     "child_evidence": d.extra.get("child_evidence", [])}
                    if d.extra.get("workflow_evidence") else {})}
                for d in docs]

    def _faq_direct_ok(self, docs, query: str = "") -> bool:
        """FAQ 直出判定：dense 分达标 **且** 问句实体与候选一致。

        faq_gap 判据已废弃：r3 模拟 FAQ 由模板生成、高度同质，实测 25 条原句查询的
        top1-top2 分差恒在 0.0002~0.0099，远低于原阈值 0.05，会让直出被 100% 挡掉；
        而 search_docs 只查 doc_collection，降级后 FAQ 条目再也召不回。

        但仅看 dense 分不够：同质数据下 top1 很容易被相邻条目挤开，问句里写明的编号
        与候选对不上时若仍直出，就是"自信地答错"。因此这里补实体一致性校验，
        不一致时返回 False 交给上层降级精排/追问。
        """
        th = self.cfg.thresholds
        if query and not relevant_docs(query, docs[:1]):
            return False
        if docs[0].dense_score < th["faq_direct"]:
            return False
        wanted = faq_ids(query)
        if wanted:
            got = faq_ids(docs[0].source) | faq_ids(getattr(docs[0], "question", ""))
            if not (wanted & got):
                return False
        return True

    def _decide(self, query: str, session_id: str, history: list[dict] | None = None):
        history = self._sessions.get(session_id, []) if history is None else history
        # 自包含问句不做指代改写。"ALM-1201 这个告警怎么处理"里的"这个/该告警"
        # 指向的是句内已经给出的告警码，而不是上一轮话题；一旦拼接上一轮问题，
        # 危害有两个：find_alarm_code 可能取到上一轮的码，或上一轮的越界/注入特征
        # 先命中，于是同一句问话的 route 会随"上一句问了什么"改变。
        # 实测（test 环境，同一 session 连问）：
        #   "5G基站传输中断排查步骤" → "ALM-1201 这个告警怎么处理"
        #   alarm_code 变空、route 从 structured_lookup 变 retrieval；
        #   若上一轮是"帮我重启基站"，则直接命中"越界特征: 重启基站"。
        # 带明确 FAQ 编号的问句同理，它自身已能定位到具体条目。
        if not self_contained(query, self.cfg):
            rewritten = resolve_coreference(query, history)
            if rewritten:
                query = rewritten
        return apply_intent_decision_gateway(
            query, self.cfg, model_path=self.intent_model_path,
            device=self.settings.embedding_device)

    def _workflow_requested(self, query):
        return self.settings.workflow_evidence_enabled and is_workflow_query(query)

    def _workflow_route(self, query, decision):
        # Existing injection/unsafe/out-of-scope guards have priority.
        if (self._workflow_requested(query) and decision.route != "direct_answer"
                and decision.intent not in {"injection", "out_of_scope"}):
            decision.route = "retrieval"
            decision.intent = "procedure_query"
            decision.reason += " → explicit workflow evidence"

    def _active_kb_version(self) -> str:
        from qa_core.storage.kb_dao import KbDao

        dao = KbDao(self.settings.mysql_dsn)
        try:
            with dao.engine.connect() as conn:
                rows = conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).all()
            if len(rows) != 1:
                raise ValueError("知识版本未就绪或存在多个 ACTIVE")
            from qa_core.storage.version_manifest import verify_read_binding
            verify_read_binding(self.settings, rows[0][0])
            return rows[0][0]
        finally:
            dao.engine.dispose()

    def _remember(self, session_id: str, query: str, answer: str,
                  route: str = "", intent: str = "", user_id: str = "anon",
                  tenant_id: str = "default", history_key: str | None = None,
                  context_query: str | None = None, needs_clarification: bool = False) -> str | None:
        """落库并返回 assistant 消息 id（前端反馈按钮用）。"""
        key = history_key or session_id
        hist = list(self._load_session(key))
        from qa_core.schemas.qa_status import outcome
        hist.append({"role": "user", "content": query, "context_query": context_query or query})
        hist.append({"role": "assistant", "content": answer, "needs_clarification": needs_clarification,
                     "status": outcome(answer, refusal=is_refusal_answer(answer), intent=intent,
                                       needs_clarification=needs_clarification)})
        hist = hist[-6:]
        self._save_session(key, hist)
        # 落库：sessions upsert + messages（供反馈/追溯）；失败不影响问答主流程
        try:
            import uuid

            import sqlalchemy as sa

            from qa_core.storage.kb_dao import KbDao

            engine = KbDao(self.settings.mysql_dsn).engine
            user_mid, assistant_mid = str(uuid.uuid4()), str(uuid.uuid4())
            trace_id = str(uuid.uuid4())
            def _write(conn, with_context: bool) -> None:
                if with_context:
                    conn.execute(sa.text(
                        "INSERT IGNORE INTO sessions (session_id, user_id, tenant_id) "
                        "VALUES (:s, :u, :tenant)"),
                        {"s": session_id, "u": user_id, "tenant": tenant_id})
                else:
                    conn.execute(sa.text(
                        "INSERT IGNORE INTO sessions (session_id, user_id) VALUES (:s, :u)"),
                        {"s": session_id, "u": user_id})
                for mid, role, content in ((user_mid, "user", query),
                                           (assistant_mid, "assistant", answer)):
                    conn.execute(sa.text(
                        "INSERT INTO messages (message_id, session_id, role, content, "
                        "route, intent, trace_id) VALUES (:m, :s, :r, :c, :route, :intent, :t)"),
                        {"m": mid, "s": session_id, "r": role,
                         "c": content[:60000], "route": route, "intent": intent,
                         "t": trace_id})

            try:
                with engine.begin() as conn:
                    _write(conn, True)
            except sa.exc.OperationalError:
                with engine.begin() as conn:
                    _write(conn, False)
            return assistant_mid
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"status": "FAILED", "error": {"code": code, "message": message}, "answer": "",
                "sources": [], "route": "", "intent": "",
                "diagnostics": {"degraded": True}}


class _VerifyFailed(Exception):
    """核验两次失败：携带引用模式答案。"""

    def __init__(self, fallback: str):
        super().__init__("verify failed")
        self.fallback = fallback
