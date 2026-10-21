"""Dashboard aggregation helpers, version readability and catalog memoisation."""
import time
from contextlib import contextmanager

import pytest
from fastapi import HTTPException, Response

from qa_core.api import route_dashboard, route_evidence
from qa_core.api.auth import AuthContext
from qa_core.schemas.retrieval import RetrievedDoc


# ---------- 看板聚合辅助 ----------
def test_group_orders_and_labels():
    class Conn:
        def execute(self, statement, params):
            return self
        def mappings(self):
            return self
        def all(self):
            return [{'status': 'cleared', 'c': 4}, {'status': 'open', 'c': 2}]
    rows = route_dashboard._group(Conn(), 'SELECT 1', 'status', {},
                                  route_dashboard.STATUS_LABELS, order=['open', 'acknowledged', 'cleared', 'closed'])
    assert [row['name'] for row in rows] == ['未处理', '已清除']
    assert [row['count'] for row in rows] == [2, 4]
    unknown = route_dashboard._group(Conn(), 'SELECT 1', 'status', {})
    assert [row['count'] for row in unknown] == [4, 2]  # 默认按数量降序


def test_daily_trend_fills_missing_days():
    from datetime import datetime
    class Conn:
        def execute(self, statement, params):
            assert 'start' in params
            return self
        def mappings(self):
            return self
        def all(self):
            return [{'bucket': datetime(2026, 10, 14).date(), 'total': 3}]
    start = datetime(2026, 10, 12)
    trend = route_dashboard._daily_trend(Conn(), 'SELECT 1', {'t': 'default'}, start, 4)
    assert trend['labels'] == ['10-12', '10-13', '10-14', '10-15']
    assert trend['values'] == [0, 0, 3, 0]


# ---------- 缓存 ----------
def test_memo_ttl_and_eviction(monkeypatch):
    store = {}
    monkeypatch.setattr(route_evidence, 'CACHE_MAX_ENTRIES', 2)
    route_evidence._remember(store, 'a', 1)
    assert route_evidence._memo(store, 'a') == 1
    route_evidence._remember(store, 'b', 2)
    route_evidence._remember(store, 'c', 3)
    assert len(store) == 2 and 'a' not in store          # 满容量时淘汰最旧键
    assert route_evidence._memo(store, 'missing') is None
    assert route_evidence._memo(store, 'c') == 3
    store['c'] = (time.monotonic() - 1, 3)               # 过期即失效
    assert route_evidence._memo(store, 'c') is None and 'c' not in store


# ---------- 阅读范围 ----------
class ScopeConn:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []
    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return self
    def all(self):
        return self.rows


class ScopeEngine:
    def __init__(self, rows):
        self.conn = ScopeConn(rows)
    def connect(self):
        return self
    def __enter__(self):
        return self.conn
    def __exit__(self, *exc):
        return False
    def dispose(self):
        pass


def test_version_scope_marks_unreadable_versions(monkeypatch):
    import json
    from qa_core.storage import version_manifest
    binding = {'doc_collection': 'local_demo_release_r1_doc', 'pipeline_revision': 'v4'}
    rows = [('local-demo-release-r1', 'ACTIVE', None, json.dumps(binding)),
            ('local-content-quality-r1', 'ARCHIVED', None, json.dumps({'doc_collection': 'old_doc'})),
            ('candidate-x', 'STAGED', None, json.dumps(binding)),
            ('no-binding', 'STAGED', None, None)]
    monkeypatch.setattr(version_manifest, 'version_binding', lambda settings: binding)
    monkeypatch.setattr(route_evidence.sa, 'create_engine', lambda *a, **k: ScopeEngine(rows))
    import types
    scope = route_evidence._version_scope(types.SimpleNamespace(mysql_dsn='mysql://stub'),
                                          AuthContext('ops', ['admin'], 'api_key'))
    by_version = {row['version']: row for row in scope['versions']}
    assert scope['active'] == 'local-demo-release-r1'
    assert by_version['local-demo-release-r1']['readable'] is True
    assert by_version['candidate-x']['readable'] is True            # 同绑定的候选可预览
    assert by_version['local-content-quality-r1']['readable'] is False
    assert '回滚' in by_version['local-content-quality-r1']['reason']
    assert by_version['no-binding']['readable'] is False


# ---------- 目录缓存与校验 ----------
def FakeDoc(doc_id='chunk-1', faq=False):
    """与产品等价的证据文档（真实 RetrievedDoc，避免测试夹具与实现漂移）。"""
    return RetrievedDoc(id=doc_id, text='body', fusion_score=0.5,
                        doc_type='faq' if faq else 'document', source='manual_01.md',
                        kb_version='ver-1', asset_id='asset-1', source_version='rev-1',
                        source_id='src-1', synthetic=True, origin_type='synthetic')


def catalog_harness(monkeypatch):
    calls = {'docs': 0, 'verify': 0}
    doc = FakeDoc()
    def fake_docs(ctx, version, kind, ledger, content):
        calls['docs'] += 1
        return [doc]
    def fake_items(ctx, version, groups, ledger, content):
        calls['verify'] += 1
        return [{'title': group[0].source, 'fragments': [], 'evidence_id': 't' + str(len(groups))} for group in groups]
    monkeypatch.setattr(route_evidence, '_catalog_docs', fake_docs)
    monkeypatch.setattr(route_evidence, '_verified_items', fake_items)
    monkeypatch.setattr(route_evidence, 'resolve_version', lambda requested, ctx: 'ver-1')
    monkeypatch.setattr(route_evidence, '_ledger_revision', lambda tenant: (0, ''))
    monkeypatch.setattr(route_evidence, '_content_revision', lambda version: (0, 0, 0))
    monkeypatch.setattr(route_evidence, 'record', lambda *a, **k: None)
    monkeypatch.setattr(route_evidence, '_enforce_rate_limit', lambda ctx: None)
    for store in (route_evidence._PAGE_CACHE, route_evidence._CATALOG_CACHE, route_evidence._REFERENCE_CACHE):
        store.clear()
    return calls


