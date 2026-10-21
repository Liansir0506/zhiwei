"""Fail-closed runtime configuration, process ownership and release state deltas."""
import copy
from unittest.mock import Mock
import psutil
import pytest
from scripts import original_local_runtime as runtime
from scripts import execute_original_local_release as release
from scripts import original_local_server as server
from qa_core.api.auth import AuthContext

def test_inherited_credentials_and_paid_mode_cannot_change_reviewed_profile(monkeypatch):
    for key,value in {'MYSQL_DSN':'mysql+pymysql://wrong:secret@other/other','APP_ENV':'prod','API_HOST':'0.0.0.0',
                      'AUTH_REQUIRED':'false','GENERATION_ENABLED':'true','AGENT_ENABLED':'true','LLM_API_KEY':'do-not-use'}.items():
        monkeypatch.setenv(key,value)
    settings=runtime.build_profile();env=runtime.environment(settings)
    assert settings.api_port==8010 and settings.app_env=='dev'
    assert env['AUTH_REQUIRED']=='true' and env['GENERATION_ENABLED']==env['AGENT_ENABLED']=='false'
    assert env['LLM_API_KEY']=='' and env['HF_HUB_OFFLINE']=='1'
    public=runtime.public_profile(settings)
    assert 'mysql_dsn' not in public and 'llm_api_key' not in public

@pytest.mark.parametrize('field,value',[
    ('generation_enabled',True),('agent_enabled',True),('auth_required',False),('workflow_evidence_enabled',False),
    ('semantic_cache_enabled',True),('llm_api_key','secret'),('llm_base_url','https://api.deepseek.com/v1'),
    ('api_host','0.0.0.0'),('api_port',18011),('redis_url','redis://127.0.0.1:16379/0'),
    ('doc_collection','doc_collection'),('milvus_uri','http://localhost:19530'),('embedding_device','cuda')])
def test_original_runtime_profile_drift_refused(field,value):
    settings=runtime.build_profile().model_copy(update={field:value})
    with pytest.raises(ValueError):runtime.validate_profile(settings)

@pytest.mark.parametrize('change',['time','module','instance','profile','cwd'])
def test_unowned_process_is_never_stopped(monkeypatch,change):
    state={'pid':345,'created_at':100.,'instance':'owned'};process=Mock()
    process.is_running.return_value=True;process.status.return_value='running'
    process.create_time.return_value=101. if change=='time' else 100.
    process.cwd.return_value=str(runtime.ROOT if change=='cwd' else runtime.PROJECT)
    process.cmdline.return_value=['python','-m','other' if change=='module' else runtime.SERVER_MODULE,
        '--instance','foreign' if change=='instance' else 'owned','--profile',
        'other' if change=='profile' else str(runtime.ROOT/'profile.json')]
    monkeypatch.setattr(runtime.psutil,'Process',lambda pid:process)
    with pytest.raises(ValueError,match='PID identity'):runtime.owned_process(state)
    process.terminate.assert_not_called();process.kill.assert_not_called()

def test_missing_pid_does_not_kill_anyone(monkeypatch):
    def missing(pid):raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(runtime.psutil,'Process',missing)
    assert runtime.owned_process({'pid':345}) is None

def test_revocation_failure_preserves_recovery_record_and_owned_key(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime,'ROOT',tmp_path);monkeypatch.setattr(runtime,'owned_process',lambda state:None)
    state={'status':'running','pid':1,'instance':'owned','key_hash':'h','actor':'ops-owner'}
    runtime.write(tmp_path/'state.json',state);(tmp_path/'api-key.txt').write_text('secret',encoding='utf-8')
    (tmp_path/'open-platform.html').write_text('private-launcher',encoding='utf-8')
    engine=Mock();engine.begin.side_effect=RuntimeError('storage unavailable')
    with pytest.raises(RuntimeError):runtime.stop(engine,state)
    assert runtime.read(tmp_path/'state.json')==state and (tmp_path/'api-key.txt').read_text()=='secret'
    assert (tmp_path/'open-platform.html').read_text(encoding='utf-8')=='private-launcher'

