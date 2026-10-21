from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qa_core.quality.sop_admission import make_plan,receipt_template,validate_receipt,admitted_material,TARGET,MESSAGE
from qa_core.indexing.chunkers.sop_evidence_guide import chunk_evidence_guide,evidence_guide_rebuild
from scripts.prepare_sop_evidence_drafts import OUTPUT
from scripts.prepare_legacy_projection import read


def plan():return make_plan(read(OUTPUT/'review-package.json'),'a'*64)


def fixture_receipt(p):
    # Only an in-memory unit fixture; never saved as a real approval.
    r=receipt_template(p);r.update(decision='approved',user_message=MESSAGE,recorded_at_utc='2026-10-14T00:00:00+00:00')
    return r


def test_pending_and_next_step_do_not_admit():
    p=plan();r=receipt_template(p)
    with pytest.raises(ValueError):admitted_material(p,r)
    r=fixture_receipt(p);r['user_message']='下一步'
    with pytest.raises(ValueError):validate_receipt(p,r)


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(plan_sha256='b'*64),lambda r:r.update(source_review_seal_sha256='b'*64),
    lambda r:r['exact_entry_bindings'].pop(),lambda r:r['exact_entry_bindings'][0].update(binding_sha256='b'*64),
    lambda r:r['target'].update(database='rag_kb'),lambda r:r['target'].update(mysql_port=3306),
    lambda r:r['target'].update(generation_enabled=True),lambda r:r['target'].update(auth_required=False),
    lambda r:r.update(outbound_authorized=True),lambda r:r.update(publication_authorized=True),
    lambda r:r.update(operational_sop_approved=True),lambda r:r.update(independent_quality_approved=True),
    lambda r:r.update(recorded_at_utc='2026-10-14T00:00:00'),
    lambda r:r.update(recorded_at_utc='2026-10-14T00:00:00+08:00'),lambda r:r.update(extra_approval=True)])
def test_wrong_content_target_scope_or_timestamp_rejected(mutation):
    p=plan();r=fixture_receipt(p);mutation(r)
    with pytest.raises(ValueError):validate_receipt(p,r)


def test_projection_preserves_identity_scope_and_does_not_mutate_pending():
    p=plan();saved=deepcopy(p);metadata,mapping=admitted_material(p,fixture_receipt(p))
    assert p==saved and len(metadata)==len(mapping['entries'])==20
    for e in p['entries']:
        m=metadata[e['filename']]
        assert m['role_scope']==['ops','admin'] and m['visibility']=='role_restricted' and m['doc_type']=='guide'
        assert all(m[k]==v for k,v in e['source_identity'].items())
        assert m['review_status']=='human_chat_approved' and m['status']=='active'
    assert TARGET['status']=='STAGED' and not TARGET['generation_enabled'] and not TARGET['agent_enabled']


def test_changed_entry_or_role_cannot_reuse_receipt():
    p=plan();r=fixture_receipt(p);p['entries'][0]['metadata_pending']['allowed_roles'].append('guest')
    with pytest.raises(ValueError):validate_receipt(p,r)


@pytest.mark.parametrize('field',['operational_sop_approved','outbound_authorized','publication_authorized'])
def test_rebinding_a_wider_plan_does_not_make_it_eligible(field):
    p=plan();p[field]=True
    with pytest.raises(ValueError):validate_receipt(p,fixture_receipt(p))


def test_candidate_writer_checks_pending_before_creating_any_directory(tmp_path,monkeypatch):
    from scripts import build_sop_evidence_candidate as builder
    from scripts.prepare_sop_evidence_admission import OUTPUT as review
    monkeypatch.setattr(builder,'CANDIDATE',tmp_path/'should-not-exist')
    with pytest.raises(ValueError):builder.build(review/'receipt-template.json')
    assert not (tmp_path/'should-not-exist').exists()


class Tokenizer:
    def encode(self,text,**kwargs):return list(text)


def test_process_adapter_preserves_guide_and_restores_released_registry():
    from scripts import rebuild_kb
    original=rebuild_kb.TEXT_CHUNKERS['guide']
    with evidence_guide_rebuild(Tokenizer()):
        chunks=rebuild_kb.TEXT_CHUNKERS['guide']('# Title\n\nObservation.',source='guide.md')
        assert all(c.doc_type=='guide' and c.metadata['doc_type']=='guide' for c in chunks)
    assert rebuild_kb.TEXT_CHUNKERS['guide'] is original


def test_guide_cannot_be_promoted_to_complete_sop_even_if_recall_ignores_type_filter():
    from qa_core.retrieval.workflow import retrieve_workflow
    docs=[SimpleNamespace(doc_type='guide',text='审批 回滚 步骤 1：',id='guide')]
    searcher=Mock();searcher.search_docs.return_value=SimpleNamespace(docs=docs)
    reranker=Mock();reranker.rerank.side_effect=lambda query,items,**kwargs:items
    result=retrieve_workflow(searcher,reranker,'AMF完整处置流程',['ops'],'test-sop-evidence-r1')
    assert not result.ready and result.packet is None
    assert searcher.search_docs.call_args.kwargs['strict_doc_types'] is True
    searcher.fetch_sop_source.assert_not_called()


@pytest.mark.parametrize('change',[
    dict(generation_enabled=True),dict(agent_enabled=True),dict(auth_required=False),dict(allow_header_roles=True),
    dict(doc_collection='local_content_quality_r1_doc'),dict(milvus_uri='http://127.0.0.1:19530'),dict(llm_api_key='fixture-only')])
def test_isolated_profile_drift_rejected(change):
    from scripts.sop_evidence_stage_profile import profile,validate_profile
    selected=profile()
    with pytest.raises(ValueError):validate_profile(selected.model_copy(update=change))
