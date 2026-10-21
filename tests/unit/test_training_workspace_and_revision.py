import copy
from pathlib import Path
import pytest
from qa_core.presentation.training_workspace import display_asset
from qa_core.quality.sop_record_revision_review import validate_revision
from scripts.prepare_sop_record_revision import OUTPUT,CANDIDATE
from scripts.prepare_legacy_projection import read


@pytest.mark.parametrize('name',['business-workspace.js','agent-workspace.js','business-isolated.html'])
def test_only_presentation_is_transformed(name):
    root=Path(__file__).resolve().parents[2];source=(root/'static'/name).read_text(encoding='utf-8')
    result=display_asset(name,source)
    assert '模拟资料' not in result and ' · 模拟快照' not in result and '模拟来源:' not in result
    assert (root/'static'/name).read_text(encoding='utf-8')==source
    assert '/admin/keys' not in result


def test_arbitrary_assets_are_not_rewritten():
    with pytest.raises(ValueError):display_asset('../../api-key.txt','secret')


def test_api_scope_is_formatted_without_rewriting_the_api():
    source=(Path(__file__).resolve().parents[2]/'static/business-workspace.js').read_text(encoding='utf-8')
    text=display_asset('business-workspace.js',source)
    assert "d.scope.replace('已准入模拟快照','业务快照')" in text
    assert 'row.items_synced' in text and '未同步 / 无账本' in text


def test_49_specific_requests_preserve_original_bodies():
    package=read(OUTPUT/'review-package.json')
    assert validate_revision(package,OUTPUT/'drafts',CANDIDATE/'raw')=={2:11,4:9,5:10,7:19}
    assert all(len(e['extensions'])>=1 for e in package['entries'])


@pytest.mark.parametrize('mutation',['roles','tenant','visibility','license','operational','cause','action','sop','registered','revision','extensions','values','evidence','publish','stage','outbound','independent'])
def test_pending_drafts_cannot_expand_scope_or_claim_completion(mutation):
    p=read(OUTPUT/'review-package.json');e=p['entries'][0]
    if mutation in ['roles','tenant','visibility']:
        key,value={'roles':('roles',['guest']),'tenant':('tenant_id','other'),'visibility':('visibility','public')}[mutation]
        e['access_proposal'][key]=value
    elif mutation=='license':e['license_status']='approved'
    elif mutation in ['operational','cause','action','sop','registered']:
        e[{'operational':'operational_ready','cause':'root_cause_confirmed','action':'action_authorized','sop':'full_sop_approved','registered':'identity_registered'}[mutation]]=True
    elif mutation=='revision':e['source_identity_proposal']['source_version']='draft-r1'
    elif mutation=='extensions':e['extensions'].pop()
    elif mutation=='values':e['extensions'][0]['value']=10
    elif mutation=='evidence':e['extensions'][0]['site_evidence_verified']=True
    elif mutation in ['publish','stage','outbound']:
        p[{'publish':'published','stage':'staged_created','outbound':'outbound_authorized'}[mutation]]=True
    elif mutation=='independent':p['independent_denominator']=20
    with pytest.raises(ValueError):validate_revision(p,OUTPUT/'drafts',CANDIDATE/'raw')


def test_modified_body_refused_without_touching_sealed_inputs(tmp_path):
    package=read(OUTPUT/'review-package.json')
    for e in package['entries']:(tmp_path/e['filename']).write_bytes((OUTPUT/'drafts'/e['filename']).read_bytes())
    (tmp_path/package['entries'][0]['filename']).write_text('changed')
    with pytest.raises(ValueError):validate_revision(package,tmp_path,CANDIDATE/'raw')


def test_legacy_status_allows_authorized_presentation_upgrade(monkeypatch):
    import scripts.training_business_runtime as runtime
    monkeypatch.setattr(runtime.core,'state',lambda:dict(status='running'))
    monkeypatch.setattr(runtime.core,'inspect',lambda:dict(status='running',pid=123))
    monkeypatch.setattr(runtime,'verify_presentation',lambda record:pytest.fail('legacy has no presentation binding'))
    result=runtime.status();assert result['status']=='running' and result['presentation_mode']=='legacy'


def test_status_poll_does_not_take_startup_mutation_lock(monkeypatch,capsys):
    import sys
    import scripts.training_business_runtime as runtime
    monkeypatch.setattr(sys,'argv',['test','status'])
    monkeypatch.setattr(runtime,'status',lambda:dict(status='starting'))
    monkeypatch.setattr(runtime.core,'lifecycle_lock',lambda:pytest.fail('status must not compete with startup lock'))
    runtime.main();assert 'starting' in capsys.readouterr().out
