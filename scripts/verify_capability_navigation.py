"""Actual local API/Edge role navigation audit; temporary keys are always removed."""
import json
import secrets
import subprocess
import uuid
import httpx
import sqlalchemy as sa
from scripts import content_quality_release_runtime as runtime
from scripts.prepare_legacy_projection import read, write, digest

REPORT = runtime.PROJECT / '.runtime/test/reports/capability-navigation-p1-20261012'


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    runtime.install_adapter()
    settings = runtime.selected_settings()
    runtime.owned.apply_profile(settings)
    state = runtime.owned.load_state()
    assert runtime.owned.inspect()['status'] == 'running'
    keys = {'ops': runtime.owned.read_key(state)}
    created = []
    engine = sa.create_engine(settings.mysql_dsn)
    from qa_core.api.auth import create_api_key, hash_key
    budget = runtime.owned.ROOT / 'generation-validation-budget.json'
    budget_before = digest(budget)
    suffix = uuid.uuid4().hex[:10]
    try:
        for role, roles, tenant in [('admin', ['admin'], 'default'), ('auditor', ['auditor'], 'default'),
                                   ('tenant_admin', ['admin'], 'capability-audit-only'), ('guest', ['guest'], 'default')]:
            keys[role] = secrets.token_urlsafe(32)
            actor = 'capability-navigation-audit-' + role + '-' + suffix
            created.append((keys[role], actor))
            create_api_key(keys[role], actor, roles, tenant_id=tenant)
        observations = []
        expected = {'ops': [200, 403, 403], 'admin': [200, 200, 200], 'auditor': [200, 200, 200],
                    'tenant_admin': [403, 403, 200], 'guest': [403, 403, 403]}
        for role, key in keys.items():
            with httpx.Client(base_url=state['url'], headers={'X-API-Key': key}, timeout=30, trust_env=False) as client:
                response = client.get('/admin/capabilities'); response.raise_for_status()
                capabilities = response.json()
                statuses = [client.get(url).status_code for url in ('/admin/ops', '/admin/eval', '/admin/permissions')]
                assert statuses == expected[role], role
                assert [capabilities[name] for name in ('can_read_ops', 'can_read_eval', 'can_read_permissions')] == [code == 200 for code in statuses]
                observations.append({'role': role, 'capabilities': capabilities, 'protected_get_statuses': statuses})
        write(REPORT / 'api-results.json', observations)
        params = {'reportDir': str(REPORT), 'url': state['url'], 'keys': keys,
                  'loginUrl': (runtime.owned.ROOT / 'open-platform.html').as_uri(), 'adminActor': created[0][1]}
        result = subprocess.run(['node', str(runtime.PROJECT / 'tests/browser/capability_navigation_smoke.js')],
                                input=json.dumps(params), capture_output=True, encoding='utf-8', timeout=240)
        (REPORT / 'browser.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        assert result.returncode == 0, 'Actual browser failed; inspect local report'
        assert digest(budget) == budget_before
        print(result.stdout.strip())
    finally:
        with engine.begin() as conn:
            for key, actor in created:
                conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h AND user_id=:u'), {'h': hash_key(key), 'u': actor})
        with engine.connect() as conn:
            remaining = sum(conn.execute(sa.text('SELECT COUNT(*) FROM api_keys WHERE user_id=:u'), {'u': actor}).scalar() for _, actor in created)
        engine.dispose()
        write(REPORT / 'cleanup.json', {'temporary_keys_remaining': remaining, 'paid_calls': 0, 'budget_unchanged': digest(budget) == budget_before})
        assert remaining == 0


if __name__ == '__main__':
    main()
