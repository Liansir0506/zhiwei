"""Read-only live recovery acceptance; no QA generation, ingestion or publication."""
import json
import subprocess
import time
import traceback
from pathlib import Path
import httpx
from scripts.recover_training_entries_20261015 import REPORT, PROJECT, write, event, fingerprints, containers, hidden_run, verify_container_identity
from scripts.preview_recovery_runtime import runtime_for


def protected():
    # Historical snapshots include runtime PID identities, so use the PID-reuse
    # guard before the unchanged snapshot helper inspects the r1 preview.
    runtime_for('guide-r1')
    from scripts.verify_sop_record_preview import protected as snapshot
    return snapshot()


def main():
    initial=json.loads((REPORT/'before.json').read_text('utf-8'))
    assert initial['files']==fingerprints()
    verify_container_identity(initial['containers'],containers())
    before = protected()
    write('read-before.json', before)
    historical = json.loads((PROJECT/'.runtime/test/reports/sop-record-preview-20261014/final-protected.json').read_text('utf-8'))
    names = ['original','original_sql','readonly','readonly_sql','stage','stage_sql','r2_snapshot','r2_sql','env','budget','ordinary_gate']
    comparison = {name: before[name] == historical[name] for name in names}
    write('historical-data-comparison.json', comparison)
    assert all(comparison.values()), 'Storage/config differs from historical protected baseline'
    event('historical_data_verified', matching_sections=len(comparison))
    from scripts import content_quality_release_runtime as original, training_business_runtime as business
    from scripts import platform_entry as entry
    original.install_adapter()
    checks = []

    def request(client, case, method, path, expected=200, **kwargs):
        response = client.request(method, path, **kwargs)
        assert response.status_code == expected, (case, response.status_code)
        checks.append(dict(case=case,status=response.status_code,passed=True))
        write('http-checks.json', checks)
        return response.json()

    key = original.owned.read_key(original.owned.load_state())
    with httpx.Client(base_url='http://127.0.0.1:8010',headers={'X-API-Key':key},timeout=180,trust_env=False) as client:
        health = request(client,'original-health','GET','/health?refresh=1')
        assert health['generation_enabled'] and not health['agent_enabled']
        request(client,'original-capabilities','GET','/admin/capabilities')
        catalog = request(client,'original-faq-catalog','GET','/kb/catalog',params=dict(doc_type='faq',page_size=10))
        assert catalog['items'] and catalog['total']==31
        source = request(client,'original-faq-source','GET','/kb/evidence/'+catalog['items'][0]['evidence_id'])
        assert source['text'] and source['source']==catalog['items'][0]['source']
        availability = request(client,'business-entry-status','GET','/business/availability')
        assert not availability['enabled_here'] and availability['isolated']['status']=='running'
        request(client,'original-business-boundary','GET','/business/alarms',503)
    event('original_real_reads_passed')
    key = (business.core.ROOT/'api-key.txt').read_text('utf-8')
    with httpx.Client(base_url='http://127.0.0.1:18021',headers={'X-API-Key':key},timeout=180,trust_env=False) as client:
        health = request(client,'business-health','GET','/health')
        assert not health['generation_enabled'] and health['agent_enabled']
        for kind, count, field in [('alarms',2,'alarm_id'),('devices',2,'device_id'),('tickets',1,'ticket_id'),('changes',1,'change_id')]:
            data = request(client,kind+'-list','GET','/business/'+kind)
            assert len(data['items'])==count
            detail = request(client,kind+'-detail','GET','/business/'+kind+'/'+data['items'][0][field])
            assert detail['entity'][field]==data['items'][0][field]
        request(client,'business-integrations','GET','/business/integrations')
        result = request(client,'readonly-linked-diagnosis','POST','/agent/diagnose',json=dict(query='请核对关联工单 SIM-TT-00001 的原文',alarm_id='SIM-ALM-000001'))
        assert result['status']=='ANSWERED' and result['retrieval_scope']['strategy']=='linked_work_order'
        assert len(result['sources'])==1
        source = request(client,'business-citation-reread','GET','/kb/evidence/'+result['sources'][0]['evidence_id'])
        assert source['text']==result['sources'][0]['text']
    event('business_real_reads_passed')
    from scripts.prepare_sop_record_revision import OUTPUT as drafts
    cases = json.loads((drafts/'existing-development-cases.json').read_text('utf-8'))['cases']
    matches = []
    for target, version in [('guide-r1','test-sop-evidence-r1'),('guide-r2','test-sop-records-r2')]:
        runtime = runtime_for(target)
        status = runtime.status(); assert status['status']=='running'
        key = (runtime.core.ROOT/'api-key.txt').read_text('utf-8')
        with httpx.Client(base_url=status['url'],headers={'X-API-Key':key},timeout=180,trust_env=False) as client:
            info = request(client,target+'-live-integrity','GET','/preview/checks')
            assert info['read_preview_ready']
            catalog = request(client,target+'-catalog','GET','/kb/catalog',params=dict(kb_version=version,doc_type='guide',page_size=30))
            assert catalog['total']==20
            item=catalog['items'][0]
            source=request(client,target+'-source','GET','/kb/evidence/'+item['evidence_id'])
            assert source['text'] and source['source']==item['source']
            if target=='guide-r2':
                for case in cases:
                    expected='guide_sop_evidence_'+case['id'].split('-')[-1]+'.md'
                    result=request(client,case['id']+'-topic','POST','/kb/search-evidence',json=dict(query=case['question'],kb_version=version,top_n=5))
                    assert result['status']=='EVIDENCE_FOUND' and result['evidence'] and all(e['source']==expected for e in result['evidence'])
                    first=result['evidence'][0]
                    source=request(client,case['id']+'-reference','GET',first['source_url'])
                    assert source['text']==first['text'] and source['source']==expected
                    matches.append(dict(id=case['id'],all_sources_match=True,development_exposed=True))
                    write('topic-checks.json',matches)
                for label, query in [('unknown','ALM-MISSING-001取证'),('conflict','SGW的ALM-AMF-REG取证'),('workflow','AMF完整处置流程')]:
                    result=request(client,label+'-no-evidence','POST','/kb/search-evidence',json=dict(query=query,kb_version=version,top_n=5))
                    assert result['status']=='NO_EVIDENCE' and not result['evidence']
                request(client,'r2-generation-denied','POST','/qa',403,json={})
        event(target+'_real_reads_passed')
    browser_dir=REPORT/'topic-browser';browser_dir.mkdir(exist_ok=True)
    runtime=runtime_for('guide-r2')
    payload=dict(report_dir=str(browser_dir),url=runtime.status()['url'],key_file=str(runtime.core.ROOT/'api-key.txt'),query=cases[0]['question'])
    result=hidden_run(['node',str(PROJECT/'scripts/verify_sop_record_topic_browser.js')],input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,timeout=300)
    write('browser-result.json',dict(returncode=result.returncode))
    assert result.returncode==0,'Browser failed; inspect safe browser-checks.json'
    event('headless_browser_passed')
    samples=[]
    observation_start=entry.stamp()
    for index in range(3):
        row=dict(at_utc=entry.stamp(),original=entry.RuntimeProbe().sample(),business=business.status()['status'],
                 guide_r1=runtime_for('guide-r1').status()['status'],guide_r2=runtime_for('guide-r2').status()['status'],
                 observer_owned=entry.observer_process() is not None)
        samples.append(row);write('stable-samples.json',samples)
        assert row['original']['status']=='ready' and row['observer_owned'] and all(row[n]=='running' for n in ['business','guide_r1','guide_r2'])
        event('stable_observation',sample=index+1)
        if index<2:time.sleep(30)
    background=[json.loads(line) for line in (entry.ROOT/'observations.jsonl').read_text('utf-8').splitlines()]
    background=[r for r in background if r['checked_at_utc']>=observation_start and 'consecutive_failures' in r]
    write('background-observations.json',background)
    assert len(background)>=2 and all(r['status']=='ready' for r in background)
    after=protected();write('read-after.json',after);assert before==after
    write('verified.json',dict(status='passed',http_checks=len(checks),topic_checks=len(matches),stable_samples=len(samples),
          background_samples=len(background),read_only_snapshots_equal=True,historical_data_match=True,paid_calls=0,
          independent_denominator=0,files=fingerprints(),containers=containers(),
          limits=['Docker-off is fault-injected unit coverage; live Docker daemon was already running.',
                  'Short observation only; no long-term SLA or independent answer-quality claim.']))
    event('live_acceptance_completed',http_checks=len(checks),topic_matches=len(matches),paid_calls=0)


if __name__=='__main__':
    try:main()
    except Exception as error:
        write('verification-failure.json',dict(error_type=type(error).__name__,frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)]))
        event('live_acceptance_failed',error_type=type(error).__name__)
        raise SystemExit(1) from None
