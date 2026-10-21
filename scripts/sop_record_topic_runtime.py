"""Upgrade the owned r2 preview only; baseline code and original previews are untouched."""
import argparse
import json
from scripts import sop_record_preview_runtime as core
from scripts.prepare_sop_record_alignment_v2 import load_verified
from scripts.prepare_sop_record_admission import load_verified as admission

MODULE='scripts.sop_record_topic_server'
EXTRA=('qa_core/retrieval/record_preview_topic.py','scripts/sop_record_topic_runtime.py','scripts/sop_record_topic_server.py')
BASE_MODULE=core.MODULE
BASE_CODE=core.CODE


def configure(upgraded=True):
    core.MODULE=MODULE if upgraded else BASE_MODULE
    core.CODE=BASE_CODE+EXTRA if upgraded else BASE_CODE


def select_record():
    state=core.record()
    configure(bool(state and 'scripts/sop_record_topic_server.py' in state.get('code_bindings',{})))
    return state


def status():
    select_record();return core.inspect()


def stop():
    select_record();return core.stop()


def start():
    load_verified();admission()
    state=select_record()
    if state and state['status']!='stopped':
        current=core.inspect()
        if core.MODULE==MODULE and current['status']=='running':return current
        core.stop()
    configure();return core.start()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status','stop']);args=parser.parse_args()
    print(json.dumps({'start':start,'status':status,'stop':stop}[args.action](),ensure_ascii=True))
