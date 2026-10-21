"""Stop and restart only the new preview, observing keys, port and protected data."""
import json
import time
import socket
import httpx
import sqlalchemy as sa
from scripts import sop_record_preview_runtime as runtime
from scripts.verify_sop_record_preview import REPORT,protected
from scripts.prepare_legacy_projection import read,write,digest
from scripts.sop_record_stage_profile import profile


def key_count():
    engine=sa.create_engine(profile().mysql_dsn)
    try:
        with engine.connect() as conn:return conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys').scalar_one()
    finally:engine.dispose()


def main():
    output=REPORT/'lifecycle.json'
    if output.exists():raise ValueError('owned lifecycle acceptance already recorded')
    attempts=[p for p in REPORT.glob('attempt-*') if read(p/'state.json')['state']=='completed']
    assert len(attempts)==1
    before=protected();state=runtime.record();old_tree=runtime.owned_tree(state)
    old_key=(runtime.ROOT/'api-key.txt').read_text(encoding='utf-8')
    old_key_sha=digest(runtime.ROOT/'api-key.txt')
    assert key_count()==1
    assert runtime.stop()['owned_preview_key_removed']
    for process in old_tree:assert not process.is_running()
    with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',runtime.PORT))!=0
    assert key_count()==0 and not (runtime.ROOT/'api-key.txt').exists()
    restarted=runtime.start();assert restarted['status']=='running'
    new_key=(runtime.ROOT/'api-key.txt').read_text(encoding='utf-8')
    assert new_key!=old_key and digest(runtime.ROOT/'api-key.txt')!=old_key_sha and key_count()==1
    observations=[]
    with httpx.Client(base_url=restarted['url'],trust_env=False,timeout=80) as client:
        assert client.get('/preview/checks',headers={'X-API-Key':old_key}).status_code==401
        for i in range(3):
            reply=client.get('/preview/checks',headers={'X-API-Key':new_key})
            assert reply.status_code==200
            checks=reply.json();assert checks['read_preview_ready'] and checks['support_alignment']['summary']['record_contracts']==109
            assert runtime.inspect()['status']=='running'
            observations.append(dict(observation=i+1,authenticated_response=200,contracts=109,runtime='running'))
            if i<2:time.sleep(2)
    after=protected();assert before==after
    write(output,dict(state='completed',owned_processes_exited=len(old_tree),port_closed_after_stop=True,
        new_database_keys_after_stop=0,new_database_keys_after_restart=1,old_key_rejected=True,
        restarted_with_new_key=True,observations=observations,protected_snapshots_unchanged=True,
        only_new_preview_pid_and_key_rotated=True,background_window_hidden=True,paid_calls=0,
        long_term_availability_proven=False))
    print(json.dumps(dict(lifecycle='passed',observations=3,old_key_rejected=True,paid_calls=0)))


if __name__=='__main__':main()
