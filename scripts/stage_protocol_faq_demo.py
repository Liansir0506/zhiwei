"""Execute the three explicitly admitted FAQ drafts in new local STAGED only."""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from unittest.mock import Mock

import sqlalchemy as sa
from pymilvus import MilvusClient

from scripts.prepare_protocol_faq_review import ROOT, OUTPUT as REVIEW, validate
from scripts.prepare_coverage_source_review import read, write, sha
from scripts.prepare_legacy_projection import seal, verify_seal
from scripts import original_local_runtime as runtime
from qa_core.quality.review_contract import check_approval
from qa_core.config.settings import Settings, get_settings

INTENT = ROOT/'.runtime/test/reports/protocol-faq-review-20261010/execution-intent.pending.json'
CANDIDATE = ROOT/'data/candidates/protocol-faq-approved-demo-r1'
REPORT = ROOT/'.runtime/test/reports/protocol-faq-staged-20261010'
VERSION = 'local-protocol-faq-r1'
DOC, FAQ = 'local_protocol_faq_r1_doc', 'local_protocol_faq_r1_faq'


def hashed(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def prepare(resume_preparation=False):
    package = validate(REVIEW)
    verify_seal(INTENT.parent)
    intent = read(INTENT)
    if intent['reviewed_package_sha256'] != sha(REVIEW/'review-package.json') or intent['exact_entry_ids'] != [e['id'] for e in package['entries']]:
        raise ValueError('confirmed scope changed')
    if REPORT.exists() or (CANDIDATE.exists() and not resume_preparation):
        raise ValueError('existing durable candidate/report; inspect, never blindly repeat execution')
    if CANDIDATE.exists():
        existing={p.relative_to(CANDIDATE).as_posix() for p in CANDIDATE.rglob('*') if p.is_file()}
        if existing != {'raw/faq_protocol_01.csv'}:
            raise ValueError('resume preparation only exact first-file incomplete projection')
        with (CANDIDATE/'raw/faq_protocol_01.csv').open(encoding='utf-8',newline='') as f:
            if list(csv.DictReader(f)) != [dict(faq_id=package['entries'][0]['id'],question=package['entries'][0]['question'],answer=package['entries'][0]['answer'])]:
                raise ValueError('partial body differs from confirmed package')
    receipt = dict(protocol='protocol-faq-chat-admission-v1',decision='approved',
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),user_message='确认，下一步',
        context='直接确认上一条明确的3份正文、来源身份/default租户ops/admin内部范围及新隔离STAGED执行问题；不伪造选项回复。',
        package_sha256=sha(REVIEW/'review-package.json'),intent_sha256=sha(INTENT),
        exact_entry_ids=intent['exact_entry_ids'],scope='three_authored_summaries_isolated_local_demo_only',
        proposer='codex_authored_drafts',reviewer='human_user_chat_confirmation',
        reviewer_authentication='chat context only; no manufactured database user',
        summary_use='允许中文自编摘要在本地隔离演示中使用；不声明拥有ETSI/IANA规范再分发许可',
        excluded=intent['excluded'],semantic_case_labels_approved=False)
    CANDIDATE.mkdir(parents=True,exist_ok=resume_preparation)
    raw=CANDIDATE/'raw';raw.mkdir(exist_ok=resume_preparation)
    metadata, mapping, approved = {}, [], []
    for index, entry in enumerate(package['entries'],1):
        e=copy.deepcopy(entry)
        e['approval'].update(status='approved',proposer=receipt['proposer'],reviewer=receipt['reviewer'],
                            evidence='Explicit user confirmation bound to receipt '+hashed(receipt))
        check_approval(e,required=True)
        name=f'faq_protocol_{index:02}.csv'
        with (raw/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['faq_id','question','answer'])
            writer.writeheader();writer.writerow(dict(faq_id=e['id'],question=e['question'],answer=e['answer']))
        identity=e['source_identity_proposal'];access=e['access_proposal']
        if access != dict(tenant_id='default',dept_id='public',visibility='internal',allowed_roles=['ops','admin'],security_level='internal',review_status='pending'):
            raise ValueError('unexpected approved access scope')
        metadata[name]=dict(tenant_id='default',dept_id='public',visibility='role_restricted',
            allowed_roles=['ops','admin'],role_scope=['ops','admin'],security_level='internal',
            source_system=identity['source_system'],source_id=identity['source_id'],
            source_version=identity['source_revision'],asset_id='kb-'+hashed([VERSION,identity['source_id']])[:32],
            status='active',effective_from=None,effective_to=None,doc_type='faq',network_tech='5G',
            dataset_id='protocol-faq-demo',origin_type='public_reference_derived',synthetic=False,
            generator_version='',generation_seed='',review_status='human_chat_approved',
            license_status='project_paraphrase_local_demo',
            evidence_refs=[package['sources'][k]['url'] for k in e['source_refs']])
        mapping.append(dict(filename=name,sha256=sha(raw/name),tenant_id='default',
            source_system=identity['source_system'],source_id=identity['source_id'],approval=e['approval']))
        approved.append(e)
    write(raw/'meta.json',metadata)
    write(CANDIDATE/'source-map.json',dict(protocol='reviewed-source-map-v1',entries=mapping))
    write(CANDIDATE/'approval-receipt.json',receipt)
    write(CANDIDATE/'approved-bodies.json',dict(entries=approved,source_package_sha256=receipt['package_sha256']))
    write(CANDIDATE/'projection.json',dict(version=VERSION,status='STAGED',collections=[DOC,FAQ],
        visibility_translation='internal display intent -> role_restricted with exact ops/admin; no broader access',
        source_lifecycle_active_is_not_kb_activation=True,review_cases_remain_pending=True,
        local_use_only=True,third_party_reproduction_permission_not_claimed=True))
    from qa_core.ingest.source_identity import validate_mapping
    validate_mapping(raw,read(CANDIDATE/'source-map.json'),metadata)
    seal(CANDIDATE);verify_seal(CANDIDATE)
    return metadata, receipt


