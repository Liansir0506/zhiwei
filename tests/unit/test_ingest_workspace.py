"""Admission, isolation and role boundaries without touching the live queue."""
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from qa_core.api.auth import AuthContext, get_auth_context
from qa_core.api import route_ingest_workspace as api
from qa_core.ingest import workspace as ws


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, 'ROOT', tmp_path/'writable')
    sealed=tmp_path/'sealed';(sealed/'raw').mkdir(parents=True)
    settings=SimpleNamespace(data_dir=str(sealed),mysql_dsn='not-a-database')
    version='local-work-'+'a'*12
    candidate={'version':version,'protocol':ws.PROTOCOL,'binding':{}}
    monkeypatch.setattr(ws,'get_candidate',lambda v: candidate if v==version else (_ for _ in ()).throw(HTTPException(409,'sealed')))
    monkeypatch.setattr(ws,'verify_candidate',lambda *a,**kw:'STAGED')
    @contextmanager
    def guard(*a,**kw): yield
    monkeypatch.setattr(ws,'candidate_write',guard)
    ctx=AuthContext('fixture',['admin'],'api_key')
    return settings,ctx,version,sealed


def test_pending_is_quarantined_and_idempotent(isolated,monkeypatch):
    settings,ctx,v,sealed=isolated
    monkeypatch.setattr(ws,'admission',lambda *args:None)
    queue=Mock(side_effect=AssertionError('pending must never queue'));monkeypatch.setattr(ws,'queue',queue)
    first,created=ws.upload(settings,ctx,v,'manual_new.md','new-source',b'new unreviewed content')
    duplicate,again=ws.upload(settings,ctx,v,'manual_new.md','new-source',b'new unreviewed content')
    assert created and not again and first==duplicate
    assert first['state']=='awaiting_review' and first['job_id'] is None
    assert ws.safe_path('uploads',first['id'],'manual_new.md').read_bytes()==b'new unreviewed content'
    assert not list(sealed.rglob('manual_new.md'));queue.assert_not_called()


@pytest.mark.parametrize('filename',['../manual_a.md','manual_/a.md','manual_a.exe','other_a.md','manual_中文.md'])
def test_invalid_filename_never_writes(isolated,filename):
    settings,ctx,v,sealed=isolated
    with pytest.raises(HTTPException):ws.upload(settings,ctx,v,filename,'s',b'x')
    assert not ws.ROOT.exists() and not list(sealed.rglob(filename.replace('../','')))


def test_unmanaged_candidate_rejected_before_write(isolated):
    settings,ctx,_,_=isolated
    with pytest.raises(HTTPException) as error:ws.upload(settings,ctx,'sealed-old','manual_a.md','s',b'x')
    assert error.value.status_code==409 and not ws.ROOT.exists()


def test_exact_approved_source_required(isolated,monkeypatch):
    settings,_,_,sealed=isolated
    filename='faq_original.csv';body=b'question,answer\nQ,A\n';checksum=hashlib.sha256(body).hexdigest()
    (sealed/'raw'/filename).write_bytes(body)
    meta={'source_id':'stable','tenant_id':'default','allowed_roles':['ops','admin']}
    entry={'filename':filename,'source_id':'stable','tenant_id':'default','sha256':checksum}
    (sealed/'raw/meta.json').write_text(json.dumps({filename:meta}))
    (sealed/'source-map.json').write_text(json.dumps({'entries':[entry]}))
    (sealed/'artifact-hashes.json').write_text('{}')
    from qa_core.ingest import source_identity
    validate=Mock();monkeypatch.setattr(source_identity,'validate_mapping',validate)
    assert ws.admission(settings,filename,'stable',checksum)['metadata']==meta
    validate.assert_called_once()
    assert ws.admission(settings,filename,'different',checksum) is None
    assert ws.admission(settings,filename,'stable','b'*64) is None
    (sealed/'raw'/filename).write_bytes(b'changed')
    assert ws.admission(settings,filename,'stable',checksum) is None


def test_candidate_code_or_collection_change_rejected(monkeypatch):
    monkeypatch.setattr(ws,'profile_binding',lambda _: {'current':'new'})
    with pytest.raises(HTTPException) as error:ws.verify_candidate(None,{'binding':{'current':'old'}})
    assert error.value.status_code==409


def test_path_escape_rejected(isolated):
    with pytest.raises(ValueError):ws.safe_path('..','sealed','manual_a.md')


@pytest.mark.parametrize('role,tenant,dept',[('ops','default',[]),('auditor','default',[]),('admin','other',[]),('admin','default',['limited'])])
def test_writer_requires_unrestricted_global_admin(role,tenant,dept):
    ctx=AuthContext('fixture',[role],'api_key',tenant_id=tenant,dept_ids=dept)
    with pytest.raises(HTTPException) as error:api.writer(ctx)
    assert error.value.status_code==403


