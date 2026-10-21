"""Real upload HTTP handler, isolated staging and in-memory queue; no live KB writes."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from qa_core.api import route_admin as api
from qa_core.api.auth import AuthContext, get_auth_context


@pytest.fixture
def upload_client(monkeypatch, tmp_path):
    ctx=AuthContext('fixture-admin',['admin'],'api_key')
    settings=SimpleNamespace(data_dir=str(tmp_path), mysql_dsn='isolated-fixture')
    monkeypatch.setattr(api,'get_settings',lambda:settings)
    monkeypatch.setattr(api,'version_binding',lambda s:{})
    monkeypatch.setattr(api,'binding_digest',lambda b:'fixture')
    monkeypatch.setattr(api,'execution_binding',lambda *a:{'fixture':True})
    @contextmanager
    def guard(version,**kwargs):
        if version != 'fixture-staged':raise HTTPException(409,'not STAGED')
        yield
    monkeypatch.setattr(api,'candidate_write',guard)
    jobs={}
    def create(**kwargs):
        key=kwargs['idempotency_key']
        if key in jobs:return jobs[key],False
        jobs[key]={'job_id':kwargs['job_id'],'status':'pending',**kwargs}
        return jobs[key],True
    from qa_core.ingest import jobs as module
    monkeypatch.setattr(module,'IngestJobDao',lambda dsn:SimpleNamespace(create=create))
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_auth_context]=lambda:ctx
    return TestClient(app),ctx,tmp_path,jobs


def submit(client,version='fixture-staged',source='fixture-source',content=b'isolation test'):
    return client.post('/admin/documents/upload',params={'kb_version':version,'source_id':source},
        content=content,headers={'X-Filename':'manual_fixture.md'})


def test_upload_queues_once_and_cleans_duplicate_staging(upload_client):
    client,_,root,jobs=upload_client
    first=submit(client);duplicate=submit(client)
    assert first.status_code==duplicate.status_code==202
    assert first.json()['created'] and not duplicate.json()['created']
    assert first.json()['job_id']==duplicate.json()['job_id']
    files=list(root.rglob('*.md'))
    assert len(files)==len(jobs)==1 and files[0].read_bytes()==b'isolation test'
    assert first.json()['status']=='pending'
    assert 'staging_path' not in first.text and 'isolated-fixture' not in first.text


def test_upload_active_candidate_conflict_cleans_temporary_file(upload_client):
    client,_,root,jobs=upload_client
    assert submit(client,version='fixture-active').status_code==409
    assert not list(root.rglob('*.md')) and not jobs


@pytest.mark.parametrize('kwargs',[{'source':''},{'version':''},{'content':b''}])
def test_upload_required_fields_do_not_queue(upload_client,kwargs):
    client,_,root,jobs=upload_client
    assert submit(client,**kwargs).status_code==400
    assert not list(root.rglob('*.md')) and not jobs


def test_ops_permission_denied_before_staging(upload_client):
    client,ctx,root,jobs=upload_client
    ctx.roles[:]=['ops']
    assert submit(client).status_code==403
    assert not list(root.iterdir()) and not jobs
