"""Fresh bounded observations after real Docker cold-start and handoff preparation."""
from datetime import datetime,timezone
import time
import httpx
from scripts import content_quality_release_runtime as current,readonly_workspace_runtime as readonly,platform_entry
from scripts.prepare_legacy_projection import write
from scripts.verify_version_workspace_survival import gui_process
from scripts.verify_runtime_handoff import REPORT
from qa_core.ingest.workspace import worker_status


def main():
    current.install_adapter();current.owned.apply_profile(current.selected_settings())
    rows=[];initial=None
    for index in range(4):
        original=current.owned.inspect();isolated=readonly.inspect();worker=worker_status();observer=platform_entry.observer_process()
        assert original['status']==isolated['status']=='running' and worker['status']=='ready' and observer is not None
        pids={'original':original['pid'],'isolated':isolated['pid'],'worker':worker['pid'],'observer':observer.pid}
        if initial is None:initial=pids
        assert initial==pids
        assert platform_entry.Dependencies().engine_ready()
        assert all(r['health']=='healthy' and r['status']=='running' for r in platform_entry.Dependencies().containers())
        row={'at':datetime.now(timezone.utc).isoformat(),'processes':{n:gui_process(pid) for n,pid in pids.items()},'health':[]}
        for url in ('http://127.0.0.1:8010','http://localhost:8010','http://127.0.0.1:18021'):
            response=httpx.get(url+'/health',timeout=15,trust_env=False);response.raise_for_status();data=response.json()
            assert data['status']=='ok' and data['generation_enabled']==('8010' in url) and data['agent_enabled']==('18021' in url)
            row['health'].append({'url':url,'status':'ok','generation_enabled':data['generation_enabled'],'agent_enabled':data['agent_enabled']})
        rows.append(row);write(REPORT/'survival.json',dict(passed=index==3,samples=rows,paid_calls=0,
            scope='4 observations 20 seconds apart after real Docker startup; no long-term SLA or user-window ownership claim'))
        print('Windowless owned runtimes healthy: sample '+str(index+1),flush=True)
        if index<3:time.sleep(20)


if __name__=='__main__':main()
