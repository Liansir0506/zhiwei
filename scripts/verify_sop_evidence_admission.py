"""Real read-only namespace checks and browser evidence; no admission or QA."""
import json
import subprocess
import uuid

import sqlalchemy as sa

from scripts.prepare_sop_evidence_admission import ROOT,OUTPUT,REPORT,DRAFTS
from scripts.prepare_sop_evidence_drafts import validate as validate_drafts
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts import content_quality_release_runtime as rt,readonly_workspace_runtime as ro
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace
from qa_core.quality.sop_admission import TARGET,validate_receipt


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report sealed')
    attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir(parents=True)
    write(attempt/'state.json',dict(state='running',paid_calls=0))
    stage='snapshot'
    try:
        validate_drafts();verify_seal(OUTPUT)
        rt.install_adapter();base=rt.selected_settings();rt.owned.apply_profile(base);iso=ro.profile()
        def snapshot():
            verify_seal(DRAFTS);verify_seal(OUTPUT)
            with sa.create_engine(iso.mysql_dsn).connect() as conn:
                exists=conn.execute(sa.text('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:n'),{'n':TARGET['database']}).scalar()
            with Workspace(iso).client() as client:
                collections={n:client.has_collection(n) for n in (TARGET['doc_collection'],TARGET['faq_collection'])}
            return dict(original=Workspace(base).snapshot(rt.VERSION),original_sql=sql_snapshot(base),readonly_sql=sql_snapshot(iso),
                target_database_exists=bool(exists),target_collections_exist=collections,
                original_pid=rt.owned.inspect()['pid'],readonly_pid=ro.inspect()['pid'],
                env_sha256=digest(ROOT/'.env.runtime'),budget_sha256=digest(rt.owned.ROOT/'generation-validation-budget.json'),
                source_seal_sha256=digest(DRAFTS/'artifact-hashes.json'),review_seal_sha256=digest(OUTPUT/'artifact-hashes.json'))
        before=snapshot();write(attempt/'before.json',before)
        assert not before['target_database_exists'] and not any(before['target_collections_exist'].values())
        stage='pending_receipt';plan=read(OUTPUT/'admission-plan.json');receipt=read(OUTPUT/'receipt-template.json')
        try:validate_receipt(plan,receipt)
        except ValueError:pass
        else:raise AssertionError('pending accepted')
        write(attempt/'pending-block.json',dict(blocked=True,new_admissions=0,candidate_written=False))
        stage='browser';folder=attempt/'browser';folder.mkdir()
        p=subprocess.run(['node','tests/browser/sop_admission_smoke.js'],cwd=ROOT,
            input=json.dumps(dict(reportDir=str(folder),reviewFile=str(OUTPUT/'review.html'))),text=True,capture_output=True,
            timeout=110,creationflags=subprocess.CREATE_NO_WINDOW)
        result=read(folder/'browser-verification.json');assert p.returncode==0 and result['passed']
        stage='after_snapshot';after=snapshot();write(attempt/'after.json',after);assert before==after
        write(attempt/'state.json',dict(state='completed',browser_checks=8,pending_blocked=True,protected_state_unchanged=True,
            target_namespace_absent=True,paid_calls=0,new_admissions=0,new_staged=0,database_created=False,
            embeddings_created=0,source_identities_registered=0))
        print(json.dumps(dict(state='completed',browser_checks=8,paid_calls=0,report=str(attempt))))
    except Exception as error:
        write(attempt/'state.json',dict(state='failed',stage=stage,error_kind=type(error).__name__,paid_calls=0))
        raise RuntimeError('SOP admission review verification failed; retained safe stage available') from None


if __name__=='__main__':main()