def run_catalog(ctx, page=1):
    return route_evidence.catalog(Response(), query='', kind='all', doc_type='', origin='all',
                                  kb_version=None, page=page, page_size=12, ctx=ctx)


def test_catalog_is_memoised_per_scope_and_page(monkeypatch):
    calls = catalog_harness(monkeypatch)
    ops = AuthContext('ops-user', ['ops'], 'api_key')
    first = run_catalog(ops)
    assert first['cached'] is False and first['total'] == 1
    second = run_catalog(ops)
    assert second['cached'] is True and calls == {'docs': 1, 'verify': 1}
    other = AuthContext('ops-user', ['auditor'], 'api_key')
    run_catalog(other)                                      # 不同角色 = 不同缓存键
    assert calls == {'docs': 2, 'verify': 2}
    run_catalog(AuthContext('another-ops', ['ops'], 'api_key'))
    assert calls['docs'] == 2                               # 同租户同角色范围共享快照缓存（审计仍按调用者记录）
    page2 = run_catalog(ops, page=2)
    assert page2['items'] == [] and calls['docs'] == 3 and calls['verify'] == 2   # 空页不触发逐条校验


def test_catalog_refuses_when_ledger_changes(monkeypatch):
    calls = catalog_harness(monkeypatch)
    run_catalog(AuthContext('ops-user', ['ops'], 'api_key'))
    monkeypatch.setattr(route_evidence, '_ledger_revision', lambda tenant: (1, '2026-10-16 10:00:00'))
    fresh = run_catalog(AuthContext('ops-user', ['ops'], 'api_key'))
    assert fresh['cached'] is False and calls['docs'] == 2   # 撤回台账变化后必须重新读取


def test_catalog_refuses_when_ingest_revision_changes(monkeypatch):
    """入库台账修订（新上传/重传）必须立即失效页面缓存，不能读到旧数据。"""
    calls = catalog_harness(monkeypatch)
    ops = AuthContext('ops-user', ['ops'], 'api_key')
    first = run_catalog(ops)
    assert first['cached'] is False
    monkeypatch.setattr(route_evidence, '_content_revision', lambda version: (1, 7, '2026-10-19 12:00:00'))
    fresh = run_catalog(ops)
    assert fresh['cached'] is False and calls['docs'] == 2   # 入库修订变化后必须重新读取


def fake_service(doc, *, source_result=None, fresh=None):
    class Searcher:
        def fetch_evidence_chunk(self, chunk_id, version, *, scope, collection_kind='doc'):
            return fresh if fresh is not None else doc
    class Service:
        searcher = Searcher()
        def source(self, token, scope):
            return source_result
    return Service()


def test_verified_reference_rejects_missing_or_changed_source(monkeypatch):
    doc = FakeDoc()
    ctx = AuthContext('ops-user', ['ops'], 'api_key')
    monkeypatch.setattr(route_evidence, '_REFERENCE_CACHE', {})
    monkeypatch.setattr(route_evidence, 'get_evidence_service', lambda: fake_service(doc, source_result=None))
    with pytest.raises(HTTPException) as error:                       # 撤回/鉴权失败 → 409
        route_evidence._verified_reference(ctx, 'ver-1', doc, (0, ''), (0, 0, 0))
    assert error.value.status_code == 409
    assert route_evidence._REFERENCE_CACHE == {}                       # 不缓存失败结果


def test_verified_reference_rejects_changed_row(monkeypatch):
    doc = FakeDoc()
    changed = FakeDoc()
    changed.source_version = 'rev-2'                                   # 快照与最新行不一致
    ctx = AuthContext('ops-user', ['ops'], 'api_key')
    monkeypatch.setattr(route_evidence, '_REFERENCE_CACHE', {})
    monkeypatch.setattr(route_evidence, 'get_evidence_service',
                        lambda: fake_service(doc, source_result={'chunk_id': doc.id}, fresh=changed))
    with pytest.raises(HTTPException) as error:
        route_evidence._verified_reference(ctx, 'ver-1', doc, (0, ''), (0, 0, 0))
    assert error.value.status_code == 409


def test_verified_reference_caches_consistent_read(monkeypatch):
    doc = FakeDoc()
    calls = {'source': 0}
    current = {'chunk_id': doc.id, 'document_id': doc.asset_id, 'source_revision': doc.source_version,
               'kb_version': doc.kb_version, 'source': doc.source, 'text': doc.text, 'synthetic': True}
    service = fake_service(doc, source_result=current)
    def counting_source(token, scope):
        calls['source'] += 1
        return current
    service.source = counting_source
    ctx = AuthContext('ops-user', ['ops'], 'api_key')
    monkeypatch.setattr(route_evidence, '_REFERENCE_CACHE', {})
    monkeypatch.setattr(route_evidence, 'get_evidence_service', lambda: service)
    route_evidence._verified_reference(ctx, 'ver-1', doc, (0, ''), (0, 0, 0))
    route_evidence._verified_reference(ctx, 'ver-1', doc, (0, ''), (0, 0, 0))
    assert calls['source'] == 1                                        # 命中缓存不重复读
