"""Real source/API/browser verification, plus unchanged data and no paid calls."""
import json
import secrets
import subprocess
import uuid
from pathlib import Path
import httpx
import sqlalchemy as sa
from scripts import sop_record_preview_runtime as runtime
from scripts.prepare_sop_record_alignment_v2 import ROOT,OUTPUT,load_verified
from scripts.verify_sop_guide_preview import protected as base_protected
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace
from scripts import sop_guide_support_runtime as previous_preview
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.build_original_rollback_baseline import apply_settings
from scripts.sop_record_stage_profile import profile
from qa_core.indexing.parsers.locations import canonical

REPORT=ROOT/'.runtime/test/reports/sop-record-preview-20261014'


def protected():
    base=base_protected()
    base['r2_snapshot']=Workspace(profile()).snapshot('test-sop-records-r2')
    base['r2_sql']=sql_snapshot(profile())
    previous=previous_preview.status()
    assert previous['status']=='running'
    base['previous_guide_pid']=previous['pid']
    base['previous_guide_key_sha256']=digest(previous_preview.core.ROOT/'api-key.txt')
    return base


def main():
    REPORT.mkdir(parents=True,exist_ok=True);attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir()
    write(attempt/'state.json',dict(state='running',paid_calls=0))
    before=protected();write(attempt/'before.json',before)
    engine=sa.create_engine(profile().mysql_dsn);created=[];checks=[]
    try:
        state=runtime.start();assert state['status']=='running';write(attempt/'runtime.json',state)
        write(attempt/'code-bindings.json',runtime.bindings())
        apply_settings(profile())
        from qa_core.api.auth import create_api_key,hash_key,verify_api_key
        keys={'admin':(runtime.ROOT/'api-key.txt').read_text(encoding='utf-8')}
        for label,roles,scope in [('ops',['ops'],{}),('guest',['guest'],{}),('foreign',['admin'],{'tenant_id':'other'}),('scoped',['admin'],{'device_types':['AMF']})]:
            key=secrets.token_urlsafe(32);actor='support-alignment-validation-'+label+'-'+attempt.name
            create_api_key(key,actor,roles,**scope);keys[label]=key;created.append((hash_key(key),actor))
        with httpx.Client(base_url=state['url'],trust_env=False,timeout=60) as client:
            def req(case,method,path,role='admin',expected=200,**kwargs):
                extra=kwargs.pop('headers',{});response=client.request(method,path,headers={'X-API-Key':keys.get(role,'invalid-control'),**extra},**kwargs)
                assert response.status_code==expected,(case,response.status_code)
                assert response.headers.get('cache-control')=='no-store'
                checks.append(dict(case=case,status=response.status_code,passed=True));write(attempt/'http-checks.json',checks)
                return response.json()
            result=req('live-alignment-and-guide-integrity','GET','/preview/checks')
            matrix=result['support_alignment'];assert matrix==load_verified()
            assert result['read_preview_ready'] and result['ordinary_publication_gate']['passed'] is False
            assert matrix['summary']['record_contracts']==109 and matrix['summary']['record_extensions']==49
            write(attempt/'live-checks.json',result)
            catalog=req('unchanged-guide-catalog','GET','/kb/catalog',params=dict(kb_version='test-sop-records-r2',doc_type='guide',page_size=30));assert catalog['total']==20
            for role in ('ops','guest','foreign','scoped','invalid'):
                req(role+'-denied','GET','/preview/checks',role,401 if role=='invalid' else 403)
            req('role-header-cannot-elevate','GET','/preview/checks','ops',403,headers={'X-User-Roles':'admin'})
            for path in ('/qa','/qa/stream','/kb/activate','/kb/upload','/admin/keys'):
                req('write-generation-denied-'+path,'POST',path,expected=403,json={})
            for path in ('/alignment.json','/api-key.txt','/server.log','/openapi.json','/preview/support'):
                req('unregistered-or-private-file-'+path,'GET',path,expected=403)
            proof=matrix['entries'][0]['records'][0]['proof']
            actual=req('real-normalized-guide-citation','GET',proof['source_url'])
            assert canonical(proof['quote']) in canonical(actual['text']) and actual['source']==proof['source']
        # Every unique guide chunk and every line proof is reread through the real source service.
        ctx=verify_api_key(keys['admin'],dsn=profile().mysql_dsn);assert ctx and ctx.roles==['admin']
        from qa_core.api.route_evidence import get_evidence_service
        get_evidence_service.cache_clear();service=get_evidence_service();cache={};proof_count=0
        for entry in matrix['entries']:
            proofs=[r['proof'] for r in entry['records']]+entry['primary_conditions']+entry['counter_conditions']+[p for r in entry['steps'] for p in r['guide_proofs']]
            for proof in proofs:
                token=proof['evidence_id']
                if token not in cache:cache[token]=service.source(token,ctx.scope)
                actual=cache[token];assert actual and actual['source']==entry['guide_source'] and actual['evidence_id']==token
                assert canonical(proof['quote']) in canonical(actual['text'])
                assert actual['location']['file_sha256']==proof['file_sha256']
                locations=[loc for group in actual['location']['matches'] for loc in group]
                assert any(loc.get('line_start',0)<=proof['line']<=loc.get('line_end',0) for loc in locations)
                proof_count+=1
        write(attempt/'real-reference-verification.json',dict(proofs_reread=proof_count,unique_chunks=len(cache),source_families=20,
            normalized_proof_contains=True,raw_file_sha_and_line_verified=True,actual_source_service=True,human_truth_verified=False))
        get_evidence_service.cache_clear()
        result=subprocess.run(['node',str(ROOT/'scripts/verify_sop_record_preview_browser.js')],
            input=json.dumps(dict(report_dir=str(attempt),url=state['url'],key_file=str(runtime.ROOT/'api-key.txt'))),
            text=True,encoding='utf-8',capture_output=True,timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
        (attempt/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('support alignment browser check failed; safe report retained')
        after=protected();write(attempt/'after.json',after);assert before==after
        assert runtime.inspect()['status']=='running'
        write(attempt/'state.json',dict(state='completed',http_checks=len(checks),proofs_reread=proof_count,unique_reference_chunks=len(cache),
            paid_calls=0,qa_requests=0,protected_data_unchanged=True,new_owned_preview_only=True,independent_denominator=0))
        print(json.dumps(dict(state='completed',http_checks=len(checks),proofs=proof_count,unique_chunks=len(cache),report=str(attempt))))
    except Exception as error:
        write(attempt/'state.json',dict(state='failed',error_kind=type(error).__name__,paid_calls=0));raise
    finally:
        with engine.begin() as conn:
            for key_hash,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':key_hash,'u':actor})
            remaining=conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar_one()
        assert remaining==1
        engine.dispose();write(attempt/'key-cleanup.json',dict(temporary_created=len(created),temporary_removed=len(created),remaining=remaining))


if __name__=='__main__':main()
