"""Original pending domain is never used as a fallback for the new workspace."""
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api.auth import AuthContext,get_auth_context
from qa_core.api import route_business_workspace as api
from scripts import readonly_workspace_runtime as rt

@pytest.fixture
def setup(monkeypatch):
    ctx=AuthContext('ops',['ops'],'api_key')
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_auth_context]=lambda:ctx
    def denied(*args):raise ValueError('private profile must not be exposed')
    monkeypatch.setattr(rt,'validate_profile',denied)
    monkeypatch.setattr(rt,'verify_admission',lambda *args:pytest.fail('Unapproved service must not query admitted rows'))
    return TestClient(app),ctx

@pytest.mark.parametrize('path',['/business/devices','/business/alarms','/business/tickets','/business/changes','/business/integrations','/business/devices/id'])
def test_original_pending_never_read(setup,path):
    response=setup[0].get(path)
    assert response.status_code==503 and response.headers['cache-control']=='no-store'
    assert 'private' not in response.text

@pytest.mark.parametrize('roles,levels',[(['guest'],[]),(['ops'],['public'])])
def test_access_check_before_profile(setup,monkeypatch,roles,levels):
    client,ctx=setup;ctx.roles=roles;ctx.security_levels=levels
    monkeypatch.setattr(rt,'validate_profile',lambda *args:pytest.fail('No profile read for denied roles'))
    assert client.get('/business/devices').status_code==403

def test_changed_admission_returns_generic_503(setup,monkeypatch):
    monkeypatch.setattr(rt,'validate_profile',lambda *args:None)
    def unavailable(*args):raise sa.exc.OperationalError('private DSN',{},Exception('private DB error'))
    monkeypatch.setattr(rt,'verify_admission',unavailable)
    response=setup[0].get('/business/devices')
    assert response.status_code==503 and 'private' not in response.text
    assert response.headers['cache-control']=='no-store'

def test_no_business_writes_registered(setup):
    response=setup[0].post('/business/devices',json={'name':'unauthorized'})
    assert response.status_code==405
