"""Negative admission boundaries; approved receipts below are in-memory fixtures only."""
from copy import deepcopy
import pytest
from qa_core.quality.sop_record_admission import (
    receipt_template, validate_receipt, admitted_material, MESSAGE, validate_plan)
from qa_core.quality.review_contract import bind_pending
from scripts.prepare_sop_record_admission import expected_plan, OUTPUT, load_verified
from scripts.prepare_legacy_projection import read


@pytest.fixture(scope='module')
def prepared():
    return expected_plan()


def fixture_receipt(p):
    r = receipt_template(p)
    r.update(decision='approved',user_message=MESSAGE,recorded_at_utc='2026-10-14T00:00:00+00:00')
    return r


def test_sealed_review_matches_original_sources(prepared):
    assert load_verified() == prepared
    assert len(prepared['entries']) == 20
    assert sum(len(e['extensions']) for e in prepared['entries']) == 49


def test_pending_does_not_create_any_candidate(tmp_path,monkeypatch):
    from scripts import build_sop_record_candidate as builder
    output=tmp_path/'not-created';monkeypatch.setattr(builder,'CANDIDATE',output)
    with pytest.raises(ValueError):
        builder.build(OUTPUT/'receipt-template.json')
    assert not output.exists()


def test_old_exact_r1_approval_cannot_authorize_revision(prepared):
    from scripts.execute_sop_evidence_stage import RECEIPT
    with pytest.raises(ValueError):validate_receipt(prepared,read(RECEIPT))


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(user_message='下一步'),
    lambda r:r.update(user_message='批准20份指南，仅限隔离本地演示STAGED'),
    lambda r:r.update(plan_sha256='a'*64),
    lambda r:r.update(source_review_seal_sha256='a'*64),
    lambda r:r.update(previous_review_seal_sha256='a'*64),
    lambda r:r['exact_entry_bindings'].pop(),
    lambda r:r['exact_entry_bindings'][0].update(binding_sha256='a'*64),
    lambda r:r['target'].update(database='rag_kb'),
    lambda r:r['target'].update(database='test_sop_evidence_r1'),
    lambda r:r['target'].update(doc_collection='test_sop_evidence_r1_doc'),
    lambda r:r['target'].update(mysql_port=3306),
    lambda r:r['target'].update(generation_enabled=True),
    lambda r:r.update(publication_authorized=True),
    lambda r:r.update(outbound_authorized=True),
    lambda r:r.update(operational_sop_approved=True),
    lambda r:r.update(independent_quality_approved=True),
    lambda r:r.update(recorded_at_utc=None),
    lambda r:r.update(recorded_at_utc='2026-10-14T00:00:00'),
    lambda r:r.update(recorded_at_utc='2026-10-14T00:00:00+08:00'),
    lambda r:r.update(extra_authority=True),
])
def test_wrong_decision_binding_or_scope_rejected(prepared,mutation):
    r=fixture_receipt(prepared);mutation(r)
    with pytest.raises(ValueError):validate_receipt(prepared,r)


@pytest.mark.parametrize('mutation',[
    lambda e:e['metadata_pending']['allowed_roles'].append('guest'),
    lambda e:e['metadata_pending'].update(tenant_id='other'),
    lambda e:e['metadata_pending'].update(doc_type='sop'),
    lambda e:e['metadata_pending'].update(synthetic=True),
    lambda e:e['source_identity'].update(source_version='draft-r1'),
    lambda e:e.update(full_procedure_supported=True),
    lambda e:e['extensions'][0].update(value=10),
    lambda e:e['extensions'][0].update(site_evidence_verified=True),
    lambda e:e['extensions'][0].update(action_authorized=True),
    lambda e:e['extensions'][0]['requested_fields'].pop(),
])
def test_rebinding_cannot_expand_permissions_or_claim_field_truth(prepared,mutation):
    p=deepcopy(prepared);e=p['entries'][0];mutation(e);bind_pending(e)
    with pytest.raises(ValueError):validate_plan(p)


def test_changed_bytes_cannot_reuse_decision(prepared):
    p=deepcopy(prepared);r=fixture_receipt(p)
    p['entries'][0]['body_sha256']='a'*64;bind_pending(p['entries'][0])
    with pytest.raises(ValueError):validate_receipt(p,r)


def test_projection_keeps_source_revision_and_old_parents_without_writes(prepared):
    old=deepcopy(prepared)
    metadata,mapping=admitted_material(prepared,fixture_receipt(prepared))
    assert prepared==old and len(metadata)==len(mapping['entries'])==20
    for e in prepared['entries']:
        m=metadata[e['filename']]
        assert m['source_version']=='draft-r2' and m['doc_type']=='guide'
        assert m['role_scope']==['ops','admin'] and m['quality']=='local_demo'
        assert len(e['original_parent_bindings'])==10


@pytest.mark.parametrize('update',[
    dict(mysql_dsn='mysql+pymysql://fixture@127.0.0.1:13306/test_sop_evidence_r1'),
    dict(doc_collection='test_sop_evidence_r1_doc'),dict(milvus_uri='http://127.0.0.1:19530'),
    dict(redis_url='redis://127.0.0.1:16379/14'),dict(api_port=8010),dict(api_host='0.0.0.0'),
    dict(generation_enabled=True),dict(agent_enabled=True),dict(auth_required=False),
    dict(allow_header_roles=True),dict(semantic_cache_enabled=True),dict(llm_api_key='fixture'),
    dict(data_dir='data/candidates/sop-record-revision-r2'),dict(reports_dir='reports'),
])
def test_old_or_wider_profile_rejected(update):
    from scripts.sop_record_stage_profile import profile,validate_profile
    with pytest.raises(ValueError):validate_profile(profile().model_copy(update=update))
