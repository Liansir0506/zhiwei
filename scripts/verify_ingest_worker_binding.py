"""Actual daemon pauses on an unbound source change; restore exact bytes."""
import hashlib
import time
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read,write
from scripts.verify_ingest_workspace import REPORT
from qa_core.ingest import workspace as ws
from scripts import workspace_ingest_worker as worker


def wait_status(expected):
    for _ in range(35):
        state=ws.worker_status()
        if state['status']==expected:return state
        time.sleep(1)
    raise TimeoutError('Worker binding observation timed out')


def main():
    success=read(REPORT/'latest-success.json')
    initial=wait_status('ready');process=worker.owned(worker.load_state());assert process
    path=runtime.PROJECT/'scripts/workspace_ingest_worker.py'
    original=path.read_bytes();checksum=hashlib.sha256(original).hexdigest()
    try:
        # This new, unbound source file is the only fault target. No frozen
        # release file, collection, approval or runtime configuration is edited.
        path.write_bytes(original+b'\n# Controlled local binding-change observation; restored in finally.\n')
        paused=wait_status('unavailable')
        assert '启动绑定已变化' in paused['message'] and paused['pid']==process.pid
    finally:path.write_bytes(original)
    restored=wait_status('ready')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==checksum and restored['pid']==initial['pid']
    candidate=ws.get_candidate(success['candidate']);ws.verify_candidate(runtime.selected_settings(),candidate,writable=True)
    write(REPORT/'binding-change-observation.json',{'passed':True,'target':'scripts/workspace_ingest_worker.py',
        'source_bytes_restored':True,'same_owned_pid':process.pid,'before':initial,'paused':paused,'restored':restored,
        'scope':'Temporary comment changes this new source binding; no knowledge/approval/runtime config writes','paid_calls':0})
    print('Actual Worker paused on binding change and resumed after exact source restoration.')


if __name__=='__main__':main()
