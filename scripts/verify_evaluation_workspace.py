"""Actual report/role API and product browser evaluation, no external model."""
import json
import secrets
import subprocess
import sys
import uuid
import httpx
import sqlalchemy as sa
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import write, digest

REPORT = runtime.PROJECT / '.runtime/test/reports/evaluation-workspace-p1-20261012'


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    runtime.install_adapter(); settings = runtime.selected_settings(); runtime.owned.apply_profile(settings)
    state = runtime.owned.load_state(); assert runtime.owned.inspect()['status'] == 'running'
    keys = {'ops': runtime.owned.read_key(state)}; created = []
    engine = sa.create_engine(settings.mysql_dsn)
    from qa_core.api.auth import create_api_key, hash_key
    budget = runtime.owned.ROOT / 'generation-validation-budget.json'
    before = digest(budget)
    suffix = uuid.uuid4().hex[:10]
    frozen_before = {str(p.relative_to(runtime.PROJECT)): digest(p) for p in
        (runtime.merge.CANDIDATE / 'artifact-hashes.json', runtime.merge.REPORT / 'artifact-hashes.json', runtime.REVIEW / 'artifact-hashes.json')}
    with engine.connect() as conn:
        message_before = conn.execute(sa.text('SELECT COUNT(*) FROM messages')).scalar()
        session_before = conn.execute(sa.text('SELECT COUNT(*) FROM sessions')).scalar()
    try:
        for role, roles, tenant in [('admin', ['admin'], 'default'), ('auditor', ['auditor'], 'default'), ('tenant_admin', ['admin'], 'eval-audit-only')]:
            keys[role] = secrets.token_urlsafe(32); actor = 'evaluation-workspace-audit-' + role + '-' + suffix
            created.append((keys[role], actor)); create_api_key(keys[role], actor, roles, tenant_id=tenant)
        observations = []
        for role, key in keys.items():
            with httpx.Client(base_url=state['url'], headers={'X-API-Key': key}, timeout=30, trust_env=False) as client:
                response = client.get('/admin/eval/reports')
                assert response.status_code == (200 if role in ('admin', 'auditor') else 403)
                if response.status_code == 200:
                    index = response.json(); assert index['active_version'] == runtime.VERSION
                    assert not index['independent_verified']
                    assert len(index['reports']) >= 12
                    assert sum(row['attribution'] == 'unattributed' for row in index['reports']) == 3
                    assert client.get('/admin/eval/reports/' + 'a'*32).status_code == 404
                    assert client.post('/admin/eval/jobs', json={'plan_id': 'a'*32, 'use_llm': True}).status_code == 422
                if role != 'admin':
                    assert client.post('/admin/eval/plans').status_code == 403
                    assert client.post('/admin/eval/jobs', json={'plan_id': 'a'*32}).status_code == 403
                observations.append({'role': role, 'read_status': response.status_code})
        write(REPORT / 'api-results.json', observations)
        params = {'reportDir': str(REPORT), 'url': state['url'], 'keys': keys, 'version': runtime.VERSION,
                  'loginUrl': (runtime.owned.ROOT / 'open-platform.html').as_uri(), 'verifySnapshot': '--snapshot-only' in sys.argv}
        result = subprocess.run(['node', str(runtime.PROJECT / 'tests/browser/evaluation_workspace_smoke.js')],
            input=json.dumps(params), capture_output=True, encoding='utf-8', timeout=240)
        (REPORT / ('snapshot-browser.log' if params['verifySnapshot'] else 'browser.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        assert result.returncode == 0, 'Browser failed; inspect local evidence'
        print(result.stdout.strip())
        if params['verifySnapshot']:
            return
        with httpx.Client(base_url=state['url'], headers={'X-API-Key': keys['admin']}, timeout=30, trust_env=False) as client:
            index = client.get('/admin/eval/reports').json()
            jobs = [job for job in index['jobs'] if job.get('requested_by') == created[0][1]]
            assert len(jobs) == 1 and jobs[0]['state'] == 'completed'
            job = jobs[0]; detail = client.get('/admin/eval/reports/' + job['report_id']).json()
            assert detail['kb_version'] == runtime.VERSION and detail['sample_count'] == 31
            assert all(detail['checks'][field]['passed'] == detail['checks'][field]['evaluated'] == 31
                for field in ('route_ok', 'hit_ok', 'status_ok', 'body_ok'))
            assert detail['checks']['reject_ok']['evaluated'] == 0
            assert not detail['independent_verified']
            assert client.post('/admin/eval/jobs', json={'plan_id': job['id']}).status_code == 409
            write(REPORT / 'current-report.json', detail); write(REPORT / 'current-job.json', job)
            write(REPORT / 'index-summary.json', {k: value for k, value in index.items() if k != 'reports'})
        with engine.connect() as conn:
            assert conn.execute(sa.text('SELECT COUNT(*) FROM messages')).scalar() == message_before
            assert conn.execute(sa.text('SELECT COUNT(*) FROM sessions')).scalar() == session_before
        assert all(digest(runtime.PROJECT / name) == value for name, value in frozen_before.items())
        write(REPORT / 'preservation.json', {'frozen_seal_hashes_unchanged': True, 'messages_unchanged': True,
            'sessions_unchanged': True, 'budget_unchanged': digest(budget) == before, 'paid_calls': 0})
    finally:
        with engine.begin() as conn:
            for key, actor in created:
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'), {'h': hash_key(key), 'u': actor})
        with engine.connect() as conn:
            remaining = sum(conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id=:u'), {'u': actor}).scalar() for _, actor in created)
        engine.dispose()
        write(REPORT / 'cleanup.json', {'temporary_keys_remaining': remaining, 'paid_calls': 0, 'budget_unchanged': digest(budget) == before})
        assert remaining == 0


if __name__ == '__main__':
    main()
