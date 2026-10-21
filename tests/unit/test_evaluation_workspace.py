"""Attribution, partial denominators and zero-model control boundaries."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.api import route_admin as api
from qa_core.quality import evaluation_workspace as ws


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, 'PROJECT', tmp_path)
    monkeypatch.setattr(ws, 'WORKSPACE', tmp_path / 'writable')
    monkeypatch.setattr(ws, 'LOCK', threading.Lock())
    settings = SimpleNamespace(reports_dir=str(tmp_path / 'sealed'))
    return tmp_path, settings


def save(root, name, value):
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def test_history_missing_version_and_false_independence_are_not_promoted(workspace):
    root, settings = workspace
    save(root / 'reports', 'eval_current.json', {'results': [], 'passed': True, 'independent_verified': True})
    save(root / 'reports', 'eval_old.json', {'kb_version': 'old', 'results': []})
    save(root / 'sealed', 'quality_current.json', {'kb_version': 'current', 'checks': [{'check': 'sop_integrity', 'passed': True, 'sop_sources': 200}]})
    rows, warnings = ws.reports(settings, 'current')
    by_name = {row['file']: row for row in rows}
    assert by_name['eval_current.json']['kb_version'] is None
    assert by_name['eval_current.json']['attribution'] == 'unattributed'
    assert by_name['eval_old.json']['attribution'] == 'historical'
    assert by_name['quality_current.json']['kind'] == 'publication_gate'
    assert by_name['quality_current.json']['sample_count'] == 0
    assert all(row['independent_verified'] is False for row in rows)
    assert not warnings


def test_conflicting_version_and_bad_json_are_visible_without_false_current(workspace):
    root, settings = workspace
    save(root / 'reports', 'eval_conflict.json', {'kb_version': 'current', 'snapshot': {'kb_version': 'old'}})
    broken = root / 'reports/eval_broken.json'; broken.write_text('{bad', encoding='utf-8')
    rows, warnings = ws.reports(settings, 'current')
    assert rows[0]['attribution'] == 'conflict' and rows[0]['kb_version'] is None
    assert warnings[0]['file'] == 'eval_broken.json'


def test_denominators_p95_and_categories_exclude_unknowns(workspace):
    root, settings = workspace
    save(root / 'reports', 'eval_partial.json', {'kb_version': 'current', 'results': [
        {'category': 'faq', 'route_ok': True, 'hit_ok': None, 'elapsed_ms': 10},
        {'category': 'faq', 'route_ok': 'yes', 'hit_ok': False, 'elapsed_ms': True},
        {'category': 'faq', 'route_ok': False, 'hit_ok': True, 'elapsed_ms': -2}]})
    row = ws.reports(settings, 'current')[0][0]
    assert row['checks']['route_ok'] == {'passed': 1, 'evaluated': 2, 'ratio': .5}
    assert row['checks']['hit_ok']['evaluated'] == 2
    assert row['checks']['reject_ok']['ratio'] is None
    assert row['latency'] == {'sample_count': 1, 'p95_ms': 10}
    assert row['by_category']['faq'] == {'total': 3, 'pass': 1, 'evaluated': 2}


def test_report_id_changes_with_bytes_and_never_uses_client_paths(workspace):
    root, settings = workspace
    path = save(root / 'reports', 'eval_fixed.json', {'kb_version': 'current'})
    first = ws.reports(settings, 'current')[0][0]['id']
    path.write_text('{"kb_version":"old"}', encoding='utf-8')
    assert ws.reports(settings, 'current')[0][0]['id'] != first
    with pytest.raises(KeyError): ws.read_record('plans', '../sealed/file')


@pytest.fixture
def plan_fixture(workspace, monkeypatch):
    root, settings = workspace
    monkeypatch.setattr(ws, 'binding', lambda settings: 'bound')
    docs = [SimpleNamespace(id='faq1', question='known question', answer='known answer', source='faq.csv', source_id='approved1')]
    usecase = SimpleNamespace(searcher=SimpleNamespace(recommended_faqs=lambda *a, **kw: docs))
    actor = AuthContext('admin-a', ['admin'], 'api_key')
    return settings, usecase, actor


@pytest.mark.parametrize('reason', ['actor', 'scope', 'version', 'expiry', 'binding', 'dataset', 'replay', 'tampered_cases', 'mode'])
def test_stale_or_foreign_plan_cannot_start(plan_fixture, monkeypatch, reason):
    settings, usecase, actor = plan_fixture
    plan = ws.prepare(settings, usecase, 'current', actor)
    record = ws.read_record('plans', plan['id']); version = 'current'
    if reason == 'actor': actor.user_id = 'other'
    if reason == 'scope': actor.dept_ids = ['narrow']
    if reason == 'version': version = 'changed'
    if reason == 'expiry': record['expires_at'] = 0
    if reason == 'binding': record['binding_sha256'] = 'changed'
    if reason == 'dataset': record['dataset_sha256'] = 'changed'
    if reason == 'tampered_cases': record['cases'][0]['query'] = 'new unreviewed question'
    if reason == 'mode': record['mode'] = 'deepseek_generation'
    if reason == 'replay': ws.write(ws.WORKSPACE / 'jobs' / (plan['id'] + '.json'), {})
    ws.write(ws.WORKSPACE / 'plans' / (plan['id'] + '.json'), record)
    runner = Mock(); monkeypatch.setattr(ws.threading, 'Thread', runner)
    with pytest.raises(ValueError): ws.execute(settings, usecase, version, actor, plan['id'])
    runner.assert_not_called()
    assert not ws.LOCK.locked()


def test_bound_faq_size_and_source_identity(plan_fixture):
    settings, usecase, actor = plan_fixture
    original = usecase.searcher.recommended_faqs
    usecase.searcher.recommended_faqs = lambda *a, **kw: original() * 41
    with pytest.raises(ValueError): ws.prepare(settings, usecase, 'current', actor)


def test_interrupted_job_is_not_resubmitted(workspace):
    ident = 'a' * 32
    ws.write(ws.WORKSPACE / 'jobs' / (ident + '.json'), {'id': ident, 'state': 'running', 'instance': 'old-process'})
    assert ws.job_state(ident)['state'] == 'interrupted'
    assert 'instance' not in ws.job_state(ident)


def test_windows_transient_replace_sharing_does_not_fail_job(workspace, monkeypatch):
    from pathlib import Path
    real_replace = Path.replace
    attempts = []
    def shared_replace(path, target):
        attempts.append(1)
        if len(attempts) <= 2:
            raise PermissionError('simulated Windows sharing violation')
        return real_replace(path, target)
    monkeypatch.setattr(Path, 'replace', shared_replace)
    monkeypatch.setattr(ws.time, 'sleep', lambda seconds: None)
    ident = 'b' * 32
    ws.write(ws.WORKSPACE / 'jobs' / (ident + '.json'), {'id': ident, 'state': 'completed'})
    assert len(attempts) == 3 and ws.job_state(ident)['state'] == 'completed'


def test_model_is_forbidden():
    with pytest.raises(RuntimeError): ws.ForbiddenModel().chat('must not send')


def test_whole_body_contract_restores_footnotes_without_hiding_content_loss():
    expected = '核对链路[1]。\n记录时间[10]和编号[100]。'
    actual = '【结论】依据标准排查 FAQ：\n核对链路〔原文注1〕。\n记录时间〔原文注10〕和编号[100]。\n【引用】[1] faq.csv'
    assert ws.faq_body_intact(actual, expected, 'faq.csv')
    assert not ws.faq_body_intact(actual.replace('记录时间', '时间'), expected, 'faq.csv')
    assert not ws.faq_body_intact(actual.replace('。\n记录', '。记录'), expected, 'faq.csv')
    assert not ws.faq_body_intact(actual, expected, 'unrelated.csv')


def test_worker_failure_is_durable_releases_slot_and_redacts_details(plan_fixture, monkeypatch):
    settings, live, actor = plan_fixture
    plan = ws.read_record('plans', ws.prepare(settings, live, 'current', actor)['id'])
    state = {'id': plan['id'], 'state': 'queued', 'instance': ws.INSTANCE}
    from qa_core.application import qa_usecase
    monkeypatch.setattr(qa_usecase, 'QaUseCase', Mock(side_effect=RuntimeError('private credential must not appear')))
    ws.LOCK.acquire(); ws._run(settings, live, plan, state)
    saved = ws.job_state(plan['id'])
    assert saved['state'] == 'failed' and 'private' not in saved['error']
    assert not ws.LOCK.locked()
    assert (ws.WORKSPACE / 'failures' / (plan['id'] + '.json')).exists()


@pytest.fixture
def client(workspace, monkeypatch):
    _, settings = workspace
    ctx = AuthContext('test-admin', ['admin'], 'api_key')
    app = FastAPI(); app.include_router(api.router); app.dependency_overrides[get_auth_context] = lambda: ctx
    monkeypatch.setattr(api, '_evaluation_versions', lambda: [{'version': 'current', 'status': 'ACTIVE'}])
    monkeypatch.setattr(api, 'get_settings', lambda: settings)
    monkeypatch.setattr(api, 'require_roles', lambda ctx, roles: (_ for _ in ()).throw(__import__('fastapi').HTTPException(403)) if not set(ctx.roles) & set(roles) else None)
    # Keep tenant and reader gates real; avoid writing audit records to nonexistent test DB.
    monkeypatch.setattr(api, 'require_global_admin', lambda ctx: api._require_global_reader(ctx, ('admin',)))
    return TestClient(app), ctx


@pytest.mark.parametrize('roles,tenant', [(['ops'], 'default'), (['admin'], 'other')])
def test_report_scope_cannot_expand(client, roles, tenant):
    c, ctx = client; ctx.roles = roles; ctx.tenant_id = tenant
    assert c.get('/admin/eval/reports').status_code == 403
    assert c.get('/admin/eval/reports/' + 'a'*32).status_code == 403
    assert c.post('/admin/eval/plans').status_code == 403
    assert c.get('/admin/eval/jobs/' + 'a'*32).status_code == 403


def test_auditor_read_only_and_current_gap(client):
    c, ctx = client; ctx.roles = ['auditor']
    data = c.get('/admin/eval/reports').json()
    assert data['active_version'] == 'current' and not data['independent_verified'] and not data['can_run']
    assert c.post('/admin/eval/plans').status_code == 403
    assert c.post('/admin/eval/jobs', json={'plan_id': 'a'*32}).status_code == 403


def test_no_arbitrary_paths_or_model_mode_and_narrow_admin(client):
    c, ctx = client
    assert c.post('/admin/eval/jobs', json={'plan_id': '../sealed'}).status_code == 422
    assert c.post('/admin/eval/jobs', json={'plan_id': 'a'*32, 'use_llm': True}).status_code == 422
    ctx.dept_ids = ['narrow']
    assert c.post('/admin/eval/plans').status_code == 403
    assert not c.get('/admin/eval/reports').json()['can_run']
