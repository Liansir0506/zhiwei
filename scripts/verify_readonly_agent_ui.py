"""Own the complete Windows server/browser lifecycle; no LLM or data import."""
import sys
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import json
import secrets
import shutil
import subprocess
import time
import httpx
import sqlalchemy as sa
from pymilvus import MilvusClient
from scripts import legacy_demo_runtime as rt
from scripts.stage_legacy_candidate import protected_snapshot
from scripts.migrate_legacy_collections import fingerprint, read_rows
from qa_core.api.auth import create_api_key, hash_key

REPORT = PROJECT / '.runtime/test/reports/readonly-agent-ui-20261009'
REPORT.mkdir(parents=True, exist_ok=True)
profile_dir = REPORT / 'edge-profile'
if (rt.load_state() or {}).get('status', 'stopped') != 'stopped':
    raise RuntimeError('Stop the existing demo explicitly before running this owned lifecycle regression.')
summary = {'passed': False, 'usage': 'dev/regression', 'paid_llm_calls': 0,
           'candidate_passage_embeddings': 0, 'original_storage_accessed': False,
           'real_domain_data': False, 'independent_frozen_cases': 0}
engine = None
guest = None
state = None
def write(name, value): rt.write(REPORT / (name + '.json'), value)

try:
    sys.argv = ['legacy_demo_runtime', 'start']
    rt.main()
    state = rt.load_state()
    settings = rt.state_profile(state)
    assert settings.agent_enabled and settings.is_test and settings.auth_required and not settings.generation_enabled
    engine = sa.create_engine(settings.mysql_dsn)
    base_values = {name: field.default for name, field in rt.Settings.model_fields.items()}
    base_values.update({k.lower():v for k,v in rt.dotenv_values(PROJECT / '.env.test').items() if k.lower() in base_values and v is not None})
    base = rt.Settings(_env_file=None, **base_values)
    def snapshot():
        client = MilvusClient(uri=settings.milvus_uri, timeout=120)
        try: vectors = {name:fingerprint(read_rows(client, name)) for name in (settings.doc_collection, settings.faq_collection)}
        finally: client.close()
        with engine.connect() as conn:
            counts = {name:conn.exec_driver_sql(f'SELECT COUNT(*) FROM `{name}`').scalar_one() for name in ('devices','alarms','monitoring_metrics','work_orders','change_records','knowledge_assets','knowledge_relations')}
            versions = [dict(row) for row in conn.execute(sa.text('SELECT version,status FROM kb_versions ORDER BY version')).mappings()]
        return {'vectors':vectors,'domain_counts':counts,'versions':versions}
    before = {'own':snapshot(),'shared':protected_snapshot(base)}
    write('storage-before', before)
    key = (rt.ROOT / 'api-key.txt').read_text(encoding='utf-8')
    guest = secrets.token_urlsafe(24)
    create_api_key(guest, state['actor'] + '-ui-guest', ['guest'], tenant_id='default')
    with httpx.Client(base_url=state['url'], headers={'X-API-Key':key}, timeout=180, trust_env=False) as client:
        health = client.get('/health'); health.raise_for_status()
        assert health.json()['agent_enabled'] and health.json()['generation_enabled'] is False
        write('health', health.json())
        warm = client.post('/kb/search-evidence', json={'query':'ALM-AMF-REG 常见原因','top_n':1}); warm.raise_for_status()
        write('warmup', {'http_status':warm.status_code,'usage':'local_model_initialization_only','agent_quality_evaluation':False})
    print('Owned API ready; local models warmed, starting real Edge UI regression.', flush=True)
    params = {'url':state['url'], 'key':key, 'guestKey':guest, 'version':rt.VERSION,
        'edge':r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        'profile':str(profile_dir), 'reportDir':str(REPORT), 'debugPort':19317}
    browser = subprocess.run(['node', 'tests/browser/readonly_agent_smoke.js'], input=json.dumps(params), text=True,
        encoding='utf-8', capture_output=True, timeout=420, cwd=PROJECT, creationflags=subprocess.CREATE_NO_WINDOW)
    (REPORT / 'browser.log').write_text(browser.stdout + browser.stderr, encoding='utf-8')
    print(browser.stdout + browser.stderr, flush=True)
    assert browser.returncode == 0, 'Browser regression failed; see browser.log'
    after = {'own':snapshot(),'shared':protected_snapshot(base)}
    write('storage-after', after)
    assert before == after
    summary.update(passed=True, browser=json.loads((REPORT / 'browser.json').read_text(encoding='utf-8')),
        storage_fingerprints_unchanged=True, shared_test_unchanged=True, domain_tables_still_empty=True)
finally:
    if engine is not None:
        if guest:
            with engine.begin() as conn: conn.execute(sa.text('DELETE FROM api_keys WHERE key_hash=:h'), {'h':hash_key(guest)})
        current_state = rt.load_state()
        if not state or current_state.get('instance') != state['instance']:
            raise RuntimeError('Runtime ownership changed; do not stop another instance.')
        rt.stop(state, engine)
        with engine.connect() as conn: enabled = conn.exec_driver_sql('SELECT COUNT(*) FROM api_keys WHERE enabled=1').scalar_one()
        assert enabled == 0
        engine.dispose()
        summary.update(server_stopped=True, enabled_keys=enabled, key_file_removed=not (rt.ROOT / 'api-key.txt').exists())
    else:
        sys.argv = ['legacy_demo_runtime','stop']; rt.main()
    if profile_dir.exists():
        assert profile_dir.resolve().parent == REPORT.resolve() and profile_dir.name == 'edge-profile'
        for attempt in range(20):
            try: shutil.rmtree(profile_dir); break
            except PermissionError: time.sleep(.25)
        assert not profile_dir.exists(), 'Owned Edge profile not cleaned'
    summary['edge_profile_removed'] = not profile_dir.exists()
    write('summary', summary)
print(json.dumps(summary, ensure_ascii=False))


