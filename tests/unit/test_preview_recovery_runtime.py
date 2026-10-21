from types import SimpleNamespace
import pytest
from scripts import preview_recovery_runtime as recovery


def test_reused_pid_never_delegates_to_original_or_touches_replacement(monkeypatch):
    monkeypatch.setattr(recovery.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 50))
    def forbidden(state):
        pytest.fail('Replacement process must not reach old identity/termination code')
    assert recovery.guard_pid_reuse(forbidden)({'pid': 12, 'created_at': 10}) is None


def test_missing_process_is_stale(monkeypatch):
    def missing(pid): raise recovery.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(recovery.psutil, 'Process', missing)
    assert recovery.guard_pid_reuse(lambda state: pytest.fail('Gone process'))({'pid': 12, 'created_at': 10}) is None


def test_same_generation_keeps_strict_identity_failure(monkeypatch):
    monkeypatch.setattr(recovery.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 10))
    def mismatch(state): raise ValueError('Same-generation identity mismatch')
    with pytest.raises(ValueError, match='identity mismatch'):
        recovery.guard_pid_reuse(mismatch)({'pid': 12, 'created_at': 10})


def test_live_owned_process_preserved(monkeypatch):
    monkeypatch.setattr(recovery.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 10))
    owned = object()
    assert recovery.guard_pid_reuse(lambda state: owned)({'pid': 12, 'created_at': 10}) is owned


def test_access_denied_cannot_be_treated_as_gone(monkeypatch):
    def denied(pid): raise recovery.psutil.AccessDenied(pid)
    monkeypatch.setattr(recovery.psutil, 'Process', denied)
    with pytest.raises(recovery.psutil.AccessDenied):
        recovery.guard_pid_reuse(lambda state: None)({'pid': 12, 'created_at': 10})
