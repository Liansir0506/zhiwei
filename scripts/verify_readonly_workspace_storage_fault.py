"""Stop only the owned test MySQL; verify fail-closed reads and restore in finally."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import subprocess
import time
import httpx
from scripts import readonly_workspace_runtime as rt
from scripts.prepare_legacy_projection import write
from scripts.verify_readonly_business_workspace import sql_snapshot

def main():
    report=rt.PROJECT/'.runtime/test/reports/readonly-business-p2-20261012'
    target='telecom-ops-rag-test-mysql-1'
    item=json.loads(subprocess.run(['docker','inspect',target],check=True,capture_output=True,text=True,encoding='utf-8').stdout)[0]
    assert item['Config']['Labels']['com.docker.compose.project']=='telecom-ops-rag-test'
    assert item['Config']['Labels']['com.docker.compose.service']=='mysql' and item['State']['Health']['Status']=='healthy'
    settings=rt.profile();before=sql_snapshot(settings);state=rt.state();assert rt.inspect()['status']=='running'
    key=(rt.ROOT/'api-key.txt').read_text(encoding='utf-8');checks=[]
    proof={'passed':False,'fault':'owned isolated MySQL temporarily stopped','target':target,
           'checks':checks,'paid_calls':0,'original_storage_stopped':False,'container_restored':False}
    try:
        subprocess.run(['docker','stop','--time','10',target],check=True,capture_output=True,timeout=30)
        with httpx.Client(base_url=state['url'],headers={'X-API-Key':key},timeout=15,trust_env=False) as client:
            for path in ('/business/availability','/business/alarms','/kb/catalog'):
                response=client.get(path);assert response.status_code==503
                assert response.headers['cache-control']=='no-store'
                assert not any(word in response.text for word in ('mysql+pymysql','password','SIM-DEV','SIM-ALM','payload_json'))
                checks.append({'path':path,'status':503,'no_rows_or_credentials_published':True})
        write(report/'storage-fault.json',proof)
    finally:
        subprocess.run(['docker','start',target],check=True,capture_output=True,timeout=30)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            status=subprocess.run(['docker','inspect','--format','{{.State.Health.Status}}',target],capture_output=True,text=True,check=True).stdout.strip()
            if status=='healthy':break
            time.sleep(2)
        assert status=='healthy','Must restore owned test storage before finishing'
        proof['container_restored']=True;write(report/'storage-fault.json',proof)
    assert sql_snapshot(settings)==before
    rt.verify_admission(settings)
    response=httpx.get(state['url']+'/business/alarms',headers={'X-API-Key':key},trust_env=False,timeout=20)
    assert response.status_code==200 and len(response.json()['items'])==2
    assert rt.inspect()['pid']==state['pid'],'Read recovery must not require application restart'
    proof.update(passed=True,protected_sql_unchanged=True,application_restarted=False,recovery_status=200,api_pid=state['pid'])
    write(report/'storage-fault.json',proof);print(json.dumps({'passed':True,'real_unavailable_checks':len(checks),'recovered':True,'paid_calls':0}),flush=True)
if __name__=='__main__':main()
