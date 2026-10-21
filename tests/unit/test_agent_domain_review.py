"""Content/ownership review boundaries; all approvals here are synthetic fixtures."""
import copy
import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.prepare_agent_domain_review import audit_snapshot, prepare, render_review, digest, file_hash, validate_package, acceptance_plan

NOW = '2026-09-19T00:00:00+00:00'


@pytest.fixture
def raw():
    return {
        'tenants': [{'tenant_id':'default','name':'SYNTHETIC fixture'}],
        'departments': [{'dept_id':'ran-ops','tenant_id':'default','name':'ran'}, {'dept_id':'core-ops','tenant_id':'default','name':'core'}],
        'users': [{'user_id':'u','tenant_id':'default','username':'synthetic'}],
        'user_departments': [{'user_id':'u','dept_id':'ran-ops','is_primary':True}],
        'devices': [{'device_id': 'SIM-DEV-0001','tenant_id':'default','dept_id':'ran-ops','name':'synthetic AMF','device_type':'AMF'},
                    {'device_id': 'SIM-DEV-0002','tenant_id':'default','dept_id':'core-ops','name':'synthetic SMF','device_type':'SMF'}],
        'alarms': [{'alarm_id':'SIM-ALM-000001','tenant_id':'default','device_id':'SIM-DEV-0001','alarm_code':'ALM-AMF-REG','source_system':'fixture','occurred_at':NOW},
                   {'alarm_id':'SIM-ALM-000002','tenant_id':'default','device_id':'SIM-DEV-0002','alarm_code':'ALM-SMF-PDU','source_system':'fixture','occurred_at':NOW}],
        'monitoring_metrics': [{'observation_id':'SIM-OBS-000001','tenant_id':'default','device_id':'SIM-DEV-0001','metric_name':'cpu','metric_value':3.0,'source_system':'fixture','sampled_at':NOW},
                               {'observation_id':'SIM-OBS-000002','tenant_id':'default','device_id':'SIM-DEV-0002','metric_name':'cpu','metric_value':4.0,'source_system':'fixture','sampled_at':NOW}],
        'work_orders': [{'ticket_id':'SIM-TT-00001','tenant_id':'default','dept_id':'ran-ops','device_id':'SIM-DEV-0001','alarm_id':'SIM-ALM-000001','title':'synthetic case','source_system':'fixture','opened_at':NOW,'assignee_user_id':'u'}],
        'changes': [{'change_id':'SIM-CHG-00001','tenant_id':'default','dept_id':'ran-ops','device_id':'SIM-DEV-0001','ticket_id':'SIM-TT-00001','title':'synthetic change','change_type':'test','source_system':'fixture','operator_user_id':'u'}],
        'knowledge_assets': [], 'knowledge_relations': []}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def inputs(tmp_path, raw):
    source = tmp_path / 'source.json'; dump(source, raw)
    projection = tmp_path / 'projection'; csv_path = projection / 'raw/fault_case_simulated_v4_01.csv'
    csv_path.parent.mkdir(parents=True)
    row = {'ticket_id':'SIM-TT-00001','source':'SIM-TT-00001','title':'synthetic case','alarm_code':'ALM-AMF-REG','equipment_type':'AMF','phenomenon':'SIM-DEV-0001 appears in a synthetic case'}
    with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    filename = csv_path.name
    dump(projection / 'source-map.json', {'entries':[{'filename':filename,'sha256':file_hash(csv_path),'source_id':'fixture-case','source_system':'fixture','tenant_id':'default','approval':{'status':'approved','evidence':'SYNTHETIC TEST ONLY'}}]})
    dump(projection / 'raw/meta.json', {filename:{'asset_id':'fixture-asset','tenant_id':'default','dept_id':'public','review_status':'human_chat_approved','synthetic':True,'source_id':'fixture-case','source_system':'fixture','source_version':'fixture-v','security_level':'internal','visibility':'public','role_scope':[],'status':'active','doc_type':'fault_case'}})
    dump(projection / 'approval-receipt.json', {'synthetic_test_only':True})
    return source, projection


