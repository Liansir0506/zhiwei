"""Defect reproductions, not acceptance passes; no live data or dependencies."""
import copy
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api import route_evidence as api
from qa_core.api.auth import AuthContext,get_auth_context
from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.schemas.evidence import evidence_of


def fixture(monkeypatch):
    for cache in (api._CATALOG_CACHE,api._REFERENCE_CACHE,api._PAGE_CACHE,api._INFLIGHT):cache.clear()
    first=RetrievedDoc('audit-1','旧版候选内容',0,source='candidate.md',doc_type='manual',asset_id='audit-asset',source_version='r1',kb_version='audit-staged')
    state={'docs':[first],'fresh':copy.deepcopy(first),'reads':0}
    def fetch(*args,**kwargs):state['reads']+=1;return state['fresh']
    service=SimpleNamespace(searcher=SimpleNamespace(catalog_docs=lambda *a,**k:state['docs'],fetch_evidence_chunk=fetch),
                            source=lambda *a,**k:evidence_of(state['fresh']))
    for name,value in [('_enforce_rate_limit',lambda *a:None),('record',lambda *a:None),
         ('resolve_version',lambda *a:'audit-staged'),('_ledger_revision',lambda *a:(0,'')),('get_evidence_service',lambda:service)]:monkeypatch.setattr(api,name,value)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_auth_context]=lambda:AuthContext('audit',['admin'],'api_key')
    return TestClient(app),state


def test_repro_cached_page_skips_fresh_content_validation(monkeypatch):
    client,state=fixture(monkeypatch)
    with client:
        first=client.get('/kb/catalog').json();reads=state['reads']
        state['fresh'].text='候选已由同版本增量入库更新'
        second=client.get('/kb/catalog')
        assert second.status_code==200
        assert second.json()['items'][0]['summary']==first['items'][0]['summary']=='旧版候选内容'
        assert state['reads']==reads  # Confirms no live reference/row verification on cache hit.


def test_repro_staged_new_source_hidden_by_version_only_catalog_cache(monkeypatch):
    client,state=fixture(monkeypatch)
    with client:
        assert client.get('/kb/catalog?page_size=1').json()['total']==1
        added=copy.deepcopy(state['docs'][0]);added.id='audit-2';added.asset_id='new-asset';added.source='new.md'
        state['docs']=[*state['docs'],added]
        result=client.get('/kb/catalog?page_size=2').json()
        assert result['total']==1  # Different page-cache key still reuses stale catalog roster.
