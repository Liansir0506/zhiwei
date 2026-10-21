"""Real stop/restart of the topical adapter; preserve baseline source state and roles."""
import json
import socket
import time
import httpx
import sqlalchemy as sa
from scripts import sop_record_topic_runtime as runtime
from scripts.verify_sop_record_topic_preview import REPORT
from scripts.verify_sop_record_preview import protected
from scripts.prepare_legacy_projection import read,write


def keys():
    engine=sa.create_engine(runtime.core.profile().mysql_dsn)
    try:
        with engine.connect() as conn:return conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar_one()
    finally:engine.dispose()


def main():
    assert not (REPORT/'lifecycle.json').exists()
    completed=[p for p in REPORT.glob('attempt-*') if (p/'state.json').exists() and read(p/'state.json')['state']=='completed']
    assert len(completed)==1
    before=protected();runtime.select_record()
    processes=runtime.core.owned_tree(runtime.core.record())
    old=(runtime.core.ROOT/'api-key.txt').read_text(encoding='utf-8')
    assert runtime.stop()['owned_preview_key_removed'] and keys()==0
    assert all(not process.is_running() for process in processes)
    with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',18025))!=0
    state=runtime.start();assert state['status']=='running'
    new=(runtime.core.ROOT/'api-key.txt').read_text(encoding='utf-8');assert new!=old and keys()==1
    observations=[]
    with httpx.Client(base_url=state['url'],trust_env=False,timeout=80) as client:
        assert client.get('/preview/checks',headers={'X-API-Key':old}).status_code==401
        for i in range(3):
            reply=client.get('/preview/checks',headers={'X-API-Key':new})
            assert reply.status_code==200 and reply.json()['support_alignment']['summary']['record_contracts']==109
            assert runtime.status()['status']=='running'
            observations.append(dict(index=i+1,status=200,record_contracts=109,runtime='running'))
            if i<2:time.sleep(2)
    assert protected()==before
    write(REPORT/'lifecycle.json',dict(state='completed',old_key_rejected=True,owned_processes_exited=len(processes),
        port_closed_after_stop=True,keys_after_stop=0,keys_after_restart=1,observations=observations,
        hidden_stop_restart=True,protected_snapshots_unchanged=True,long_term_availability_proven=False,paid_calls=0))
    print(json.dumps(dict(adapter_lifecycle='passed',observations=3,paid_calls=0)))


if __name__=='__main__':main()
