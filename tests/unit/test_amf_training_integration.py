from types import SimpleNamespace
import re
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from qa_core.api.auth import AuthContext
from qa_core.presentation import amf_training as training


@pytest.mark.parametrize('roles,extra,allowed',[
 (['ops'],{},True),(['admin'],{},True),(['auditor'],{},False),(['guest'],{},False),
 (['ops'],{'tenant_id':'other'},False),(['ops'],{'dept_ids':['ran-ops']},False),
 (['ops'],{'device_types':['SMF']},False),(['ops'],{'device_types':['AMF']},False),
 (['admin'],{'security_levels':['public']},False),(['admin'],{'source':'header'},False)])
def test_training_scope_does_not_expand_business_or_source_permissions(roles,extra,allowed):
    ctx=AuthContext('unit',roles,'api_key')
    for field,value in extra.items():setattr(ctx,field,value)
    assert training.can_train(ctx) is allowed


def test_sandbox_document_contains_exact_engine_but_no_draft_body_or_external_assets():
    text,policy=training.exercise_document()
    assert "connect-src 'none'" in policy and "default-src 'none'" in policy
    assert 'sop-draft.md' not in text
    assert not re.search(r'<(?:a|link|script)\b[^>]*\b(?:href|src)\s*=',text,re.I)
    assert (training.OUTPUT/'engine.js').read_text('utf-8') in text
    assert text.count('<script nonce=')==3 and '<style nonce=' in text
    assert 'real_action_authorized' in text and 'source_authenticity_verified' in text


def test_shell_adds_only_training_navigation_to_known_shape():
    from scripts.readonly_workspace_runtime import PROJECT
    original=(PROJECT/'static/business-isolated.html').read_text('utf-8')
    text=training.workspace_html(original)
    assert text.count('id="trainingTab"')==1 and text.count('id="view-training"')==1
    assert 'training-exercise-bridge.js' in text and 'readonly_key' in text
    assert (PROJECT/'static/business-isolated.html').read_text('utf-8')==original
    with pytest.raises(ValueError):training.workspace_html('<html>unknown</html>')


def client(monkeypatch,role='ops',failure=False):
    from qa_core.api import auth
    def authenticate_headers(*,x_api_key):
        if x_api_key!='unit-key':raise HTTPException(401,'Invalid key')
        return AuthContext('unit',[role],'api_key')
    monkeypatch.setattr(auth,'authenticate_headers',authenticate_headers)
    def verify(record):
        if failure:raise ValueError('Private config detail')
    app=FastAPI()
    @app.api_route('/{path:path}',methods=['GET','POST'])
    def fallback(path):raise HTTPException(403,'Not a training route')
    training.install(app,{},SimpleNamespace(verify_presentation=verify))
    return TestClient(app)


def test_http_authentication_sandbox_and_no_store(monkeypatch):
    with client(monkeypatch) as c:
        assert c.get('/training/amf',headers={'X-User-Roles':'admin'}).status_code==401
        response=c.get('/training/amf',headers={'X-API-Key':'unit-key'})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert 'sandbox allow-scripts allow-downloads' in response.headers['content-security-policy']
        assert 'allow-same-origin' not in response.headers['content-security-policy']
        assert c.post('/training/amf',json={}).status_code==403
        assert c.get('/training/sop-draft.md',headers={'X-API-Key':'unit-key'}).status_code==403


def test_auditor_has_business_role_but_no_new_training_permission(monkeypatch):
    with client(monkeypatch,'auditor') as c:
        headers={'X-API-Key':'unit-key','X-User-Roles':'admin'}
        assert c.get('/training/capabilities',headers=headers).json()['can_train_amf'] is False
        assert c.get('/training/amf',headers=headers).status_code==403


def test_changed_binding_fails_closed_without_private_error_text(monkeypatch):
    with client(monkeypatch,failure=True) as c:
        r=c.get('/training/amf',headers={'X-API-Key':'unit-key'})
        assert r.status_code==503 and 'Private' not in r.text
        assert r.headers['cache-control']=='no-store'


def test_launcher_replaces_only_known_owned_server(monkeypatch):
    from scripts import training_exercise_runtime as runtime
    seen=[]
    monkeypatch.setattr(runtime.base.subprocess,'Popen',lambda args,**kwargs:seen.append(args))
    original=runtime.base.subprocess.Popen
    with runtime.upgraded_launcher():
        runtime.base.subprocess.Popen(['pythonw','-c','from scripts.training_business_server import main; main()','--instance','abc'])
        runtime.base.subprocess.Popen(['docker','start','storage'])
    assert seen[0][2]=='from scripts.training_exercise_server import main; main()'
    assert seen[1]==['docker','start','storage']
    assert runtime.base.subprocess.Popen is original
