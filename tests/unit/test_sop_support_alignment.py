import copy
import json
from pathlib import Path
import pytest
from qa_core.quality.sop_support_alignment import align_guide,pointer,validate_matrix
from scripts.prepare_sop_support_alignment import OUTPUT,CANDIDATE,DRAFTS

PACKAGE=json.loads((DRAFTS/'review-package.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('entry',PACKAGE['entries'],ids=[e['id'] for e in PACKAGE['entries']])
def test_twenty_guides_remain_record_contracts_without_field_actions(entry):
    result=align_guide(entry,(CANDIDATE/'raw'/entry['filename']).read_bytes().decode('utf-8'))
    assert len(result['steps'])==10 and len(result['records'])==3
    assert len(result['primary_conditions'])==len(result['counter_conditions'])==2
    assert all(r['operational_supported'] is False and r['site_evidence_verified'] is False for r in result['steps'])
    assert result['steps'][5]['text_status']=='action_missing'
    assert result['steps'][7]['text_status']=='recovery_missing' and result['steps'][8]['text_status']=='observation_missing'
    assert result['manufacturer_document'] is None and result['independent_quality'] is None


def test_generic_change_boilerplate_is_not_a_topic_change_record():
    e=PACKAGE['entries'][0];text=(CANDIDATE/'raw'/e['filename']).read_bytes().decode('utf-8')
    assert '批准变更' in text
    assert align_guide(e,text)['steps'][1]['text_status']=='change_records_missing'


def test_raw_windows_newlines_are_not_silently_rehashed():
    e=PACKAGE['entries'][0];text=(CANDIDATE/'raw'/e['filename']).read_bytes().decode('utf-8')
    assert '\r\n' in text
    align_guide(e,text)
    with pytest.raises(ValueError,match='bytes drift'):align_guide(e,text.replace('\r\n','\n'))


@pytest.mark.parametrize('value',['missing','duplicate'])
def test_ambiguous_or_missing_source_quote_refused(value):
    with pytest.raises(ValueError,match='one exact'):
        pointer('anchor\nanchor' if value=='duplicate' else 'other','guide.md','anchor')


@pytest.mark.parametrize('mutation',['guide_count','parent_count','parent_duplicate','steps','records','primary','counter',
    'root_cause','action','full_sop','site_evidence','manufacturer','independent','step_action','step_evidence','wrong_source',
    'wrong_sha','publish','activate','human','denominator'])
def test_matrix_cannot_grant_missing_truth_or_change_provenance(mutation):
    matrix=json.loads((OUTPUT/'alignment.json').read_text(encoding='utf-8'));e=matrix['entries'][0]
    if mutation=='guide_count':matrix['entries'].pop()
    elif mutation=='parent_count':e['parent_references'].pop()
    elif mutation=='parent_duplicate':e['parent_references'][-1]=dict(e['parent_references'][0])
    elif mutation=='steps':e['steps'][0]['old_step']=11
    elif mutation=='records':e['records'].pop()
    elif mutation=='primary':e['primary_conditions'].pop()
    elif mutation=='counter':e['counter_conditions'].pop()
    elif mutation in {'root_cause','action','full_sop','site_evidence'}:
        e[{'root_cause':'root_cause_confirmed','action':'action_authorized','full_sop':'full_sop_approved','site_evidence':'site_evidence_provided'}[mutation]]=True
    elif mutation=='manufacturer':e['manufacturer_document']='invented manual'
    elif mutation=='independent':e['independent_quality']=1.0
    elif mutation=='step_action':e['steps'][0]['operational_supported']=True
    elif mutation=='step_evidence':e['steps'][0]['site_evidence_verified']=True
    elif mutation=='wrong_source':e['records'][0]['proof']['source']='other-guide.md'
    elif mutation=='wrong_sha':e['records'][0]['proof']['file_sha256']='0'*64
    elif mutation in {'publish','activate','human'}:
        matrix[{'publish':'publication_eligible','activate':'activated','human':'human_semantic_approved'}[mutation]]=True
    elif mutation=='denominator':matrix['independent_denominator']=200
    with pytest.raises(ValueError):validate_matrix(matrix)


def test_old_parent_body_is_not_returned_in_guide_namespace():
    matrix=json.loads((OUTPUT/'alignment.json').read_text(encoding='utf-8'));validate_matrix(matrix)
    for e in matrix['entries']:
        for p in e['parent_references']:
            assert set(p)=={'filename','asset_id','file_sha256','metadata_sha256','step_lines'}
            assert set(p['step_lines'])=={str(i) for i in range(1,11)}


def test_alignment_failure_precedes_any_preview_stop_or_key_creation(monkeypatch):
    import scripts.sop_guide_support_runtime as runtime
    def invalid():raise ValueError('analysis provenance invalid')
    monkeypatch.setattr(runtime,'load_verified',invalid)
    monkeypatch.setattr(runtime,'select_record',lambda:pytest.fail('must validate before changing owned process/key'))
    with pytest.raises(ValueError,match='provenance'):runtime.start()


@pytest.mark.parametrize('upgraded',[False,True])
def test_controller_selects_recorded_module_ownership(monkeypatch,upgraded):
    import scripts.sop_guide_support_runtime as runtime
    state=dict(code_bindings={'scripts/sop_guide_support_server.py':'fixture'} if upgraded else {})
    monkeypatch.setattr(runtime.core,'record',lambda:state)
    try:
        runtime.select_record()
        assert runtime.core.MODULE==(runtime.MODULE if upgraded else runtime.BASE_MODULE)
        assert ('scripts/sop_guide_support_server.py' in runtime.core.CODE) is upgraded
    finally:runtime.configure(False)


def test_support_failure_cannot_replace_baseline_integrity(monkeypatch):
    import scripts.sop_guide_support_server as server
    called=[]
    def baseline(settings):called.append('baseline');return dict(read_preview_ready=True)
    def invalid():called.append('alignment');raise ValueError('changed parent source')
    monkeypatch.setattr(server,'base_verify_live',baseline);monkeypatch.setattr(server,'load_verified',invalid)
    with pytest.raises(ValueError,match='changed parent'):server.verify_support_live(object())
    assert called==['baseline','alignment']
