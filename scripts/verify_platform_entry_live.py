"""Controlled own-entry interruption/recovery; storage and content remain intact."""
import json
from pathlib import Path
import time
from scripts import platform_entry as entry
from scripts.prepare_legacy_projection import write, digest

REPORT = entry.PROJECT / '.runtime/test/reports/platform-entry-p0-20261012'


def rows():
    return [json.loads(line) for line in (entry.ROOT / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]


def wait_observation(since, status, timeout=80):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = [row for row in rows()[since:] if row['status'] == status and 'consecutive_failures' in row]
        if observed:
            return observed[-1]
        time.sleep(2)
    raise RuntimeError('background observer did not record expected state: ' + status)


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    probe = entry.RuntimeProbe()
    before = probe.sample()
    assert before['status'] == 'ready' and entry.observer_process() is not None
    profile_before = digest(entry.PROJECT / '.runtime/original-local/profile.json')
    selection_before = digest(entry.PROJECT / '.runtime/original-local/content-quality-release-selection.json')
    budget_path = entry.PROJECT / '.runtime/original-local/generation-validation-budget.json'
    budget_before = digest(budget_path)
    start = len(rows())
    # The recorded owned service is stopped using its existing identity-checked command.
    result = entry.run(['powershell.exe', '-NoProfile', '-File', str(entry.PROJECT / 'scripts/stop_content_quality.ps1')], timeout=90)
    assert result.returncode == 0
    print('Owned entry stopped; waiting for actual background failure observation.', flush=True)
    try:
        interrupted = wait_observation(start, 'unavailable')
        write(REPORT / 'interruption.json', interrupted)
    finally:
        entry.prepare()
    recovered = wait_observation(start, 'ready', timeout=100)
    print('Owned entry recovered; collecting three consecutive background samples.', flush=True)
    baseline = len(rows())
    deadline = time.monotonic() + 160
    while time.monotonic() < deadline:
        subsequent = [row for row in rows()[baseline:] if 'consecutive_failures' in row]
        if len(subsequent) >= 3:
            break
        time.sleep(2)
    assert len(subsequent) >= 3 and all(row['status'] == 'ready' for row in subsequent)
    assert digest(entry.PROJECT / '.runtime/original-local/profile.json') == profile_before
    assert digest(entry.PROJECT / '.runtime/original-local/content-quality-release-selection.json') == selection_before
    assert digest(budget_path) == budget_before
    summary = dict(before=before, recovered=recovered, stable_samples=subsequent,
                   observed_interruption=True, background_observer_owned=True,
                   same_profile_and_selection=True, model_budget_unchanged=True, paid_calls=0,
                   docker_off_and_preheat='fault-injected unit scenarios; actual Docker engine not stopped',
                   limitation='short continuous observation and one controlled own-entry recovery; not a long-term SLA')
    write(REPORT / 'live-result.json', summary)
    write(REPORT / 'observations.json', rows()[start:])
    print(json.dumps({'observed_interruption': True, 'stable_background_samples': len(subsequent), 'paid_calls': 0}))


if __name__ == '__main__':
    main()
