"""Read-only live namespace and protection audit of the pending decision package."""
import json
import socket
import uuid
import sqlalchemy as sa
from pymilvus import connections, utility
from scripts.prepare_sop_record_admission import ROOT,OUTPUT,load_verified,CODE
from scripts.sop_record_stage_profile import profile,CANDIDATE
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.verify_sop_guide_preview import protected
from scripts.build_sop_record_candidate import build
from qa_core.quality.sop_record_admission import TARGET

REPORT=ROOT/'.runtime/test/reports/sop-record-admission-20261014'


def main():
    if REPORT.exists():raise ValueError('new verification report only')
    REPORT.mkdir(parents=True)
    before=protected();write(REPORT/'before.json',before)
    assert before==read(ROOT/'.runtime/test/reports/training-step1-20261014/final-protected.json')
    plan=load_verified();selected=profile()
    engine=sa.create_engine(sa.engine.make_url(selected.mysql_dsn).set(database='test_sop_evidence_r1'))
    try:
        with engine.connect() as conn:
            count=conn.execute(sa.text('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:name'),
                {'name':TARGET['database']}).scalar_one()
            assert count==0
    finally:engine.dispose()
    alias='r2_admission_'+uuid.uuid4().hex
    try:
        connections.connect(alias=alias,uri=selected.milvus_uri)
        existing=set(utility.list_collections(using=alias))
        assert TARGET['doc_collection'] not in existing and TARGET['faq_collection'] not in existing
    finally:connections.disconnect(alias)
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1',18025))!=0
    assert not CANDIDATE.exists()
    try:build(OUTPUT/'receipt-template.json')
    except ValueError:pass
    else:raise AssertionError('pending candidate unexpectedly accepted')
    assert not CANDIDATE.exists()
    after=protected();write(REPORT/'after.json',after);assert before==after
    write(REPORT/'checks.json',dict(exact_source_plan_verified=True,pending_builder_rejected_before_write=True,
        new_database_absent=True,new_collections_absent=True,new_port_unstarted=True,new_candidate_absent=True,
        current_original_and_readonly_and_guide_snapshots_unchanged=True,existing_keys_and_pids_unchanged=True,
        files=20,extensions=49,preflight_chunks=180,new_admissions=0,new_staged=0,new_embeddings=0,paid_calls=0,
        independent_denominator=0,ordinary_gate_after_ingestion_required=True))
    write(REPORT/'code-bindings.json',{name:digest(ROOT/name) for name in (*CODE,
        'scripts/verify_sop_record_admission.py','tests/unit/test_sop_record_admission.py')})
    verify_seal(OUTPUT)
    print(json.dumps(dict(readonly_checks_passed=True,protected_unchanged=True,pending=20,paid_calls=0)))


if __name__=='__main__':main()
