"""Offline plan integrity; controlled fixtures do not count as live retrieval."""
import copy
import csv
import json
from pathlib import Path
import pytest
from qa_core.schemas.retrieval import RetrievedDoc
from qa_core.schemas.evidence import evidence_of
from scripts.prepare_agent_relation_plan import baseline, record_roster, review, validate_output
from scripts.prepare_agent_domain_review import file_hash

def historical_fixture():
    candidate={'target':{'version':'synthetic-v'},'prerequisites':{'knowledge_assets':[{'asset_id':'linked','source_version':'r1'}]}}
    contexts={kind:{'knowledge':[{'asset_id':'linked','source_version':'r1'}],'truncated':False} for kind in ('alarms','devices')}
    sources=[evidence_of(RetrievedDoc(id='c1',text='controlled historical body',fusion_score=1,asset_id='other',source_version='r1',kb_version='synthetic-v',source='synthetic-ticket'))]
    responses=[{'case':case,'http_status':200,'body':{'query':'controlled query'},'response':{'run_id':case,'mode':'domain','status':'ANSWERED',
        'kb_version':'synthetic-v','observations':[{}]*5,'sources':copy.deepcopy(sources),'budget':{'calls_used':4}}} for case in ('A1-alarm','A2-device')]
    return responses,contexts,candidate

def test_baseline_counts_only_completed_distinct_historical_cases():
    values=historical_fixture();result=baseline(*values)
    assert result['total_citations']==2 and result['associated_asset_citations']==0
    assert result['independent_frozen']==0 and result['record_precision_evaluated'] is False
    assert result==json.loads(json.dumps(result))

@pytest.mark.parametrize('mutation',['missing_case','foreign_context','truncated','wrong_version','modified_body'])
def test_baseline_refuses_unbound_or_incomplete_historical_evidence(mutation):
    responses,contexts,candidate=historical_fixture()
    if mutation=='missing_case':responses.pop()
    elif mutation=='foreign_context':contexts['alarms']['knowledge'][0]['asset_id']='wrong'
    elif mutation=='truncated':contexts['devices']['truncated']=True
    elif mutation=='wrong_version':responses[0]['response']['kb_version']='other-v'
    else:responses[0]['response']['sources'][0]['text']='tampered original'
    with pytest.raises(ValueError):baseline(responses,contexts,candidate)

def source_csv(tmp_path, rows):
    path=tmp_path/'fault_case.csv'
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['ticket_id','source','title','phenomenon','root_cause','resolution'])
        writer.writeheader();writer.writerows(rows)
    return path,{'checksum':file_hash(path),'asset_id':'synthetic-asset','source_version':'r1'}

def row(ticket='T1',source='T1'):
    return {'ticket_id':ticket,'source':source,'title':'synthetic case','phenomenon':'quoted line\nsecond line','root_cause':'controlled cause','resolution':'controlled verification'}

def test_record_identity_preserves_logical_record_and_multiline_physical_lines(tmp_path):
    path,asset=source_csv(tmp_path,[row()]);result=record_roster(path,asset)
    record=result['records'][0]
    assert record['ticket_id']=='T1' and record['record']==1 and record['line_start']==2 and record['line_end']==3
    assert result['status']=='offline_source_reconstruction_not_live_vector_read'

@pytest.mark.parametrize('mutation',['source_label','duplicate_ticket','checksum'])
def test_record_roster_does_not_guess_source_label_or_duplicate_identity(tmp_path,mutation):
    rows=[row(source='custom-name')] if mutation=='source_label' else ([row(),row()] if mutation=='duplicate_ticket' else [row()])
    path,asset=source_csv(tmp_path,rows)
    if mutation=='checksum':asset['checksum']='different'
    with pytest.raises(ValueError):record_roster(path,asset)

def test_plan_package_rederivation_rejects_rewritten_hashes(monkeypatch,tmp_path):
    import scripts.prepare_agent_relation_plan as planner
    values={'plan.json':{'status':'designed_not_implemented'},'historical-baseline.json':{'total_citations':2,'associated_asset_citations':0}}
    monkeypatch.setattr(planner,'prepare',lambda:copy.deepcopy(values))
    expected={**values,'review.html':review(values)}
    for name,value in expected.items():
        (tmp_path/name).write_text(value if isinstance(value,str) else json.dumps(value),encoding='utf-8')
    altered={'status':'implemented'};(tmp_path/'plan.json').write_text(json.dumps(altered),encoding='utf-8')
    (tmp_path/'artifact-hashes.json').write_text(json.dumps({name:file_hash(tmp_path/name) for name in expected}),encoding='utf-8')
    with pytest.raises(ValueError,match='rederived'):validate_output(tmp_path)

def test_plan_review_escapes_record_text():
    values={'historical-baseline.json':{'total_citations':2,'associated_asset_citations':0},'plan.json':{'note':'<script>alert(1)</script>'}}
    page=review(values)
    assert '<script>' not in page and '&lt;script&gt;' in page
