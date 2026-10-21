"""Four bounded live observations; evidence of this window, not an uptime SLA."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from datetime import datetime,timezone
import json
import time
import httpx
from scripts import readonly_workspace_runtime as rt
from scripts import content_quality_release_runtime as current
from scripts.prepare_legacy_projection import read,write
from qa_core.ingest.workspace import worker_status

def main():
    report=rt.PROJECT/'.runtime/test/reports/readonly-business-p2-20261012'
    report.mkdir(parents=True,exist_ok=True)
    current.install_adapter();original=current.selected_settings();current.owned.apply_profile(original)
    key=current.owned.read_key(current.owned.load_state())
    initial={'original':current.owned.inspect()['pid'],'isolated':rt.inspect()['pid'],'worker':worker_status()['pid']}
    values=[]
    for i in range(4):
        rows={'at':datetime.now(timezone.utc).isoformat(),'original':current.owned.inspect(),
              'isolated':rt.inspect(),'worker':worker_status()}
        assert rows['original']['status']=='running' and rows['isolated']['status']=='running' and rows['worker']['status']=='ready'
        assert {name:rows[name]['pid'] for name in initial}==initial
        for url in ('http://127.0.0.1:8010','http://localhost:8010','http://127.0.0.1:18021'):
            response=httpx.get(url+'/health',headers={'X-API-Key':key} if '8010' in url else {},timeout=12,trust_env=False)
            response.raise_for_status();data=response.json();assert data['status']=='ok'
            assert data['agent_enabled']==('18021' in url) and data['generation_enabled']==('8010' in url)
            rows[url]=data
        values.append(rows);write(report/'survival.json',{'passed':i==3,'samples':values,'scope':'4 readings in a bounded observation window; not an uptime SLA','paid_calls':0})
        print(json.dumps({'sample':i+1,'status':'healthy','pids':initial}),flush=True)
        if i<3:time.sleep(20)
if __name__=='__main__':main()
