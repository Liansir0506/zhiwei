"""Controlled development regressions; no live storage or query models."""
import copy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from qa_core.application.agent_relations import select_targets, RelationSelectionError
from qa_core.application.agent_tools import digest
from qa_core.application.evidence_service import EvidenceService
from qa_core.retrieval.constraints import EvidenceConstraint, RecordUnavailable
from qa_core.retrieval.searcher import Searcher
from qa_core.schemas.retrieval import RetrievedDoc, RetrievalResult
from qa_core.config.settings import Settings
from qa_core.security.policy import AccessScope
from tests.unit.test_readonly_agent import Backend, run
from tests.unit.test_workflow_evidence import chunks

SCOPE = AccessScope.build(roles=['ops'], requested_security_levels=['internal'])
FIELDS = {'id', 'text', 'source', 'source_version', 'asset_id', 'ticket_id', 'parent_id',
          'kb_version', 'tenant_id', 'dept_id', 'visibility', 'allowed_roles', 'role_scope',
          'security_level', 'device_type', 'status', 'effective_from', 'effective_to', 'doc_type'}

def targets(exact=False):
    return EvidenceConstraint((('asset', 'r'),), triples=(('asset', 'r', 'T-1'),) if exact else None,
                              revision='bound', checksums=(('asset', 'r', 'a'*64),))

def ticket_context():
    context=Backend().rows
    context['work_orders']=[{'ticket_id':'T-1'}]
    context['_relations'].append({'asset_id':'asset','source_version':'r','relation_type':'work_order','relation_id':'T-1'})
    return context

def select(context, query='注册失败原因'):
    return select_targets(context, 'v', query, digest(context))

def searcher():
    client=Mock();client.describe_collection.return_value={'fields':[{'name':n} for n in FIELDS]}
    cache=Mock();cache.get_with_lock.return_value=None
    return Searcher(client=client,cache=cache,embedder=Mock(),settings=Settings(_env_file=None)),client,cache

def test_pair_and_triple_lists_do_not_form_cartesian_product():
    c=EvidenceConstraint((('A','r1'),('B','r2')),triples=(('A','r1','T1'),('B','r2','T2')))
    assert not c.allows(RetrievedDoc('x','body',1,asset_id='A',source_version='r1',ticket_id='T2'))
    assert '(asset_id == "A" and source_version == "r1" and ticket_id == "T1")' in c.expression()
    with pytest.raises(ValueError): EvidenceConstraint((('A','r1'),),triples=(('B','r1','T1'),))

def test_related_file_mode_does_not_imply_selected_ticket():
    c=select(ticket_context())
    assert not c.exact and c.allows(RetrievedDoc('x','body',1,asset_id='asset',source_version='r',ticket_id='T-2'))

@pytest.mark.parametrize('query',['核对关联工单原文','核对关联工单 T-1 的原文'])
def test_exact_mode_selects_only_visible_typed_work_order(query):
    c=select(ticket_context(),query)
    assert c.exact and c.triples==(('asset','r','T-1'),)

@pytest.mark.parametrize('query',['核对关联工单 T-2 原文','核对关联工单 unrelated-ticket 原文','核对关联工单 2 原文','核对关联工单 T2 原文'])
def test_same_file_unlinked_identity_is_not_silently_substituted(query):
    with pytest.raises(RelationSelectionError) as e: select(ticket_context(),query)
    assert e.value.reason=='linked_record_unavailable'

def test_unsupported_work_order_selector_clarifies_instead_of_substituting():
    with pytest.raises(RelationSelectionError) as e: select(ticket_context(),'核对关联工单第二条原文')
    assert e.value.status=='NEEDS_CLARIFICATION'

@pytest.mark.parametrize('query',['核对关联工单原文','核对关联工单 T-1 和 T-2 原文'])
def test_multiple_work_orders_require_one_explicit_selection(query):
    c=ticket_context();c['work_orders'].append({'ticket_id':'T-2'})
    c['_relations'].append({'asset_id':'asset','source_version':'r','relation_type':'work_order','relation_id':'T-2'})
    with pytest.raises(RelationSelectionError) as e: select(c,query)
    assert e.value.status=='NEEDS_CLARIFICATION'

