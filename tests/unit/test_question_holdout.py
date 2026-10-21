from copy import deepcopy

import pytest

from qa_core.quality.independent_readiness import intake_template, screen_intake, sha
from qa_core.quality.question_holdout import roster, screen, template


def registry():
    return dict(source_index=[dict(is_current=True, asset_id='asset-current', source_id='shared-source',
        filename='current.md', source_version='r1', doc_type='sop', file_sha256='1'*64,
        metadata_sha256='2'*64, canonical_body_sha256='3'*64, blocking_aliases=['shared-source', 'asset-current']),
        dict(is_current=False, asset_id='pending', canonical_body_sha256='4'*64, blocking_aliases=['pending'])],
        raw_query_hashes=[sha('public')], canonical_query_hashes=[sha('public')],
        blocking_link_index=dict(source_ids=['asset-current','shared-source','pending'],
            body_families=['3'*64,'4'*64], topic_groups=['known-topic'], template_groups=['old-template']))


def case(ident='new'):
    return dict(id=ident, query_sha256=sha('opaque-'+ident), canonical_query_sha256=sha('canonical-'+ident),
        truth_sha256='a'*64, evidence_manifest_sha256='b'*64, source_ids=['asset-current'],
        body_families=['3'*64], topic_groups=['known-topic'], template_groups=['new-template-'+ident],
        development_exposed=False, expected_kind='answer', request_context=dict(tenant_id='default', roles=['ops']))


def run(cases, modify=None):
    p=template('v1','c'*64);p['cases']=cases
    if modify:modify(p)
    r=registry()
    return screen(p,r,roster(r),'v1','c'*64)


def test_known_corpus_allowed_only_under_explicit_new_question_policy():
    r=run([case()])
    assert r['static_screen_passed'] and not r['scope_declaration']['source_independent']
    old=intake_template('v1','c'*64);old['cases']=[case()]
    strict=screen_intake(old,registry(),'v1','c'*64)
    assert not strict['static_link_screen_passed']


def test_hash_pass_and_anonymous_role_declarations_never_authorize():
    result=run([case()],lambda p:p['custody'].update(status='submitted',receipt_sha256='d'*64,
                                                  reviewed_at_utc='2026-10-13T00:00:00Z'))
    assert result['static_screen_passed'] and not result['run_authorized'] and not result['metrics_eligible']
    assert result['quality_metrics'] is None and result['verified_question_holdout_frozen']==0
    assert result['verified_source_independent_frozen']==0


def test_empty_manifest_has_no_denominator():
    result=run([])
    assert result['state']=='blocked' and 'no_new_human_cases' in result['blockers']
    assert result['quality_metrics'] is None


@pytest.mark.parametrize('field,value,reason',[
    ('query_sha256',sha('public'),'known_exposed_question_hash'),
    ('canonical_query_sha256',sha('public'),'known_exposed_question_hash'),
    ('template_groups',['old-template'],'known_exposed_question_template'),
    ('development_exposed',True,'declared_development_exposed'),
    ('source_ids',['pending'],'source_not_in_exact_current_corpus'),
    ('source_ids',['shared-source'],'source_not_in_exact_current_corpus'),
    ('body_families',['4'*64],'source_body_binding_mismatch')])
def test_exposure_pending_alias_and_false_body_binding_block(field,value,reason):
    c=case();c[field]=value
    assert reason in run([c])['blocked_cases'][0]['reasons']


def test_source_and_topic_reuse_do_not_contaminate_unrelated_new_question_group():
    a=case('a');a['query_sha256']=sha('public')
    result=run([a,case('b')])
    assert [c['id'] for c in result['blocked_cases']]==['a']


def test_question_template_exposure_propagates_transitively():
    a,b,c=case('a'),case('b'),case('c')
    a['query_sha256']=sha('public');a['template_groups']=['bridge-1']
    b['template_groups']=['bridge-1','bridge-2'];c['template_groups']=['bridge-2']
    result=run([a,b,c]);assert len(result['blocked_cases'])==3
    assert result['blocked_cases'][-1]['reasons']==['connected_to_blocked_question_template_group']


def test_duplicate_question_commitments_never_increase_denominator():
    a,b=case('a'),case('b');b['canonical_query_sha256']=a['canonical_query_sha256']
    result=run([a,b]);assert len(result['blocked_cases'])==2
    assert all('duplicate_question_commitment' in c['reasons'] for c in result['blocked_cases'])


@pytest.mark.parametrize('mutate',[
    lambda p:p.update(approved=True),
    lambda p:p.update(policy='source_body_topic_template_group_holdout'),
    lambda p:p.update(target_kb_version='v2'),
    lambda p:p.update(baseline_binding_sha256='d'*64),
    lambda p:p.update(scope='admin'),
    lambda p:p['custody'].update(status='approved'),
    lambda p:p['custody'].update(reviewer_alias=p['custody']['proposer_alias']),
    lambda p:p['custody'].update(status='submitted'),
    lambda p:p['custody'].update(receipt_sha256='bad'),
    lambda p:p['custody'].update(reviewed_at_utc='2026-10-13T00:00:00'),
    lambda p:p['custody'].update(reviewed_at_utc='2026-10-13T00:00:00+08:00'),
    lambda p:p['cases'][0].update(query='private plaintext'),
    lambda p:p['cases'][0].update(truth='private plaintext'),
    lambda p:p['cases'][0].update(truth_sha256='bad'),
    lambda p:p['cases'][0].update(source_ids=[]),
    lambda p:p['cases'][0].update(body_families=['3'*64,'3'*64]),
    lambda p:p['cases'][0].update(development_exposed=0),
    lambda p:p['cases'][0].update(request_context=dict(tenant_id='default',roles=['admin'])),
    lambda p:p['cases'].append(deepcopy(p['cases'][0])),
])
def test_schema_scope_and_self_approval_rejected(mutate):
    with pytest.raises(ValueError):run([case()],mutate)


def test_caller_cannot_replace_roster_with_pending_asset():
    r=registry();sources=roster(r);sources[0]['asset_id']='pending'
    p=template('v1','c'*64);p['cases']=[case()]
    with pytest.raises(ValueError):screen(p,r,sources,'v1','c'*64)


def test_ambiguous_current_asset_rejected():
    r=registry();r['source_index'].append(deepcopy(r['source_index'][0]))
    with pytest.raises(ValueError):roster(r)
