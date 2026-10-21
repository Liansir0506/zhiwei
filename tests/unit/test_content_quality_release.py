from copy import deepcopy
from contextlib import nullcontext
import pytest
from scripts import content_quality_release_runtime as release
from scripts.execute_content_quality_release import Operations,transition


def plan():
    return dict(version=release.VERSION,previous_active=release.OLD,validation_paid_calls=0,agent_enabled=False)


def receipt(mode='citation'):
    return dict(decision='approved',reviewer_kind='human_user_chat_confirmation',user_message=release.MODES[mode],
        scope='content_quality_local_demo_publication',plan_sha256='bound',version=release.VERSION,mode=mode,
        new_material_outbound_approved=mode=='generation',independent_quality_approved=False,
        recorded_at_utc='2026-10-11T00:00:00+00:00')


@pytest.mark.parametrize('mode',['citation','generation'])
def test_exact_mode_receipt(mode):
    release.validate_receipt(receipt(mode),plan(),'bound')


@pytest.mark.parametrize('change',[
    {'mode':'other'},{'decision':'pending'},{'version':release.OLD},{'plan_sha256':'other'},
    {'scope':'production'},{'new_material_outbound_approved':True},{'independent_quality_approved':True},
    {'user_message':'下一步'},{'recorded_at_utc':'yesterday'},{'recorded_at_utc':'2026-10-11T00:00:00'},
    {'recorded_at_utc':'2026-10-11T08:00:00+08:00'},{'extra':'unreviewed'}])
def test_scope_escalation_or_invalid_receipt_rejected(change):
    value=receipt();value.update(change)
    with pytest.raises(ValueError):release.validate_receipt(value,plan(),'bound')


@pytest.mark.parametrize('change',[{'validation_paid_calls':1},{'agent_enabled':True},{'previous_active':'other'}])
def test_plan_cannot_authorize_unreviewed_work(change):
    value=plan();value.update(change)
    with pytest.raises(ValueError):release.validate_receipt(receipt(),value,'bound')


def test_modes_preserve_binding_and_bound_generation():
    citation=release.profile('citation');generation=release.profile('generation')
    assert citation.doc_collection==generation.doc_collection==release.merge.DOC
    assert citation.faq_collection==generation.faq_collection==release.merge.FAQ
    assert citation.api_port==generation.api_port==8010
    assert not citation.generation_enabled and not citation.llm_api_key
    assert citation.llm_base_url=='http://127.0.0.1:1/disabled'
    assert generation.generation_enabled and generation.llm_api_key==release.BASE.llm_api_key
    assert not generation.agent_enabled and not citation.agent_enabled
    assert not generation.semantic_cache_enabled


def test_no_selection_reuses_original_profile(monkeypatch,tmp_path):
    monkeypatch.setattr(release,'SELECTION',tmp_path/'absent.json')
    assert release.selected_settings() is release.BASE


class FakeOperations:
    def __init__(self,fail=None,recovery_fail=False):self.events=[];self.fail=fail;self.recovery_fail=recovery_fail
    def step(self,name):
        self.events.append(name)
        if name==self.fail or (name=='restore' and self.recovery_fail):raise ValueError(name)
    def preflight(self):self.step('preflight')
    def install(self):self.step('install')
    def start(self):self.step('start')
    def verify_live(self):self.step('verify_live')
    def restore(self):self.step('restore')
    def verify_restored(self):self.step('verify_restored')


@pytest.mark.parametrize('failure',['install','start','verify_live'])
def test_each_transition_failure_requires_restoration(failure):
    ops=FakeOperations(failure)
    with pytest.raises(ValueError,match=failure):transition(ops)
    assert ops.events[-2:]==['restore','verify_restored']


def test_preflight_failure_never_mutates():
    ops=FakeOperations('preflight')
    with pytest.raises(ValueError):transition(ops)
    assert ops.events==['preflight']


def test_recovery_failure_is_explicit():
    ops=FakeOperations('start',True)
    with pytest.raises(RuntimeError,match='recovery could not be verified'):transition(ops)
    assert 'verify_restored' not in ops.events


def test_success_never_restores():
    ops=FakeOperations();transition(ops)
    assert ops.events==['preflight','install','start','verify_live']


@pytest.mark.parametrize('committed',[False,True])
def test_failed_rollback_retains_profile_matching_actual_active(monkeypatch,tmp_path,committed):
    selection=tmp_path/'selection.json';selection.write_text('{}')
    monkeypatch.setattr(release,'SELECTION',selection)
    monkeypatch.setattr(release,'install_adapter',lambda:None)
    monkeypatch.setattr(release.owned,'command_lock',lambda engine:nullcontext())
    monkeypatch.setattr(release.owned,'stop',lambda engine:None)
    ops=Operations.__new__(Operations);ops.started_install=True;ops.engine=None;ops.receipt=receipt()
    active=[release.VERSION];ops.active=lambda:active[0];ops.journal=lambda phase:None
    def fail_call(action,version,settings):
        assert action=='rollback' and version==release.OLD and settings is release.BASE
        if committed:active[0]=release.OLD
        raise RuntimeError('response failed')
    ops.call=fail_call;ops.start=lambda:pytest.fail('cannot claim restart after failed rollback')
    with pytest.raises(RuntimeError):ops.restore()
    assert selection.exists()==(not committed)


def test_process_adapter_installs_only_reviewed_class(monkeypatch,tmp_path):
    import qa_core.application.qa_usecase as usecase
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher
    from qa_core.retrieval.supported_evidence_v2 import relevant_docs
    monkeypatch.setattr(release,'SELECTION',tmp_path/'absent.json')
    for name in ['build_profile','validate_profile','verify_active','SERVER_MODULE']:
        monkeypatch.setattr(release.owned,name,getattr(release.owned,name))
    release.install_adapter()
    assert release.owned.build_profile() is release.BASE
    assert release.owned.SERVER_MODULE==release.ORIGINAL_SERVER
    monkeypatch.setattr(usecase,'Searcher',usecase.Searcher)
    monkeypatch.setattr(usecase,'relevant_docs',usecase.relevant_docs)
    from scripts.content_quality_local_server import install_retrieval
    install_retrieval()
    assert usecase.Searcher is SupportedFaqSearcher
    assert usecase.relevant_docs is relevant_docs


def test_new_bodies_exactly_match_review_roster():
    from scripts.prepare_content_quality_release import outbound_entries
    entries=outbound_entries()
    assert len(entries)==28 and len({e['source_id'] for e in entries})==28
    assert all(e['answer'] and e['synthetic'] and e['access_scope']['tenant_id']=='default' for e in entries)
