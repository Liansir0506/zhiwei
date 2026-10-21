import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.api.route_version_workspace import capabilities, Manager, make_router
from qa_core.quality import version_workspace as vw

def ctx(role='admin',**scope):return AuthContext('actor',[role],'api_key',**scope)

@pytest.mark.parametrize('role,scope,read,write',[
    ('admin',{},True,True),('auditor',{},True,False),('ops',{},False,False),('guest',{},False,False),
    ('admin',{'tenant_id':'other'},False,False),('admin',{'dept_ids':['ran']},False,False),
    ('admin',{'device_types':['AMF']},False,False),('admin',{'security_levels':['internal']},False,False),
    ('auditor',{'tenant_id':'other'},False,False)])
def test_capabilities_do_not_expand_global_permissions(role,scope,read,write):
    actual=capabilities(ctx(role,**scope));assert actual['can_read_versions'] is read;assert actual['can_manage_versions'] is write

@pytest.fixture
def ws(tmp_path,monkeypatch):
    monkeypatch.setattr(vw,'ROOT',tmp_path)
    settings=SimpleNamespace(mysql_dsn='mysql+pymysql://rag:private@127.0.0.1:13306/test_version_flow_aaaaaaaaaaaa',
        milvus_uri='http://127.0.0.1:29530',doc_collection='test_version_flow_aaaaaaaaaaaa_doc',
        faq_collection='test_version_flow_aaaaaaaaaaaa_faq',auth_required=True,generation_enabled=False,agent_enabled=False)
    return vw.Workspace(settings,tmp_path/'session',sandbox=True)

@pytest.mark.parametrize('field,value',[('mysql_dsn','mysql+pymysql://rag:private@127.0.0.1:3306/rag_kb'),
    ('milvus_uri','http://127.0.0.1:19530'),('doc_collection','old_doc'),('generation_enabled',True),('agent_enabled',True),('auth_required',False)])
def test_execution_profile_rejects_original_or_unsafe_namespace(ws,field,value):
    data=vars(ws.settings).copy();data[field]=value
    with pytest.raises(ValueError):vw.Workspace(SimpleNamespace(**data),ws.root,sandbox=True)

def test_record_identifiers_and_root_cannot_escape(ws):
    with pytest.raises(ValueError):ws.path('../outside')
    with pytest.raises(HTTPException) as error:ws.read('plans','../../secret')
    assert error.value.status_code==404

def plan(ws,**change):
    payload=dict(id='a'*32,actor='actor',expires_at=time.time()+600,confirmation='publish:next',action='publish',version='next')
    payload.update(change);ws.write('plans',payload);return payload

@pytest.mark.parametrize('change,actor,confirmation,status',[
    ({},'other','publish:next',403),({'expires_at':0},'actor','publish:next',409),({},'actor','next',400)])
def test_execution_refuses_actor_expiry_and_incomplete_confirmation_without_sql(ws,change,actor,confirmation,status,monkeypatch):
    record=plan(ws,**change)
    def forbidden():raise AssertionError('Must reject before SQL')
    monkeypatch.setattr(ws,'publication_lock',forbidden)
    with pytest.raises(HTTPException) as error:ws.execute(record['id'],confirmation,actor)
    assert error.value.status_code==status

def test_original_pointer_execution_never_enabled(ws):
    record=plan(ws);ws.sandbox=False
    with pytest.raises(HTTPException) as error:ws.execute(record['id'],'publish:next','actor')
    assert error.value.status_code==409

def test_restarted_gate_is_interrupted_and_not_replayed(ws):
    ws.write('jobs',dict(id='b'*32,state='running',instance='previous',actor='actor'))
    result=Manager().read(ws,'b'*32);assert result['state']=='interrupted';assert 'instance' not in result
    assert ws.read('jobs','b'*32)['state']=='interrupted'

def test_reviewer_change_refuses_gate(ws,monkeypatch):
    ws.write('reviews',dict(id='c'*32,actor='other',version='next',fingerprint='old'))
    monkeypatch.setattr(ws,'inspection',lambda _:pytest.fail('Do not inspect after actor denial'))
    with pytest.raises(HTTPException) as error:ws.check_review('c'*32,'actor')
    assert error.value.status_code==403

def test_snapshot_change_invalidates_engineering_review(ws,monkeypatch):
    ws.write('reviews',dict(id='c'*32,actor='actor',version='next',fingerprint=vw.digest({'snapshot':'before'})))
    monkeypatch.setattr(ws,'inspection',lambda _:{'snapshot':'after'})
    with pytest.raises(HTTPException) as error:ws.check_review('c'*32,'actor')
    assert error.value.status_code==409

def test_request_validation_is_422_and_no_store_not_503(ws):
    app=FastAPI();app.include_router(make_router(lambda:ws));app.dependency_overrides[get_auth_context]=lambda:ctx()
    with TestClient(app) as client:
        response=client.post('/admin/version-workspace/reviews',json={'version':'next','approved':True})
        assert response.status_code==422;assert response.headers['cache-control']=='no-store'

def test_failed_storage_response_does_not_leak_credentials(ws,monkeypatch):
    monkeypatch.setattr(ws,'versions',lambda:(_ for _ in ()).throw(RuntimeError(ws.settings.mysql_dsn)))
    app=FastAPI();app.include_router(make_router(lambda:ws));app.dependency_overrides[get_auth_context]=lambda:ctx()
    with TestClient(app) as client:
        response=client.get('/admin/version-workspace');assert response.status_code==503
        assert 'private' not in response.text;assert response.headers['cache-control']=='no-store'

def test_each_version_probe_owns_its_milvus_channel(ws,monkeypatch):
    import pymilvus
    aliases=[];closed=[]
    class Client:
        def __init__(self,**kwargs):self.alias=kwargs['alias'];aliases.append(self.alias)
        def close(self):closed.append(self.alias)
    monkeypatch.setattr(pymilvus,'MilvusClient',Client)
    with ws.client():
        with ws.client():pass
    assert len(set(aliases))==2 and set(closed)==set(aliases)
    assert all(alias.startswith('version-workspace-') for alias in aliases)