def configure():
    base=runtime.build_profile()
    values=base.model_dump()
    values.update(doc_collection=DOC,faq_collection=FAQ,data_dir=str(CANDIDATE),
        reports_dir=str(REPORT/'quality'),generation_enabled=False,agent_enabled=False,
        semantic_cache_enabled=False,auth_required=True,allow_header_roles=False,
        llm_api_key='',llm_base_url='http://127.0.0.1:1/disabled',
        redis_url='redis://127.0.0.1:6379/14',embedding_device='cpu',rerank_device='cpu')
    settings=Settings(_env_file=None,**values)
    url=sa.engine.make_url(settings.mysql_dsn)
    if url.host not in {'localhost','127.0.0.1'} or url.database!='rag_kb' or settings.milvus_uri not in {'http://localhost:19530','http://127.0.0.1:19530'}:
        raise ValueError('exact original local storage required')
    if settings.doc_collection!=DOC or settings.faq_collection!=FAQ or settings.generation_enabled or settings.agent_enabled or settings.llm_api_key or not settings.auth_required:
        raise ValueError('exact isolated candidate configuration required')
    os.environ.update({name.upper(): 'true' if value is True else 'false' if value is False else str(value)
                       for name,value in settings.model_dump().items()})
    get_settings.cache_clear()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
    assert get_settings().doc_collection==DOC and not get_settings().generation_enabled
    return base,settings


def sql_protected(engine):
    with engine.connect() as conn:
        versions=[dict(r) for r in conn.execute(sa.text('SELECT * FROM kb_versions WHERE version<>:v ORDER BY version'),{'v':VERSION}).mappings()]
        assets=[dict(r) for r in conn.execute(sa.text('SELECT * FROM knowledge_assets WHERE kb_version<>:v OR kb_version IS NULL ORDER BY asset_id'),{'v':VERSION}).mappings()]
    return dict(versions=json.loads(json.dumps(versions,default=str)),assets_sha256=hashed(assets),assets_count=len(assets))


def vector_protected(client,names):
    from scripts.migrate_legacy_collections import read_rows,fingerprint
    return {name:dict(rows=len(rows),sha256=fingerprint(rows)) for name in names if client.has_collection(name)
            for rows in [read_rows(client,name)]}