def test_success_task_cannot_retry_or_mutate_upload(monkeypatch):
    record={'job_id':'job','kb_version':'local-work-'+'a'*12}
    monkeypatch.setattr(api,'get_settings',lambda:None)
    monkeypatch.setattr(ws,'get_upload',lambda _:record)
    monkeypatch.setattr(ws,'get_candidate',lambda _: {})
    monkeypatch.setattr(ws,'verify_candidate',lambda *a,**kw:None)
    queue=Mock();monkeypatch.setattr(ws,'queue',queue)
    dao=SimpleNamespace(get=lambda *a:{'status':'success'},engine=SimpleNamespace(dispose=Mock()))
    monkeypatch.setattr(api,'jobs',lambda _:dao)
    with pytest.raises(HTTPException):api.retry('id',AuthContext('fixture',['admin'],'api_key'))
    queue.assert_not_called();dao.engine.dispose.assert_called_once()


def test_unmanaged_http_upload_rejected_without_reading_body(monkeypatch):
    monkeypatch.setattr(api,'get_settings',lambda:None)
    def rejected(_): raise HTTPException(409,'sealed candidate')
    monkeypatch.setattr(ws,'get_candidate',rejected)
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_auth_context]=lambda:AuthContext('fixture',['admin'],'api_key')
    response=TestClient(app).post('/admin/ingest-workspace/uploads?kb_version=old&source_id=s',content=b'anything',headers={'X-Filename':'manual_a.md'})
    assert response.status_code==409 and response.headers['cache-control']=='no-store'


def test_worker_validates_payload_before_claimable_execution(monkeypatch):
    from scripts import workspace_ingest_worker as worker
    with pytest.raises(ValueError):worker.validate_job({'payload_json':{},'tenant_id':'default','job_type':'upload'},{},None,'pin')
    monkeypatch.setattr(ws,'profile_binding',lambda _: {})
    monkeypatch.setattr(ws,'get_candidate',lambda _: {'binding':{}})
    monkeypatch.setattr(ws,'verify_candidate',lambda *a,**kw:None)
    monkeypatch.setattr(ws,'settings_for',lambda *a:None)
    monkeypatch.setattr(worker,'execution_binding',lambda *a:{'correct':True})
    record={'id':'id','kb_version':'v','job_id':'job','sha256':'sha','source_id':'s','filename':'faq_a.csv'}
    payload={'workspace_protocol':ws.PROTOCOL,'workspace_upload_id':'id','candidate_binding_sha256':ws.digest({}),
        'execution_binding':{'wrong':True},'sha256':'sha','source_id':'s','filename':'faq_a.csv'}
    with pytest.raises(ValueError):worker.validate_job({'payload_json':payload,'tenant_id':'default','job_type':'upload','job_id':'job','kb_version':'v'},record,None,ws.digest({}))


def test_running_worker_rejects_changed_disk_or_runtime_binding_before_claim(monkeypatch):
    from scripts import workspace_ingest_worker as worker
    monkeypatch.setattr(ws,'profile_binding',lambda _: {'current':'new'})
    with pytest.raises(worker.WorkerBindingChanged):worker.verify_start_binding(None,ws.digest({'current':'old'}))
    worker.verify_start_binding(None,ws.digest({'current':'new'}))


@pytest.mark.parametrize('change',[{'generation_enabled':True},{'doc_collection':'other'},{'data_dir':'C:/outside'}])
def test_worker_profile_rejects_generation_collection_or_path_change(isolated,change):
    from scripts import workspace_ingest_worker as worker
    from qa_core.config.settings import Settings
    base=Settings(_env_file=None)
    selected=base.model_copy(update={'data_dir':str(ws.ROOT),'reports_dir':str(ws.ROOT/'reports'),
        'generation_enabled':False,'agent_enabled':False,'llm_api_key':'','llm_base_url':'http://127.0.0.1:1/disabled'})
    with pytest.raises(ValueError):worker.apply_worker_profile(base,selected.model_copy(update=change))


def test_worker_preserves_process_with_wrong_ownership(monkeypatch):
    from scripts import workspace_ingest_worker as worker
    process=SimpleNamespace(cmdline=lambda:['unrelated'],create_time=lambda:100,cwd=lambda:str(ws.PROJECT),terminate=Mock())
    monkeypatch.setattr(worker.psutil,'Process',lambda _:process)
    with pytest.raises(ValueError):worker.owned({'pid':123,'created_at':100,'token':'token'})
    process.terminate.assert_not_called()
