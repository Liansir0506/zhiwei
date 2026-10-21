"""Exercise the existing explicitly no-LLM FAQ evaluation workflow, no knowledge writes."""
import json,secrets,time
from pathlib import Path
import httpx
import sqlalchemy as sa
from scripts import demo_release_runtime_r2 as r
from scripts.build_original_rollback_baseline import apply_settings


def main():
    root=Path('.runtime/test/reports/ops-audit-20261019');rows=[]
    def save(): (root/'controlled-evaluation.json').write_text(json.dumps(rows,ensure_ascii=True,indent=2),'utf-8')
    r.install_adapter();s=r.selected_settings();apply_settings(s)
    from qa_core.api.auth import create_api_key,hash_key
    key=secrets.token_urlsafe(32);actor='ops-audit-eval-20261019-'+secrets.token_hex(6);create_api_key(key,actor,['admin'])
    engine=sa.create_engine(s.mysql_dsn)
    try:
        with httpx.Client(base_url='http://127.0.0.1:8010',trust_env=False,timeout=60,headers={'X-API-Key':key}) as c:
            prepared=c.post('/admin/eval/plans');rows.append(dict(stage='prepare',status=prepared.status_code,result=prepared.json()));save()
            if prepared.status_code!=200:return
            plan=prepared.json();assert plan['mode']=='no_llm'
            response=c.post('/admin/eval/jobs',json={'plan_id':plan['id']});rows.append(dict(stage='execute',status=response.status_code,result=response.json()));save()
            if response.status_code!=202:return
            deadline=time.monotonic()+320
            while time.monotonic()<deadline:
                response=c.get('/admin/eval/jobs/'+plan['id']);state=response.json()
                if state.get('state') not in ['queued','running']:
                    rows.append(dict(stage='finished',status=response.status_code,result=state));save()
                    if state.get('report_id'):
                        report=c.get('/admin/eval/reports/'+state['report_id']);rows.append(dict(stage='report',status=report.status_code,result=report.json()));save()
                    break
                time.sleep(2)
            print(json.dumps({'evaluation_state':state.get('state'),'completed':state.get('completed')}))
    finally:
        with engine.begin() as c:c.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'),dict(h=hash_key(key),u=actor))
        engine.dispose();rows.append(dict(stage='temporary-key-removed',paid_calls=0));save()


if __name__=='__main__':main()
