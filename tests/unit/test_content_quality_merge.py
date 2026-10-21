import copy
from datetime import datetime,timezone
import pytest
from qa_core.quality.review_contract import bind_pending
from scripts import stage_content_quality_merge as merge


@pytest.fixture
def reviewed(monkeypatch):
    entries=[bind_pending(dict(id=f'FAQ-REVISION-{i:02}',synthetic=True,
        access_proposal=dict(tenant_id='default',roles=['ops','admin'],visibility='internal',security_level='internal')))
        for i in range(1,21)]
    package=dict(entries=entries,baseline_sha256='digest')
    receipt=dict(decision='approved',reviewer_kind='human_user_chat_confirmation',user_message=merge.MESSAGE,
        package_sha256='digest',exact_entry_ids=[e['id'] for e in entries],
        scope='twenty_synthetic_faqs_isolated_local_staged_replace_templates_new_version_only',
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),publication_authorized=False,outbound_authorized=False,
        independent_quality_approved=False)
    monkeypatch.setattr(merge,'verify_seal',lambda path:None)
    monkeypatch.setattr(merge,'digest',lambda path:'digest')
    monkeypatch.setattr(merge,'read',lambda path:receipt if path==merge.RECEIPT else package)
    return package,receipt


def test_receipt_matches_exact_new_body_scope(reviewed):
    assert merge.validate()==reviewed


@pytest.mark.parametrize('field,value',[
    ('publication_authorized',True),('outbound_authorized',True),('independent_quality_approved',True),
    ('scope','all_pending_sources'),('exact_entry_ids',['FAQ-REVISION-01']),('user_message','下一阶段')])
def test_expanded_or_inferred_permission_rejected(reviewed,field,value):
    reviewed[1][field]=value
    with pytest.raises(ValueError,match='receipt'):merge.validate()


def test_changed_body_scope_requires_review_even_with_fresh_binding(reviewed):
    package,_=reviewed
    e=copy.deepcopy(package['entries'][0]);e['access_proposal']['roles']=['all']
    package['entries'][0]=bind_pending(e)
    with pytest.raises(ValueError,match='scope changed'):merge.validate()
