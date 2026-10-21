"""Seal actual STAGED development results without activation or outbound calls."""
import shutil
import difflib
import xml.etree.ElementTree as ET
from scripts import stage_content_quality_merge as merge
from scripts.prepare_legacy_projection import read,write,digest,seal,verify_seal


def main():
    merge.validate();verify_seal(merge.CANDIDATE)
    if (merge.REPORT/'artifact-hashes.json').exists():raise ValueError('sealed report cannot be rewritten')
    assert read(merge.QA_REPORT/'qa-summary.json')['passed']
    for name in ('structural-verification.json',):
        assert read(merge.REPORT/name)['passed'],name
    for name in ('extra-summary.json','http-auth.json'):
        assert read(merge.REPORT/'extra-recovery-r2'/name)['passed'],name
    gate=read(merge.REPORT/'gate.json');assert gate['passed'],'actual full gate not passed; preserve failure'
    from qa_core.quality.publication import verify_publication
    readiness=verify_publication(merge.settings_for(),merge.VERSION,gate['report'])
    write(merge.REPORT/'publication-snapshot-check.json',readiness)
    usecase=merge.ROOT/'qa_core/application/qa_usecase.py'
    original=usecase.read_text(encoding='utf-8')
    revised=original.replace('from qa_core.retrieval.searcher import Searcher',
        'from qa_core.retrieval.supported_searcher import SupportedFaqSearcher as Searcher').replace(
        'from qa_core.retrieval.relevance import relevant_docs',
        'from qa_core.retrieval.supported_evidence_v2 import relevant_docs')
    assert revised!=original and original.count('from qa_core.retrieval.relevance import relevant_docs')==1
    patch=''.join(difflib.unified_diff(original.splitlines(True),revised.splitlines(True),
        fromfile='a/qa_core/application/qa_usecase.py',tofile='b/qa_core/application/qa_usecase.py'))
    (merge.REPORT/'qa-integration-v2.patch').write_text(patch,encoding='utf-8')
    write(merge.REPORT/'next-stage-plan.json',dict(current_active='local-merged-r2',candidate=merge.VERSION,
        collections=[merge.DOC,merge.FAQ],current_usecase_sha256=digest(usecase),
        import_patch_sha256=digest(merge.REPORT/'qa-integration-v2.patch'),patch_applied=False,
        remaining=['new immutable runtime/release bindings','concrete reversible publication review','authorized activation and browser verification'],
        publication_authorized=False,new_material_outbound_authorized=False,
        candidate_generation_enabled=False,independent_quality_approved=False))
    status=merge.runtime.inspect();assert status['status']=='running'
    assert digest(merge.ROOT/'.env.runtime')=='045ef4acd5b9babfea24d0c3e9b61679deb51a84c6803eb2e4f553139dbc19ff'
    budget=read(merge.ROOT/'.runtime/original-local/generation-validation-budget.json')
    assert budget['used']==3 and budget['limit']==4
    suite=ET.parse(merge.ROOT/'.runtime/test/phase3-unit-r8.xml').getroot().find('testsuite')
    assert suite is not None and suite.attrib['failures']==suite.attrib['errors']=='0'
    for pattern in ('phase3-unit-r8.*','content-quality-*.log'):
        for path in (merge.ROOT/'.runtime/test').glob(pattern):
            shutil.copyfile(path,merge.REPORT/path.name)
    paths=['qa_core/indexing/chunkers/supported_rebuild.py','scripts/stage_content_quality_merge.py',
        'scripts/verify_content_quality_merge_extra.py','scripts/finalize_content_quality_merge.py',
        'qa_core/retrieval/supported_evidence.py','qa_core/retrieval/supported_evidence_v2.py',
        'qa_core/retrieval/supported_searcher.py','qa_core/indexing/chunkers/paragraph_budget.py',
        'tests/unit/test_content_quality_merge.py','tests/unit/test_supported_rebuild.py',
        'tests/unit/test_supported_searcher.py','tests/unit/test_supported_evidence_v2.py']
    write(merge.REPORT/'code-hashes.json',{p:digest(merge.ROOT/p) for p in paths})
    write(merge.REPORT/'final-summary.json',dict(passed=True,version=merge.VERSION,status='STAGED',
        files=306,chunks=4357,new_admissions=20,new_embeddings=1396,copied_embeddings=2961,
        full_gate_passed=True,unit_tests=int(suite.attrib['tests']),paid_calls=0,generation_budget=dict(used=3,limit=4),
        active='local-merged-r2',runtime={k:status[k] for k in ('status','pid','generation_enabled','agent_enabled')},
        independent_quality_approved=False,publication_performed=False,outbound_authorized=False,
        runtime_recovered=True,long_term_runtime_stability_proven=False,
        selector_integration='scoped candidate process; frozen 8010 import unchanged',
        sop_support_review_performed=False,complete_vendor_procedures_supported=False))
    seal(merge.REPORT);verify_seal(merge.REPORT)
    print('PASS: complete isolated STAGED development merge sealed; ACTIVE untouched')


if __name__=='__main__':main()
