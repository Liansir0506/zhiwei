"""Verify hash-only preparation and live-state preservation; never evaluate questions."""
from datetime import datetime,timezone
from pathlib import Path
import time
import uuid
import httpx
from scripts import content_quality_release_runtime as rt,readonly_workspace_runtime as readonly
from scripts.prepare_independent_quality import OUTPUT,validate
from scripts.prepare_legacy_projection import read,write,digest,verify_seal
from scripts.verify_readonly_business_workspace import sql_snapshot
from qa_core.quality.version_workspace import Workspace
from qa_core.quality.independent_readiness import screen_intake

REPORT=rt.PROJECT/'.runtime/test/reports/independent-quality-step3-20261013'


def main():
    folder=REPORT/('attempt-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    write(folder/'state.json',dict(state='running',new_questions=0,paid_calls=0))
    validate();rt.install_adapter();settings=rt.selected_settings();rt.owned.apply_profile(settings)
    ws=Workspace(settings)
    protected=[OUTPUT,rt.merge.CANDIDATE,rt.merge.REPORT,rt.REVIEW,
        rt.PROJECT/'data/candidates/independent-quality-preparation-r1',
        rt.PROJECT/'.runtime/test/reports/sop-device-support-step2-20261013',
        rt.PROJECT/'.runtime/test/reports/runtime-handoff-step1-20261013']
    def snapshot():
        for p in protected:verify_seal(p)
        return dict(active=ws.snapshot(rt.VERSION),sql=sql_snapshot(settings),readonly_sql=sql_snapshot(readonly.profile()),
            env=digest(rt.PROJECT/'.env.runtime'),budget=digest(rt.owned.ROOT/'generation-validation-budget.json'),
            seals={str(p.relative_to(rt.PROJECT)):digest(p/'artifact-hashes.json') for p in protected})
    before=snapshot();write(folder/'before.json',before)
    stage='intake_screen'
    try:
        registry=read(OUTPUT/'exposure-registry.json');binding=read(OUTPUT/'baseline-binding.json')['binding_sha256']
        template=read(OUTPUT/'intake-manifest-template.json')
        result=screen_intake(template,registry,rt.VERSION,binding)
        assert result['state']=='blocked' and result['case_count']==0 and not result['metrics_eligible']
        write(folder/'intake-empty-screen.json',result)
        # Use one already-exposed hash record only. No new or hidden question is read/authored.
        known=next(r for r in registry['records'] if r['declared_split']=='frozen')
        case=dict(id='EXPOSED-HISTORICAL-CONTROL',query_sha256=known['query_sha256'],
            canonical_query_sha256=known['canonical_query_sha256'],truth_sha256='0'*64,
            evidence_manifest_sha256='0'*64,source_ids=['already-exposed-control'],
            body_families=['already-exposed-body'],topic_groups=['already-exposed-topic'],
            template_groups=['already-exposed-template'],development_exposed=False,expected_kind='answer',
            request_context={'tenant_id':'default','roles':['ops']})
        template['cases']=[case]
        denied=screen_intake(template,registry,rt.VERSION,binding)
        assert denied['state']=='blocked' and 'known_exposed_question_hash' in denied['blocked_cases'][0]['reasons']
        write(folder/'historical-frozen-reuse-denied.json',denied)
        pending=dict(case,id='PUBLIC-SOURCE-ONLY-CONTROL',query_sha256='1'*64,canonical_query_sha256='2'*64,
                     source_ids=['project.rfc-summary.bfd-detection'])
        template['cases']=[pending]
        pending_denied=screen_intake(template,registry,rt.VERSION,binding)
        assert 'known_development_source_ids' in pending_denied['blocked_cases'][0]['reasons']
        write(folder/'source-only-pending-reuse-denied.json',pending_denied)
        # The 28 newly exposed public counterexamples remain in the blocking inventory.
        last=[r for r in registry['records'] if r['input']=='data/candidates/sop-device-support-review-r2/development-review-cases.json']
        assert len(last)==28 and all(r['developer_exposed'] for r in last)
        write(folder/'support-dev-registration.json',dict(records=len(last),all_exposed=True,
            canonical_query_hashes=sorted({r['canonical_query_sha256'] for r in last}),independent_frozen=0))
        stage='runtime_health';health=[]
        for index in range(3):
            original=rt.owned.inspect();isolated=readonly.inspect()
            assert original['status']==isolated['status']=='running'
            row=dict(at=datetime.now(timezone.utc).isoformat(),original_pid=original['pid'],readonly_pid=isolated['pid'],health=[])
            for url in ('http://127.0.0.1:8010','http://localhost:8010','http://127.0.0.1:18021'):
                response=httpx.get(url+'/health',timeout=20,trust_env=False);response.raise_for_status();data=response.json()
                assert data['status']=='ok' and data['generation_enabled']==('8010' in url) and data['agent_enabled']==('18021' in url)
                row['health'].append(dict(url=url,status='ok'))
            health.append(row);write(folder/'health-observations.json',dict(samples=health,scope='bounded observations, not long-term SLA'))
            if index<2:time.sleep(10)
        assert len({(r['original_pid'],r['readonly_pid']) for r in health})==1
        stage='after_snapshot';after=snapshot();write(folder/'after.json',after);assert before==after
        validate()
        write(folder/'state.json',dict(state='completed',empty_intake_blocked=True,old_frozen_reuse_blocked=True,
            source_only_pending_reuse_blocked=True,preparation_seal_sha256=digest(OUTPUT/'artifact-hashes.json'),
            newly_exposed_dev_records=28,protected_state_unchanged=True,new_questions=0,new_truth=0,
            new_admissions=0,frozen=0,paid_calls=0,qa_evaluation_executed=False))
        print('Completed: empty intake and old frozen reuse blocked; 28 dev cases registered; protected state unchanged; model calls 0')
    except Exception as exc:
        write(folder/'state.json',dict(state='failed',stage=stage,error_type=type(exc).__name__,paid_calls=0))
        raise RuntimeError('Independent preparation verification incomplete; no acceptance claim') from None


if __name__=='__main__':main()