def test_pending_minimal_candidate_preserves_source_rows_and_does_not_copy_knowledge(inputs, raw):
    envelope, source_audit, audit = prepare(*inputs)
    assert source_audit['relation_and_time_valid'] and audit['relation_and_time_valid']
    assert envelope['review_status'] == 'pending' and envelope['record_count'] == 16
    assert not envelope['snapshot']['tenants'] and not envelope['snapshot']['knowledge_assets']
    assert len(envelope['binding']['source_rows']) == 12
    assert len(envelope['snapshot']['knowledge_relations']) == 4
    assert envelope['snapshot']['devices'] == raw['devices']
    assert envelope['binding']['csv_record']['number'] == 1


@pytest.mark.parametrize('table,field,value,code', [
    ('work_orders','alarm_id','SIM-ALM-000002','ticket_alarm_device_mismatch'),
    ('changes','device_id','SIM-DEV-0002','change_ticket_device_mismatch'),
    ('devices','tenant_id','foreign','department_ownership'),
    ('monitoring_metrics','device_id','absent','device_ownership'),
    ('work_orders','dept_id','absent','department_ownership'),
    ('user_departments','dept_id','absent','user_department_ownership'),
    ('alarms','occurred_at','2026-09-19T00:00:00','timezone_missing'),
    ('work_orders','assignee_user_id','absent','ticket_user_ownership'),
    ('changes','operator_user_id','absent','change_user_ownership'),
])
def test_relationship_or_time_failure_caught_when_shape_is_valid(raw, table, field, value, code):
    raw[table][0][field] = value
    audit = audit_snapshot(raw)
    assert audit['schema_valid'] and not audit['relation_and_time_valid']
    assert code in audit['issue_counts']


def test_wrong_type_and_nonfinite_metrics_rejected(raw):
    raw['devices'][0]['device_id'] = 123
    with pytest.raises(ValidationError): audit_snapshot(raw)
    raw['devices'][0]['device_id'] = 'SIM-DEV-0001'
    raw['monitoring_metrics'][0]['metric_value'] = float('nan')
    with pytest.raises(ValueError): audit_snapshot(raw)


@pytest.mark.parametrize('table,code', [('devices','duplicate_id'),('user_departments','duplicate_membership')])
def test_duplicate_identity_is_rejected(raw, table, code):
    raw[table].append(copy.deepcopy(raw[table][0]))
    assert code in audit_snapshot(raw)['issue_counts']


def test_relation_type_is_not_just_an_id_match(raw):
    raw['knowledge_assets'] = [{'asset_id':'k','tenant_id':'default','filename':'x.md','doc_type':'manual','source_system':'fixture'}]
    raw['knowledge_relations'] = [{'asset_id':'k','relation_type':'change','relation_id':'SIM-DEV-0001'}]
    assert 'typed_knowledge_relation' in audit_snapshot(raw)['issue_counts']


def test_utc_instants_compared_instead_of_lexical_offsets(raw):
    raw['work_orders'][0].update(opened_at='2026-09-19T10:00:00+08:00',closed_at='2026-09-19T01:00:00+00:00')
    assert 'reversed_ticket_time' in audit_snapshot(raw)['issue_counts']


@pytest.mark.parametrize('alteration', ['pending_source','csv_changed','wrong_ticket','relation_changed','metadata_scope'])
def test_candidate_requires_approved_content_and_actual_case_link(inputs, alteration):
    source, projection = inputs
    if alteration == 'pending_source':
        path = projection / 'source-map.json'; data = json.loads(path.read_text()); data['entries'][0]['approval']['status']='pending'; dump(path,data)
    elif alteration == 'csv_changed':
        with (projection / 'raw/fault_case_simulated_v4_01.csv').open('a') as handle: handle.write('changed')
    elif alteration == 'wrong_ticket':
        data = json.loads(source.read_text()); data['work_orders'][0]['title']='different'; dump(source,data)
    elif alteration == 'relation_changed':
        data = json.loads(source.read_text()); data['changes'][0]['device_id']='SIM-DEV-0002'; dump(source,data)
    else:
        path=projection/'raw/meta.json'; data=json.loads(path.read_text()); data[next(iter(data))]['tenant_id']='foreign'; dump(path,data)
    with pytest.raises(ValueError): prepare(source,projection)


