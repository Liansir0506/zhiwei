"""Reload tested boundary rules in the attested entry, preserving key and profile."""
from pathlib import Path
import sys
import time

import httpx
import psutil
import sqlalchemy as sa

from scripts import content_quality_release_runtime as rt, readonly_workspace_runtime as readonly
from scripts.prepare_legacy_projection import digest,write,read
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace

REPORT=rt.PROJECT/'.runtime/test/reports/development-selfcheck-20261013'


def main():
    report=REPORT/'runtime-reload.json'
    if report.exists():raise ValueError('reload record already exists')
    if Path(sys.executable).name.lower()!='pythonw.exe':raise ValueError('hidden pythonw lifecycle required')
    rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    engine=sa.create_engine(settings.mysql_dsn)
    def protected():
        return dict(active=Workspace(settings).snapshot(rt.VERSION),original_sql=sql_snapshot(settings),
            readonly_sql=sql_snapshot(readonly.profile()),key_sha256=digest(rt.owned.ROOT/'api-key.txt'),
            profile_sha256=digest(rt.owned.ROOT/'profile.json'),env_sha256=digest(rt.PROJECT/'.env.runtime'),
            budget_sha256=digest(rt.owned.ROOT/'generation-validation-budget.json'),readonly_pid=readonly.inspect()['pid'])
    before=None
    try:
        attempts=[read(p/'state.json') for p in REPORT.glob('attempt-*')]
        assert any(a.get('component_pass')==43 and a.get('http_pass')==19 and a.get('protected_state_unchanged') for a in attempts)
        before=protected();previous_pid=rt.owned.inspect()['pid']
        write(report,dict(state='running',previous_pid=previous_pid,before=before))
        with rt.owned.command_lock(engine):current=rt.owned.restart(settings,engine)
        process=psutil.Process(current['pid'])
        assert Path(process.exe()).name.lower()=='pythonw.exe'
        assert current['pid']!=previous_pid
        observations=[]
        for _ in range(3):
            identity=rt.owned.inspect()
            health=httpx.get(identity['url']+'/health',timeout=10,trust_env=False).json()
            assert health['status']=='ok' and health['generation_enabled'] and not health['agent_enabled']
            observations.append(dict(pid=identity['pid'],healthy=True));time.sleep(3)
        after=protected();assert before==after
        write(report,dict(state='completed',previous_pid=previous_pid,pid=current['pid'],
            executable='pythonw.exe',before=before,after=after,protected_state_unchanged=True,
            observations=observations,paid_calls=0,config_switch=False,key_rotated=False))
    except Exception as exc:
        write(report,dict(state='failed',error_kind=type(exc).__name__,before=before,paid_calls=0))
        raise
    finally:engine.dispose()


if __name__=='__main__':main()
