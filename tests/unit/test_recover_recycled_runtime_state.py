"""Recycled-PID recovery must be provably safe: only stale records change."""
import json
import os
from pathlib import Path

import psutil
import pytest

from scripts import recover_recycled_runtime_state as recovery


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, 'REPORT_ROOT', tmp_path / 'reports')
    return tmp_path


@pytest.fixture
def closed_port():
    listener = __import__('socket').socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


def live_record(status='running'):
    process = psutil.Process(os.getpid())
    return {'status': status, 'pid': process.pid, 'created_at': process.create_time(),
            'instance': 'test-instance', 'url': 'http://127.0.0.1:8010'}


def recycled_record(status='running'):
    process = psutil.Process(os.getpid())
    return {'status': status, 'pid': process.pid, 'created_at': process.create_time() - 3600,
            'instance': 'test-instance', 'url': 'http://127.0.0.1:8010'}


def write_record(path, record):
    path.write_text(json.dumps(record), encoding='utf-8')
    return path


def test_classify_absent_for_missing_and_dead_pids():
    assert recovery.classify({'pid': None}) == 'absent'
    assert recovery.classify({'pid': 99999999, 'created_at': 1.0}) == 'absent'


def test_classify_owned_when_created_at_matches():
    assert recovery.classify(live_record()) == 'owned'


def test_classify_recycled_only_when_created_at_differs():
    assert recovery.classify(recycled_record()) == 'recycled'
    # No recorded create_time is not proof of staleness.
    assert recovery.classify({'pid': os.getpid()}) == 'owned'


def test_live_identity_is_never_rewritten(sandbox, closed_port):
    path = write_record(sandbox / 'state.json', live_record())
    before = path.read_text(encoding='utf-8')
    assert recovery.recover_record(path, guard_port=closed_port) == {'record': str(path), 'action': 'idle', 'kind': 'owned'}
    assert path.read_text(encoding='utf-8') == before
    assert not list((sandbox / 'reports').rglob('*.json'))


def test_stopped_record_is_idle(sandbox):
    path = write_record(sandbox / 'state.json', recycled_record(status='stopped'))
    before = path.read_text(encoding='utf-8')
    assert recovery.recover_record(path, guard_port=8010)['action'] == 'idle'
    assert path.read_text(encoding='utf-8') == before


def test_recycled_record_is_rewritten_with_evidence(sandbox, closed_port):
    record = recycled_record()
    path = write_record(sandbox / 'state.json', record)
    result = recovery.recover_record(path, guard_port=closed_port)
    assert result['action'] == 'recovered' and result['recycled_pid'] == record['pid']
    after = json.loads(path.read_text(encoding='utf-8'))
    assert 'pid' not in after and after['status'] == 'running'
    assert after['stale_recycled_pid'] == record['pid'] and after['stale_recovered_at_utc']
    evidence = json.loads(Path(result['evidence']).read_text(encoding='utf-8'))
    assert evidence['before'] == record and evidence['after'] == after
    assert evidence['recycled_pid_now']['pid'] == record['pid']
    assert not list(Path(str(path)).parent.glob('*.tmp'))


def test_mark_stopped_writes_stopped_record(sandbox):
    path = write_record(sandbox / 'observer.json', recycled_record())
    result = recovery.recover_record(path, mark='stopped')
    after = json.loads(path.read_text(encoding='utf-8'))
    assert result['mark'] == 'stopped' and after['status'] == 'stopped'
    assert after['stopped_at_utc'] == after['stale_recovered_at_utc']


def test_guard_port_refuses_while_port_answers(sandbox):
    listener = __import__('socket').socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        path = write_record(sandbox / 'state.json', recycled_record())
        before = path.read_text(encoding='utf-8')
        with pytest.raises(RuntimeError):
            recovery.recover_record(path, guard_port=port)
        assert path.read_text(encoding='utf-8') == before
        assert not list((sandbox / 'reports').rglob('*.json'))
    finally:
        listener.close()


def test_guard_port_passes_when_closed(sandbox):
    listener = __import__('socket').socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    listener.close()
    path = write_record(sandbox / 'state.json', recycled_record())
    assert recovery.recover_record(path, guard_port=port)['action'] == 'recovered'


def test_missing_and_unreadable_records_are_reported(sandbox):
    assert recovery.recover_record(sandbox / 'absent.json')['action'] == 'missing'
    broken = sandbox / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    assert recovery.recover_record(broken)['action'] == 'unreadable'


def test_recover_platform_records_targets_real_paths(sandbox, monkeypatch, closed_port):
    app = write_record(sandbox / 'state.json', recycled_record())
    observer = write_record(sandbox / 'observer.json', recycled_record())
    monkeypatch.setattr(recovery, 'APP_RECORD', app)
    monkeypatch.setattr(recovery, 'OBSERVER_RECORD', observer)
    monkeypatch.setattr(recovery, 'APP_PORT', closed_port)
    result = recovery.recover_platform_records()
    assert result['app']['action'] == 'recovered' and result['observer']['action'] == 'recovered'
    assert json.loads(observer.read_text(encoding='utf-8'))['status'] == 'stopped'
    assert json.loads(app.read_text(encoding='utf-8'))['status'] == 'running'
