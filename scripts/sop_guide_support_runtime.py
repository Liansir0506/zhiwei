"""Upgrade only the owned guide preview through an adapter; sealed v1 files stay unchanged."""
import argparse
import json
from scripts import sop_guide_preview_runtime as core
from scripts.prepare_sop_support_alignment import load_verified

MODULE='scripts.sop_guide_support_server'
EXTRA=('qa_core/quality/sop_support_alignment.py','scripts/prepare_sop_support_alignment.py',
       'scripts/sop_guide_support_runtime.py','scripts/sop_guide_support_server.py',
       'static/guide-support.js','static/guide-support.css')
BASE_MODULE=core.MODULE
BASE_CODE=core.CODE


def configure(upgraded=True):
    core.MODULE=MODULE if upgraded else BASE_MODULE
    core.CODE=BASE_CODE+EXTRA if upgraded else BASE_CODE


def select_record():
    record=core.record()
    configure(bool(record and 'scripts/sop_guide_support_server.py' in record.get('code_bindings',{})))
    return record


def status():
    select_record();return core.inspect()


def stop():
    select_record();return core.stop()


def start():
    load_verified()  # No process/key mutation if the alignment cannot be verified.
    record=select_record()
    if record and record.get('status')!='stopped':
        inspected=core.inspect()
        if core.MODULE==MODULE and inspected['status']=='running':return inspected
        if core.MODULE!=BASE_MODULE:raise ValueError('inspect and stop owned changed upgrade before restart')
        core.stop()
    configure();return core.start()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status','stop']);args=parser.parse_args()
    print(json.dumps({'start':start,'status':status,'stop':stop}[args.action](),ensure_ascii=False))
