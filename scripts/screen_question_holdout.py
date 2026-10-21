"""Screen one explicitly provided hash-only human handoff; never freeze or run.

No private directory discovery, question/gold files, credentials or model access.
Results go to a new dedicated screen directory, never into a sealed candidate.
"""
import argparse
import json
from pathlib import Path
import uuid

from qa_core.quality.question_holdout import screen
from scripts.prepare_question_holdout import ROOT, OUTPUT, PREVIOUS, VERSION, validate
from scripts.prepare_legacy_projection import read, write, seal, digest


def run(manifest=None):
    validate()
    path = Path(manifest).absolute() if manifest else OUTPUT/'intake-manifest-template.json'
    if path.suffix.lower() != '.json' or not path.is_file() or path.resolve() != path or path.stat().st_size > 2*1024*1024:
        raise ValueError('explicit regular bounded JSON commitment manifest required')
    # Parse only the caller-designated metadata, never neighbouring/private files.
    payload = read(path)
    b = read(OUTPUT/'baseline-binding.json')
    result = screen(payload,read(PREVIOUS/'exposure-registry.json'),read(OUTPUT/'source-roster.json'),
                    VERSION,b['binding_sha256'])
    root = ROOT/'.runtime/quality/question-intake/screens'
    if root.resolve() != root:
        raise ValueError('unsafe screen output root')
    destination = root/uuid.uuid4().hex
    destination.mkdir(parents=True,exist_ok=False)
    write(destination/'screen.json',dict(**result,input_file_sha256=digest(path),
        preparation_seal_sha256=digest(OUTPUT/'artifact-hashes.json'),
        note='hash/link screen only; role aliases do not authenticate human custody or truth'))
    seal(destination)
    return dict(state=result['state'],case_count=result['case_count'],
                blocked_case_count=len(result['blocked_cases']),run_authorized=False,
                frozen=0,quality_metrics=None,report=str(destination/'screen.json'))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',help='Only hash commitment JSON, never private questions or gold')
    args=parser.parse_args()
    print(json.dumps(run(args.manifest),ensure_ascii=True))