def test_private_launcher_uses_owned_key_and_returns_only_file_path(monkeypatch,tmp_path):
    import hashlib
    monkeypatch.setattr(runtime,'ROOT',tmp_path);key='x'*43
    (tmp_path/'api-key.txt').write_text(key,encoding='utf-8')
    path=runtime.write_login_file({'key_hash':hashlib.sha256(key.encode()).hexdigest()})
    assert path==str(tmp_path/'open-platform.html') and key not in path
    text=(tmp_path/'open-platform.html').read_text(encoding='utf-8')
    assert 'http://localhost:8010/#local_demo_key='+key in text
    assert 'location.replace' in text and 'https://' not in text

def test_stale_launcher_key_is_rejected_before_file_write(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime,'ROOT',tmp_path);(tmp_path/'api-key.txt').write_text('stale',encoding='utf-8')
    with pytest.raises(ValueError):runtime.write_login_file({'key_hash':'different-hash'})
    assert not (tmp_path/'open-platform.html').exists()

def test_key_file_substitution_refused(monkeypatch,tmp_path):
    monkeypatch.setattr(runtime,'ROOT',tmp_path);(tmp_path/'api-key.txt').write_text('wrong',encoding='utf-8')
    with pytest.raises(ValueError,match='owned key'):runtime.read_key({'key_hash':'approved-hash'})

@pytest.mark.parametrize('versions',[[],[('v4-foundation-r3','legacy.json')],[(runtime.BASELINE,'b.json'),(runtime.SUCCESSOR,'s.json')]])
def test_public_start_requires_one_legitimate_local_active(monkeypatch,versions):
    engine=Mock();engine.connect.return_value.__enter__=Mock(return_value=engine)
    engine.connect.return_value.__exit__=Mock(return_value=False);engine.execute.return_value.all.return_value=versions
    gate=Mock();monkeypatch.setattr('qa_core.quality.publication.verify_publication',gate)
    with pytest.raises(ValueError):runtime.verify_active(runtime.build_profile(),engine)
    gate.assert_not_called()

def example_delta():
    records=[{'version':v,'status':s,'published_at':None,'activated_by':None,'quality_report_path':v+'.json'}
        for v,s in [('v4-foundation-r3','ACTIVE'),(runtime.BASELINE,'STAGED'),(runtime.SUCCESSOR,'STAGED'),('unrelated','STAGED')]]
    before={'kb_versions':{'records':records},'knowledge_assets':{'sha256':'same'}};after=copy.deepcopy(before)
    for row in after['kb_versions']['records']:
        if row['version']=='v4-foundation-r3':row['status']='ARCHIVED'
        if row['version'] in (runtime.BASELINE,runtime.SUCCESSOR):
            row.update(status='ACTIVE' if row['version']==runtime.SUCCESSOR else 'ARCHIVED',published_at='now',activated_by='real-admin')
    return before,after

def test_only_actual_two_publication_state_deltas_allowed():
    before,after=example_delta();release.assert_after(before,after,'real-admin')

def test_mysql_automatic_update_timestamp_is_a_publication_delta():
    before,after=example_delta()
    for row in before['kb_versions']['records']:row['updated_at']='previous'
    for row in after['kb_versions']['records']:row['updated_at']='previous' if row['version']=='unrelated' else 'publication-time'
    release.assert_after(before,after,'real-admin')

def test_resumption_preserves_baseline_actor_and_uses_new_successor_actor():
    before,after=example_delta();after['kb_versions']['records'][2]['activated_by']='resume-admin'
    release.assert_after(before,after,{runtime.BASELINE:'real-admin',runtime.SUCCESSOR:'resume-admin'})
    with pytest.raises(ValueError):release.assert_after(before,after,{runtime.BASELINE:'forged',runtime.SUCCESSOR:'resume-admin'})

