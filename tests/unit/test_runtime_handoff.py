import time
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.api.route_version_workspace import make_router, HandoffManager
from qa_core.quality import runtime_handoff as handoff
from qa_core.quality.version_workspace import Workspace
from qa_core.ingest.binding import digest


@pytest.fixture
def ws(tmp_path,monkeypatch):
    import qa_core.quality.version_workspace as version
    monkeypatch.setattr(version,'ROOT',tmp_path)
    return Workspace(SimpleNamespace(),tmp_path/'original')


def test_sandbox_cannot_claim_original_handoff(ws):
    ws.sandbox=True
    with pytest.raises(HTTPException) as error:handoff.Preparer(ws)
    assert error.value.status_code==409


@pytest.mark.parametrize('version,status',[('current','pending'),('other','running'),('other','retrying')])
def test_affected_or_executing_jobs_block_preparation(version,status):
    with pytest.raises(HTTPException) as error:handoff.attest_jobs([{'job_id':'job','kb_version':version,'status':status}],{'current','recovery'})
    assert error.value.status_code==409


def test_unrelated_pending_history_remains_quarantined_not_cancelled():
    source=[{'job_id':'old','kb_version':'old-incompatible','status':'pending'}]
    result=handoff.attest_jobs(source,{'current','recovery'})
    assert result[0]['disposition']=='quarantined_history_not_claimed_or_modified'
    assert source==[{'job_id':'old','kb_version':'old-incompatible','status':'pending'}]


def test_prepare_is_receipt_only_no_execution_or_admission(ws,monkeypatch):
    prep=handoff.Preparer(ws)
    monkeypatch.setattr(ws,'publication_lock',lambda:nullcontext())
    monkeypatch.setattr(prep,'inspect',lambda target:{'target':{'version':target},'execution_authorized':False})
    result=prep.prepare('next','admin')
    assert result['state']=='prepared_for_review' and result['execution_authorized'] is False
    assert ws.read('runtime-handoffs',result['id'])==result
    assert not ws.path('executions').exists()


@pytest.mark.parametrize('actor,expires,status',[('other',time.time()+100,403),('admin',0,409)])
def test_owner_and_expiry_refused_before_live_inspection(ws,actor,expires,status,monkeypatch):
    ws.write('runtime-handoffs',dict(id='a'*32,actor='admin',expires_at=expires))
    prep=handoff.Preparer(ws)
    monkeypatch.setattr(ws,'publication_lock',lambda:pytest.fail('No dependency reads after denial'))
    with pytest.raises(HTTPException) as error:prep.revalidate('a'*32,actor)
    assert error.value.status_code==status


@pytest.mark.parametrize('changed',[False,True])
def test_revalidation_binds_all_inspection_fields(ws,changed,monkeypatch):
    value={'target':{'version':'next'},'full_configuration':'one','runtime_pid':1,'snapshot':'bound'}
    ws.write('runtime-handoffs',dict(id='a'*32,actor='admin',expires_at=time.time()+100,inspection=value,fingerprint=digest(value)))
    prep=handoff.Preparer(ws);monkeypatch.setattr(ws,'publication_lock',lambda:nullcontext())
    newer=dict(value,runtime_pid=2) if changed else value
    monkeypatch.setattr(prep,'inspect',lambda _:newer)
    if changed:
        with pytest.raises(HTTPException) as error:prep.revalidate('a'*32,'admin')
        assert error.value.status_code==409
    else:
        result=prep.revalidate('a'*32,'admin');assert result['state']=='revalidated' and result['execution_authorized'] is False


@pytest.mark.parametrize('role,scope,status',[
    ('ops',{},403),('guest',{},403),('auditor',{},403),('admin',{'tenant_id':'foreign'},403),
    ('admin',{'dept_ids':['ran']},403),('admin',{'device_types':['AMF']},403)])
def test_prepare_does_not_expand_permissions(ws,role,scope,status):
    app=FastAPI();app.include_router(make_router(lambda:ws))
    app.dependency_overrides[get_auth_context]=lambda:AuthContext('actor',[role],'api_key',**scope)
    with TestClient(app) as client:
        response=client.post('/admin/version-workspace/runtime-handoffs',json={'version':'next'})
    assert response.status_code==status and response.headers['cache-control']=='no-store'
    assert not ws.path('runtime-handoff-jobs').exists()


def test_caller_cannot_supply_profile_or_authorize_switch(ws):
    app=FastAPI();app.include_router(make_router(lambda:ws));app.dependency_overrides[get_auth_context]=lambda:AuthContext('actor',['admin'],'api_key')
    with TestClient(app) as client:
        response=client.post('/admin/version-workspace/runtime-handoffs',json={'version':'next','approved':True,'mysql_dsn':'mysql://secret'})
    assert response.status_code==422 and 'secret' not in response.text


def test_restarted_handoff_is_interrupted_without_replay(ws):
    ws.write('runtime-handoff-jobs',dict(id='b'*32,state='running',instance='previous'))
    result=HandoffManager().read(ws,'b'*32)
    assert result['state']=='interrupted' and 'instance' not in result


def test_no_handoff_execution_endpoint(ws):
    router=make_router(lambda:ws)
    assert not any(route.path.endswith('/runtime-handoffs/{ident}/execute') for route in router.routes)


def test_profile_secrets_not_serialized(ws,monkeypatch,tmp_path):
    # Test the real serializer path with a fake live snapshot/report, not a copied response.
    from scripts.prepare_original_citation_release import public_profile
    from qa_core.config.settings import Settings
    release=SimpleNamespace(VERSION='current',ORIGINAL_SERVER='legacy',owned=SimpleNamespace(public_profile=public_profile))
    from scripts.prepare_legacy_projection import seal,write
    package=tmp_path/'package';package.mkdir();write(package/'source.json',{'approved':True});seal(package)
    settings=Settings(_env_file=None,data_dir=str(package),mysql_dsn='mysql+pymysql://hidden:password@127.0.0.1:3306/rag_kb',llm_api_key='private-model-key')
    snapshot={'files':[],'collections':{},'sources':[]}
    report=tmp_path/'report.json';write(report,{'passed':True})
    monkeypatch.setattr(handoff,'verify_read_binding',lambda *a:{'snapshot':snapshot})
    monkeypatch.setattr(handoff.publication,'load_bound_report',lambda *a:{'snapshot':snapshot,'checks':[]})
    monkeypatch.setattr(handoff.publication,'capture_snapshot',lambda *a:snapshot)
    monkeypatch.setattr(ws,'client',lambda:nullcontext(object()))
    from qa_core.security import withdrawals
    monkeypatch.setattr(withdrawals,'assert_publishable_sources',lambda *a:None)
    result=handoff.Preparer(ws).inspect_profile(release,release.VERSION,settings,{'status':'ACTIVE','quality_report_path':str(report)})
    import json
    body=json.dumps(result)
    assert 'private-model-key' not in body and 'password' not in body and 'hidden:' not in body
    assert result['settings_field_count']==len(settings.model_dump())
