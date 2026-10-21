"""Recover local runtime records whose recorded PID was recycled by Windows.

A Windows PID can only be handed to a new process after the recorded process has
exited, and a live process keeps its create_time forever. So a create_time
mismatch on the recorded PID proves the recorded process is gone; the record is
factually stale and the approved stop/start path must be allowed to run.

This module rewrites only such provably stale records. It never terminates or
signals any process, never alters a record whose recorded identity is still
live, and refuses to touch the application record while port 8010 still answers
(an answering port with a recycled PID would mean an unowned service holds the
owned port, which needs a human decision). It is not part of any release code
binding; the frozen lifecycle functions stay untouched.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import socket
import sys

import psutil

PROJECT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT / '.runtime/test/reports'
APP_RECORD = PROJECT / '.runtime/original-local/state.json'
OBSERVER_RECORD = PROJECT / '.runtime/platform-entry/observer.json'
APP_PORT = 8010


def stamp():
    return datetime.now(timezone.utc).isoformat()


def report():
    path = REPORT_ROOT / ('runtime-stale-recovery-' + datetime.now().strftime('%Y%m%d'))
    path.mkdir(parents=True, exist_ok=True)
    return path


def classify(record):
    """Return 'absent' | 'recycled' | 'owned'; touches no process state."""
    pid = record.get('pid')
    if not pid:
        return 'absent'
    created = record.get('created_at')
    if created is None:
        return 'owned'
    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return 'absent'
        create_time = process.create_time()
    except psutil.NoSuchProcess:
        return 'absent'
    except psutil.Error:
        return 'owned'
    return 'recycled' if abs(create_time - created) > .01 else 'owned'


def port_answers(port, timeout=1.5):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


def live_pid_facts(record):
    try:
        process = psutil.Process(record.get('pid'))
        return dict(pid=process.pid, name=process.name(), create_time=process.create_time())
    except psutil.Error:
        return None


def recover_record(path, *, guard_port=None, mark=None):
    """Rewrite one record only when its recorded PID was provably recycled."""
    path = Path(path)
    if not path.exists():
        return {'record': str(path), 'action': 'missing'}
    try:
        original = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {'record': str(path), 'action': 'unreadable'}
    if not isinstance(original, dict) or original.get('status') in (None, 'stopped'):
        return {'record': str(path), 'action': 'idle', 'status': original.get('status') if isinstance(original, dict) else None}
    kind = classify(original)
    if kind != 'recycled':
        return {'record': str(path), 'action': 'idle', 'kind': kind}
    if guard_port is not None and port_answers(guard_port):
        raise RuntimeError('port ' + str(guard_port) + ' still answers; refusing to treat the record as stale')
    record = dict(original)
    recycled = record.pop('pid')
    record['stale_recovered_at_utc'] = stamp()
    record['stale_recycled_pid'] = recycled
    if mark == 'stopped':
        record['status'] = 'stopped'
        record['stopped_at_utc'] = record['stale_recovered_at_utc']
    evidence = {'action': 'recovered', 'record': str(path), 'mark': mark, 'guard_port': guard_port,
                'reason': 'recorded PID was recycled; the recorded process had exited',
                'recycled_pid': recycled, 'recycled_pid_now': live_pid_facts(original),
                'recorded_created_at': original.get('created_at'),
                'recovered_at_utc': record['stale_recovered_at_utc'], 'before': original, 'after': record}
    target = report() / (path.stem + '-recycled-' + datetime.now().strftime('%H%M%S-%f') + '.json')
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
    print('Recovered stale record ' + str(path) + ': recorded PID ' + str(recycled)
          + ' was recycled; evidence kept at ' + str(target.relative_to(PROJECT)) + '.', file=sys.stderr)
    return {'record': str(path), 'action': 'recovered', 'recycled_pid': recycled,
            'evidence': str(target), 'mark': mark}


def recover_platform_records():
    """Recover both owned records that the approved entry scripts read."""
    return {'app': recover_record(APP_RECORD, guard_port=APP_PORT),
            'observer': recover_record(OBSERVER_RECORD, mark='stopped')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', action='store_true', help='only the owned application record')
    parser.add_argument('--observer', action='store_true', help='only the observer record')
    args = parser.parse_args()
    if args.app and args.observer:
        parser.error('choose one of --app or --observer')
    if args.observer:
        result = {'observer': recover_record(OBSERVER_RECORD, mark='stopped')}
    elif args.app:
        result = {'app': recover_record(APP_RECORD, guard_port=APP_PORT)}
    else:
        result = recover_platform_records()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
