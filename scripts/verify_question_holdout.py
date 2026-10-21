"""Verify known-corpus holdout preparation with real exposure controls, not QA."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

import httpx

from qa_core.quality.question_holdout import screen
from qa_core.quality.version_workspace import Workspace
from scripts import content_quality_release_runtime as rt, readonly_workspace_runtime as readonly
from scripts.prepare_question_holdout import OUTPUT, PREVIOUS, VERSION, validate
from scripts.prepare_legacy_projection import read, write, digest, verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot

REPORT = rt.PROJECT/'.runtime/test/reports/question-holdout-step4-20261013'


def main():
    attempt = REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir(parents=True,exist_ok=False)
    write(attempt/'state.json',dict(state='running',qa_evaluation_executed=False,model_calls=0))
    validate();rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    ws=Workspace(settings)
    protected=[OUTPUT,PREVIOUS,rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW,
               rt.PROJECT/'.runtime/test/reports/independent-quality-step3-20261013']

    def snapshot():
        for p in protected:verify_seal(p)
        return dict(active=ws.snapshot(VERSION),original_sql=sql_snapshot(settings),
            readonly_sql=sql_snapshot(readonly.profile()),env_sha256=digest(rt.PROJECT/'.env.runtime'),
            budget_sha256=digest(rt.owned.ROOT/'generation-validation-budget.json'),
            seals={str(p.relative_to(rt.PROJECT)):digest(p/'artifact-hashes.json') for p in protected})

    stage='before_snapshot'
    try:
        before=snapshot();write(attempt/'before.json',before)
        stage='real_registry_controls'
        registry=read(PREVIOUS/'exposure-registry.json');sources=read(OUTPUT/'source-roster.json')
        manifest=read(OUTPUT/'intake-manifest-template.json');b=read(OUTPUT/'baseline-binding.json')
        evaluate=lambda p:screen(p,registry,sources,VERSION,b['binding_sha256'])
        empty=evaluate(manifest);assert empty['state']=='blocked' and empty['case_count']==0
        s=sources[0]
        # Fabricated commitments are developer controls, not human questions/gold.
        control=dict(id='DEVELOPMENT-METADATA-CONTROL',query_sha256='0'*64,canonical_query_sha256='1'*64,
            truth_sha256='2'*64,evidence_manifest_sha256='3'*64,source_ids=[s['asset_id']],
            body_families=[s['canonical_body_sha256']],topic_groups=['development-control-topic'],
            template_groups=['development-control-template'],development_exposed=False,
            expected_kind='answer',request_context=dict(tenant_id='default',roles=['ops']))
        p=deepcopy(manifest);p['cases']=[control];shared=evaluate(p)
        assert shared['static_screen_passed'] and shared['state']=='needs_human_custody_truth_and_semantic_review'
        assert not shared['run_authorized'] and not shared['metrics_eligible']
        old=next(r for r in registry['records'] if r['declared_split']=='frozen')
        p=deepcopy(manifest);p['cases']=[dict(control,query_sha256=old['query_sha256'],canonical_query_sha256=old['canonical_query_sha256'])]
        exposed=evaluate(p);assert 'known_exposed_question_hash' in exposed['blocked_cases'][0]['reasons']
        p=deepcopy(manifest);p['cases']=[dict(control,source_ids=['project.rfc-summary.bfd-detection'])]
        pending=evaluate(p);assert 'source_not_in_exact_current_corpus' in pending['blocked_cases'][0]['reasons']
        p=deepcopy(manifest);p['cases']=[dict(control,body_families=['9'*64])]
        wrong_body=evaluate(p);assert 'source_body_binding_mismatch' in wrong_body['blocked_cases'][0]['reasons']
        controls=dict(purpose='synthetic metadata controls only, not a hidden question set or quality denominator',
            empty=empty,shared_known_source=shared,old_frozen_reuse=exposed,pending_reuse=pending,wrong_body=wrong_body,
            human_cases=0,human_truth_files=0)
        write(attempt/'registry-controls.json',controls)
        stage='health';observations=[]
        for i in range(3):
            original=rt.owned.inspect();isolated=readonly.inspect()
            assert original['status']==isolated['status']=='running'
            row=dict(at=datetime.now(timezone.utc).isoformat(),original_pid=original['pid'],readonly_pid=isolated['pid'],health=[])
            for url in ('http://127.0.0.1:8010','http://localhost:8010','http://127.0.0.1:18021'):
                response=httpx.get(url+'/health',timeout=15,trust_env=False);response.raise_for_status();data=response.json()
                assert data['status']=='ok' and data['generation_enabled']==('8010' in url) and data['agent_enabled']==('18021' in url)
                row['health'].append(dict(url=url,status='ok'))
            observations.append(row)
            write(attempt/'health-observations.json',dict(samples=observations,scope='three observations ten seconds apart; not long-term SLA'))
            if i<2:time.sleep(10)
        assert len({(r['original_pid'],r['readonly_pid']) for r in observations})==1
        stage='after_snapshot';after=snapshot();write(attempt/'after.json',after);assert before==after
        validate()
        write(attempt/'state.json',dict(state='completed',preparation_seal_sha256=digest(OUTPUT/'artifact-hashes.json'),
            real_registry_controls=5,health_samples=3,protected_state_unchanged=True,new_human_cases=0,new_truth=0,
            frozen=0,model_calls=0,new_admissions=0,qa_evaluation_executed=False,published=False))
        print('Completed: five metadata controls, three health observations, protected state unchanged; no QA evaluation')
    except Exception as exc:
        write(attempt/'state.json',dict(state='failed',stage=stage,error_type=type(exc).__name__,model_calls=0))
        raise RuntimeError('Preparation verification incomplete; see bounded stage record') from None


if __name__ == '__main__':main()
