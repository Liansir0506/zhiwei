"""在线问答 API：同步、WebSocket 与 HTTP SSE，保持旧入口兼容。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import re
import threading
import time

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from qa_core.api.auth import (AuthContext, get_auth_context, get_websocket_auth_context,
                              require_roles)
from qa_core.api.rate_limit import check_rate_limit
from qa_core.application.qa_usecase import QaUseCase
from qa_core.config.settings import get_settings
from qa_core.security.audit import audit_access

router = APIRouter()
_usecase: QaUseCase | None = None
_sse_slots = threading.BoundedSemaphore(5)
_SSE_TOTAL_TIMEOUT_S = 120.0


def get_usecase() -> QaUseCase:
    global _usecase
    if _usecase is None:
        _usecase = QaUseCase()
    return _usecase


class QaRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    session_id: str = "default"
    top_n: int = 5


class QaSseRequest(QaRequest):
    kb_version: str | None = Field(default=None, min_length=1, max_length=64)


class FeedbackRequest(BaseModel):
    message_id: str
    rating: str = Field(pattern="^(up|down)$")
    reason: str | None = None
    comment: str | None = None


@router.get('/qa/recommendations')
def recommendations(response: Response, kb_version: str | None = None,
                    ctx: AuthContext = Depends(get_auth_context)):
    from qa_core.api.route_evidence import resolve_version
    from qa_core.retrieval.workflow import is_workflow_query
    response.headers['Cache-Control'] = 'no-store'
    _enforce_rate_limit(ctx)
    try:
        version = resolve_version(kb_version, ctx)
        docs = get_usecase().searcher.recommended_faqs(ctx.roles, version,
            tenant_id=ctx.tenant_id, scope=ctx.scope)
        items, seen = [], set()
        for doc in docs:
            if is_workflow_query(doc.question):
                continue
            # Numbered synthetic variants share one subject; show subject diversity.
            group = re.sub(r'(?:SIM-FAQ-|FAQ-SIM-)\d+|\d+', '', doc.question)
            if group in seen or len(doc.question) > 500:
                continue
            seen.add(group)
            items.append({'query': doc.question, 'source': doc.source,
                          'synthetic': doc.synthetic, 'doc_type': 'faq'})
            if len(items) == 12:
                break
        return {'kb_version': version, 'items': items}
    except HTTPException:
        raise
    except Exception:
        logging.getLogger(__name__).exception('FAQ recommendations unavailable')
        raise HTTPException(503, '推荐问题暂不可用') from None


def _enforce_rate_limit(ctx: AuthContext) -> None:
    # admin 角色豁免严格限流（运维管理操作密集）；普通用户按配置 QPS
    limit = 60 if "admin" in ctx.roles else get_settings().rate_limit_qps
    check_rate_limit(ctx.user_id, limit)


def _scoped_session_id(ctx: AuthContext, supplied: str) -> str:
    """服务端按租户/主体隔离会话键，避免猜测 session_id 读取他人上下文。"""
    raw = f"{ctx.tenant_id}:{ctx.user_id}:{supplied.strip() or 'default'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:36]


@router.post("/qa")
def qa(body: QaRequest, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    _enforce_rate_limit(ctx)
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="qa", resource="/qa", decision="allow",
                 roles=ctx.roles, scope=ctx.scope.to_dict(), query=body.query)
    try:
        result = get_usecase().ask(body.query, user_id=ctx.user_id, roles=ctx.roles,
                                   session_id=_scoped_session_id(ctx, body.session_id),
                                   tenant_id=ctx.tenant_id, scope=ctx.scope)
    except HTTPException:
        raise
    except Exception:
        logging.getLogger(__name__).exception("qa backend failure")
        raise HTTPException(503, {"status": "FAILED", "code": "E500", "message": "问答服务暂不可用"}) from None
    if "error" in result:
        status = 400 if result["error"]["code"].startswith("E0") else 503
        raise HTTPException(status_code=status, detail={"status": "FAILED", **result["error"]})
    return result


def _sse_frame(event: dict) -> str:
    name = {"start": "meta", "delta": "token", "end": "done"}.get(event["event"], event["event"])
    data = event.get("data") or {}
    if name == "meta":
        data = {**data, "token_mode": "provisional_until_done"}
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/qa/sse")
async def qa_sse(body: QaSseRequest, request: Request,
                 ctx: AuthContext = Depends(get_auth_context)):
    """Bounded bridge around the existing blocking stream; final answer is in done."""
    _enforce_rate_limit(ctx)
    # Explicit STAGED preview uses the same gate as read-only evidence. Merely
    # passing a version in the request never grants the caller preview access.
    if body.kb_version:
        from qa_core.api.route_evidence import resolve_version
        pinned_version = resolve_version(body.kb_version, ctx)
    else:
        pinned_version = None
    if not _sse_slots.acquire(blocking=False):
        raise HTTPException(429, "流式推理繁忙，请稍后重试")
    try:
        audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                     auth_source=ctx.source, action="qa_sse", resource="/qa/sse",
                     decision="allow", roles=ctx.roles, scope=ctx.scope.to_dict(), query=body.query)
        usecase = get_usecase()
    except Exception:
        _sse_slots.release()
        raise
    events: queue.Queue = queue.Queue(maxsize=16)
    stop = threading.Event()
    session_id = _scoped_session_id(ctx, body.session_id)

    def put(item) -> bool:
        while not stop.is_set():
            try:
                events.put(item, timeout=.25)
                return True
            except queue.Full:
                continue
        return False

    def produce() -> None:
        stream = None
        try:
            stream = iter(usecase.ask_stream(body.query, roles=ctx.roles, session_id=session_id,
                                             user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                                             scope=ctx.scope, kb_version=pinned_version))
            while not stop.is_set():
                try:
                    item = next(stream)
                except StopIteration:
                    break
                if stop.is_set() or not put(item):
                    break
                if item.get("event") in {"end", "error"}:
                    break
        except Exception:
            logging.getLogger(__name__).exception("qa sse backend failure")
            put({"event": "error", "data": {"status": "FAILED", "code": "E500",
                                            "message": "问答服务暂不可用"}})
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logging.getLogger(__name__).exception("qa sse stream close failure")
            put(None)
            _sse_slots.release()

    async def stream_response():
        worker = threading.Thread(target=produce, daemon=True, name="qa-sse")
        worker.start()
        began = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    break
                if time.monotonic() - began >= _SSE_TOTAL_TIMEOUT_S:
                    yield _sse_frame({"event": "error", "data": {"status": "FAILED",
                        "code": "E504", "message": "流式请求超时"}})
                    break
                try:
                    item = await asyncio.to_thread(events.get, True, .25)
                except queue.Empty:
                    continue
                if item is None:
                    break
                yield _sse_frame(item)
                if item.get("event") in {"end", "error"}:
                    break
        finally:
            # A currently blocked model call cannot be preempted. The stop flag
            # prevents the next piece/request and its slot stays held until exit.
            stop.set()

    return StreamingResponse(stream_response(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-store",
                                      "X-Accel-Buffering": "no"})


@router.websocket("/qa/ws")
async def qa_ws(websocket: WebSocket):
    try:
        # 浏览器 WebSocket API 不支持自定义认证 Header；key 参数仍走同一 API Key 校验。
        ctx = get_websocket_auth_context(websocket.headers, websocket.query_params)
    except HTTPException:
        # WebSocket 无 HTTP 响应体；策略与 HTTP 一致，以 1008 表示鉴权失败。
        await websocket.close(code=1008)
        return
    await websocket.accept()
    usecase = get_usecase()
    try:
        while True:
            data = await websocket.receive_json()
            query = str(data.get("query", "")).strip()
            session_id = str(data.get("session_id", "default"))
            if not query:
                await websocket.send_json({"event": "error",
                                           "data": {"status": "FAILED", "code": "E001", "message": "query 为空"}})
                continue
            try:
                _enforce_rate_limit(ctx)
            except HTTPException as exc:
                await websocket.send_json({"event": "error", "data": {
                    "status": "FAILED", "code": "E429", "message": str(exc.detail)}})
                continue
            audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                         auth_source=ctx.source, action="qa_stream", resource="/qa/ws",
                         decision="allow", roles=ctx.roles, scope=ctx.scope.to_dict(), query=query)
            # ask_stream 是同步生成器（检索/CrossEncoder/LLM SSE 均为阻塞调用），
            # 直接在事件循环里迭代会阻塞 WS 帧收发（事件攒到结尾、ping 超时断连）——
            # 移入后台线程执行，经队列桥接回事件循环，实现事件逐条实时推送。
            import queue as _queue
            import threading as _threading

            ev_queue: _queue.Queue = _queue.Queue()

            def _produce() -> None:
                try:
                    for event in usecase.ask_stream(
                        query, roles=ctx.roles, session_id=_scoped_session_id(ctx, session_id),
                        user_id=ctx.user_id, tenant_id=ctx.tenant_id, scope=ctx.scope):
                        ev_queue.put(event)
                except Exception as exc:  # noqa: BLE001 —— 线程内异常转 error 事件
                    ev_queue.put({"event": "error",
                                  "data": {"status": "FAILED", "code": "E500", "message": "问答服务暂不可用"}})
                finally:
                    ev_queue.put(None)

            _threading.Thread(target=_produce, daemon=True, name="ask-stream").start()
            while True:
                event = await asyncio.to_thread(ev_queue.get)
                if event is None:
                    break
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return


@router.post("/feedback")
def feedback(body: FeedbackRequest, ctx: AuthContext = Depends(get_auth_context)) -> dict:
    require_roles(ctx, ("ops", "admin"))
    _enforce_rate_limit(ctx)
    dao_engine = sa.create_engine(get_settings().mysql_dsn)

    with dao_engine.begin() as conn:
        owner = conn.execute(sa.text(
            "SELECT s.user_id, s.tenant_id FROM messages m JOIN sessions s ON s.session_id=m.session_id "
            "WHERE m.message_id=:message_id AND m.role='assistant'"),
            {"message_id": body.message_id}).first()
        if owner is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        if "admin" not in ctx.roles and (owner[0] != ctx.user_id or owner[1] != ctx.tenant_id):
            audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                         auth_source=ctx.source, action="feedback", resource="/feedback",
                         decision="deny", reason="message_owner_mismatch", roles=ctx.roles,
                         scope=ctx.scope.to_dict())
            raise HTTPException(status_code=403, detail="不能反馈其他用户或租户的消息")
        conn.execute(sa.text(
            "INSERT INTO feedback (message_id, rating, reason, comment) "
            "VALUES (:m, :r, :reason, :comment)"),
            {"m": body.message_id, "r": body.rating,
             "reason": body.reason, "comment": body.comment})
        if body.rating == "down":
            # T4 修复：query_text 存用户原问题（此前误存助手答案）；同时留回答、会话、版本，
            # 供“差评→补资料→复测→关闭”闭环使用；同一消息重复差评合并到同一条记录。
            original = conn.execute(sa.text(
                "SELECT m2.content, m2.session_id FROM messages m1 "
                "JOIN messages m2 ON m2.session_id=m1.session_id AND m2.role='user' "
                "AND (m2.created_at < m1.created_at "
                "     OR (m2.created_at = m1.created_at AND m2.message_id < m1.message_id)) "
                "WHERE m1.message_id=:m ORDER BY m2.created_at DESC, m2.message_id DESC LIMIT 1"),
                {"m": body.message_id}).first()
            answer_row = conn.execute(sa.text(
                "SELECT content, kb_version FROM messages WHERE message_id=:m"),
                {"m": body.message_id}).first()
            dup = conn.execute(sa.text(
                "SELECT id FROM bad_cases WHERE message_id=:m LIMIT 1"),
                {"m": body.message_id}).first()
            if dup:
                conn.execute(sa.text(
                    "UPDATE bad_cases SET cause=:reason WHERE id=:id"),
                    {"reason": body.reason, "id": dup[0]})
            else:
                conn.execute(sa.text(
                    "INSERT INTO bad_cases (query_text, answer_text, cause, message_id, session_id, kb_version) "
                    "VALUES (:q, :a, :reason, :m, :sid, :v)"),
                    {"q": (original[0] if original else body.comment or "(未找到原问题)")[:500],
                     "a": answer_row[0] if answer_row else None,
                     "reason": body.reason, "m": body.message_id,
                     "sid": original[1] if original else None,
                     "v": answer_row[1] if answer_row else None})
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action="feedback", resource="/feedback", decision="allow",
                 roles=ctx.roles, scope=ctx.scope.to_dict())
    return {"ok": True}
