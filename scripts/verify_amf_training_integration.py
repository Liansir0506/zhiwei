"""Upgrade the owned business presentation and verify isolation against real HTTP."""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import traceback
import httpx
import sqlalchemy as sa
from scripts import training_exercise_runtime as runtime
from scripts.prepare_legacy_projection import write,digest,verify_seal
from scripts.verify_sop_record_preview import protected
from scripts.prepare_amf_sop_exercise import OUTPUT,validate

PROJECT=runtime.base.core.PROJECT
REPORT=PROJECT/'.runtime/test/reports/amf-workspace-integration-20261015'


def stage(name,**extra):
    row=dict(stage=name,at_utc=datetime.now(timezone.utc).isoformat(),**extra)
    with (REPORT/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=True)+'\n')
    print(json.dumps(row),flush=True)


def run_hidden(args,**kwargs):
    return subprocess.run(args,cwd=PROJECT,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW,**kwargs)


def start():
    result=run_hidden(['powershell.exe','-NoProfile','-File',str(PROJECT/'scripts/start_training_workspace.ps1')],timeout=320)
    write(REPORT/'startup-result.json',dict(returncode=result.returncode))
    if result.returncode:raise RuntimeError('Owned presentation startup failed; inspect sanitized startup state')
    state=runtime.status();assert state['status']=='running' and state['training_module']
    return state


