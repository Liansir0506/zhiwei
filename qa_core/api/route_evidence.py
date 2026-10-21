"""Evidence APIs do not generate answers, publish knowledge or read arbitrary files."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import logging
import threading
from time import monotonic
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response, Query
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from qa_core.api.auth import AuthContext, get_auth_context, require_roles
from datetime import datetime, timezone
from qa_core.api.route_qa import _enforce_rate_limit
from qa_core.config.settings import get_settings
from qa_core.schemas.evidence import decode_reference, evidence_of
from qa_core.security.audit import audit_access

router = APIRouter(prefix="/kb", tags=["evidence"])
logger = logging.getLogger(__name__)

# Published versions are immutable sealed snapshots, so reusing identical reads for a short
# window cannot show different knowledge. Every request still resolves the version, re-reads
# the withdrawal ledger, re-verifies the requested page against live Milvus rows and keeps
# ACL/digest checks; only the expensive identical repeat work inside the TTL is reused, and
# the cache key always carries the version and the caller's scope fingerprint.
CACHE_TTL_SECONDS = 300.0
CACHE_MAX_ENTRIES = 512
_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: dict = {}
_REFERENCE_CACHE: dict = {}
_PAGE_CACHE: dict = {}


def _memo(store, key):
    with _CACHE_LOCK:
        row = store.get(key)
        if row is None:
            return None
        if row[0] < monotonic():
            store.pop(key, None)
            return None
        return row[1]


def _remember(store, key, value):
    with _CACHE_LOCK:
        if len(store) >= CACHE_MAX_ENTRIES:
            for stale in [name for name, row in store.items() if row[0] < monotonic()]:
                store.pop(stale, None)
            while len(store) >= CACHE_MAX_ENTRIES:
                store.pop(next(iter(store)))
        store[key] = (monotonic() + CACHE_TTL_SECONDS, value)


_INFLIGHT: dict = {}


def _single_flight(store, key, compute, wait_seconds=60.0):
    """同一键的并发读取合并为一次计算：后来者等首位完成，首位失败则各自重算。"""
    with _CACHE_LOCK:
        event = _INFLIGHT.get(key)
        leader = event is None
        if leader:
            event = _INFLIGHT[key] = threading.Event()
    if leader:
        try:
            value = compute()
        finally:
            with _CACHE_LOCK:
                finished = _INFLIGHT.pop(key, None)
            if finished:
                finished.set()
        return value
    event.wait(timeout=wait_seconds)
    return _memo(store, key)


def _read_scope(ctx):
    return (ctx.tenant_id, tuple(sorted(ctx.roles)), ctx.scope.fingerprint)


@lru_cache(maxsize=4)
def _revision_engine(dsn):
    return sa.create_engine(dsn, pool_pre_ping=True)


def _content_revision(version):
    """入库台账修订：同一版本新增/重跑入库后必须立刻失效页面缓存。

    已发布版本名义上不可变，但 STAGED 候选会继续接收上传；只靠 300 秒 TTL 会让
    候选预览在入库完成后仍显示旧清单。读不到台账时返回不可复用的键（fail-closed）。"""
    try:
        engine = _revision_engine(get_settings().mysql_dsn)
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(updated_at),'') "
                "FROM ingest_records WHERE kb_version=:v"), {"v": version}).one()
        return (int(row[0]), int(row[1]), str(row[2]))
    except sa.exc.SQLAlchemyError:
        return ('unavailable', version, monotonic())


def _ledger_revision(tenant_id):
    """Withdrawals must take effect immediately; never memoise the ledger itself.

    台账读不到时返回一个不可复用的键：本次请求不做任何缓存命中，也不写入缓存，
    既保留“撤回即时生效”，也不会把不可读台账误当成“没有撤回”。"""
    from qa_core.security.withdrawals import _ledger_engine
    try:
        with _ledger_engine(get_settings().mysql_dsn).connect() as conn:
            row = conn.execute(sa.text("SELECT COUNT(*), COALESCE(MAX(created_at), '') "
                "FROM knowledge_source_withdrawals WHERE tenant_id=:t"), {"t": tenant_id}).one()
        return (int(row[0]), str(row[1]))
    except sa.exc.SQLAlchemyError:
        return ('unavailable', tenant_id, monotonic())


def _catalog_docs(ctx, version, kind, ledger, content):
    key = ('docs', version, _read_scope(ctx), kind, ledger, content)
    docs = _memo(_CATALOG_CACHE, key)
    if docs is None:
        docs = get_evidence_service().searcher.catalog_docs(
            ctx.roles, version, tenant_id=ctx.tenant_id, scope=ctx.scope, kind=kind)
        _remember(_CATALOG_CACHE, key, docs)
    return docs


def _verified_reference(ctx, version, doc, ledger, content):
    """Re-verify one catalog entry against the live snapshot; a mismatch fails closed.

    保持原有双重校验：证据服务按 token 重读（ACL、撤回、摘要），并另取一次最新行逐字段比对；
    收益来自把每页的校验并发化并对不可变快照做短缓存，而不是削减校验本身。"""
    collection_kind = 'faq' if doc.doc_type == 'faq' else 'doc'
    service = get_evidence_service()
    token = evidence_of(doc, collection_kind)['evidence_id']
    key = ('reference', version, _read_scope(ctx), ledger, content, doc.id, token)
    cached = _memo(_REFERENCE_CACHE, key)
    if cached is not None:
        return token, cached
    current = service.source(token, ctx.scope)
    fresh = service.searcher.fetch_evidence_chunk(doc.id, version,
        scope=ctx.scope, collection_kind=collection_kind)
    if (current is None or fresh is None
            or evidence_of(fresh, collection_kind)['evidence_id'] != token
            or any(getattr(fresh, name) != getattr(doc, name) for name in
                   ('question', 'source', 'source_id', 'source_version', 'asset_id',
                    'synthetic', 'origin_type', 'doc_type'))):
        # Do not return counts or a partial page from an invalidated snapshot.
        raise HTTPException(409, '知识清单已变化，请刷新后重试')
    _remember(_REFERENCE_CACHE, key, current)
    return token, current


def _verified_items(ctx, version, page_groups, ledger, content):
    """Verify the page concurrently; bounded so a page never stalls on one slow read."""
    def build(group):
        doc = group[0]
        collection_kind = 'faq' if doc.doc_type == 'faq' else 'doc'
        token, current = _verified_reference(ctx, version, doc, ledger, content)
        return {'title': doc.question if collection_kind == 'faq' else doc.source,
            'question': doc.question if collection_kind == 'faq' else '',
            'doc_type': doc.doc_type, 'source': current['source'], 'kb_version': version,
            'source_revision': current['source_revision'], 'source_id': doc.source_id,
            'synthetic': current['synthetic'], 'origin_type': doc.origin_type,
            'visible_matching_chunks': len(group), 'evidence_id': token,
            'fragments': [{'evidence_id': evidence_of(fragment, collection_kind)['evidence_id']}
                          for fragment in group],
            'summary': current['text'][:160]}
    workers = max(1, min(12, len(page_groups)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(build, page_groups))


def _version_scope(settings, ctx) -> dict:
    """Which knowledge versions this service can actually read, and why others cannot.

    A version is readable only while the running configuration matches the version's own
    sealed binding (collection set, pipeline revision, embedding); that is why switching
    between archived and active versions is a governed activate/rollback, not a preview."""
    from qa_core.storage.version_manifest import version_binding
    current = version_binding(settings)
    engine = sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT v.version, v.status, v.published_at, m.binding_json "
                "FROM kb_versions v LEFT JOIN kb_version_manifests m ON m.version = v.version "
                "ORDER BY v.published_at IS NULL, v.published_at DESC, v.version")).all()
    finally:
        engine.dispose()
    import json
    versions, active = [], None
    for version, status, published_at, binding_json in rows:
        binding = json.loads(binding_json) if isinstance(binding_json, str) else (binding_json or {})
        matching = bool(binding) and binding == current
        if status == 'ACTIVE':
            active = version
        if status == 'ACTIVE':
            reason = ''
        elif not binding:
            reason = '版本未登记读取绑定，不能预览'
        elif not matching:
            reason = '版本的集合绑定与当前服务不同；需经版本与发布回滚/激活后查看'
        elif status == 'STAGED':
            reason = ''
        else:
            reason = '仅当前激活版本可直接阅读'
        versions.append({'version': version, 'status': status,
            'published_at': published_at.isoformat() if hasattr(published_at, 'isoformat') else published_at,
            'readable': bool(matching) and status in ('ACTIVE', 'STAGED'), 'reason': reason})
    return {'active': active, 'doc_collection': current.get('doc_collection'),
            'faq_collection': current.get('faq_collection'), 'versions': versions}



@router.post('/cache/invalidate')
def invalidate_cache(response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """场景实验室：显式失效知识目录/条目/页面缓存（不触碰知识内容与版本）。"""
    response.headers['Cache-Control'] = 'no-store'
    require_roles(ctx, ('admin',))
    _enforce_rate_limit(ctx)
    with _CACHE_LOCK:
        cleared = len(_CATALOG_CACHE) + len(_REFERENCE_CACHE) + len(_PAGE_CACHE)
        _CATALOG_CACHE.clear(); _REFERENCE_CACHE.clear(); _PAGE_CACHE.clear()
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
                 auth_source=ctx.source, action='kb_cache_invalidate', resource='/kb',
                 decision='allow', roles=list(ctx.roles))
    return {'cleared_entries': cleared, 'invalidated_at': datetime.now(timezone.utc).isoformat()}


@router.get('/capabilities')
def capabilities(response: Response, ctx: AuthContext = Depends(get_auth_context)):
    response.headers['Cache-Control'] = 'no-store'
    from qa_core.api.auth import require_global_admin
    global_admin = False
    if 'admin' in ctx.roles:
        try:
            require_global_admin(ctx); global_admin = True
        except HTTPException:
            pass
    return {'can_upload': 'admin' in ctx.roles,
            'can_read_jobs': bool(set(ctx.roles) & {'ops', 'auditor', 'admin'}),
            'can_retry_upload': 'admin' in ctx.roles, 'can_retry_rebuild': global_admin,
            'can_preview_staged': 'admin' in ctx.roles}


@router.get('/version-scope')
def version_scope(response: Response, ctx: AuthContext = Depends(get_auth_context)):
    """阅读范围：当前服务实际可读的知识版本，以及其它版本不可预览的原因。"""
    response.headers['Cache-Control'] = 'no-store'
    return _version_scope(get_settings(), ctx)


@router.get('/catalog')
def catalog(response: Response, query: str = Query('', max_length=200),
            kind: Literal['all', 'faq', 'document'] = 'all',
            doc_type: str = Query('', max_length=32),
            origin: Literal['all', 'synthetic', 'non_synthetic'] = 'all',
            kb_version: str | None = Query(None, min_length=1, max_length=64),
            page: int = Query(1, ge=1, le=1000), page_size: int = Query(12, ge=1, le=30),
            ctx: AuthContext = Depends(get_auth_context)):
    response.headers['Cache-Control'] = 'no-store'
    _enforce_rate_limit(ctx)
    try:
        version = resolve_version(kb_version, ctx)
        ledger = _ledger_revision(ctx.tenant_id)
        content = _content_revision(version)
        needle = query.strip().casefold()
        cache_key = ('page', version, _read_scope(ctx), ledger, content, kind, doc_type, origin, needle, page, page_size)
        cached = _memo(_PAGE_CACHE, cache_key)
        if cached is not None:
            result = dict(cached, cached=True)
        else:
            def build_page():
                docs = _catalog_docs(ctx, version, kind, ledger, content)
                groups = {}
                for doc in docs:
                    if doc_type and doc.doc_type != doc_type:
                        continue
                    if origin != 'all' and doc.synthetic != (origin == 'synthetic'):
                        continue
                    if needle and needle not in '\n'.join([doc.source, doc.question, doc.answer, doc.text]).casefold():
                        continue
                    identity = ('faq', doc.id) if doc.doc_type == 'faq' else ('doc', doc.asset_id, doc.source_version, doc.source, doc.doc_type)
                    groups.setdefault(identity, []).append(doc)
                ordered = sorted((sorted(group, key=lambda d: str(d.id)) for group in groups.values()),
                                 key=lambda group: (group[0].doc_type, group[0].source, str(group[0].id)))
                page_groups = ordered[(page-1)*page_size:page*page_size]
                items = _verified_items(ctx, version, page_groups, ledger, content) if page_groups else []
                payload = {'kb_version': version, 'items': items, 'total': len(ordered), 'page': page,
                           'page_size': page_size, 'pages': (len(ordered)+page_size-1)//page_size,
                           'scope_note': '当前权限及筛选条件可见；文档按来源修订归组，数量仅含匹配片段',
                           'cached': False}
                _remember(_PAGE_CACHE, cache_key, payload)
                return payload
            result = _single_flight(_PAGE_CACHE, cache_key, build_page)
            if result is None:                       # 首位失败：本次自行重算（异常照常抛出）
                result = build_page()
    except HTTPException:
        record(ctx, 'catalog_read', 'deny'); raise
    except Exception:
        record(ctx, 'catalog_read', 'deny', 'verification_unavailable')
        logger.exception('catalog unavailable')
        raise HTTPException(503, '知识清单验证暂不可用或超出读取上限，请稍后重试') from None
    record(ctx, 'catalog_read', 'allow')
    return result


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    kb_version: str | None = Field(default=None, min_length=1, max_length=64)
    top_n: int = Field(default=5, ge=1, le=10)


def resolve_version(requested, ctx):
    engine = sa.create_engine(get_settings().mysql_dsn)
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT version, status FROM kb_versions "
                "WHERE version=:v" if requested else
                "SELECT version, status FROM kb_versions WHERE status='ACTIVE'"),
                {"v": requested} if requested else {}).all()
        if len(rows) != 1:
            raise HTTPException(404, "知识版本不可用")
        version, status = rows[0]
        if status != "ACTIVE" and not (status == "STAGED" and "admin" in ctx.roles):
            raise HTTPException(404, "知识版本不可用")
        from qa_core.storage.version_manifest import verify_read_binding
        verify_read_binding(get_settings(), version)
        return version
    finally:
        engine.dispose()


@lru_cache(maxsize=1)
def get_evidence_service():
    from qa_core.application.evidence_service import create_evidence_service
    return create_evidence_service(get_settings())


def record(ctx, action, decision, reason=""):
    audit_access(get_settings().mysql_dsn, actor=ctx.user_id, tenant_id=ctx.tenant_id,
        auth_source=ctx.source, action=action, resource="/kb/evidence", decision=decision,
        roles=ctx.roles, scope=ctx.scope.to_dict(), reason=reason)


@router.post("/search-evidence")
def search_evidence(body: EvidenceRequest, response: Response,
                    ctx: AuthContext = Depends(get_auth_context)):
    _enforce_rate_limit(ctx)
    response.headers["Cache-Control"] = "no-store"
    try:
        version = resolve_version(body.kb_version, ctx)
        if not body.query.strip():
            raise HTTPException(422, "query 不能为空")
        result = get_evidence_service().search(body.query.strip(), version, ctx.scope, body.top_n)
    except HTTPException:
        record(ctx, "evidence_search", "deny")
        raise
    except Exception:
        record(ctx, "evidence_search", "deny", "verification_unavailable")
        logger.exception("evidence search unavailable")
        raise HTTPException(503, "证据验证暂不可用") from None
    record(ctx, "evidence_search", "allow")
    return result


@router.get("/evidence/{evidence_id}")
def view_evidence(evidence_id: str, response: Response,
                  ctx: AuthContext = Depends(get_auth_context)):
    _enforce_rate_limit(ctx)
    response.headers["Cache-Control"] = "no-store"
    try:
        try:
            version = decode_reference(evidence_id)[0]
        except ValueError:
            raise HTTPException(404, "引用不存在或不可访问") from None
        resolve_version(version, ctx)
        result = get_evidence_service().source(evidence_id, ctx.scope)
        if result is None:
            raise HTTPException(404, "引用不存在或不可访问")
    except HTTPException:
        record(ctx, "evidence_view", "deny")
        raise
    except Exception:
        record(ctx, "evidence_view", "deny", "verification_unavailable")
        logger.exception("evidence view unavailable")
        raise HTTPException(503, "证据验证暂不可用") from None
    record(ctx, "evidence_view", "allow")
    return result
