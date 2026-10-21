"""Real-file and local-browser development checks; no QA, model or ingestion."""
from copy import deepcopy
import json
import subprocess
import time
import uuid

import httpx

from scripts import content_quality_release_runtime as rt,readonly_workspace_runtime as readonly
from scripts.prepare_sop_evidence_drafts import OUTPUT,PARENT,EXPOSURE,validate
from scripts.sop_evidence_draft_content import SPECS
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace
from qa_core.quality.sop_evidence_preview import preview
from scripts.screen_question_holdout_v2 import run as screen

REPORT=rt.PROJECT/'.runtime/test/reports/sop-evidence-drafts-20261014'


def main():
    if (REPORT/'artifact-hashes.json').exists():raise ValueError('report already sealed')
    attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir(parents=True)
    write(attempt/'state.json',dict(state='running',paid_calls=0))
    stage='protected_snapshot'
    try:
        validate();verify_seal(EXPOSURE)
        rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
        ws=Workspace(settings)
        protected=[PARENT,rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW,
            rt.PROJECT/'.runtime/test/reports/development-selfcheck-20261013',
            rt.PROJECT/'data/candidates/known-corpus-question-holdout-r1',OUTPUT,EXPOSURE]
        def snapshot():
            for p in protected:verify_seal(p)
            return dict(active=ws.snapshot(rt.VERSION),original_sql=sql_snapshot(settings),
                readonly_sql=sql_snapshot(readonly.profile()),original_pid=rt.owned.inspect()['pid'],
                readonly_pid=readonly.inspect()['pid'],env_sha256=digest(rt.PROJECT/'.env.runtime'),
                key_sha256=digest(rt.owned.ROOT/'api-key.txt'),budget_sha256=digest(rt.owned.ROOT/'generation-validation-budget.json'),
                seals={str(p.relative_to(rt.PROJECT)):digest(p/'artifact-hashes.json') for p in protected})
        before=snapshot();write(attempt/'before.json',before)
        stage='public_simulation';cases=read(OUTPUT/'development-preview-cases.json')['cases'];results=[]
        variants=[('primary','hypothesis_review'),('counter','alternative_review'),('both','stop_conflict'),
                  ('neither','no_supported_branch'),('missing','stop_evidence'),('unauthorized','stop_access')]
        for c in cases:
            spec=SPECS[c['specification_key']]
            for variant,expected in variants:
                packet=deepcopy(c['packet'])
                if variant=='counter':packet.update(primary_supported=False,counter_supported=True)
                if variant=='both':packet['counter_supported']=True
                if variant=='neither':packet.update(primary_supported=False,counter_supported=False)
                if variant=='missing':packet['records'][spec[1][0][0]]['state']='missing'
                if variant=='unauthorized':packet['authorized']=False
                result=preview(spec,packet)
                assert result['state']==expected and not result['action_authorized'] and not result['root_cause_confirmed']
                results.append(dict(id=c['id']+'-'+variant,passed=True,result=result,simulation_only=True))
        write(attempt/'preview-results.json',results);assert len(results)==120
        stage='later_exposure_screen';receipt=screen();assert receipt['state']=='blocked' and receipt['frozen']==0
        assert receipt['development_overlay_count']==5
        write(attempt/'empty-intake-screen.json',receipt)
        stage='offline_browser';bdir=attempt/'browser';bdir.mkdir()
        process=subprocess.run(['node',str(rt.PROJECT/'tests/browser/sop_evidence_drafts_smoke.js')],
            input=json.dumps(dict(reportDir=str(bdir),reviewFile=str(OUTPUT/'review.html'))),text=True,capture_output=True,
            timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
        browser=read(bdir/'browser-verification.json')
        assert process.returncode==0 and browser['passed'] and len(browser['checks'])==9
        stage='short_health_observation';observations=[]
        for _ in range(3):
            original,iso=rt.owned.inspect(),readonly.inspect()
            health=httpx.get(original['url']+'/health',timeout=8,trust_env=False).json()
            other=httpx.get(iso['url']+'/health',timeout=8,trust_env=False).json()
            assert health['status']==other['status']=='ok'
            assert health['generation_enabled'] and not health['agent_enabled'] and not other['generation_enabled']
            assert (original['pid'],iso['pid'])==(before['original_pid'],before['readonly_pid'])
            observations.append(dict(original_pid=original['pid'],readonly_pid=iso['pid'],healthy=True));time.sleep(3)
        write(attempt/'health-observations.json',observations)
        stage='after_snapshot';after=snapshot();write(attempt/'after.json',after);assert before==after
        write(attempt/'state.json',dict(state='completed',simulation_checks=120,browser_checks=9,health_samples=3,
            protected_state_unchanged=True,qa_requests=0,model_calls=0,paid_calls=0,temporary_keys_created=0,
            new_admissions=0,complete_sop_approved=0,independent_frozen=0,current_version=rt.VERSION))
        print(json.dumps(dict(state='completed',simulation_checks=120,browser_checks=9,paid_calls=0,report=str(attempt))))
    except Exception as error:
        write(attempt/'state.json',dict(state='failed',stage=stage,error_kind=type(error).__name__,paid_calls=0))
        raise RuntimeError('SOP draft verification failed; see safe retained stage') from None


if __name__=='__main__':main()
