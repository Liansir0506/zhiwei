"""Windowless startup for the same approved business profile plus training UI."""
from datetime import datetime,timezone
from scripts import training_exercise_runtime as runtime
from scripts.readonly_workspace_bootstrap import WindowlessCommands
from scripts.platform_entry import Dependencies
from scripts.prepare_legacy_projection import write


def main():
    runtime.base.core.subprocess=WindowlessCommands()
    try:
        Dependencies().local_engine()
        runtime.select()
        with runtime.base.core.lifecycle_lock():
            if runtime.base.core.inspect().get('status')!='running':runtime.base.core.prepare_storage()
            result=runtime.start()
        write(runtime.base.core.ROOT/'exercise-startup.json',dict(status=result['status'],at_utc=datetime.now(timezone.utc).isoformat()))
    except Exception as error:
        write(runtime.base.core.ROOT/'exercise-startup.json',dict(status='failed',error_kind=type(error).__name__,at_utc=datetime.now(timezone.utc).isoformat()))


if __name__=='__main__':main()
