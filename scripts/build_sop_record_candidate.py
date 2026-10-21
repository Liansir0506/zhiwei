"""Project an actual recorded r2 decision into a new candidate; no database writes."""
import argparse
import json
import shutil
from pathlib import Path
from scripts.prepare_sop_record_admission import ROOT, DRAFTS, load_verified
from scripts.sop_record_stage_profile import CANDIDATE
from scripts.prepare_legacy_projection import read, write, seal, verify_seal
from qa_core.quality.sop_record_admission import admitted_material, TARGET
from qa_core.ingest.source_identity import validate_mapping


def validate_inputs(receipt_path):
    plan = load_verified(); receipt = read(receipt_path)
    metadata, mapping = admitted_material(plan, receipt)
    validate_mapping(DRAFTS/'drafts', mapping, metadata)
    return plan, receipt, metadata, mapping


def build(receipt_path):
    plan, receipt, metadata, mapping = validate_inputs(receipt_path)
    # Verify all authorization and bytes before creating even the output directory.
    if CANDIDATE.exists() or CANDIDATE.resolve() != CANDIDATE or not CANDIDATE.is_relative_to(ROOT/'.runtime/test'):
        raise ValueError('new unlinked candidate only; no sealed input writes')
    raw = CANDIDATE/'raw'; raw.mkdir(parents=True)
    for e in plan['entries']:
        shutil.copyfile(DRAFTS/'drafts'/e['filename'],raw/e['filename'])
    write(raw/'meta.json',metadata); write(CANDIDATE/'source-map.json',mapping)
    write(CANDIDATE/'approval-receipt.json',receipt)
    write(CANDIDATE/'projection.json',dict(target=TARGET,files=20,expected_chunks=180,
        stage_intent='STAGED',database_version_created=False,embeddings_created=False,
        independent_quality_approved=False,operational_sop_approved=False,outbound_authorized=False,publication_authorized=False))
    validate_mapping(raw,mapping,metadata); seal(CANDIDATE); verify_seal(CANDIDATE); load_verified()
    return dict(raw_candidate_prepared=True,files=20,database_version_created=False,paid_calls=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',required=True); parser.add_argument('--execute',action='store_true')
    args = parser.parse_args()
    if args.execute:
        print(json.dumps(build(Path(args.receipt).absolute())))
    else:
        validate_inputs(Path(args.receipt).absolute())
        print(json.dumps(dict(ready=True,execution=False,files=20,paid_calls=0)))
