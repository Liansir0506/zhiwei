"""Read-only final checks and seal the phase-two development evidence."""
import time
import xml.etree.ElementTree as ET
import sqlalchemy as sa
from scripts import original_local_runtime as runtime
from scripts.prepare_legacy_projection import digest, read, write, seal, verify_seal


def main():
    root=runtime.PROJECT
    report=root/'.runtime/test/reports/evidence-quality-phase2-20261010-r2'
    candidate=root/'data/candidates/content-quality-review-r1'
    assert not (report/'artifact-hashes.json').exists()
    for directory in [candidate,root/'data/candidates/triage-faq-approved-demo-r1',
        root/'.runtime/test/reports/triage-faq-staged-20261010',
        root/'.runtime/test/reports/merged-protocol-release-20261010']:
        verify_seal(directory)
    assert digest(root/'.env.runtime')=='045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
    suites=list(ET.parse(report/'unit.xml').getroot().iter('testsuite'))
    assert all(int(s.attrib.get('failures',0))==int(s.attrib.get('errors',0))==0 for s in suites)
    tests=sum(int(s.attrib['tests']) for s in suites)
    checks=[]
    for i in range(6):
        status=runtime.inspect()
        assert status['status']=='running' and status['generation_enabled'] and not status['agent_enabled']
        checks.append({k:status[k] for k in ('status','pid','generation_enabled','agent_enabled')})
        if i<5:time.sleep(5)
    assert len({c['pid'] for c in checks})==1
    settings=runtime.build_profile()
    engine=sa.create_engine(settings.mysql_dsn,pool_pre_ping=True)
    try:
        active=runtime.verify_active(settings,engine)
    finally:engine.dispose()
    assert read(report/'qa-summary.json')['passed']
    assert read(report/'review-browser.json')['passed']
    budget=read(root/'.runtime/original-local/generation-validation-budget.json')
    assert budget['used']==3 and budget['limit']==4
    paths=['qa_core/retrieval/supported_evidence.py','qa_core/indexing/chunkers/paragraph_budget.py',
        'scripts/verify_evidence_quality_phase2.py','scripts/prepare_content_quality_revision.py',
        'scripts/verify_content_quality_review_browser.py','scripts/start_original_local_background.ps1',
        'tests/unit/test_supported_evidence.py','tests/unit/test_paragraph_budget.py']
    write(report/'code-hashes.json',{p:digest(root/p) for p in paths})
    write(report/'final-summary.json',dict(passed=True,unit_tests=tests,active=active,
        runtime_checks=checks,candidate_summary=read(candidate/'summary.json'),paid_calls=0,
        generation_budget=dict(used=3,limit=4),new_faq_approval='pending',
        deployed=False,chunk_rebuild_adapter_integrated=False,independent_quality_passed=False,
        runtime_recovered=True,long_term_stability_not_proven=True))
    seal(report);verify_seal(report)
    print('PASS: phase-two development sealed; unit tests='+str(tests)+'; live version preserved')


if __name__=='__main__':main()
