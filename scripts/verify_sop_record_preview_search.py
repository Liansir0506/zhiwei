"""Real network search of the restarted preview, with evidence reread and scope refusal."""
import json
import subprocess
import httpx
from scripts import sop_record_preview_runtime as runtime
from scripts.verify_sop_record_preview import REPORT,protected
from scripts.prepare_sop_record_revision import OUTPUT as DRAFTS
from scripts.prepare_sop_support_alignment import load_verified as old_matrix
from scripts.prepare_legacy_projection import read,write


def main():
    assert read(REPORT/'lifecycle.json')['state']=='completed'
    folder=REPORT/'search-verification'
    if folder.exists():raise ValueError('separate new search acceptance only')
    folder.mkdir();before=protected()
    key=(runtime.ROOT/'api-key.txt').read_text(encoding='utf-8')
    state=runtime.inspect();assert state['status']=='running'
    case=read(DRAFTS/'existing-development-cases.json')['cases'][0]
    checks=[]
    with httpx.Client(base_url=state['url'],trust_env=False,timeout=180,headers={'X-API-Key':key}) as client:
        def request(label,method,path,expected=200,**kwargs):
            result=client.request(method,path,**kwargs)
            assert result.status_code==expected,(label,result.status_code)
            assert result.headers.get('cache-control')=='no-store'
            checks.append(dict(case=label,status=expected,passed=True));write(folder/'http-checks.json',checks)
            return result.json()
        result=request('real-AMF-retrieval','POST','/kb/search-evidence',json=dict(query=case['question'],kb_version='test-sop-records-r2',top_n=5))
        write(folder/'retrieval.json',result)
        evidence=next(e for e in result['evidence'] if e['source']=='guide_sop_evidence_001.md')
        reread=request('network-search-reference-reread','GET',evidence['source_url'])
        assert reread['text']==evidence['text'] and reread['kb_version']=='test-sop-records-r2'
        none=request('complete-workflow-not-supported','POST','/kb/search-evidence',json=dict(query='AMF完整处置流程',kb_version='test-sop-records-r2',top_n=5))
        assert none['status']=='NO_EVIDENCE' and not none['evidence']
        request('wrong-version-search','POST','/kb/search-evidence',409,json=dict(query='AMF',kb_version='test-sop-evidence-r1',top_n=5))
        request('missing-explicit-version','GET','/kb/catalog',409)
        old_ref=old_matrix()['entries'][0]['records'][0]['proof']['source_url']
        request('old-r1-reference-not-exposed','GET',old_ref,404)
    payload=dict(report_dir=str(folder),url=state['url'],key_file=str(runtime.ROOT/'api-key.txt'),query=case['question'])
    result=subprocess.run(['node',str(runtime.PROJECT/'scripts/verify_sop_record_search_browser.js')],input=json.dumps(payload),
        text=True,encoding='utf-8',capture_output=True,timeout=250,creationflags=subprocess.CREATE_NO_WINDOW)
    (folder/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:raise RuntimeError('restarted preview browser search failed; retained safe report')
    after=protected();assert before==after
    write(folder/'state.json',dict(state='completed',http_checks=len(checks),real_network_search=True,
        development_exposed=True,independent_denominator=0,protected_snapshots_unchanged=True,qa_requests=0,paid_calls=0))
    print(json.dumps(dict(search_http_checks=len(checks),real_browser_search=True,paid_calls=0)))


if __name__=='__main__':main()
