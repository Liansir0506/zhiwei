from copy import deepcopy
import pytest
from qa_core.quality.independent_readiness import extract_records,inventory,sha,intake_template,screen_intake
from scripts.audit_holdout_readiness import canonical_query


def registry():
    records=extract_records({'cases':[dict(id='old',query='HTTP 429？',split='frozen',development_exposed=False,
        source_ids=['source-old'],body_families=['body-old'],topic_groups=['topic-old'],template_groups=['template-old'])]},'old.json','a'*64)
    return inventory(records,[],[dict(blocking_aliases=['same.md','source-old'],canonical_body_sha256='body-old')],coverage_notes=['limited'])


def case(ident='new'):
    return dict(id=ident,query_sha256=sha('independently-held-query'),canonical_query_sha256=sha('independentlyheldquery'),
        truth_sha256='b'*64,evidence_manifest_sha256='c'*64,source_ids=['new-source'],body_families=['new-body'],
        topic_groups=['new-topic'],template_groups=['new-template'],development_exposed=False,
        expected_kind='answer',request_context={'tenant_id':'default','roles':['ops']})


def payload(cases):
    p=intake_template('v1','d'*64);p['cases']=cases;return p


def screen(p):return screen_intake(p,registry(),'v1','d'*64)


def test_public_old_frozen_false_exposure_does_not_restore_independence():
    r=registry();assert r['summary']['legacy_declared_frozen_occurrences']==1
    assert r['summary']['verified_independent_frozen']==0 and r['records'][0]['developer_exposed']
    assert not r['groups'][0]['may_split_into_frozen']


def test_empty_intake_does_not_compute_quality_denominator():
    r=screen(payload([]));assert r['state']=='blocked' and 'no_new_human_cases' in r['blockers']
    assert r['verified_independent_frozen']==0 and not r['metrics_eligible']


def test_apparently_clean_hashes_still_need_custody_and_semantic_review():
    p=payload([case()]);p['custody'].update(status='submitted',proposer_id='human-A',reviewer_id='human-B',receipt_sha256='e'*64)
    r=screen(p);assert r['static_link_screen_passed']
    assert r['state']=='needs_verified_custody_and_semantic_review'
    assert not r['run_authorized'] and not r['metrics_eligible'] and r['verified_independent_frozen']==0


def test_punctuation_and_full_width_rephrasing_is_exposed():
    c=case();c['canonical_query_sha256']=sha(canonical_query('http-４２９!'))
    assert 'known_exposed_question_hash' in screen(payload([c]))['blocked_cases'][0]['reasons']


@pytest.mark.parametrize('field,value',[('source_ids','source-old'),('source_ids','same.md'),('body_families','body-old'),
                                      ('topic_groups','topic-old'),('template_groups','template-old')])
def test_known_development_link_rejects_case(field,value):
    c=case();c[field]=[value]
    assert 'known_development_'+field in screen(payload([c]))['blocked_cases'][0]['reasons']


def test_exposure_propagates_through_candidate_group_transitively():
    a=case('a');a['source_ids']=['source-old'];a['topic_groups']=['bridge-1']
    b=case('b');b['query_sha256']=sha('b');b['canonical_query_sha256']=sha('b');b['topic_groups']=['bridge-1'];b['template_groups']=['bridge-2']
    c=case('c');c['query_sha256']=sha('c');c['canonical_query_sha256']=sha('c');c['topic_groups']=['other'];c['template_groups']=['bridge-2']
    result=screen(payload([a,b,c]));blocked={x['id']:x['reasons'] for x in result['blocked_cases']}
    assert set(blocked)=={'a','b','c'} and 'connected_to_exposed_group' in blocked['c']


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(approved=True),
    lambda p:p.update(target_kb_version='v2'),
    lambda p:p.update(baseline_binding_sha256='e'*64),
    lambda p:p['custody'].update(status='approved'),
    lambda p:p['cases'][0].update(query='unseen plain-text question'),
    lambda p:p['cases'][0].update(truth_sha256='not-a-hash'),
    lambda p:p['cases'][0].update(body_families=[]),
    lambda p:p['cases'][0].update(request_context={'tenant_id':'default','roles':['admin']}),
    lambda p:p['cases'].append(deepcopy(p['cases'][0])),
    lambda p:p.update(custody=None),
])
def test_invalid_or_self_authorizing_intake_is_rejected(mutation):
    p=payload([case()]);mutation(p)
    with pytest.raises(ValueError):screen(p)


def test_ambiguous_source_alias_keeps_all_blocking_bodies():
    records=extract_records({'question':'public question','source':'duplicate.md'},'input','a'*64)
    r=inventory(records,[],[dict(blocking_aliases=['duplicate.md','s1'],canonical_body_sha256='b1'),
                           dict(blocking_aliases=['duplicate.md','s2'],canonical_body_sha256='b2')],coverage_notes=[])
    assert {'b1','b2'}<=set(r['records'][0]['group_links']['body_families'])
    assert r['summary']['verified_independent_frozen']==0


def test_source_only_public_pending_card_is_blocked_without_any_questions():
    r=inventory([],[],[dict(blocking_aliases=['pending-card'],canonical_body_sha256='known-pending-body',is_current=False)],coverage_notes=[])
    c=case();c['source_ids']=['pending-card'];p=payload([c])
    result=screen_intake(p,r,'v1','d'*64)
    assert 'known_development_source_ids' in result['blocked_cases'][0]['reasons']
    assert r['summary']['current_sources']==0 and r['summary']['public_source_occurrences']==1
