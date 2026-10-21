"""Hidden dependency preparation and training presentation startup."""
import json
from datetime import datetime,timezone
from scripts import training_business_runtime as runtime
from scripts.readonly_workspace_bootstrap import WindowlessCommands
from scripts.prepare_legacy_projection import write


def main():
    runtime.core.subprocess=WindowlessCommands()
    try:
        with runtime.core.lifecycle_lock():
            if runtime.core.inspect().get('status')!='running':runtime.core.prepare_storage()
            result=runtime.start()
        write(runtime.core.ROOT/'training-startup.json',dict(status=result['status'],at_utc=datetime.now(timezone.utc).isoformat()))
    except Exception as error:
        write(runtime.core.ROOT/'training-startup.json',dict(status='failed',error_kind=type(error).__name__,at_utc=datetime.now(timezone.utc).isoformat()))
        raise


if __name__=='__main__':main()
