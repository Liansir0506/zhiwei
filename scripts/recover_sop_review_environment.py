"""Restore attested readonly entry via existing controller; no new configuration."""
from pathlib import Path
import sys
import time

from scripts import readonly_workspace_bootstrap as boot,content_quality_release_runtime as current
from scripts.prepare_legacy_projection import digest,read,write
from scripts.verify_sop_evidence_drafts import REPORT


def main():
    target=REPORT/'environment-recovery.json'
    if target.exists():raise ValueError('recovery evidence already recorded')
    if Path(sys.executable).name.lower()!='pythonw.exe':raise ValueError('windowless helper required')
    ro=boot.runtime;old_readonly_key=digest(ro.ROOT/'api-key.txt')
    old_original_key=read(current.PROJECT/'.runtime/test/reports/development-selfcheck-20261013/runtime-reload.json')['after']['key_sha256']
    record=dict(state='running',phase='readonly_storage_warmup',paid_calls=0,
        original_historical_key_sha256=old_original_key,readonly_previous_key_sha256=old_readonly_key)
    write(target,record)
    try:
        ro.subprocess=boot.WindowlessCommands()
        ro.prepare_storage()
        status=ro.inspect()
        if status['status']!='running':
            assert status['status']=='stale'
            record['phase']='readonly_owned_stale_cleanup';write(target,record)
            ro.stop();ro.start()
        record['phase']='original_unified_prepare';write(target,record)
        current.install_adapter();deadline=time.monotonic()+420
        while time.monotonic()<deadline:
            try:
                state=current.owned.inspect()
                if state['status']=='running':break
            except Exception:pass
            time.sleep(3)
        else:raise TimeoutError('original unified preparation did not become ready')
        assert ro.inspect()['status']=='running'
        record.update(state='completed',phase='ready',original_running=True,readonly_running=True,
            original_key_reissued=digest(current.owned.ROOT/'api-key.txt')!=old_original_key,
            readonly_key_reissued=digest(ro.ROOT/'api-key.txt')!=old_readonly_key,
            existing_controllers_only=True,new_configuration_published=False,
            executable='pythonw.exe',paid_calls=0)
        write(target,record)
    except Exception as error:
        record.update(state='failed',error_kind=type(error).__name__);write(target,record)


if __name__=='__main__':main()
