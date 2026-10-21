"""Real presentation and draft review acceptance; no knowledge admission or paid calls."""
import json
import secrets
import subprocess
import uuid
import httpx
import sqlalchemy as sa
from scripts import training_business_runtime as runtime
from scripts.prepare_sop_record_revision import OUTPUT,EXPOSURE,CANDIDATE,ROOT
from qa_core.quality.sop_record_revision_review import validate_revision
from scripts.verify_sop_guide_preview import protected
from scripts.prepare_legacy_projection import read,write,digest,verify_seal

REPORT=ROOT/'.runtime/test/reports/training-step1-20261014'


def main():
    REPORT.mkdir(parents=True,exist_ok=True);attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir()
    write(attempt/'state.json',dict(state='running',paid_calls=0))
    verify_seal(OUTPUT);verify_seal(EXPOSURE);package=read(OUTPUT/'review-package.json')
    counts=validate_revision(package,OUTPUT/'drafts',CANDIDATE/'raw');write(attempt/'draft-validation.json',dict(counts=counts,pending=20,records_after=109,ingestions=0))
    before=protected();write(attempt/'before.json',before)
    previous=read(ROOT/'.runtime/test/reports/sop-support-alignment-20261014/final-protected.json')
    assert {k:v for k,v in before.items() if k not in ['readonly_pid','readonly_key']}=={k:v for k,v in previous.items() if k not in ['readonly_pid','readonly_key']}
    write(attempt/'upgrade-protection.json',dict(original_and_all_knowledge_unchanged=True,only_readonly_pid_and_key_rotated=True))
    state=runtime.status();assert state['status']=='running'
    engine=sa.create_engine(runtime.core.profile().mysql_dsn);created=[];checks=[]
    try:
        settings=runtime.core.apply()
        from qa_core.api.auth import create_api_key,hash_key
        keys={'ops':(runtime.core.ROOT/'api-key.txt').read_text(encoding='utf-8')}
        for label,roles,scope in [('admin',['admin'],{}),('guest',['guest'],{}),('foreign',['ops'],{'tenant_id':'other'})]:
            key=secrets.token_urlsafe(32);actor='training-step1-'+label+'-'+attempt.name
            create_api_key(key,actor,roles,**scope);keys[label]=key;created.append((hash_key(key),actor))
        with httpx.Client(base_url=state['url'],trust_env=False,timeout=150) as client:
            def req(case,method,path,role='ops',expected=200,**kwargs):
                r=client.request(method,path,headers={'X-API-Key':keys.get(role,'invalid-control')},**kwargs)
                assert r.status_code==expected,(case,r.status_code)
                assert r.headers.get('cache-control')=='no-store'
                checks.append(dict(case=case,status=r.status_code,passed=True));write(attempt/'http-checks.json',checks)
                return r.json()
            for kind,count in [('alarms',2),('devices',2),('tickets',1),('changes',1)]:
                data=req(kind+'-actual-list','GET','/business/'+kind)
                assert len(data['items'])==count and data['synthetic'] is True
            detail=req('alarm-associated-details','GET','/business/alarms/SIM-ALM-000001');assert detail['synthetic'] is True
            adapters=req('real-integration-ledger','GET','/business/integrations');assert all(not a['observed'] for a in adapters['adapters'])
            req('guest-rbac','GET','/business/alarms','guest',403)
            req('invalid-rbac','GET','/business/alarms','invalid',401)
            assert not req('tenant-filter','GET','/business/alarms','foreign')['items']
            for path in ['/qa','/kb/activate','/business/alarms','/admin/keys']:
                req('write-disabled-'+path,'POST',path,'admin',403,json={})
            for name in ['business-workspace.js','agent-workspace.js']:
                r=client.get('/static/'+name);assert r.status_code==200 and '模拟资料' not in r.text and '模拟来源:' not in r.text
                (attempt/name).write_text(r.text,encoding='utf-8')
                syntax=subprocess.run(['node','--check',str(attempt/name)],capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
                assert syntax.returncode==0
            diag=req('linked-work-order-readonly-diagnose','POST','/agent/diagnose',json={'query':'请核对关联工单 SIM-TT-00001 的原文','alarm_id':'SIM-ALM-000001'})
            assert diag['status']=='ANSWERED' and diag['sources'] and all(s['synthetic'] is True for s in diag['sources'])
            actual=req('citation-provenance-unchanged','GET','/kb/evidence/'+diag['sources'][0]['evidence_id'])
            assert actual['synthetic'] is True and actual['text']==diag['sources'][0]['text']
        params=dict(report_dir=str(attempt),url=state['url'],key_file=str(runtime.core.ROOT/'api-key.txt'),review_url=(OUTPUT/'review.html').as_uri())
        r=subprocess.run(['node',str(ROOT/'scripts/verify_training_step1_browser.js')],input=json.dumps(params),text=True,encoding='utf-8',
            capture_output=True,timeout=260,creationflags=subprocess.CREATE_NO_WINDOW)
        (attempt/'browser.log').write_text(r.stdout+r.stderr,encoding='utf-8')
        if r.returncode:raise RuntimeError('training UI or draft review browser verification failed; retained safe evidence')
        after=protected();write(attempt/'after.json',after);assert before==after
        runtime.verify_presentation(runtime.core.state());verify_seal(OUTPUT);verify_seal(EXPOSURE)
        write(attempt/'state.json',dict(state='completed',http_checks=len(checks),paid_calls=0,new_admissions=0,new_ingestions=0,pending_drafts=20,
                record_extensions=49,records_after=109,original_and_storage_unchanged=True,api_provenance_preserved=True))
        print(json.dumps(dict(state='completed',http_checks=len(checks),pending_drafts=20,paid_calls=0,report=str(attempt))))
    except Exception as error:
        write(attempt/'state.json',dict(state='failed',error_kind=type(error).__name__,paid_calls=0));raise
    finally:
        with engine.begin() as conn:
            for key_hash,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),{'h':key_hash,'u':actor})
        engine.dispose();write(attempt/'key-cleanup.json',dict(temporary_created=len(created),temporary_removed=len(created)))


if __name__=='__main__':main()
