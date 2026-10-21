import copy
from pathlib import Path
import hashlib
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from qa_core.quality.record_preview_checks import check_snapshot
from scripts.sop_record_preview_server import admin


@pytest.fixture
def sample(tmp_path):
    metadata={}; files=[]
    for i in range(20):
        name=f'guide_{i}.md'; data=f'guide {i}'.encode(); (tmp_path/name).write_bytes(data)
        metadata[name]=dict(asset_id=f'asset-{i}',doc_type='guide',tenant_id='default',allowed_roles=['ops','admin'],
            role_scope=['ops','admin'],visibility='role_restricted',security_level='internal',status='active')
        files.append(dict(filename=name,asset_id=f'asset-{i}',doc_type='guide',status='done',chunk_count=9,
                          file_hash=hashlib.sha256(data).hexdigest()))
    current=dict(files=files,collections=dict(doc=dict(count=180),faq=dict(count=0)),sha='approved vector hash')
    return current,copy.deepcopy(current),metadata,tmp_path,[('test-sop-records-r2','STAGED')]


def test_integrity_does_not_grant_publication_or_truth(sample):
    result=check_snapshot(*sample)
    assert result['read_preview_ready'] and len(result['checks'])==7
    assert result['publication_eligible'] is False and result['full_sop_approved'] is False
    assert result['independent_quality'] is None and result['independent_denominator']==0


@pytest.mark.parametrize('mutation',['extra','missing','duplicate','failed','sop','chunks','asset','vector','faq','snapshot','active','extra_version','body','roles','tenant','visibility','pending'])
def test_changed_approved_scope_cannot_be_previewed(sample,mutation):
    current,expected,metadata,raw,status=sample
    if mutation=='extra':current['files'].append(dict(current['files'][0],filename='extra.md'))
    elif mutation=='missing':current['files'].pop()
    elif mutation=='duplicate':current['files'][-1]=copy.deepcopy(current['files'][0])
    elif mutation in {'failed','sop','chunks','asset'}:
        field,value={'failed':('status','failed'),'sop':('doc_type','sop'),'chunks':('chunk_count',8),'asset':('asset_id','other')}[mutation]
        current['files'][0][field]=value
    elif mutation=='vector':current['collections']['doc']['count']=179
    elif mutation=='faq':current['collections']['faq']['count']=1
    elif mutation=='snapshot':current['sha']='different vector hash'
    elif mutation=='active':status[:]=[('test-sop-records-r2','ACTIVE')]
    elif mutation=='extra_version':status.append(('old','STAGED'))
    elif mutation=='body':(raw/'guide_0.md').write_text('changed')
    else:
        field,value={'roles':('allowed_roles',['guest']),'tenant':('tenant_id','other'),
            'visibility':('visibility','public'),'pending':('status','reviewing')}[mutation]
        metadata['guide_0.md'][field]=value
    assert check_snapshot(*sample)['read_preview_ready'] is False


@pytest.mark.parametrize('roles,tenant,departments,devices,levels',[
    (['ops'],'default',[],[],[]),(['guest'],'default',[],[],[]),(['admin'],'other',[],[],[]),
    (['admin'],'default',['dept'],[],[]),(['admin'],'default',[],['AMF'],[]),(['admin'],'default',[],[],['public'])])
def test_preview_cannot_widen_access(roles,tenant,departments,devices,levels,monkeypatch):
    import scripts.sop_record_preview_server as server
    # Role gate's audit storage is unrelated to this pure scope test.
    def global_admin(ctx):
        if 'admin' not in ctx.roles or ctx.tenant_id!='default':raise HTTPException(403,'denied')
    monkeypatch.setattr(server,'require_global_admin',global_admin)
    with pytest.raises(HTTPException) as error:
        admin(SimpleNamespace(roles=roles,tenant_id=tenant,dept_ids=departments,device_types=devices,security_levels=levels))
    assert error.value.status_code==403


def test_default_unscoped_admin_allowed(monkeypatch):
    monkeypatch.setattr('scripts.sop_record_preview_server.require_global_admin',lambda ctx:None)
    ctx=SimpleNamespace(roles=['admin'],tenant_id='default',dept_ids=[],device_types=[],security_levels=[])
    assert admin(ctx) is ctx


