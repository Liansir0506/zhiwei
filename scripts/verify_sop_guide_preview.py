"""Real server/Edge acceptance of readonly STAGED. No answer generation."""
import json
from pathlib import Path
import secrets
import subprocess
import uuid
import httpx
import sqlalchemy as sa
from scripts import sop_guide_preview_runtime as rt
from scripts import content_quality_release_runtime as original, readonly_workspace_runtime as readonly
from scripts.sop_evidence_stage_profile import profile
from scripts.prepare_legacy_projection import read,write,digest,verify_seal,seal
from scripts.prepare_sop_evidence_drafts import OUTPUT as DRAFTS
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace

REPORT=rt.PROJECT/'.runtime/test/reports/sop-guide-preview-20261014'


def protected():
    original.install_adapter(); selected=original.selected_settings()
    return dict(original=Workspace(selected).snapshot(original.VERSION),original_sql=sql_snapshot(selected),
        readonly=Workspace(readonly.profile()).snapshot('test-legacy-demo-r1'),readonly_sql=sql_snapshot(readonly.profile()),
        stage=Workspace(profile()).snapshot('test-sop-evidence-r1'),stage_sql=sql_snapshot(profile()),
        env=digest(rt.PROJECT/'.env.runtime'),budget=digest(original.owned.ROOT/'generation-validation-budget.json'),
        original_pid=original.owned.inspect()['pid'],readonly_pid=readonly.inspect()['pid'],
        original_key=digest(original.owned.ROOT/'api-key.txt'),readonly_key=digest(readonly.ROOT/'api-key.txt'),
        ordinary_gate=digest(Path(profile().reports_dir)/'quality_test-sop-evidence-r1.json'))


def main():
    REPORT.mkdir(parents=True,exist_ok=True)
    attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir()
    write(attempt/'state.json',dict(state='running',paid_calls=0))
    before=protected();write(attempt/'before.json',before)
    created=[];engine=sa.create_engine(profile().mysql_dsn);checks=[]
    try:
        status=rt.start();assert status['status']=='running';write(attempt/'runtime.json',status)
        write(attempt/'code-bindings.json',rt.bindings())
        from scripts.build_original_rollback_baseline import apply_settings
        apply_settings(profile())
        from qa_core.api.auth import create_api_key,hash_key
        keys={'admin':(rt.ROOT/'api-key.txt').read_text(encoding='utf-8')}
        for label,roles,scope in [('ops',['ops'],{}),('guest',['guest'],{}),('foreign',['admin'],{'tenant_id':'other'}),
                                  ('scoped',['admin'],{'device_types':['AMF']})]:
            key=secrets.token_urlsafe(32);actor='guide-preview-verification-'+label+'-'+attempt.name
            create_api_key(key,actor,roles,**scope);keys[label]=key;created.append((hash_key(key),actor))
        with httpx.Client(base_url=f'http://127.0.0.1:{rt.PORT}',trust_env=False,timeout=160) as client:
            def req(case,method,path,role='admin',expected=200,**kwargs):
                extra=kwargs.pop('headers',{});headers={'X-API-Key':keys.get(role,'invalid-control'),**extra}
                response=client.request(method,path,headers=headers,**kwargs)
                assert response.status_code==expected,(case,response.status_code)
                assert response.headers.get('cache-control')=='no-store'
                checks.append(dict(case=case,status=response.status_code,passed=True));write(attempt/'http-checks.json',checks)
                return response.json()
            info=req('guide-integrity','GET','/preview/checks');assert info['read_preview_ready'] and not info['publication_eligible']
            write(attempt/'guide-integrity.json',info)
            caps=req('readonly-capabilities','GET','/kb/capabilities');assert not caps['can_upload'] and not caps['can_publish'] and not caps['can_generate']
            catalog=req('actual-twenty-guide-catalog','GET','/kb/catalog',params=dict(kb_version='test-sop-evidence-r1',doc_type='guide',page_size=30))
            assert len(catalog['items'])==catalog['total']==20;write(attempt/'catalog.json',catalog)
            item=catalog['items'][0];source=req('actual-citation','GET','/kb/evidence/'+item['evidence_id']);assert source['source']==item['source']
            for role in ('ops','guest','foreign','scoped','invalid'):
                req(role+'-restricted','GET','/preview/checks',role,expected=401 if role=='invalid' else 403)
            req('header-role-cannot-elevate','GET','/kb/catalog','ops',403,params={'kb_version':'test-sop-evidence-r1'},headers={'X-User-Roles':'admin'})
            req('explicit-version-required','GET','/kb/catalog',expected=409)
            req('other-version-denied','POST','/kb/search-evidence',expected=409,json={'query':'AMF','kb_version':'other'})
            req('invalid-reference','GET','/kb/evidence/invalid',expected=404)
            for path in ('/qa','/qa/stream','/kb/activate','/kb/upload','/admin/keys','/business/alarms'):
                req('no-write-or-generation-'+path,'POST',path,expected=403,json={})
            for path in ('/state.json','/api-key.txt','/server.log','/openapi.json'):
                req('no-private-file-'+path,'GET',path,expected=403)
            page=client.get('/?name=../.runtime/sop-guide-preview/api-key.txt');assert '取证指南候选预览' in page.text and keys['admin'] not in page.text
            checks.append(dict(case='fixed-static-file-no-path-injection',status=page.status_code,passed=True));write(attempt/'http-checks.json',checks)
        case=read(DRAFTS/'development-preview-cases.json')['cases'][0]
        payload=dict(report_dir=str(attempt),key_file=str(rt.ROOT/'api-key.txt'),url=f'http://127.0.0.1:{rt.PORT}',
                     query=case['question'],expected_source='guide_sop_evidence_001.md')
        result=subprocess.run(['node',str(rt.PROJECT/'scripts/verify_sop_guide_preview_browser.js')],input=json.dumps(payload),
            text=True,encoding='utf-8',capture_output=True,timeout=260,creationflags=subprocess.CREATE_NO_WINDOW)
        (attempt/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('real browser verification failed; safe report retained')
        after=protected();write(attempt/'after.json',after);assert before==after
        from qa_core.quality.guide_preview_checks import verify_live
        verify_live(profile());assert rt.inspect()['status']=='running'
        write(attempt/'state.json',dict(state='completed',http_checks=len(checks),paid_calls=0,
              real_network_server=True,real_edge=True,protected_snapshots_unchanged=True,independent_denominator=0,
              ordinary_publication_gate_passed=False,source_question_exposed=True))
        print(json.dumps(dict(state='completed',http_checks=len(checks),paid_calls=0,report=str(attempt))))
    except Exception as error:
        write(attempt/'state.json',dict(state='failed',error_kind=type(error).__name__,paid_calls=0))
        raise
    finally:
        with engine.begin() as conn:
            for key_hash,actor in created:
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':key_hash,'u':actor})
            remaining=conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar()
        engine.dispose()
        write(attempt/'key-cleanup.json',dict(temporary_created=len(created),temporary_removed=len(created),remaining=remaining,
              persistent_key_scope='owned technical admin key in new isolated namespace only; original roles unchanged'))


if __name__=='__main__':main()
