"""Governance scope, denominators and exact-record mutation guards."""
from types import SimpleNamespace
from unittest.mock import Mock,MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api import route_admin as api
from qa_core.api.auth import AuthContext,get_auth_context


@pytest.fixture
def setup(monkeypatch):
    ctx=AuthContext('test-admin',['admin'],'api_key')
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_auth_context]=lambda:ctx
    engine=MagicMock();conn=engine.begin.return_value.__enter__.return_value
    monkeypatch.setattr(api,'_engine',lambda:engine)
    monkeypatch.setattr(api,'create_api_key',Mock())
    monkeypatch.setattr(api,'_latest_eval_report',lambda:{'file':'fixture.json','results':[],'metrics':{}})
    return TestClient(app),ctx,conn


@pytest.mark.parametrize('path',['/admin/ops','/admin/overview','/admin/overview/raw','/admin/eval'])
def test_other_tenant_cannot_read_global_records(setup,path):
    client,ctx,conn=setup;ctx.tenant_id='other'
    assert client.get(path).status_code==403
    conn.execute.assert_not_called()


def test_empty_eval_has_no_zero_latency_pass(setup):
    response=setup[0].get('/admin/eval')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    data=response.json()
    assert data['checks']['route_ok']=={'passed':0,'evaluated':0,'ratio':None}
    assert data['report']['independent_evaluation']=='未验证独立样本及冻结评测证明'


def test_eval_denominators_only_include_explicit_booleans(setup,monkeypatch):
    monkeypatch.setattr(api,'_latest_eval_report',lambda:{'results':[
        {'category':'faq','route_ok':True,'hit_ok':None,'reject_ok':None},
        {'category':'faq','route_ok':False,'hit_ok':True,'reject_ok':False},
        {'category':'faq','route_ok':'unknown','hit_ok':False,'reject_ok':None}]})
    data=setup[0].get('/admin/eval').json()
    assert data['checks']['route_ok']=={'passed':1,'evaluated':2,'ratio':.5}
    assert data['checks']['hit_ok']['evaluated']==2
    assert data['checks']['reject_ok']['evaluated']==1


@pytest.mark.parametrize('roles,eval_allowed,manage',[(['ops'],False,False),(['auditor'],True,False),(['admin'],True,True)])
def test_capabilities_match_actual_roles(setup,roles,eval_allowed,manage):
    client,ctx,_=setup;ctx.roles[:]=roles
    data=client.get('/admin/capabilities').json()
    assert data['can_read_ops'] and data['can_read_eval']==eval_allowed
    assert data['can_manage_keys']==manage


def test_revoke_exact_record_is_tenant_scoped(setup):
    client,ctx,conn=setup;ctx.tenant_id='tenant-a';conn.execute.return_value.rowcount=1
    assert client.delete('/admin/key-records/72').json()=={'ok':True,'revoked':1}
    sql,params=conn.execute.call_args.args
    assert params=={'id':72,'tenant':'tenant-a'}
    assert 'tenant_id=:tenant' in str(sql) and 'enabled=1' in str(sql)


def test_revoke_missing_record_conflict(setup):
    client,_,conn=setup;conn.execute.return_value.rowcount=0
    assert client.delete('/admin/key-records/72').status_code==409


def test_readonly_role_cannot_revoke(setup):
    client,ctx,conn=setup;ctx.roles[:]=['auditor']
    assert client.delete('/admin/key-records/72').status_code==403
    conn.execute.assert_not_called()


@pytest.mark.parametrize('roles',[[],['root'],['ops','unknown']])
def test_key_roles_validated_before_write(setup,roles):
    assert setup[0].post('/admin/keys',json={'key':'fixture-only-key','user_id':'test','roles':roles}).status_code==422
    api.create_api_key.assert_not_called()


def test_permission_schema_failure_does_not_fallback_to_all_tenants(setup):
    import sqlalchemy as sa
    client,_,conn=setup;conn.execute.side_effect=sa.exc.OperationalError('query',{},Exception('missing schema'))
    assert client.get('/admin/permissions').status_code==503
    assert conn.execute.call_count==1
