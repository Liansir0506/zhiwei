"""Bounded live observations of the windowless owned runtimes; no paid requests."""
from datetime import datetime, timezone
import json
import time
import ctypes
from ctypes import wintypes
import psutil
import httpx
from scripts import content_quality_release_runtime as current
from scripts import readonly_workspace_runtime as readonly
from scripts import platform_entry
from scripts.prepare_legacy_projection import write
from qa_core.ingest.workspace import worker_status


def gui_process(pid):
    process = psutil.Process(pid)
    assert process.name().lower() == 'pythonw.exe', 'Owned runtime is not windowless'
    descendants = process.children(recursive=True)
    names = [p.name().lower() for p in descendants]
    owned_ids = {pid, *(p.pid for p in descendants)}
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    visible = []
    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in owned_ids and user32.IsWindowVisible(hwnd):
            visible.append(owner.value)
        return True
    user32.EnumWindows(visit, 0)
    assert not visible, 'An owned background process has a visible window'
    return {'pid': pid, 'created_at': process.create_time(), 'name': process.name(),
            'descendant_names': names, 'visible_owned_windows': 0,
            'note': 'Hidden console hosts may exist for CREATE_NO_WINDOW child commands; visible owned windows are checked separately'}


def main():
    report = current.PROJECT / '.runtime/test/reports/version-workspace-p2-20261012'
    current.install_adapter()
    current.owned.apply_profile(current.selected_settings())
    key = current.owned.read_key(current.owned.load_state())
    samples = []
    initial = None
    for index in range(4):
        services = {'original': current.owned.inspect(), 'isolated': readonly.inspect(),
                    'worker': worker_status()}
        assert services['original']['status'] == services['isolated']['status'] == 'running'
        assert services['worker']['status'] == 'ready'
        observer = platform_entry.observer_process()
        assert observer is not None
        pids = {name: state['pid'] for name, state in services.items()}
        pids['observer'] = observer.pid
        if initial is None:
            initial = pids
        assert pids == initial, 'An owned process changed during the observation window'
        sample = {'at': datetime.now(timezone.utc).isoformat(), 'processes': {
            name: gui_process(pid) for name, pid in pids.items()}, 'health': []}
        for url in ('http://127.0.0.1:8010', 'http://localhost:8010', 'http://127.0.0.1:18021'):
            response = httpx.get(url + '/health', timeout=15, trust_env=False,
                                headers={'X-API-Key': key} if '8010' in url else {})
            response.raise_for_status()
            data = response.json()
            assert data['status'] == 'ok'
            assert data['generation_enabled'] == ('8010' in url)
            assert data['agent_enabled'] == ('18021' in url)
            sample['health'].append({'url': url, 'status': data['status'],
                                    'generation_enabled': data['generation_enabled'],
                                    'agent_enabled': data['agent_enabled']})
        samples.append(sample)
        write(report / 'survival.json', {'passed': index == 3, 'samples': samples,
              'scope': 'Four observations 20 seconds apart; not a long-term uptime SLA or visual confirmation of existing user windows',
              'owned_processes_windowless': True, 'paid_calls': 0})
        print(json.dumps({'sample': index + 1, 'status': 'healthy', 'owned_processes_windowless': True}), flush=True)
        if index < 3:
            time.sleep(20)


if __name__ == '__main__':
    main()
