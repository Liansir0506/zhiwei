"""Recover approved previews after PID reuse without touching unrelated processes."""
import argparse
import json
import psutil


def guard_pid_reuse(original):
    def checked(state):
        if state and state.get('pid'):
            try:
                process = psutil.Process(state['pid'])
                # A different creation time proves the recorded process is gone.
                # Do not inspect, terminate or adopt the replacement process.
                if abs(process.create_time() - state['created_at']) > .01:
                    return None
            except psutil.NoSuchProcess:
                return None
        # Same-generation command/cwd/instance mismatches remain hard failures.
        return original(state)
    return checked


def runtime_for(target):
    if target == 'guide-r1':
        from scripts import sop_guide_support_runtime as runtime
    elif target == 'guide-r2':
        from scripts import sop_record_topic_runtime as runtime
    else:
        raise ValueError('Unknown approved preview')
    if not getattr(runtime.core.owned_process, '_pid_reuse_guard', False):
        guarded = guard_pid_reuse(runtime.core.owned_process)
        guarded._pid_reuse_guard = True
        runtime.core.owned_process = guarded
    return runtime


def execute(target, action):
    runtime = runtime_for(target)
    if action == 'start':
        if runtime.status()['status'] != 'running':
            runtime.stop()
        return runtime.start()
    return {'status': runtime.status, 'stop': runtime.stop}[action]()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['guide-r1', 'guide-r2'])
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    args = parser.parse_args()
    print(json.dumps(execute(args.target, args.action), ensure_ascii=True))
