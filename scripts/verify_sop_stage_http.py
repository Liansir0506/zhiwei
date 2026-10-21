"""Real FastAPI evidence handlers against STAGED SQL/vectors. No QA or provider."""
import json
import secrets
import time
import uuid

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.execute_sop_evidence_stage import REPORT,VERSION
from scripts.prepare_sop_evidence_drafts import OUTPUT as DRAFTS
from scripts.prepare_legacy_projection import read,write
from scripts.sop_evidence_stage_profile import profile
from scripts.build_original_rollback_baseline import apply_settings
from qa_core.storage.kb_dao import KbDao


def main():
    assert read(REPORT/'state.json')['state']=='completed'
    folder=REPORT/('http-'+uuid.uuid4().hex[:8]);folder.mkdir()
    write(folder/'state.json',dict(state='running',paid_calls=0))
    selected=profile();apply_settings(selected)
    import os;os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    import torch;torch.set_num_threads(4)
    from qa_core.api.auth import create_api_key,hash_key
    from qa_core.api.route_evidence import router,get_evidence_service
    get_evidence_service.cache_clear()
    engine=sa.create_engine(selected.mysql_dsn);keys={};created=[];checks=[];retrieval=[]
    try:
        token=uuid.uuid4().hex[:12]
        for label,roles,tenant in [('admin',['admin'],'default'),('ops',['ops'],'default'),('guest',['guest'],'default'),('foreign',['admin'],'other')]:
            key=secrets.token_urlsafe(32);actor='sop-stage-http-'+label+'-'+token
            create_api_key(key,actor,roles,tenant_id=tenant);keys[label]=key;created.append((hash_key(key),actor))
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            def req(label,method,path,role='admin',status=200,**kwargs):
                headers={'X-API-Key':keys.get(role,'invalid-stage-control')}
                extra=kwargs.pop('headers',{});headers.update(extra)
                response=client.request(method,path,headers=headers,**kwargs)
                assert response.status_code==status,(label,response.status_code)
                if status==200:assert response.headers.get('cache-control')=='no-store'
                checks.append(dict(id=label,status=response.status_code,passed=True));write(folder/'checks.json',checks)
                return response.json()
            catalog=req('admin-catalog','GET','/kb/catalog',params=dict(kb_version=VERSION,kind='document',doc_type='guide',page_size=30))
            assert catalog['total']==20 and len(catalog['items'])==20
            write(folder/'catalog.json',catalog)
            for role in ('ops','guest'):
                req(role+'-staged-denied','GET','/kb/catalog',role,404,params=dict(kb_version=VERSION))
            req('invalid-key','GET','/kb/catalog','invalid',401,params=dict(kb_version=VERSION))
            req('header-role-override-denied','GET','/kb/catalog','ops',404,params=dict(kb_version=VERSION),headers={'X-User-Roles':'admin'})
            foreign=req('foreign-staged-hidden','GET','/kb/catalog','foreign',params=dict(kb_version=VERSION))
            assert foreign['total']==0
            for item in catalog['items']:
                actual=req(item['source']+'-source','GET','/kb/evidence/'+item['evidence_id'])
                assert actual['source']==item['source'] and actual['kb_version']==VERSION and actual['location']['kind']=='source_spans'
            deadline=time.monotonic()+420
            for case in read(DRAFTS/'development-preview-cases.json')['cases']:
                if time.monotonic()>deadline:raise TimeoutError('bounded retrieval deadline')
                expected='guide_sop_evidence_'+case['id'].split('-')[-1]+'.md'
                found=req(case['id']+'-search','POST','/kb/search-evidence',json=dict(query=case['question'],kb_version=VERSION,top_n=5))
                topic_match=found['status']=='EVIDENCE_FOUND' and any(e['source']==expected for e in found['evidence'])
                retrieval.append(dict(id=case['id'],query=case['question'],expected_source=expected,
                    topic_match=topic_match,result=found,development_exposed=True,human_gold=False))
                write(folder/'retrieval.json',retrieval)
                print(case['id'],'MATCH' if topic_match else 'MISS',flush=True)
                if topic_match:
                    evidence=next(e for e in found['evidence'] if e['source']==expected)
                    reread=req(case['id']+'-citation','GET',evidence['source_url'])
                    assert reread['text']==evidence['text'] and reread['evidence_id']==evidence['evidence_id']
            noflow=req('no-complete-workflow','POST','/kb/search-evidence',json=dict(query='AMF完整处置流程',kb_version=VERSION,top_n=5))
            assert noflow['status']=='NO_EVIDENCE' and not noflow['evidence']
        assert KbDao(selected.mysql_dsn).get_version(VERSION)['status']=='STAGED'
        write(folder/'state.json',dict(state='completed',http_checks=len(checks),retrieval_total=len(retrieval),
            retrieval_topic_matches=sum(r['topic_match'] for r in retrieval),paid_calls=0,qa_requests=0,
            real_fastapi_handlers=True,network_server_started=False,independent_frozen=0,human_gold=False,
            temporary_keys_created=len(created)))
        print(json.dumps(dict(state='completed',http_checks=len(checks),retrieval_total=len(retrieval),topic_matches=sum(r['topic_match'] for r in retrieval),paid_calls=0)))
    except Exception as error:
        write(folder/'state.json',dict(state='failed',error_kind=type(error).__name__,paid_calls=0))
        raise RuntimeError('STAGED evidence HTTP verification failed; retained safe report') from None
    finally:
        with engine.begin() as conn:
            for key_hash,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':key_hash,'u':actor})
            remaining=conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar()
        write(folder/'key-cleanup.json',dict(created=len(created),removed=len(created),remaining=remaining,scope='new isolated database only'))
        get_evidence_service.cache_clear();engine.dispose()


if __name__=='__main__':main()
