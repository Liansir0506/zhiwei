"""Diagnostic wrapper around the unchanged approved lifecycle controller."""
import argparse
import contextlib
import io
import os
import sys
import time
import traceback
import psutil
from scripts.platform_entry import ROOT, stamp, write_json


def execute(token):
    started = time.monotonic()
    state = {'token': token, 'pid': os.getpid(), 'created_at': psutil.Process().create_time(),
             'started_at_utc': stamp(), 'status': 'starting'}
    def phase(name, **extra):
        state.update(phase=name, elapsed_seconds=round(time.monotonic() - started, 2), **extra)
        write_json(ROOT / 'bootstrap.json', state)
        with (ROOT / 'bootstrap-events.jsonl').open('a', encoding='utf-8') as log:
            import json
            log.write(json.dumps(state, ensure_ascii=False) + '\n')
    phase('validate_bindings')
    try:
        from scripts import demo_release_runtime_r2 as release
        release.install_adapter()
        verify = release.owned.verify_active
        launch = release.owned.launch
        def verified(*args, **kwargs):
            phase('verify_active_snapshot')
            result = verify(*args, **kwargs)
            phase('active_snapshot_verified')
            return result
        def launched(*args, **kwargs):
            phase('application_warmup')
            result = launch(*args, **kwargs)
            phase('application_ready')
            return result
        release.owned.verify_active = verified
        release.owned.launch = launched
        # Native output contains private lifecycle metadata; retain only safe stages.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            release.owned.main()
        phase('completed', status='completed')
        return 0
    except BaseException as error:
        frames = [{'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
                  for frame in traceback.extract_tb(error.__traceback__)]
        phase(state['phase'], status='failed', error_type=type(error).__name__, frames=frames)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token', required=True)
    args = parser.parse_args()
    if len(args.token) != 32 or any(char not in '0123456789abcdef' for char in args.token):
        parser.error('invalid bootstrap token')
    sys.argv = [sys.argv[0], 'start']
    return execute(args.token)


if __name__ == '__main__':
    sys.exit(main())
