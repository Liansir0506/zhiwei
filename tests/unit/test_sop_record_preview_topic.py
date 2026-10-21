from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from qa_core.retrieval.record_preview_topic import constraint_for_query
from scripts.prepare_sop_record_alignment_v2 import load_verified
from scripts.prepare_sop_record_admission import load_verified as admission


@pytest.fixture(scope='module')
def sources():return load_verified(),admission()


@pytest.mark.parametrize('query,expected',[
    ('AMF注册失败取证',[1]),('amf 注册失败',[1]),('针对AMF的ALM-AMF-REG取证',[1]),
    ('ALM-PFCP-HB取证',[6]),('gNodeB取证',[4,5,7,11]),('AMF与SMF对比',[1,2]),
    ('ALM-AMF-REG和ALM-SMF-PDU取证',[1,2]),('UPF取证',[3,6,12]),
    ('ALM-MISSING-001取证',[]),('SGW的ALM-AMF-REG取证',[]),
    ('ALM-AMF-REG-EXTRA取证',[])])
def test_explicit_topic_narrows_to_exact_admitted_revisions(sources,query,expected):
    m,p=sources;c,topic=constraint_for_query(query,m,p)
    assert c is not None and not c.exact
    assert set(c.pairs)=={(f'pending-sop-evidence-{i:03d}','draft-r2') for i in expected}
    assert all('source_version == "draft-r2"' in c.expression() for _ in expected)
    assert c.allows(SimpleNamespace(asset_id='pending-sop-evidence-001',source_version='draft-r1')) is False


@pytest.mark.parametrize('query',['请查看取证方法','XAMF123取证','SAMF取证','部署前核对配置'])
def test_no_false_partial_device_match(sources,query):
    m,p=sources;c,topic=constraint_for_query(query,m,p)
    assert c is None and topic['kind']=='exploratory'


def test_evidence_service_carries_constraint_into_recall_and_revalidation(sources):
    from qa_core.application.evidence_service import EvidenceService
    from qa_core.security.policy import AccessScope
    from qa_core.schemas.retrieval import RetrievedDoc,RetrievalResult
    m,p=sources;c,_=constraint_for_query('AMF取证',m,p)
    searcher=Mock();reranker=Mock()
    selected=RetrievedDoc(id='unit',text='AMF取证',doc_type='guide',fusion_score=0,source='guide_sop_evidence_001.md',
        kb_version='test-sop-records-r2',asset_id='pending-sop-evidence-001',source_version='draft-r2',rerank_score=1)
    searcher.search_docs.return_value=RetrievalResult(query_variants=['AMF取证'],docs=[selected])
    reranker.rerank.return_value=[selected]
    # A changed source/asset after recall is refused before text emission.
    searcher.fetch_evidence_chunk.return_value=RetrievedDoc(id='unit',text='AMF取证',doc_type='guide',fusion_score=0,
        asset_id='pending-sop-evidence-010',source_version='draft-r2')
    service=EvidenceService(searcher,reranker,SimpleNamespace(workflow_evidence_enabled=False,rerank_min=0))
    result=service.search('AMF取证','test-sop-records-r2',AccessScope.build(roles=['admin']),5,constraint=c)
    assert searcher.search_docs.call_args.kwargs['constraint'] is c
    assert result['status']=='NO_EVIDENCE' and not result['evidence']


def test_real_http_route_uses_topic_override_before_lazy_included_router(monkeypatch):
    from fastapi.testclient import TestClient
    from qa_core.security.policy import AccessScope
    import qa_core.api.auth as auth
    import qa_core.api.route_evidence as evidence
    import scripts.sop_record_topic_server as server
    ctx=SimpleNamespace(roles=['admin'],tenant_id='default',dept_ids=[],device_types=[],security_levels=[],
        scope=AccessScope.build(roles=['admin']))
    monkeypatch.setattr(auth,'authenticate_headers',lambda **kw:ctx)
    monkeypatch.setattr(server.core,'require_global_admin',lambda ctx:None)
    monkeypatch.setattr(server.core,'verify_live',lambda settings:{'read_preview_ready':True})
    monkeypatch.setattr(server,'_enforce_rate_limit',lambda ctx:None)
    monkeypatch.setattr(server,'record',lambda *args:None)
    monkeypatch.setattr(server,'resolve_version',lambda version,ctx:version)
    service=Mock();service.search.return_value=dict(status='NO_EVIDENCE',evidence=[],kb_version='test-sop-records-r2')
    monkeypatch.setattr(server,'get_evidence_service',lambda:service)
    monkeypatch.setattr(evidence,'_enforce_rate_limit',lambda ctx:None)
    monkeypatch.setattr(evidence,'record',lambda *args:None)
    def unbounded(*args):pytest.fail('old included search route must not handle the request')
    monkeypatch.setattr(evidence,'resolve_version',unbounded)
    with TestClient(server.create_app('route-test')) as client:
        response=client.post('/kb/search-evidence',headers={'X-API-Key':'unit-only'},
            json=dict(query='AMF的ALM-AMF-REG取证',kb_version='test-sop-records-r2',top_n=5))
        assert response.status_code==200 and response.json()['topic_scope']['kind']=='alarm'
    assert service.search.call_args.kwargs['constraint'].pairs==( ('pending-sop-evidence-001','draft-r2'), )