@pytest.mark.parametrize('mutation',['empty','other_release','missing_revision','bad_checksum','truncated','too_many'])
def test_incomplete_or_unrelated_roster_fails_closed(mutation):
    c=ticket_context()
    if mutation=='empty': c['_relations']=[]
    if mutation=='other_release': c['knowledge'][0]['kb_version']='old'
    if mutation=='missing_revision': c['knowledge'][0]['source_version']=''
    if mutation=='bad_checksum': c['knowledge'][0]['checksum']=''
    if mutation=='truncated': c['truncated']=True
    if mutation=='too_many': c['knowledge']*=21
    with pytest.raises(RelationSelectionError): select(c)

def test_empty_constraint_never_calls_recall_or_embeds():
    s,client,cache=searcher()
    assert not s.search_docs(['q'],['ops'],'v',scope=SCOPE,constraint=EvidenceConstraint(())).docs
    client.search.assert_not_called();s.embedder.embed_query.assert_not_called();cache.get_with_lock.assert_not_called()

def test_hard_asset_pair_survives_dense_sparse_soft_retry():
    s,client,cache=searcher();client.search.return_value=[[]];cache.get.return_value=None;s.embedder.embed_query.return_value=[0.0]
    r=s.search_docs(['q'],['ops'],'v',scope=SCOPE,doc_types=['manual'],constraint=targets())
    assert r.filter_relaxed and len(client.search.call_args_list)==4
    for call in client.search.call_args_list:
        expr=call.kwargs['filter']
        assert targets().expression() in expr
        assert 'tenant_id == "default"' in expr and 'status == "active"' in expr

def test_cache_is_canonical_but_separates_changed_target_and_revision():
    s,client,cache=searcher();s._recall=Mock(return_value=([],[]))
    a=EvidenceConstraint((('A','r'),('B','r')),revision='one')
    b=EvidenceConstraint((('B','r'),('A','r')),revision='one')
    c=EvidenceConstraint((('A','r'),('B','r')),revision='two')
    for t in [a,b,c,targets()]: s.search_docs(['q'],['ops'],'v',scope=SCOPE,constraint=t)
    keys=[call.args[-1] for call in cache.get_with_lock.call_args_list]
    assert keys[0]==keys[1] and len(set(keys))==3

def test_cached_foreign_asset_and_revision_are_intersected_again():
    s,client,cache=searcher()
    cache.get_with_lock.return_value=[RetrievedDoc('ok','body',1,asset_id='asset',source_version='r').to_dict(),
        RetrievedDoc('bad','body',1,asset_id='foreign',source_version='r').to_dict(),
        RetrievedDoc('old','body',1,asset_id='asset',source_version='old').to_dict()]
    r=s.search_docs(['q'],['ops'],'v',scope=SCOPE,constraint=targets())
    assert [d.id for d in r.docs]==['ok'] and r.cache_hit

@pytest.mark.parametrize('when',['search','read','after_audit'])
def test_relation_changes_discard_observations_sources_and_explanation(when):
    b=Backend()
    change=lambda: b.rows['_relations'].clear()
    if when=='search': b.on_search=change
    if when=='read': b.on_read=lambda n:change() if n==1 else None
    if when=='after_audit': b.on_audit=lambda event:change() if event=='end' else None
    result=run(b,alarm_id='a')
    assert result['stop_reason']=='relation_changed'
    assert not result['sources'] and not result['observations'] and result['retrieval_scope'] is None

def test_knowledge_mode_omits_domain_constraint_and_explanation():
    b=Backend();b.rows['_relations']=[]
    result=run(b)
    assert result['status']=='ANSWERED' and result['retrieval_scope'] is None
    assert not b.domains

def test_authorized_entity_bound_denies_before_database_connection():
    from qa_core.storage.domain_dao import DomainDao
    dao=object.__new__(DomainDao);dao.engine=Mock()
    with pytest.raises(ValueError,match='bound'):
        dao.knowledge_edges_for_entities('t',[('device',str(i)) for i in range(82)])
    dao.engine.connect.assert_not_called()

@pytest.mark.parametrize('fail',['complete','part_removed_at_publication'])
def test_exact_agent_final_record_verification_is_required(fail):
    from qa_core.application.agent_tools import AgentStop
    b=Backend();b.rows=ticket_context()
    def verify(version,references):
        assert version=='v' and references=={b.data['evidence_id']}
        if fail!='complete': raise AgentStop('record_incomplete','NO_EVIDENCE')
    b.verify_record=Mock(side_effect=verify)
    result=run(b,query='核对关联工单 T-1 原文',alarm_id='a')
    b.verify_record.assert_called_once()
    if fail=='complete':
        assert result['status']=='ANSWERED' and result['budget']['calls_used']==4
        assert result['retrieval_scope']['ticket_id']=='T-1'
    else:
        assert result['stop_reason']=='record_incomplete' and not result['sources'] and result['retrieval_scope'] is None

