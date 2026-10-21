"""Independent read-oriented audit of the current deployment; no release or LLM call."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import time
import traceback
import httpx
import sqlalchemy as sa
from scripts import demo_release_runtime_r2 as release
from scripts.build_original_rollback_baseline import apply_settings

PROJECT=Path(__file__).resolve().parents[1]
REPORT=PROJECT/'.runtime/test/reports/ops-audit-20261019'
PRIVATE=PROJECT/'.runtime/ops-audit-20261019'


def write(name,value):
    (REPORT/name).write_text(json.dumps(value,ensure_ascii=True,indent=2,default=str),'utf-8')


def protected(settings):
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        with engine.connect() as c:
            tables=['kb_versions','kb_version_manifests','knowledge_assets','knowledge_relations',
                    'knowledge_source_withdrawals','ingest_jobs','ingest_records','evidence_locations',
                    'devices','alarms','work_orders','change_records','monitoring_metrics']
            rows={}
            for table in tables:
                raw=[dict(r) for r in c.exec_driver_sql('SELECT * FROM `'+table+'`').mappings()]
                encoded=sorted(json.dumps(r,sort_keys=True,default=str,ensure_ascii=True) for r in raw)
                rows[table]=dict(count=len(raw),sha256=hashlib.sha256(json.dumps(encoded).encode()).hexdigest())
    finally:engine.dispose()
    for name in ['.env.runtime','.runtime/original-local/generation-validation-budget.json',
                 '.runtime/original-local/demo-release-selection-r2.json']:
        path=PROJECT/name
        if path.exists():rows[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def main():
    REPORT.mkdir(parents=True,exist_ok=True);PRIVATE.mkdir(parents=True,exist_ok=True)
    release.install_adapter();settings=release.selected_settings();apply_settings(settings)
    before=protected(settings);write('protected-before.json',before)
    from qa_core.api.auth import create_api_key,hash_key
    key=release.owned.read_key(release.owned.load_state());keys={'ops':key};created=[];checks=[]
    engine=sa.create_engine(settings.mysql_dsn)
    try:
        for role,roles,scope in [('admin',['admin'],{}),('auditor',['auditor'],{}),('guest',['guest'],{}),
            ('scoped',['ops'],dict(dept_id='nonexistent-audit-dept',device_types=['SMF'])),
            ('public',['ops'],dict(security_levels=['public'])),('foreign',['ops'],dict(tenant_id='audit-empty-20261019'))]:
            value=secrets.token_urlsafe(32);actor='ops-audit-20261019-'+role+'-'+secrets.token_hex(4)
            create_api_key(value,actor,roles,**scope);keys[role]=value;created.append((hash_key(value),actor))
        private=PRIVATE/'keys.json';private.write_text(json.dumps(keys),'utf-8')
        with httpx.Client(base_url='http://127.0.0.1:8010',trust_env=False,timeout=90) as client:
            def get(role,path,**kwargs):
                started=time.monotonic()
                try:
                    response=client.get(path,headers={'X-API-Key':keys.get(role,'invalid-audit')},**kwargs)
                    try:data=response.json()
                    except ValueError:data={'body':response.text[:120]}
                    row=dict(role=role,path=path,status=response.status_code,elapsed_ms=round((time.monotonic()-started)*1000),no_store=response.headers.get('cache-control')=='no-store')
                    checks.append(row);write('http-checks.json',checks)
                    return response.status_code,data
                except Exception as error:
                    checks.append(dict(role=role,path=path,error_type=type(error).__name__));write('http-checks.json',checks);return 0,{}
            for role in keys:
                status,data=get(role,'/admin/capabilities');write('capabilities-'+role+'.json',dict(status=status,result=data))
            dashboards={}
            for role in ['ops','admin','auditor','scoped','public','foreign','guest','invalid']:
                status,data=get(role,'/dashboard/summary');dashboards[role]=dict(status=status,result=data)
            write('dashboard-scope-probes.json',dashboards)
            controls={}
            for role in ['ops','scoped','public','foreign']:
                status,data=get(role,'/domain/suggest',params=dict(kind='device',q='AMF'))
                controls[role]=dict(status=status,result=data)
            write('domain-scope-controls.json',controls)
            paths=['/admin/overview','/admin/ops','/admin/eval/reports','/admin/permissions','/admin/ingest-workspace',
                '/admin/version-workspace/capabilities','/admin/version-workspace/summary','/admin/version-workspace/history',
                '/admin/runtime-handoff-jobs','/kb/version-scope','/kb/capabilities','/business/availability','/business/alarms']
            api={}
            for role in ['ops','admin','auditor','guest']:
                for path in paths:
                    status,data=get(role,path)
                    label=role+' '+path
                    if role=='admin' or path=='/business/availability':api[label]=dict(status=status,result=data)
            write('workspace-read-probes.json',api)
            _,catalog=get('ops','/kb/catalog',params=dict(kind='faq',page_size=1))
            if catalog.get('items'):
                faq=catalog['items'][0]
                _,source=get('ops','/kb/evidence/'+faq['evidence_id'])
                write('faq-source.json',dict(question=faq.get('question'),source=faq.get('source'),text=source.get('text'),evidence_id=faq['evidence_id']))
            for page in [1,1,2]:get('ops','/kb/catalog',params=dict(page=page,page_size=12))
            _,recommendations=get('ops','/qa/recommendations');write('recommendations.json',recommendations)
            if recommendations.get('items'):
                question=recommendations['items'][0]['query']
                # Source code guarantees exact FAQ questions bypass generation. Verify the
                # lookup locally with a disabled generation profile before using the UI.
                from qa_core.retrieval.supported_searcher import SupportedFaqSearcher
                from qa_core.security.policy import AccessScope
                from qa_core.schemas.evidence import decode_reference
                # Browser only fills the question; it never submits /qa. Live generative
                # quality and fees remain outside this read-only audit.
                write('qa-audit-scope.json',dict(question=question,submit=False,paid_calls=0,reason='No new paid generation authorization; FAQ source and composer are exercised separately.'))
            payload=dict(project=str(PROJECT),report_dir=str(REPORT/'browser'),url='http://127.0.0.1:8010',keys_file=str(private))
            (REPORT/'browser').mkdir(exist_ok=True)
            result=subprocess.run(['node',str(PROJECT/'scripts/audit_ops_pages_20261019.js')],input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,timeout=650,creationflags=subprocess.CREATE_NO_WINDOW)
            write('browser-command.json',dict(returncode=result.returncode,message=result.stderr.strip()[:250]))
        after=protected(settings);write('protected-after.json',after)
        write('data-preservation.json',dict(equal=before==after,paid_calls=0,publishing=0,ingestion=0,qa_submissions=0))
    finally:
        with engine.begin() as c:
            for h,actor in created:c.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=h,u=actor))
            remaining=c.execute(sa.text("SELECT COUNT(*) FROM api_keys WHERE user_id LIKE 'ops-audit-20261019-%'")).scalar_one()
        engine.dispose();(PRIVATE/'keys.json').unlink(missing_ok=True)
        write('temporary-key-cleanup.json',dict(created=len(created),removed=len(created),remaining=remaining))
    print(json.dumps(dict(http_checks=len(checks),browser_exit=result.returncode,protected_equal=before==after,paid_calls=0)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        write('audit-failure.json',dict(error_type=type(error).__name__,frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)]))
        print('Audit failed: '+type(error).__name__)
