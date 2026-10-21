import json
from types import SimpleNamespace
import pytest
from scripts import platform_runtime_bootstrap as bootstrap
from scripts import demo_release_runtime_r2 as release


def fake_lifecycle(monkeypatch, tmp_path, error=None):
    monkeypatch.setattr(bootstrap, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'install_adapter', lambda: None)
    owned = SimpleNamespace(verify_active=lambda: 'published', launch=lambda: 'ready')
    def main():
        assert owned.verify_active() == 'published'
        if error:
            raise error
        assert owned.launch() == 'ready'
        print('private output must not be recorded')
    owned.main = main
    monkeypatch.setattr(release, 'owned', owned)


def test_bootstrap_records_verified_snapshot_and_warmup_stages(monkeypatch, tmp_path):
    fake_lifecycle(monkeypatch, tmp_path)
    assert bootstrap.execute('a' * 32) == 0
    text = (tmp_path / 'bootstrap-events.jsonl').read_text(encoding='utf-8')
    phases = [json.loads(line)['phase'] for line in text.splitlines()]
    assert phases == ['validate_bindings', 'verify_active_snapshot', 'active_snapshot_verified',
                      'application_warmup', 'application_ready', 'completed']
    assert 'private output' not in text


def test_bootstrap_failure_keeps_stage_without_secret_exception_text(monkeypatch, tmp_path):
    fake_lifecycle(monkeypatch, tmp_path, error=ValueError('private-password://secret'))
    assert bootstrap.execute('b' * 32) == 1
    text = (tmp_path / 'bootstrap.json').read_text(encoding='utf-8')
    state = json.loads(text)
    assert state['status'] == 'failed' and state['phase'] == 'active_snapshot_verified'
    assert state['error_type'] == 'ValueError' and 'private-password' not in text
