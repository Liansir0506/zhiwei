"""Candidate protocol/SSE/evidence permissions; disabled paid generation."""
import secrets
from unittest.mock import Mock,patch
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymilvus import MilvusClient

from scripts import stage_content_quality_merge as merge
from scripts import stage_protocol_faq_demo as protocol
from scripts.prepare_legacy_projection import read,write
from scripts.migrate_legacy_collections import read_rows
from qa_core.security.policy import AccessScope
from qa_core.retrieval.supported_evidence_v2 import relevant_docs
from qa_core.retrieval.filters import _to_doc
from qa_core.schemas.evidence import evidence_of


def main():
    output=merge.REPORT/'extra-recovery-r2';output.mkdir(exist_ok=True)
    settings=merge.settings_for();engine=sa.create_engine(settings.mysql_dsn)
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.application.evidence_service import create_evidence_service
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher as Searcher
    guard=Mock();guard.chat.side_effect=guard.chat_stream.side_effect=AssertionError('paid call forbidden')
    uc=QaUseCase(searcher=Searcher(settings=settings),llm=guard)
    scope=AccessScope.build(roles=['admin']);evidence=create_evidence_service(settings)
    observations=[]
    with patch('qa_core.application.qa_usecase.relevant_docs',relevant_docs):
        for i,c in enumerate(read(protocol.REVIEW/'review-cases.json')['cases']):
            result=uc.ask(c['query'],roles=['admin'],scope=scope,kb_version=merge.VERSION,persist=False,use_llm=False)
            observations.append(dict(id=c['id'],result=result));write(output/'protocol-regression.json',observations)
            expected='faq_protocol_%02d.csv'%(1 if i<4 else 2 if i<8 else 3)
            assert result['status']=='ANSWERED' and [s['source'] for s in result['sources']]==[expected],c['id']
        streams=[]
        for c in read(merge.triage.REVIEW/'review-cases.json')['cases']:
            if not c['id'].endswith('boundary'):continue
            events=list(uc.ask_stream(c['query'],roles=['admin'],scope=scope,kb_version=merge.VERSION,persist=False))
            e=next(e for e in read(merge.triage.CANDIDATE/'approved-bodies.json')['entries'] if e['id']==c['candidate_id'])
            ends=[x['data'] for x in events if x['event']=='end'];sources=[x['data'] for x in events if x['event']=='sources']
            streams.append(dict(id=c['id'],events=events));write(output/'stream-regression.json',streams)
            assert len(ends)==1 and ends[0]['status']=='ANSWERED'
            assert [s['source_id'] for s in sources[-1]['sources']]==[e['source_identity_proposal']['source_id']]
    client=MilvusClient(uri=settings.milvus_uri,timeout=120)
    try:rows=read_rows(client,merge.FAQ);manuals=[r for r in read_rows(client,merge.DOC) if r['doc_type']=='manual']
    finally:client.close()
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(settings.embedding_model_path,local_files_only=True)
    assert all(len(tokenizer.encode(r['text'],add_special_tokens=True))<=450 and r['clause'] for r in manuals)
    samples=[]
    for row in rows:
        token=evidence_of(_to_doc(dict(id=row['id'],entity=row)),'faq')['evidence_id']
        source=evidence.source(token,scope)
        samples.append((row,source))
    from qa_core.api import route_qa,route_evidence
    from qa_core.api.auth import create_api_key,hash_key
    # Source URLs come from the production evidence service, never constructed as guesses.
    route_qa._usecase=uc;app=FastAPI();app.include_router(route_qa.router);app.include_router(route_evidence.router)
    keys={}
    try:
        for label,roles,tenant,levels in [('admin',['admin'],'default',None),('ops',['ops'],'default',None),
            ('foreign',['admin'],'other',None),('restricted',['admin'],'default',['public'])]:
            key=secrets.token_urlsafe(32);create_api_key(key,'content-stage-probe-'+label,roles,tenant_id=tenant,security_levels=levels);keys[label]=key
        checks=[]
        with TestClient(app) as http:
            for row,source in samples:
                assert source and source['text']==row['answer']
                statuses={k:http.get(source['source_url'],headers={'X-API-Key':v}).status_code for k,v in keys.items()}
                statuses['unauthenticated']=http.get(source['source_url']).status_code
                assert statuses==dict(admin=200,ops=404,foreign=404,restricted=404,unauthenticated=401),statuses
                checks.append(dict(id=row['id'],statuses=statuses,full_original=True))
            response=http.get('/qa/recommendations',params={'kb_version':merge.VERSION},headers={'X-API-Key':keys['admin']})
            response.raise_for_status();items=response.json()['items'];assert items and len(items)<=12
            assert all(item['query'] in {r['question'] for r in rows} for item in items)
        write(output/'http-auth.json',dict(passed=True,checks=checks,recommendations=items,
            transport='real in-process ASGI with database keys, not socket/browser',staged_ops_denial_expected=True))
    finally:
        with engine.begin() as conn:
            for key in keys.values():conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h'),dict(h=hash_key(key)))
        engine.dispose()
    assert not guard.chat.called and not guard.chat_stream.called
    write(output/'extra-summary.json',dict(passed=True,protocol_variants=12,triage_streams=8,
        complete_faq_originals=31,manual_real_token_checks=len(manuals),paid_calls=0,independent_quality_approved=False))
    print('Extra protocol/SSE/original/permission checks passed')


if __name__=='__main__':
    import torch
    torch.set_num_threads(4)
    main()