@pytest.mark.parametrize('failure_at',[1,2])
def test_read_response_is_discarded_on_dependency_or_snapshot_failure(monkeypatch,failure_at):
    from fastapi.testclient import TestClient
    import scripts.sop_record_preview_server as server
    import qa_core.api.auth as auth
    ctx=SimpleNamespace(roles=['admin'],tenant_id='default',dept_ids=[],device_types=[],security_levels=[])
    monkeypatch.setattr(auth,'authenticate_headers',lambda **kw:ctx)
    monkeypatch.setattr(server,'require_global_admin',lambda ctx:None)
    calls=[]
    def verify(settings):
        calls.append(1)
        if len(calls)==failure_at:raise ValueError('dependency or snapshot unavailable')
        return dict(private_source='must never escape on drift')
    monkeypatch.setattr(server,'verify_live',verify)
    with TestClient(server.create_app('unit-test')) as client:
        result=client.get('/preview/checks',headers={'X-API-Key':'unit-fixture-only'})
        assert result.status_code==503 and 'private_source' not in result.text
        assert result.headers['Cache-Control']=='no-store'
        assert len(calls)==failure_at


@pytest.mark.parametrize('path',['/qa','/kb/activate','/kb/upload','/admin/keys'])
def test_unregistered_write_and_generation_never_touch_dependencies(monkeypatch,path):
    from fastapi.testclient import TestClient
    import scripts.sop_record_preview_server as server
    def unavailable(settings):raise AssertionError('readonly boundary must reject before dependency work')
    monkeypatch.setattr(server,'verify_live',unavailable)
    with TestClient(server.create_app('unit-test')) as client:
        result=client.post(path,json={})
        assert result.status_code==403 and result.headers['Cache-Control']=='no-store'


def test_stop_cleans_owned_key_when_windows_launcher_already_exited(monkeypatch,tmp_path):
    from contextlib import contextmanager
    import psutil
    import scripts.sop_record_preview_runtime as runtime
    state=dict(instance='fixture',actor='sop-record-preview-admin-fixture',key_hash='fixture-hash',status='running')
    monkeypatch.setattr(runtime,'record',lambda:state)
    def terminate():raise psutil.NoSuchProcess(123)
    monkeypatch.setattr(runtime,'owned_tree',lambda state:[SimpleNamespace(terminate=terminate)])
    monkeypatch.setattr(runtime,'ROOT',tmp_path)
    monkeypatch.setattr(runtime,'profile',lambda:SimpleNamespace(mysql_dsn='fixture'))
    executed=[]
    class Engine:
        @contextmanager
        def begin(self):
            yield SimpleNamespace(execute=lambda sql,params:executed.append(params))
        def dispose(self):pass
    monkeypatch.setattr(runtime.sa,'create_engine',lambda dsn:Engine())
    for name in ('api-key.txt','open-preview.html'):(tmp_path/name).write_text('fixture')
    assert runtime.stop()['owned_preview_key_removed']
    assert executed==[dict(h='fixture-hash',u='sop-record-preview-admin-fixture')]
    assert not (tmp_path/'api-key.txt').exists() and not (tmp_path/'open-preview.html').exists()


def test_stop_rejects_credential_ownership_mismatch_before_process_work(monkeypatch):
    import scripts.sop_record_preview_runtime as runtime
    monkeypatch.setattr(runtime,'record',lambda:dict(status='running',instance='fixture',actor='original-admin'))
    monkeypatch.setattr(runtime,'owned_tree',lambda state:pytest.fail('unowned process must not be touched'))
    with pytest.raises(ValueError,match='credential ownership'):
        runtime.stop()


