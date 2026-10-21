"""Joint acceptance: feedback ownership and writes use an isolated SQL adapter."""
from unittest.mock import Mock,MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qa_core.api import route_qa
from qa_core.api.auth import AuthContext,get_auth_context


@pytest.fixture
def feedback_client(monkeypatch):
    app=FastAPI();app.include_router(route_qa.router)
    ctx=AuthContext('owner',['ops'],'api_key',tenant_id='tenant-a')
    app.dependency_overrides[get_auth_context]=lambda:ctx
    engine=MagicMock();conn=engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.first.return_value=('owner','tenant-a')
    monkeypatch.setattr(route_qa.sa,'create_engine',lambda *a,**k:engine)
    monkeypatch.setattr(route_qa,'audit_access',Mock())
    monkeypatch.setattr(route_qa,'_enforce_rate_limit',Mock())
    return TestClient(app),ctx,conn


@pytest.mark.parametrize('owner',[('other','tenant-a'),('owner','other-tenant'),None])
def test_feedback_inaccessible_message_never_inserts(feedback_client,owner):
    client,_,conn=feedback_client;conn.execute.return_value.first.return_value=owner
    response=client.post('/feedback',json={'message_id':'fixture-message','rating':'up'})
    assert response.status_code==(404 if owner is None else 403)
    assert conn.execute.call_count==1


def test_feedback_down_creates_feedback_and_bad_case_only_after_owner_check(feedback_client):
    client,_,conn=feedback_client
    response=client.post('/feedback',json={'message_id':'fixture-message','rating':'down','reason':'引用无关'})
    assert response.status_code==200 and response.json()['ok']
    assert conn.execute.call_count==3
    assert 'INSERT INTO feedback' in str(conn.execute.call_args_list[1].args[0])
    assert 'INSERT INTO bad_cases' in str(conn.execute.call_args_list[2].args[0])


def test_feedback_auditor_cannot_write(feedback_client):
    client,ctx,conn=feedback_client;ctx.roles[:]=['auditor']
    assert client.post('/feedback',json={'message_id':'fixture-message','rating':'up'}).status_code==403
    # Denial may write the security audit; it must never create feedback/bad cases.
    statements=[str(call.args[0]) for call in conn.execute.call_args_list]
    assert all('INSERT INTO feedback' not in sql and 'INSERT INTO bad_cases' not in sql for sql in statements)
