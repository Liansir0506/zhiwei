"""Require every returned evidence item to stay in its admitted explicit topic."""
import json
import subprocess
import uuid
import httpx
from scripts import sop_record_topic_runtime as runtime
from scripts.verify_sop_record_preview import protected
from scripts.prepare_sop_record_revision import OUTPUT as DRAFTS
from scripts.prepare_sop_record_alignment_v2 import load_verified
from scripts.prepare_legacy_projection import read,write

REPORT=runtime.core.PROJECT/'.runtime/test/reports/sop-record-preview-topic-20261014'


def main():
    REPORT.mkdir(parents=True,exist_ok=True)
    attempt=REPORT/('attempt-'+uuid.uuid4().hex[:8]);attempt.mkdir()
    before=protected();write(attempt/'before.json',before)
    assert before==read(runtime.core.PROJECT/'.runtime/test/reports/sop-record-preview-20261014/final-protected.json')
    state=runtime.status();assert state['status']=='running'
    key=(runtime.core.ROOT/'api-key.txt').read_text(encoding='utf-8')
    checks=[];retrieval=[]
    with httpx.Client(base_url=state['url'],trust_env=False,timeout=180,headers={'X-API-Key':key}) as client:
        def request(label,method,path,expected=200,**kwargs):
            response=client.request(method,path,**kwargs)
            assert response.status_code==expected,(label,response.status_code)
            assert response.headers.get('cache-control')=='no-store'
            checks.append(dict(case=label,status=expected,passed=True));write(attempt/'http-checks.json',checks)
            return response.json()
        info=request('retained-live-support-and-integrity','GET','/preview/checks')
        assert info['support_alignment']['summary']['record_contracts']==109
        for case in read(DRAFTS/'existing-development-cases.json')['cases']:
            expected='guide_sop_evidence_'+case['id'].split('-')[-1]+'.md'
            result=request(case['id']+'-topic-search','POST','/kb/search-evidence',json=dict(query=case['question'],kb_version='test-sop-records-r2',top_n=5))
            correct=result['status']=='EVIDENCE_FOUND' and bool(result['evidence']) and all(e['source']==expected for e in result['evidence'])
            retrieval.append(dict(id=case['id'],all_returned_sources_match=correct,expected_source=expected,result=result,development_exposed=True))
            write(attempt/'retrieval.json',retrieval);assert correct
            first=result['evidence'][0];original=request(case['id']+'-citation','GET',first['source_url'])
            assert original['text']==first['text'] and original['source']==expected
            print(case['id'],'ALL_TOPIC_MATCH',flush=True)
        for label,query in [('unknown-alarm','ALM-MISSING-001取证'),('conflicting-device','SGW的ALM-AMF-REG取证'),('complete-workflow','AMF完整处置流程')]:
            result=request(label,'POST','/kb/search-evidence',json=dict(query=query,kb_version='test-sop-records-r2',top_n=5))
            assert result['status']=='NO_EVIDENCE' and not result['evidence']
        request('old-version-still-denied','POST','/kb/search-evidence',409,json=dict(query='AMF',kb_version='test-sop-evidence-r1'))
        request('generation-still-denied','POST','/qa',403,json={})
    payload=dict(report_dir=str(attempt),url=state['url'],key_file=str(runtime.core.ROOT/'api-key.txt'),
        query=read(DRAFTS/'existing-development-cases.json')['cases'][0]['question'])
    result=subprocess.run(['node',str(runtime.core.PROJECT/'scripts/verify_sop_record_topic_browser.js')],
        input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,timeout=250,creationflags=subprocess.CREATE_NO_WINDOW)
    (attempt/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('topic preview browser validation failed; safe report retained')
    after=protected();write(attempt/'after.json',after);assert before==after
    write(attempt/'state.json',dict(state='completed',http_checks=len(checks),exposed_development_queries=20,
        every_returned_source_topic_matches=20,citation_rereads=20,protected_snapshots_unchanged=True,
        paid_calls=0,new_ingestions=0,independent_denominator=0))
    print(json.dumps(dict(http_checks=len(checks),all_item_topic_matches=20,paid_calls=0)))


if __name__=='__main__':main()