def main(resume=False):
    REPORT.mkdir(parents=True,exist_ok=True)
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('Report is sealed')
    if (REPORT/'before.json').exists() and not resume:raise ValueError('Preserve prior run; use explicit resume')
    validate()
    if resume:
        before=json.loads((REPORT/'before.json').read_text('utf-8'));stage('resuming_after_browser_harness_fix')
    else:
        before=protected();write(REPORT/'before.json',before)
        write(REPORT/'exercise-seal-before.json',dict(sha256=digest(OUTPUT/'artifact-hashes.json')))
        stage('protected_baseline_captured')
    state=start();write(REPORT/'runtime.json',state);stage('upgraded_business_running')
    selected=runtime.base.core.apply()
    from qa_core.api.auth import create_api_key,hash_key
    keys={'ops':(runtime.base.core.ROOT/'api-key.txt').read_text('utf-8')};created=[];checks=[]
    engine=sa.create_engine(selected.mysql_dsn)
    try:
        for label,roles,scope in [('admin',['admin'],{}),('auditor',['auditor'],{}),('guest',['guest'],{}),
                ('foreign',['ops'],dict(tenant_id='training-other')),('scoped',['ops'],dict(device_types=['AMF'])),
                ('public',['admin'],dict(security_levels=['public']))]:
            key=secrets.token_urlsafe(32);actor='amf-training-test-'+label+'-'+secrets.token_hex(6)
            create_api_key(key,actor,roles,**scope);created.append((hash_key(key),actor));keys[label]=key
        with httpx.Client(base_url=state['url'],trust_env=False,timeout=60) as client:
            def request(case,method,path,role='ops',expected=200,**kwargs):
                extra=kwargs.pop('headers',{})
                response=client.request(method,path,headers={'X-API-Key':keys.get(role,''),**extra},**kwargs)
                assert response.status_code==expected,(case,response.status_code)
                assert response.headers.get('cache-control')=='no-store'
                checks.append(dict(case=case,status=response.status_code,passed=True));write(REPORT/'http-checks.json',checks)
                return response
            shell=request('training-navigation-shell','GET','/').text;assert 'id="trainingTab"' in shell
            for role in ('ops','admin'):
                assert request(role+'-capability','GET','/training/capabilities',role).json()['can_train_amf']
                response=request(role+'-exercise','GET','/training/amf',role)
                assert 'sandbox allow-scripts allow-downloads' in response.headers['content-security-policy']
                assert "connect-src 'none'" in response.headers['content-security-policy']
                assert all(key not in response.text for key in keys.values())
                assert 'sop-draft.md' not in response.text and (OUTPUT/'engine.js').read_text('utf-8') in response.text
            for role in ('auditor','guest','foreign','scoped','public'):
                assert not request(role+'-capability-denied','GET','/training/capabilities',role).json()['can_train_amf']
                request(role+'-exercise-denied','GET','/training/amf',role,403)
            request('no-key-denied','GET','/training/amf','invalid',401)
            request('header-role-cannot-elevate','GET','/training/amf','auditor',403,headers={'X-User-Roles':'admin'})
            request('no-key-role-header-denied','GET','/training/capabilities','invalid',401,headers={'X-User-Roles':'admin'})
            for path in ['/training/sop-draft.md','/training/review-package.json','/training/source-binding.js','/training/api-key.txt']:
                request('unregistered-file-'+path,'GET',path,expected=403)
            request('training-post-denied','POST','/training/amf',expected=403,json={})
            for path in ['/business/alarms','/admin/integrations/sync','/kb/activate','/kb/upload','/qa']:
                request('business-write-still-denied-'+path,'POST',path,expected=403,json={})
            for kind,count,field in [('alarms',2,'alarm_id'),('devices',2,'device_id'),('tickets',1,'ticket_id'),('changes',1,'change_id')]:
                rows=request('existing-'+kind,'GET','/business/'+kind).json()['items'];assert len(rows)==count
                detail=request('existing-'+kind+'-detail','GET','/business/'+kind+'/'+rows[0][field]).json()
                assert detail['entity'][field]==rows[0][field]
            catalog=request('existing-knowledge-catalog','GET','/kb/catalog',params=dict(page_size=1)).json()
            first=catalog['items'][0]
            source=request('existing-source-reread','GET','/kb/evidence/'+first['evidence_id']).json()
            assert source['text'] and source['source']==first['source']
            health=request('unchanged-business-generation-boundary','GET','/health').json()
            assert health['agent_enabled'] and not health['generation_enabled']
        stage('real_http_passed',checks=len(checks))
        browser=REPORT/'browser';browser.mkdir(exist_ok=True)
        payload=dict(report_dir=str(browser),url=state['url'],key_file=str(runtime.base.core.ROOT/'api-key.txt'))
        result=run_hidden(['node',str(PROJECT/'scripts/verify_amf_training_bridge_browser.js')],input=json.dumps(payload),text=True,encoding='utf-8',timeout=300)
        write(REPORT/'browser-result.json',dict(returncode=result.returncode))
        # Browser messages contain only named checks; credentials are never printed.
        if result.returncode:
            write(REPORT/'browser-failure.json',dict(message=result.stderr.strip()[:500]));raise RuntimeError('Browser validation failed')
        stage('real_browser_passed')
        process=runtime.status()['pid'];key_sha=digest(runtime.base.core.ROOT/'api-key.txt')
        start();assert runtime.status()['pid']==process and digest(runtime.base.core.ROOT/'api-key.txt')==key_sha
        after=protected();write(REPORT/'after.json',after)
        excluded={'readonly_pid','readonly_key'}
        assert {k:v for k,v in before.items() if k not in excluded}=={k:v for k,v in after.items() if k not in excluded}
        validate();assert digest(OUTPUT/'artifact-hashes.json')==json.loads((REPORT/'exercise-seal-before.json').read_text('utf-8'))['sha256']
        write(REPORT/'result.json',dict(status='passed',http_checks=len(checks),paid_calls=0,qa_requests=0,new_admissions=0,
             new_ingestions=0,new_publications=0,independent_denominator=0,protected_data_unchanged=True,
             only_owned_business_pid_and_key_rotated=True,idempotent_start_preserves_current_pid_and_key=True))
        stage('completed',paid_calls=0)
    finally:
        with engine.begin() as conn:
            for key_hash,actor in created:conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=key_hash,u=actor))
            remaining=conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar_one()
        engine.dispose();write(REPORT/'temporary-key-cleanup.json',dict(created=len(created),removed=len(created),remaining=remaining))
        assert remaining==1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    try:main(args.resume)
    except Exception as error:
        write(REPORT/'failure.json',dict(error_type=type(error).__name__,frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)]))
        stage('failed',error_type=type(error).__name__)
        raise SystemExit(1) from None