@pytest.mark.parametrize('change',['asset','unrelated','actor','gate_path','extra_version'])
def test_release_cannot_hide_unrelated_writes_or_forged_actor(change):
    before,after=example_delta()
    if change=='asset':after['knowledge_assets']['sha256']='changed'
    if change=='unrelated':after['kb_versions']['records'][3]['status']='ARCHIVED'
    if change=='actor':after['kb_versions']['records'][1]['activated_by']='forged'
    if change=='gate_path':after['kb_versions']['records'][2]['quality_report_path']='other.json'
    if change=='extra_version':after['kb_versions']['records'].append({'version':'unexpected'})
    with pytest.raises(ValueError):release.assert_after(before,after,'real-admin')

def test_both_address_families_are_bound_only_on_loopback(monkeypatch):
    ipv4,ipv6=Mock(),Mock()
    monkeypatch.setattr(server.socket,'socket',Mock(side_effect=[ipv4,ipv6]))
    assert server.loopback_sockets(8010)==[ipv4,ipv6]
    ipv4.bind.assert_called_once_with(('127.0.0.1',8010))
    ipv6.bind.assert_called_once_with(('::1',8010))
    ipv6.setsockopt.assert_called_once_with(server.socket.IPPROTO_IPV6,server.socket.IPV6_V6ONLY,1)

def test_ipv6_port_conflict_closes_all_listeners_instead_of_serving_half_the_entry(monkeypatch):
    ipv4,ipv6=Mock(),Mock();ipv6.bind.side_effect=OSError('occupied')
    monkeypatch.setattr(server.socket,'socket',Mock(side_effect=[ipv4,ipv6]))
    with pytest.raises(OSError):server.loopback_sockets(8010)
    ipv4.close.assert_called_once();ipv6.close.assert_called_once()

def test_terminate_owned_never_touches_unrelated_descendants(monkeypatch):
    state={'pid':1,'instance':'owned'};root,child,other=Mock(pid=1),Mock(pid=2),Mock(pid=3)
    root.children.return_value=[child,other];child.create_time.return_value=other.create_time.return_value=100.
    def owned(identity):
        if identity['pid']==3:raise ValueError('unowned worker')
        return {1:root,2:child}[identity['pid']]
    monkeypatch.setattr(runtime,'owned_process',owned)
    runtime.terminate_owned(state)
    root.terminate.assert_called_once();child.terminate.assert_called_once();other.terminate.assert_not_called()

@pytest.mark.parametrize('invalid',['profile','key_role','missing_process'])
def test_restart_attests_profile_key_and_process_before_stopping_anything(monkeypatch,tmp_path,invalid):
    settings=runtime.build_profile();runtime.write(tmp_path/'profile.json',runtime.public_profile(settings))
    state={'status':'running','profile_sha256':runtime.digest(tmp_path/'profile.json'),'actor':'owned','key_hash':'h','pid':1}
    monkeypatch.setattr(runtime,'ROOT',tmp_path);monkeypatch.setattr(runtime,'load_state',lambda:state)
    monkeypatch.setattr(runtime,'read_key',lambda state:'existing-key');monkeypatch.setattr(runtime,'apply_profile',lambda s:s)
    monkeypatch.setattr(runtime,'verify_active',Mock())
    ctx=AuthContext(user_id='owned',roles=['admin'] if invalid=='key_role' else ['ops'],source='api_key')
    monkeypatch.setattr('qa_core.api.auth.verify_api_key',lambda key:ctx)
    monkeypatch.setattr(runtime,'owned_process',lambda state:None if invalid=='missing_process' else Mock())
    terminate=Mock();monkeypatch.setattr(runtime,'terminate_owned',terminate)
    if invalid=='profile':state['profile_sha256']='changed'
    with pytest.raises(ValueError):runtime.restart(settings,Mock())
    terminate.assert_not_called()
