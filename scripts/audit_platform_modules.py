"""Live read-only module audit with actual ops and temporary admin comparison."""
import json
import secrets
from pathlib import Path
import subprocess
import sqlalchemy as sa
import httpx
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import write,read

ROOT=runtime.PROJECT/'.runtime/test/reports/platform-module-audit-20261012'


def summarize(url,data):
    if not isinstance(data,dict):return {'type':type(data).__name__}
    if url.startswith('/kb/catalog'):
        return {k:data.get(k) for k in ('total','pages','page','kb_version','scope_note')}|{'items':len(data.get('items',[]))}
    if url=='/admin/eval':return dict(file=data.get('file'),results=len(data.get('results',[])),report=data.get('report'))
    if url=='/admin/permissions':return dict(keys=len(data.get('keys',[])),matrix_rows=len(data.get('matrix',[])))
    if url=='/admin/ops':return dict(totals=data.get('totals'),route_distribution=data.get('route_distribution'),hourly_rows=len(data.get('hourly_messages',[])))
    if url=='/kb/rebuild?limit=30':return dict(jobs=len(data.get('jobs',[])),statuses=[j['status'] for j in data.get('jobs',[])])
    if url=='/kb/versions':return dict(versions=[{'version':v['version'],'status':v['status']} for v in data.get('versions',[])])
    if url=='/qa/recommendations':return dict(kb_version=data.get('kb_version'),items=len(data.get('items',[])))
    if url.startswith('/health'):return {k:data.get(k) for k in ('status','components','generation_enabled','agent_enabled','environment')}
    if 'capabilities' in url:return data
    return {k:data[k] for k in ('detail','error') if k in data}


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    runtime.install_adapter();settings=runtime.selected_settings();runtime.owned.apply_profile(settings)
    state=runtime.owned.load_state();ops_key=runtime.owned.read_key(state)
    from qa_core.api.auth import create_api_key,hash_key
    admin_key=secrets.token_urlsafe(32);actor='platform-module-audit-20261012'
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    create_api_key(admin_key,actor,['admin'],tenant_id='default')
    try:
        observations=[]
        endpoints=['/health?refresh=1','/admin/capabilities','/kb/capabilities','/kb/versions',
            '/qa/recommendations','/kb/catalog?kind=all&page_size=12',
            '/kb/catalog?kind=faq&page_size=12','/kb/catalog?kind=document&page_size=12',
            '/kb/rebuild?limit=30','/admin/ops','/admin/overview','/admin/eval','/admin/permissions']
        for role,key in [('ops',ops_key),('admin',admin_key)]:
            with httpx.Client(base_url=state['url'],headers={'X-API-Key':key},timeout=60,trust_env=False) as http:
                for url in endpoints:
                    try:
                        response=http.get(url);data=response.json()
                        item=dict(role=role,url=url,status=response.status_code,summary=summarize(url,data))
                    except Exception as error:item=dict(role=role,url=url,error_type=type(error).__name__)
                    observations.append(item);write(ROOT/'http-observations.json',observations)
                    print(role+' '+url+' '+str(item.get('status',item.get('error_type'))),flush=True)
                # Feature is disabled by the attested profile: this never runs tools or generation.
                assert not settings.agent_enabled
                result=http.post('/agent/diagnose',json={'query':'检查本地演示模块可用性'})
                observations.append(dict(role=role,url='/agent/diagnose',method='POST',status=result.status_code,
                    disabled_feature_only=True,summary=summarize('/agent/diagnose',result.json())))
        write(ROOT/'http-observations.json',observations)
        with engine.connect() as conn:
            counts={table:conn.execute(sa.text('SELECT COUNT(*) FROM '+table)).scalar() for table in
                ['kb_versions','knowledge_assets','ingest_records','ingest_jobs','messages','sessions','feedback','bad_cases']}
            active=conn.execute(sa.text("SELECT version FROM kb_versions WHERE status='ACTIVE'")).scalars().all()
        write(ROOT/'data-presence.json',dict(counts=counts,active=active,
            runtime_reports_dir=str(settings.reports_dir),visible_eval_files=len(list(Path(settings.reports_dir).glob('eval_*.json'))),
            project_historical_eval_files=len(list((runtime.PROJECT/'reports').glob('eval_*.json'))),
            paid_calls=0,qa_submitted=False,business_actions_performed=False))
        params=dict(edge='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',reportDir=str(ROOT),
            url=state['url'],loginUrl=(runtime.owned.ROOT/'open-platform.html').as_uri(),opsKey=ops_key,adminKey=admin_key)
        result=subprocess.run(['node',str(ROOT/'module-browser.js')],input=json.dumps(params),capture_output=True,
            encoding='utf-8',timeout=210)
        (ROOT/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode:raise RuntimeError('module browser audit failed; inspect private report log')
        print(result.stdout.strip(),flush=True)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=hash_key(admin_key),u=actor))
        engine.dispose()


if __name__=='__main__':main()
