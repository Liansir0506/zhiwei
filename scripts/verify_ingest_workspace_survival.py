"""Bounded, read-only observation after product upload and caller exit."""
import time
from datetime import datetime, timezone
import httpx
import psutil
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read,write
from scripts.verify_ingest_workspace import REPORT
from scripts import workspace_ingest_worker as worker
from qa_core.ingest import workspace as ws


def main():
    runtime.install_adapter();settings=runtime.selected_settings();runtime.owned.apply_profile(settings)
    evidence=read(REPORT/'latest-success.json');observations=[]
    for index in range(4):
        state=runtime.owned.load_state(); assert runtime.owned.inspect()['status']=='running'
        process=worker.owned(worker.load_state());assert process
        for host in ('127.0.0.1','localhost'):
            health=httpx.get('http://'+host+':8010/health',timeout=10,trust_env=False).json()
            assert health['status']=='ok' and health['generation_enabled'] and not health['agent_enabled']
        status=ws.worker_status();assert status['status']=='ready' and status['binding_sha256']==ws.digest(ws.profile_binding(settings))
        observations.append({'read_at_utc':datetime.now(timezone.utc).isoformat(),'api_pid':state['pid'],'worker_pid':process.pid,
            'worker_status':status['status'],'worker_heartbeat':status['heartbeat_at'],'worker_rss_mb':round(process.memory_info().rss/1024**2,1),
            'ipv4_health':'ok','localhost_health':'ok','generation_enabled':True,'agent_enabled':False})
        write(REPORT/'survival.json',{'passed':index==3,'observations':observations,'scope':'四次有界只读观察；不代表长期SLA','paid_calls':0})
        if index!=3:time.sleep(20)
    assert len({row['worker_pid'] for row in observations})==len({row['api_pid'] for row in observations})==1
    print('Four persistent API/Worker health samples passed; no model requests.')


if __name__=='__main__':main()
