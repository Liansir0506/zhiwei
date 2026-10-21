"""Hash-only intake screen including later selfcheck exposure. No freeze or QA."""
import argparse
import json
from pathlib import Path
import uuid

from qa_core.quality.question_holdout import screen
from qa_core.quality.development_exposure import include_overlays
from scripts.prepare_question_holdout import ROOT,OUTPUT,PREVIOUS,VERSION,validate
from scripts.prepare_legacy_projection import read,write,seal,digest


def run(manifest=None):
    validate()
    path=Path(manifest).absolute() if manifest else OUTPUT/'intake-manifest-template.json'
    if path.suffix.lower()!='.json' or not path.is_file() or path.resolve()!=path or path.stat().st_size>2*1024*1024:
        raise ValueError('explicit regular bounded hash-only JSON required')
    payload=read(path);preparation=digest(OUTPUT/'artifact-hashes.json')
    registry,overlays=include_overlays(read(PREVIOUS/'exposure-registry.json'),
        ROOT/'.runtime/quality/question-intake/development-exposure',ROOT,preparation)
    b=read(OUTPUT/'baseline-binding.json')
    result=screen(payload,registry,read(OUTPUT/'source-roster.json'),VERSION,b['binding_sha256'])
    root=ROOT/'.runtime/quality/question-intake/screens-v2'
    if root.resolve()!=root:raise ValueError('unsafe result root')
    destination=root/uuid.uuid4().hex;destination.mkdir(parents=True,exist_ok=False)
    write(destination/'screen.json',dict(**result,input_file_sha256=digest(path),
        preparation_seal_sha256=preparation,development_overlays=overlays,
        exposure_coverage_complete=False,note='later exposure included; human custody, truth and semantics remain unverified'))
    seal(destination)
    return dict(state=result['state'],case_count=result['case_count'],development_overlay_count=len(overlays),
        run_authorized=False,frozen=0,quality_metrics=None,report=str(destination/'screen.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest');args=parser.parse_args()
    print(json.dumps(run(args.manifest),ensure_ascii=True))
