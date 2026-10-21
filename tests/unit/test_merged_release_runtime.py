from copy import deepcopy
import pytest
from qa_core.config.settings import Settings
from scripts import merged_release_runtime as merged


def receipt():
    return dict(decision='approved',reviewer_kind='human_user_chat_confirmation',user_message='批准具体候选',
        plan_sha256='bound',version=merged.VERSION,scope='local_demo_publication',new_faq_outbound_approved=True)


def plan():
    return dict(version=merged.VERSION,previous_active='local-release-r1',generation_validation_requests=0)


@pytest.mark.parametrize('change',[{'decision':'pending'},{'user_message':''},{'plan_sha256':'other'},
    {'version':'local-protocol-faq-r1'},{'scope':'production'},{'new_faq_outbound_approved':False}])
def test_unapproved_or_different_selection_rejected(change):
    value=receipt();value.update(change)
    with pytest.raises(ValueError):merged.validate_selection(value,plan(),'bound')


def test_no_selection_preserves_old_runtime(monkeypatch,tmp_path):
    monkeypatch.setattr(merged,'SELECTION',tmp_path/'absent.json')
    base=Settings(_env_file=None)
    assert merged.selected_settings(base) is base
    assert merged.allowed_active_versions()==()


def test_runtime_selection_cannot_authorize_paid_validation():
    value=plan();value['generation_validation_requests']=4
    with pytest.raises(ValueError):merged.validate_selection(receipt(),value,'bound')
