"""Catalog scope, stable pagination, and current-content validation."""
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api import route_evidence as api
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.schemas.evidence import evidence_of


def document(id='1', **kwargs):
    return RetrievedDoc(id, '只读检查证据', 0, source='manual.md', doc_type='manual',
        asset_id='a', source_version='r1', kb_version='v1', **kwargs)


@pytest.fixture
def setup(monkeypatch):
    docs = [document('2'), document('1'), document('3', synthetic=True)]
    docs[-1].asset_id = 'b'
    searcher = Mock()
    searcher.catalog_docs.side_effect = lambda *a, **k: docs
    def fetch_chunk(id, version, *, scope, collection_kind='doc'):
        return next(d for d in docs if d.id == id)
    searcher.fetch_evidence_chunk.side_effect = fetch_chunk
    service = SimpleNamespace(searcher=searcher, source=lambda token, scope:
        next(evidence_of(d) for d in docs if evidence_of(d)['evidence_id'] == token))
    ctx = AuthContext('u', ['ops'], 'api_key', tenant_id='tenant-a')
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[get_auth_context] = lambda: ctx
    monkeypatch.setattr(api, '_enforce_rate_limit', Mock())
    monkeypatch.setattr(api, 'record', Mock())
    monkeypatch.setattr(api, 'resolve_version', lambda requested, ctx: requested or 'v1')
    monkeypatch.setattr(api, 'get_evidence_service', lambda: service)
    for store in (api._PAGE_CACHE, api._CATALOG_CACHE, api._REFERENCE_CACHE):
        store.clear()
    return TestClient(app), docs, service, ctx


def test_catalog_grouping_pagination_is_stable_and_scoped(setup):
    client, docs, service, ctx = setup
    first = client.get('/kb/catalog?page_size=1')
    assert first.status_code == 200
    assert first.headers['cache-control'] == 'no-store'
    value = first.json()
    assert value['total'] == 2 and value['pages'] == 2
    assert value['items'][0]['visible_matching_chunks'] == 2
    docs.reverse()
    again = client.get('/kb/catalog?page_size=1').json()
    # 稳定分页：反转底层顺序后，已缓存页内容不变（命中缓存正是稳定性的来源）
    assert again['cached'] is True
    assert {k: v for k, v in again.items() if k != 'cached'} == {k: v for k, v in value.items() if k != 'cached'}
    args = service.searcher.catalog_docs.call_args.kwargs
    assert args['scope'] == ctx.scope and args['tenant_id'] == 'tenant-a'
    assert client.get('/kb/catalog?page=2&page_size=1').json()['items'][0]['synthetic']


@pytest.mark.parametrize('params,total', [('query=不存在',0), ('origin=synthetic',1),
    ('origin=non_synthetic',1), ('doc_type=faq',0), ('query=只读',2)])
def test_catalog_filters_and_empty_state(setup, params, total):
    client, *_ = setup
    assert client.get('/kb/catalog?'+params).json()['total'] == total


@pytest.mark.parametrize('field', ['question','source','origin_type','synthetic','asset_id','text'])
def test_catalog_metadata_change_fails_closed(setup, field):
    client, docs, service, _ = setup
    fresh = copy.deepcopy(docs[1]); setattr(fresh, field, True if field == 'synthetic' else 'changed')
    service.searcher.fetch_evidence_chunk.return_value = fresh
    service.searcher.fetch_evidence_chunk.side_effect = None
    response = client.get('/kb/catalog')
    assert response.status_code == 409 and 'items' not in response.json()


def test_catalog_withdrawn_reference_fails_closed(setup):
    client, _, service, _ = setup
    service.source = lambda *a: None
    assert client.get('/kb/catalog').status_code == 409


def test_catalog_verification_error_does_not_leak_partial_data(setup):
    client, _, service, _ = setup
    service.searcher.catalog_docs.side_effect = RuntimeError('private backend detail')
    response = client.get('/kb/catalog')
    assert response.status_code == 503 and 'private' not in response.text


@pytest.mark.parametrize('query', ['page=0','page_size=31','kind=invalid','origin=invalid'])
def test_catalog_rejects_unbounded_or_invalid_request(setup, query):
    assert setup[0].get('/kb/catalog?'+query).status_code == 422


def test_capabilities_use_actual_role_and_tenant(setup):
    client, _, _, ctx = setup
    caps = client.get('/kb/capabilities').json()
    assert caps['can_read_jobs'] and not caps['can_upload']
    ctx.roles.append('admin')
    caps = client.get('/kb/capabilities').json()
    assert caps['can_upload'] and not caps['can_retry_rebuild']
    ctx.tenant_id = 'default'
    assert client.get('/kb/capabilities').json()['can_retry_rebuild']


def test_catalog_recall_retains_scope_and_does_not_return_vectors(monkeypatch):
    from qa_core.retrieval.searcher import Searcher
    from qa_core.security.policy import AccessScope
    searcher=Searcher.__new__(Searcher)
    searcher.settings=SimpleNamespace(mysql_dsn='unused')
    searcher.require_evidence_schema=Mock(return_value=('scoped',{'id','text','dense','sparse'}))
    searcher._filter_expr=Mock(return_value='full scoped filter')
    searcher._call=Mock(return_value=[])
    scope=AccessScope.build(tenant_id='tenant-a',roles=['ops'],dept_ids=['netops'])
    assert searcher.catalog_docs(['ops'],'v1',scope=scope)==[]
    assert searcher._call.call_count==2
    for call in searcher._call.call_args_list:
        assert call.kwargs['filter']=='full scoped filter'
        assert call.kwargs['output_fields']==['id','text']
        assert call.kwargs['consistency_level']=='Strong'
    assert searcher._filter_expr.call_args.args[1]==scope
    searcher._call.return_value=[{}]*10000
    with pytest.raises(ValueError,match='bounded'):searcher.catalog_docs(['ops'],'v1',scope=scope)
    searcher._call.reset_mock()
    empty=AccessScope(roles=('ops',),security_levels=())
    assert searcher.catalog_docs(['ops'],'v1',scope=empty)==[]
    searcher._call.assert_not_called()