def test_withdrawn_record_cannot_publish_partial_other_parts(monkeypatch):
    from qa_core.security.withdrawals import WithdrawalPolicy
    s,client,cache=searcher();docs=record_docs(3)
    client.query.return_value=[{'id':doc.id} for doc in docs]
    s.fetch_evidence_chunk=Mock(side_effect=lambda identity,*a,**kw:docs[int(identity)])
    monkeypatch.setattr(WithdrawalPolicy,'filter_docs',lambda owner,ds,tenant:ds[1:])
    with pytest.raises(RecordUnavailable): s.fetch_record('v',scope=SCOPE,constraint=targets(True))

def record_docs(n):
    return [RetrievedDoc(str(i),'现象：注册失败只读记录'+str(i),0,asset_id='asset',source_version='r',ticket_id='T-1',source='T-1',
        extra={'source_location':{'format':'csv','ambiguous':False,'file_sha256':'a'*64,
                'matches':[[{'kind':'csv_record','record':1,'line_start':2,'line_end':2}]]}}) for i in range(n)]

@pytest.mark.parametrize('mutation',['valid','missing_part','over_budget','no_location','ambiguous','wrong_source','wrong_ticket','wrong_checksum','missing_schema'])
def test_exact_record_requires_all_persisted_parts_and_unambiguous_location(monkeypatch,mutation):
    s,client,cache=searcher();docs=record_docs(3 if mutation!='over_budget' else 4)
    if mutation=='missing_part': docs.pop()
    if mutation=='no_location': docs[0].extra={}
    if mutation=='ambiguous': docs[0].extra['source_location']['ambiguous']=True
    if mutation=='wrong_source': docs[0].source='custom label'
    if mutation=='wrong_ticket': docs[0].ticket_id='T-2'
    if mutation=='wrong_checksum': docs[0].extra['source_location']['file_sha256']='b'*64
    if mutation=='missing_schema': client.describe_collection.return_value['fields']=[{'name':n} for n in FIELDS-{'ticket_id'}]
    client.query.return_value=[{'id':d.id} for d in docs]
    s.fetch_evidence_chunk=Mock(side_effect=lambda identity,*a,**kw:next(d for d in docs if d.id==identity))
    monkeypatch.setattr('qa_core.storage.kb_dao.KbDao',lambda dsn:SimpleNamespace(engine=Mock()))
    monkeypatch.setattr('qa_core.storage.location_dao.LocationDao.record_chunk_count',lambda *a:3)
    if mutation=='valid':
        assert len(s.fetch_record('v',scope=SCOPE,constraint=targets(True)))==3
        assert client.query.call_args.kwargs['consistency_level']=='Strong'
        assert targets(True).expression() in client.query.call_args.kwargs['filter']
        s.embedder.embed_query.assert_not_called()
    else:
        with pytest.raises(RecordUnavailable): s.fetch_record('v',scope=SCOPE,constraint=targets(True))

def test_exact_service_never_ranks_or_initializes_query_models():
    s=Mock();s.fetch_record.return_value=record_docs(1);reranker=Mock()
    result=EvidenceService(s,reranker,Settings(_env_file=None)).search('核对关联工单原文','v',SCOPE,3,constraint=targets(True))
    assert result['status']=='EVIDENCE_FOUND'
    assert result['evidence'][0]['scores']['rerank'] is None
    reranker.rerank.assert_not_called();s.search_docs.assert_not_called()

def test_related_unsafe_sop_still_requires_safety_controls():
    docs=chunks(False);s=Mock();s.search_docs.return_value=RetrievalResult(['q'],docs)
    s.fetch_sop_source.return_value=docs
    reranker=Mock();reranker.rerank.side_effect=lambda q,d,**kw:d
    constraint=EvidenceConstraint((('asset-a','v-test'),))
    r=EvidenceService(s,reranker,Settings(_env_file=None)).search('ALM-AMF-REG SOP','v-test',SCOPE,3,constraint=constraint)
    assert r['status']=='NO_EVIDENCE' and r['reason']=='missing_safety_controls'
    assert s.search_docs.call_args.kwargs['constraint']==constraint
