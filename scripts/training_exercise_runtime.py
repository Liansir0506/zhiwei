"""Own the same business entry, upgrading presentation with local AMF training."""
import argparse
import json
from contextlib import contextmanager
from scripts import training_business_runtime as base
from scripts.preview_recovery_runtime import guard_pid_reuse

BASE_CODE=base.CODE
EXTRA=('qa_core/presentation/amf_training.py','static/training-exercise-bridge.js','static/training-exercise-bridge.css',
       'scripts/training_exercise_runtime.py','scripts/training_exercise_server.py',
       'scripts/training_exercise_bootstrap.py','scripts/start_training_workspace.ps1')


def configure(upgraded=True):
    base.CODE=BASE_CODE+EXTRA if upgraded else BASE_CODE
    if not getattr(base.core.owned_process,'_training_pid_guard',False):
        guarded=guard_pid_reuse(base.core.owned_process);guarded._training_pid_guard=True;base.core.owned_process=guarded


def select():
    state=base.core.state()
    configure(bool(state and EXTRA[0] in state.get('presentation_bindings',{})))
    return state


def status():
    select();result=base.status();result['training_module']=base.CODE!=BASE_CODE
    return result


def stop():select();return base.stop()


@contextmanager
def upgraded_launcher():
    original=base.subprocess.Popen
    def launch(args,*positional,**kwargs):
        if isinstance(args,list) and len(args)>2 and args[1:3]==['-c','from scripts.training_business_server import main; main()']:
            args=list(args);args[2]='from scripts.training_exercise_server import main; main()'
        return original(args,*positional,**kwargs)
    base.subprocess.Popen=launch
    try:yield
    finally:base.subprocess.Popen=original


def start():
    from scripts.prepare_amf_sop_exercise import validate
    validate()
    state=select()
    if state and state.get('status')!='stopped':
        if base.CODE!=BASE_CODE and base.status()['status']=='running':return status()
        base.stop()
    configure()
    with upgraded_launcher():base.start()
    return status()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status','stop']);args=parser.parse_args()
    if args.action=='status':result=status()
    else:
        with base.core.lifecycle_lock():result={'start':start,'stop':stop}[args.action]()
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
