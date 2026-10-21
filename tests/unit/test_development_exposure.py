from copy import deepcopy

import pytest

from qa_core.quality.development_exposure import include_overlays
from qa_core.quality.independent_readiness import sha
from qa_core.quality.question_holdout import roster, screen, template
from scripts.prepare_legacy_projection import write, seal, digest


def registry():
    return dict(source_index=[dict(is_current=True, asset_id='asset', source_id='source',
        filename='current.md', source_version='r1', doc_type='manual', file_sha256='1'*64,
        metadata_sha256='2'*64, canonical_body_sha256='3'*64, blocking_aliases=['asset','source'])],
        raw_query_hashes=[], canonical_query_hashes=[],
        blocking_link_index=dict(source_ids=['asset'],body_families=['3'*64],
                                 topic_groups=['topic'],template_groups=[]))


def setup(tmp_path):
    root=tmp_path/'overlays';folder=root/'attempt';folder.mkdir(parents=True)
    dataset=tmp_path/'development.json';write(dataset,{'query':'public developer example'})
    row=dict(query_sha256=sha('public developer example'),canonical_query_sha256=sha('canonical example'),
             developer_exposed=True,group_links=dict(template_groups=['exposed-template']))
    payload=dict(protocol='development-exposure-overlay-v1',all_development_exposed=True,human_gold=False,
        independent_cases=0,source_holdout_preparation_seal='a'*64,dataset_path=str(dataset),
        dataset_sha256=digest(dataset),records=[row])
    write(folder/'exposure.json',payload);seal(folder)
    return root,folder,dataset,payload


def case():
    return dict(id='new',query_sha256=sha('unseen'),canonical_query_sha256=sha('unseen canonical'),
        truth_sha256='b'*64,evidence_manifest_sha256='c'*64,source_ids=['asset'],body_families=['3'*64],
        topic_groups=['topic'],template_groups=['new-template'],development_exposed=False,
        expected_kind='answer',request_context=dict(roles=['ops'],tenant_id='default'))


@pytest.mark.parametrize('field,value,reason',[
    ('query_sha256',sha('public developer example'),'known_exposed_question_hash'),
    ('canonical_query_sha256',sha('canonical example'),'known_exposed_question_hash'),
    ('template_groups',['exposed-template'],'known_exposed_question_template')])
def test_later_exposure_cannot_enter_question_holdout(tmp_path,field,value,reason):
    root,_,_,_=setup(tmp_path);original=registry();saved=deepcopy(original)
    merged,receipts=include_overlays(original,root,tmp_path,'a'*64)
    assert original==saved and len(receipts)==1
    p=template('v1','d'*64);c=case();c[field]=value;p['cases']=[c]
    result=screen(p,merged,roster(merged),'v1','d'*64)
    assert reason in result['blocked_cases'][0]['reasons']
    assert not result['run_authorized'] and result['quality_metrics'] is None


def test_known_source_reuse_remains_allowed_but_does_not_authorize_run(tmp_path):
    root,_,_,_=setup(tmp_path);merged,_=include_overlays(registry(),root,tmp_path,'a'*64)
    p=template('v1','d'*64);p['cases']=[case()]
    result=screen(p,merged,roster(merged),'v1','d'*64)
    assert result['static_screen_passed'] and not result['run_authorized']
    p['cases']=[];result=screen(p,merged,roster(merged),'v1','d'*64)
    assert result['state']=='blocked' and result['verified_question_holdout_frozen']==0


@pytest.mark.parametrize('mutation', ['dataset','seal','preparation','plaintext','not_exposed','claim_independent'])
def test_overlay_drift_or_false_claim_fails_closed(tmp_path,mutation):
    root,folder,dataset,p=setup(tmp_path)
    if mutation=='dataset':write(dataset,{'query':'different'})
    elif mutation=='seal':write(folder/'extra.json',{})
    else:
        if mutation=='preparation':p['source_holdout_preparation_seal']='e'*64
        if mutation=='plaintext':p['records'][0]['question']='private prose'
        if mutation=='not_exposed':p['records'][0]['developer_exposed']=False
        if mutation=='claim_independent':p['independent_cases']=1
        write(folder/'exposure.json',p);seal(folder)
    with pytest.raises(ValueError):include_overlays(registry(),root,tmp_path,'a'*64)


def test_unsealed_or_unexpected_overlay_is_not_silently_skipped(tmp_path):
    root=tmp_path/'overlays';root.mkdir();(root/'interrupted').mkdir()
    with pytest.raises(FileNotFoundError):include_overlays(registry(),root,tmp_path,'a'*64)


def test_outside_workspace_is_rejected(tmp_path):
    with pytest.raises(ValueError):include_overlays(registry(),tmp_path,tmp_path/'project','a'*64)