def execute(resume_preparation=False,resume_prepared=False):
    if resume_prepared:
        validate(REVIEW);verify_seal(CANDIDATE)
        receipt=read(CANDIDATE/'approval-receipt.json')
        if receipt['package_sha256']!=sha(REVIEW/'review-package.json') or receipt['intent_sha256']!=sha(INTENT) or receipt['decision']!='approved':
            raise ValueError('prepared receipt differs from confirmed package')
        if not REPORT.is_dir() or any(REPORT.iterdir()):
            raise ValueError('resume before storage only exact empty report; durable writes cannot replay')
        metadata=read(CANDIDATE/'raw/meta.json')
    else:
        metadata,receipt=prepare(resume_preparation)
        REPORT.mkdir(parents=True)
    base,settings=configure()
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    client=MilvusClient(uri=settings.milvus_uri,timeout=60)
    protected_names=[base.doc_collection,base.faq_collection,'kb_doc','kb_faq']
    try:
        prior=sql_protected(engine);vectors=vector_protected(client,protected_names)
        write(REPORT/'protected-before.json',dict(sql=prior,vectors=vectors))
        with engine.connect() as conn:
            if conn.execute(sa.text('SELECT 1 FROM kb_versions WHERE version=:v'),{'v':VERSION}).first():
                raise ValueError('STAGED version collision')
            for meta in metadata.values():
                if conn.execute(sa.text('SELECT 1 FROM knowledge_assets WHERE asset_id=:a OR (tenant_id=:t AND source_system=:s AND source_id=:i)'),
                    dict(a=meta['asset_id'],t='default',s=meta['source_system'],i=meta['source_id'])).first():
                    raise ValueError('source identity collision')
        if any(client.has_collection(name) for name in [DOC,FAQ]):
            raise ValueError('new collection collision')
        from qa_core.ingest.source_identity import validate_mapping
        validate_mapping(CANDIDATE/'raw',read(CANDIDATE/'source-map.json'),metadata)
        from scripts.init_collections import build
        build(client,DOC,False);build(client,FAQ,True)
        from qa_core.domain.models import KnowledgeAsset
        from qa_core.storage.domain_dao import _db_values
        with engine.begin() as conn:
            for name,meta in metadata.items():
                model=KnowledgeAsset(asset_id=meta['asset_id'],tenant_id='default',filename=name,doc_type='faq',
                    source_system=meta['source_system'],source_id=meta['source_id'],source_version=meta['source_version'],
                    kb_version=VERSION,checksum=sha(CANDIDATE/'raw'/name),dept_id='public',security_level='internal',
                    visibility='role_restricted',role_scope=['ops','admin'],status='active',reviewed_by=None,
                    approved_at=datetime.fromisoformat(receipt['recorded_at_utc']))
                values=_db_values(model)
                values['approved_at']=values['approved_at'].astimezone(timezone.utc).replace(tzinfo=None)
                columns=list(values)
                conn.execute(sa.text('INSERT INTO knowledge_assets ('+','.join('`'+c+'`' for c in columns)+') VALUES ('+','.join(':'+c for c in columns)+')'),values)
            conn.execute(sa.text('INSERT INTO audit_log(action,actor,payload_json) VALUES(:a,:u,:p)'),
                dict(a='knowledge_admission',u='codex-approved-local-cli',p=json.dumps(dict(version=VERSION,files=3,
                    receipt_sha256=sha(CANDIDATE/'approval-receipt.json'),scope=receipt['scope'],publication_authorized=False))))
        from scripts.rebuild_kb import run_rebuild
        stats=run_rebuild(VERSION,raw_dir=CANDIDATE/'raw',strict_metadata=True,
            reviewed_source_map=CANDIDATE/'source-map.json',record_locations=True,tokenizer_chunking=True,
            progress=lambda stage,done,total,stats:print(stage,done,'/',total,flush=True))
        write(REPORT/'ingestion.json',stats)
        if stats['failed'] or stats['done']!=3:
            raise ValueError('incomplete ingestion; inspect durable state, no automatic replay')
        from qa_core.storage.kb_dao import KbDao
        kb=KbDao(settings.mysql_dsn)
        assert kb.get_version(VERSION)['status']=='STAGED'
        from qa_core.storage.version_manifest import verify_read_binding
        assert verify_read_binding(settings,VERSION)
        rows=client.query(collection_name=FAQ,filter=f'kb_version == "{VERSION}"',output_fields=['id','question','answer','asset_id','source_id','source_version','visibility','allowed_roles','dense'],limit=10,consistency_level='Strong')
        assert len(rows)==3 and all(len(r['dense'])==1024 and r['visibility']=='role_restricted' and sorted(r['allowed_roles'])==['admin','ops'] for r in rows)
        write(REPORT/'rows.json',[{k:v for k,v in row.items() if k!='dense'} for row in rows])
        verify_qa(settings,engine)
        after=sql_protected(engine);after_vectors=vector_protected(client,protected_names)
        write(REPORT/'protected-after.json',dict(sql=after,vectors=after_vectors))
        assert prior==after and vectors==after_vectors
        assert sha(ROOT/'.env.runtime')=='045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
        assert read(runtime.ROOT/'generation-validation-budget.json')['used']==3
        write(REPORT/'execution-summary.json',dict(passed=True,version=VERSION,status='STAGED',
            admitted_faqs=3,real_embedding_rows=3,embedding_dimensions=1024,
            active_and_protected_vectors_unchanged=True,source_locations=True,new_paid_requests=0,
            publication=False,independent_quality_passed=False,other_pending_unchanged=True))
        print('3 FAQ documents embedded and verified in isolated STAGED; ACTIVE protected',flush=True)
    finally:
        client.close();engine.dispose()


