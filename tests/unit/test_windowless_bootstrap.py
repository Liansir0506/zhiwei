"""Prevent background dependency commands from opening a Windows console."""
import subprocess
from types import SimpleNamespace
import pytest
from scripts import readonly_workspace_bootstrap as bootstrap


def test_dependency_command_hides_window_and_preserves_options(monkeypatch):
    calls = []
    sentinel = object()
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel
    monkeypatch.setattr(bootstrap, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, 'run', run)
    proxy = bootstrap.WindowlessCommands()
    assert proxy.run(['docker', 'info'], capture_output=True, timeout=10, creationflags=8) is sentinel
    assert calls == [((['docker', 'info'],), dict(capture_output=True, timeout=10, creationflags=0x08000008))]
    assert proxy.Popen is subprocess.Popen
    assert subprocess.run is run


def test_dependency_failure_is_not_swallowed(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)
    monkeypatch.setattr(subprocess, 'run', failed)
    with pytest.raises(subprocess.TimeoutExpired):
        bootstrap.WindowlessCommands().run(['docker', 'info'], timeout=10)


def test_bootstrap_only_replaces_runtime_reference(monkeypatch):
    shared = subprocess.run
    observed = []
    monkeypatch.setattr(bootstrap.runtime, 'subprocess', subprocess)
    monkeypatch.setattr(bootstrap.runtime, 'main', lambda: observed.append(bootstrap.runtime.subprocess))
    bootstrap.main()
    assert len(observed) == 1 and isinstance(observed[0], bootstrap.WindowlessCommands)
    assert subprocess.run is shared
