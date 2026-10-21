"""Real local browser/SSE boundary checks; no provider questions or mock responses."""
import hashlib
import json
import subprocess
import uuid

import httpx
import sqlalchemy as sa

from scripts import content_quality_release_runtime as rt, readonly_workspace_runtime as readonly
from scripts.prepare_legacy_projection import read,write,digest
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace

REPORT=rt.PROJECT/'.runtime/test/reports/development-selfcheck-20261013'


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    reload=read(REPORT/'runtime-reload.json');assert reload['state']=='completed'
    entry=rt.owned.inspect();assert entry['pid']==reload['pid']
    engine=sa.create_engine(settings.mysql_dsn)
    folder=REPORT/('browser-'+uuid.uuid4().hex[:8]);folder.mkdir()
    session='development-boundary-'+uuid.uuid4().hex
    state=rt.owned.load_state();key=rt.owned.read_key(state)
    scoped=hashlib.sha256(f"default:{state['actor']}:{session}".encode()).hexdigest()[:36]
    def protected():
        return dict(active=Workspace(settings).snapshot(rt.VERSION),original_sql=sql_snapshot(settings),
            readonly_sql=sql_snapshot(readonly.profile()),key_sha256=digest(rt.owned.ROOT/'api-key.txt'),
            profile_sha256=digest(rt.owned.ROOT/'profile.json'),env_sha256=digest(rt.PROJECT/'.env.runtime'),
            budget_sha256=digest(rt.owned.ROOT/'generation-validation-budget.json'))
    try:
        completed=[p for p in REPORT.glob('attempt-*') if
            read(p/'state.json').get('component_pass')==43 and read(p/'state.json').get('http_pass')==19]
        winning=max(completed,key=lambda p:read(p/'state.json')['completed_at'])
        outcome=read(winning/'state.json');assert outcome['component_pass']==43 and outcome['http_pass']==19
        rows=read(winning/'component-results.json')
        cases=[dict(id=r['id'],query=r['query'],expected=r['result']['answer']) for r in rows
               if r['id'] in ('developer-injection','developer-weather')]
        assert len(cases)==2
        from qa_core.intent.rules import classify_by_rules
        from qa_core.config.scenario import get_scenario
        from qa_core.rewrite.normalizer import normalize_query
        assert all(classify_by_rules(c['query'],get_scenario()).route=='direct_answer' for c in cases)
        with engine.connect() as conn:
            assert conn.execute(sa.text('SELECT COUNT(*) FROM sessions WHERE session_id=:s'),{'s':scoped}).scalar()==0
        before=protected();write(folder/'before.json',before)
        p=subprocess.run(['node',str(rt.PROJECT/'tests/browser/development_boundary_smoke.js')],
            input=json.dumps(dict(url=entry['url'],key=key,version=rt.VERSION,reportDir=str(folder),
                                 session=session,cases=cases)),text=True,capture_output=True,timeout=130,
            creationflags=subprocess.CREATE_NO_WINDOW)
        # Key passed through stdin only; raw node stderr is never persisted or printed.
        result=read(folder/'browser-verification.json');assert p.returncode==0 and result['passed']
        with engine.connect() as conn:
            sessions=conn.execute(sa.text('SELECT user_id,tenant_id FROM sessions WHERE session_id=:s'),{'s':scoped}).mappings().all()
            messages=conn.execute(sa.text('SELECT role,content FROM messages WHERE session_id=:s ORDER BY created_at,message_id'),{'s':scoped}).mappings().all()
        assert len(sessions)==1 and sessions[0]['user_id']==state['actor'] and sessions[0]['tenant_id']=='default'
        assert sorted((m['role'],m['content']) for m in messages)==sorted(
            [(role,text) for c in cases for role,text in [('user',normalize_query(c['query'])),('assistant',c['expected'])]])
        after=protected();write(folder/'after.json',after);assert before==after
        assert rt.owned.inspect()['pid']==entry['pid']
        write(folder/'state.json',dict(state='completed',browser_checks=len(result['checks']),
            paid_calls=0,provider_branch='direct_answer; component raising provider guard verified',
            protected_state_unchanged=True,created_development_sessions=1,created_development_messages=4,
            session_id=scoped,temporary_keys_created=0,independent_cases=0))
        print(json.dumps(dict(state='completed',browser_checks=len(result['checks']),paid_calls=0,
                             report=str(folder/'browser-verification.json'))))
    except Exception as exc:
        write(folder/'state.json',dict(state='failed',error_kind=type(exc).__name__,paid_calls=0))
        raise RuntimeError('Owned browser check failed; see retained safe report') from None
    finally:engine.dispose()


if __name__=='__main__':main()