def verify_qa(settings,engine):
    from qa_core.retrieval.searcher import Searcher
    from qa_core.application.qa_usecase import QaUseCase
    from qa_core.application.evidence_service import create_evidence_service
    from qa_core.security.policy import AccessScope
    guard=Mock();guard.chat.side_effect=AssertionError('paid generation forbidden');guard.chat_stream.side_effect=AssertionError('paid generation forbidden')
    uc=QaUseCase(searcher=Searcher(settings=settings),llm=guard)
    evidence=create_evidence_service(settings)
    scope=AccessScope.build(roles=['admin']);bodies=read(CANDIDATE/'approved-bodies.json')['entries']
    samples=[]
    for entry in bodies:
        result=uc.ask(entry['question'],roles=['admin'],scope=scope,kb_version=VERSION,persist=False,use_llm=False)
        write(REPORT/(entry['id'].lower()+'-qa.json'),result)
        assert result['route']=='faq_exact' and result['status']=='ANSWERED' and entry['answer'] in result['answer']
        source=evidence.source(result['sources'][0]['evidence_id'],scope)
        assert source and source['text']==entry['answer'] and source['location']['kind']!='legacy'
        samples.append(dict(query=entry['question'],answer=result['answer'],source=source))
    write(REPORT/'exact-qa.json',dict(samples=samples,passed=True,persist=False,use_llm=False))
    observations=[]
    for case in read(REVIEW/'review-cases.json')['cases']:
        result=uc.ask(case['query'],roles=['admin'],scope=scope,kb_version=VERSION,persist=False,use_llm=False)
        observations.append(dict(id=case['id'],query=case['query'],required_behavior=case['required_response_behavior'],
            status=result.get('status'),route=result.get('route'),answer=result.get('answer'),sources=result.get('sources',[]),
            human_semantic_label_status='pending',independent_evaluation=False))
        print('development observation',case['id'],result.get('status'),flush=True)
    write(REPORT/'development-observations.json',dict(rows=observations,metrics_generated=False))
    assert not guard.chat.called and not guard.chat_stream.called
    verify_http(settings,engine,uc,samples)


def verify_http(settings,engine,uc=None,samples=None):
    if samples is None:
        samples=read(REPORT/'exact-qa.json')['samples']
    if uc is None:
        from qa_core.application.qa_usecase import QaUseCase
        from qa_core.retrieval.searcher import Searcher
        uc=QaUseCase(searcher=Searcher(settings=settings),llm=Mock())
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qa_core.api import route_evidence,route_qa
    from qa_core.api.auth import create_api_key,hash_key
    route_qa._usecase=uc
    app=FastAPI();app.include_router(route_evidence.router);app.include_router(route_qa.router)
    keys={}
    try:
        for label,role,tenant,levels in [('admin','admin','default',None),('ops','ops','default',None),('foreign','admin','other',None),('restricted','admin','default',['public'])]:
            key=secrets.token_urlsafe(32)
            create_api_key(key,'protocol-stage-probe-'+label,['admin'] if role=='admin' else ['ops'],tenant_id=tenant,security_levels=levels)
            keys[label]=key
        with TestClient(app) as http:
            checks=[]
            for sample in samples:
                url=sample['source']['source_url']
                statuses={label:http.get(url,headers={'X-API-Key':key}).status_code for label,key in keys.items()}
                statuses['unauthenticated']=http.get(url).status_code
                assert statuses==dict(admin=200,ops=404,foreign=404,restricted=404,unauthenticated=401),statuses
                fetched=http.get(url,headers={'X-API-Key':keys['admin']}).json()
                assert fetched['text']==sample['source']['text']
                checks.append(dict(source=url,statuses=statuses,complete_original=True))
            recommendations=http.get('/qa/recommendations',params={'kb_version':VERSION},headers={'X-API-Key':keys['admin']})
            assert recommendations.status_code==200 and len(recommendations.json()['items'])==3
            write(REPORT/'http-auth.json',dict(passed=True,transport='in-process real FastAPI ASGI; real database keys/handlers, not socket/browser',checks=checks,recommendations=recommendations.json(),staged_ops_denial_expected=True))
    finally:
        with engine.begin() as conn:
            for key in keys.values():
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h'),dict(h=hash_key(key)))
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'protocol-stage-probe-%'")).scalar()==0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resume-preparation',action='store_true',help='only exact incomplete first-CSV preparation; no durable DB replay')
    parser.add_argument('--resume-prepared',action='store_true',help='only sealed candidate with empty report; preflight must prove absent version/collections')
    args=parser.parse_args()
    if not args.execute:parser.error('explicit --execute required for confirmed three-item scope')
    execute(args.resume_preparation,args.resume_prepared)