def test_review_text_is_escaped_and_has_no_import_action(inputs):
    envelope, _, _ = prepare(*inputs)
    envelope['snapshot']['devices'][0]['name']='<script>malicious()</script>'
    page=render_review(envelope)
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert 'pending' in page and '不批准、导入' in page


def test_package_validation_rederives_plan_after_hash_rewrite(inputs, tmp_path):
    source,projection=inputs; envelope,source_audit,candidate_audit=prepare(source,projection)
    package=tmp_path/'review'; package.mkdir()
    values={'candidate.json':envelope,'source-audit.json':source_audit,'candidate-audit.json':candidate_audit,'acceptance-plan.json':acceptance_plan()}
    for name,value in values.items(): dump(package/name,value)
    (package/'review.html').write_text(render_review(envelope),encoding='utf-8')
    hashes={name:file_hash(package/name) for name in [*values,'review.html']}; dump(package/'artifact-hashes.json',hashes)
    assert validate_package(package,source,projection)['review_status']=='pending'
    values['acceptance-plan.json']['execution']['paid_llm_calls']=4; dump(package/'acceptance-plan.json',values['acceptance-plan.json'])
    hashes['acceptance-plan.json']=file_hash(package/'acceptance-plan.json'); dump(package/'artifact-hashes.json',hashes)
    with pytest.raises(ValueError): validate_package(package,source,projection)


@pytest.mark.parametrize('env,dsn,generation', [('dev','mysql+pymysql://rag@127.0.0.1:13306/test_legacy_stage_fb36d947ab67',False),
    ('test','mysql+pymysql://rag@127.0.0.1:13306/rag_kb_test',False),
    ('test','mysql+pymysql://rag@127.0.0.1:3306/test_legacy_stage_fb36d947ab67',False),
    ('test','mysql+pymysql://rag@127.0.0.1:13306/test_legacy_stage_fb36d947ab67',True)])
def test_admission_cannot_use_original_or_shared_storage(inputs,env,dsn,generation):
    from qa_core.config.settings import Settings
    from scripts.execute_agent_domain_admission import check_target
    candidate,_,_=prepare(*inputs)
    settings=Settings(_env_file=None,app_env=env,auth_required=True,generation_enabled=generation,mysql_dsn=dsn,
        milvus_uri='http://127.0.0.1:29530',doc_collection='test_legacy_stage_fb36d947ab67_doc',faq_collection='test_legacy_stage_fb36d947ab67_faq')
    with pytest.raises(ValueError):check_target(settings,candidate)


def test_admission_normalizes_explicit_timezone_to_utc_without_changing_source(raw):
    from qa_core.domain.models import Alarm
    from scripts.execute_agent_domain_admission import utc_values
    model=Alarm.model_validate({**raw['alarms'][0],'occurred_at':'2026-09-19T08:00:00+08:00'})
    values=utc_values(model)
    assert values['occurred_at'].isoformat()=='2026-09-19T00:00:00'
    assert model.occurred_at.isoformat()=='2026-09-19T08:00:00+08:00'


@pytest.mark.parametrize('field,value',[('status','pending'),('candidate_sha256','changed'),('artifact_manifest_sha256','changed'),('record_count',12),('scope','production')])
def test_receipt_scope_and_content_mismatch_rejected_before_storage(monkeypatch,inputs,field,value):
    import scripts.execute_agent_domain_admission as executor
    candidate,_,_=prepare(*inputs)
    monkeypatch.setattr(executor,'validate_package',lambda *args:candidate)
    monkeypatch.setattr(executor,'load',lambda *args:candidate)
    monkeypatch.setattr(executor,'file_hash',lambda *args:'manifest')
    receipt={'protocol':executor.PROTOCOL,'status':'approved','scope':'isolated_local_demo_only','candidate_sha256':digest(candidate),
        'artifact_manifest_sha256':'manifest','target':candidate['target'],'record_count':16,'approved_at_utc':NOW,
        'evidence':{'answer':'批准16条，仅限隔离本地演示（推荐）','question_item_id':'["request_user_input_async","call_0773e14b44634178aa4503ded1c3a758",0]','synthetic_test_only':True}}
    receipt[field]=value
    with pytest.raises(ValueError):executor.validate_receipt('synthetic-test-package',receipt)