@pytest.mark.parametrize('mutation',[None,'role','tenant','identity','type','checksum','department','retired','reviewer','version','duplicate','missing'])
def test_live_database_source_permissions_are_not_replaced_by_vector_metadata(sample,mutation):
    from qa_core.quality.record_preview_checks import asset_scope_matches
    _,_,metadata,raw,_=sample
    assets=[]
    for filename,m in metadata.items():
        assets.append(dict(m,filename=filename,kb_version='test-sop-records-r2',dept_id=None,effective_from=None,
                           effective_to=None,reviewed_by=None,checksum=hashlib.sha256((raw/filename).read_bytes()).hexdigest()))
    if mutation=='duplicate':assets[-1]=dict(assets[0])
    elif mutation=='missing':assets.pop()
    elif mutation:
        k,v={'role':('role_scope',['guest']),'tenant':('tenant_id','other'),'identity':('asset_id','other'),
             'type':('doc_type','sop'),'checksum':('checksum','tampered'),'department':('dept_id','new-dept'),
             'retired':('status','retired'),'reviewer':('reviewed_by','invented'),'version':('kb_version','other')}[mutation]
        assets[0][k]=v
    assert asset_scope_matches(assets,metadata,raw) is (mutation is None)



@pytest.fixture(scope='module')
def revision_matrix():
    from scripts.prepare_sop_record_alignment_v2 import load_verified
    return load_verified()


def test_exact_revision_counts_and_coverage_without_field_truth(revision_matrix):
    s=revision_matrix['summary']
    assert s['record_contracts']==109 and s['record_extensions']==49
    assert s['literal_gaps_addressed']=={'2':11,'4':9,'5':10,'7':19}
    assert s['field_evidence_verified']==s['operational_actions_approved']==s['independent_denominator']==0
    for entry in revision_matrix['entries']:
        assert all(entry['steps'][i-1]['text_status'].endswith('_missing') for i in (6,8,9))


@pytest.mark.parametrize('mutation',[
    'record','field_value','observed','extension_action','action','full_sop','root','manufacturer',
    'step_action','step_evidence','wrong_sha','wrong_source','parent','steps','summary','publish','independent'])
def test_revision_cannot_fake_support_or_change_denominator(revision_matrix,mutation):
    from qa_core.quality.sop_record_alignment_v2 import validate_matrix
    m=copy.deepcopy(revision_matrix);e=m['entries'][0];x=next(r for r in e['records'] if r['kind']=='revision_extension')
    if mutation=='record':e['records'].pop()
    elif mutation=='field_value':x['value']=10
    elif mutation=='observed':x['site_evidence_verified']=True
    elif mutation=='extension_action':x['action_authorized']=True
    elif mutation in {'action','full_sop','root'}:e[{'action':'action_authorized','full_sop':'full_sop_approved','root':'root_cause_confirmed'}[mutation]]=True
    elif mutation=='manufacturer':e['manufacturer_document']='invented'
    elif mutation=='step_action':e['steps'][5]['operational_supported']=True
    elif mutation=='step_evidence':e['steps'][0]['site_evidence_verified']=True
    elif mutation=='wrong_sha':x['proof']['file_sha256']='a'*64
    elif mutation=='wrong_source':x['proof']['source']='other'
    elif mutation=='parent':e['parent_references'].pop()
    elif mutation=='steps':e['steps'][5]['text_status']='records_requested'
    elif mutation=='summary':m['summary']['record_contracts']=200
    elif mutation=='publish':m['publication_eligible']=True
    elif mutation=='independent':m['independent_denominator']=20
    with pytest.raises(ValueError):validate_matrix(m)


@pytest.mark.parametrize('field',['version','protocol','source_url','evidence_id'])
def test_read_validator_rejects_rebound_analysis(revision_matrix,field,monkeypatch):
    import scripts.prepare_sop_record_alignment_v2 as prepare
    m=copy.deepcopy(revision_matrix)
    if field in {'version','protocol'}:m[field]='other'
    else:m['entries'][0]['records'][0]['proof'][field]='other'
    read_original=prepare.read
    monkeypatch.setattr(prepare,'read',lambda path:m if Path(path)==prepare.OUTPUT/'alignment.json' else read_original(path))
    with pytest.raises(ValueError):prepare.load_verified()


def test_record_view_has_separate_session_key_and_version():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    script=(root/'static/guide-record-preview.js').read_text(encoding='utf-8')
    assert 'const version="test-sop-records-r2", storage="sop-record-preview-key"' in script
    assert '本次已补齐文字要求' in script and '核对本项原文' in script
