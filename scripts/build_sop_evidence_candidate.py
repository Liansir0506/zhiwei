"""After a real recorded decision, materialize new immutable raw input only."""
import argparse
import json
import shutil
from pathlib import Path

from scripts.prepare_sop_evidence_admission import ROOT,OUTPUT, DRAFTS
from scripts.prepare_sop_evidence_drafts import validate as validate_drafts
from scripts.prepare_legacy_projection import read,write,verify_seal,digest,seal
from qa_core.quality.sop_admission import admitted_material,TARGET
from qa_core.ingest.source_identity import validate_mapping

CANDIDATE=ROOT/'.runtime/test/sop-evidence-stage-r1/candidate'


def validate_inputs(receipt_path):
    validate_drafts();verify_seal(OUTPUT)
    plan=read(OUTPUT/'admission-plan.json');receipt=read(receipt_path)
    if plan['source_review_seal_sha256']!=digest(DRAFTS/'artifact-hashes.json'):
        raise ValueError('source preparation drift')
    metadata,mapping=admitted_material(plan,receipt)
    validate_mapping(DRAFTS/'drafts',mapping,metadata)
    return plan,receipt,metadata,mapping


def build(receipt_path):
    plan,receipt,metadata,mapping=validate_inputs(receipt_path)
    # Receipt validation and source-byte checks precede every mutation.
    if CANDIDATE.exists() or CANDIDATE.resolve()!=CANDIDATE or not CANDIDATE.is_relative_to(ROOT/'.runtime/test'):
        raise ValueError('new unlinked test candidate only; never overwrite durable state')
    raw=CANDIDATE/'raw';raw.mkdir(parents=True)
    for entry in plan['entries']:shutil.copyfile(DRAFTS/'drafts'/entry['filename'],raw/entry['filename'])
    write(raw/'meta.json',metadata);write(CANDIDATE/'source-map.json',mapping)
    write(CANDIDATE/'approval-receipt.json',receipt)
    write(CANDIDATE/'projection.json',dict(target=TARGET,source_seal_sha256=plan['source_review_seal_sha256'],files=20,
        stage_intent='STAGED',database_version_created=False,embeddings_created=False,
        independent_quality_approved=False,operational_sop_approved=False,outbound_authorized=False,publication_authorized=False))
    validate_mapping(raw,mapping,metadata);seal(CANDIDATE);verify_seal(CANDIDATE);validate_drafts();verify_seal(OUTPUT)
    return dict(raw_candidate_prepared=True,database_version_created=False,files=20,paid_calls=0,path=str(CANDIDATE))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--receipt',required=True)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if args.execute:print(json.dumps(build(Path(args.receipt).absolute())))
    else:
        validate_inputs(Path(args.receipt).absolute())
        print(json.dumps(dict(ready=True,execution=False,files=20,paid_calls=0)))
