from copy import deepcopy

import pytest

from qa_core.quality.sop_evidence_preview import preview
from scripts.sop_evidence_draft_content import SPECS
from scripts.prepare_sop_evidence_drafts import simulated_packet


@pytest.mark.parametrize('key',list(SPECS))
@pytest.mark.parametrize('variant,expected',[
    ('primary','hypothesis_review'),('counter','alternative_review'),('both','stop_conflict'),
    ('neither','no_supported_branch'),('missing','stop_evidence'),('unauthorized','stop_access')])
def test_all_families_and_opposite_missing_branches_never_authorize_action(key,variant,expected):
    s=SPECS[key];p=simulated_packet(s)
    if variant=='counter':p.update(primary_supported=False,counter_supported=True)
    if variant=='both':p['counter_supported']=True
    if variant=='neither':p.update(primary_supported=False,counter_supported=False)
    if variant=='missing':p['records'][s[1][0][0]]['state']='missing'
    if variant=='unauthorized':p['authorized']=False
    saved=deepcopy(p);r=preview(s,p)
    assert r['state']==expected and p==saved
    assert not any(r[k] for k in ('operational_ready','root_cause_confirmed','action_authorized',
                                  'authority_verified','source_content_verified','independent_quality'))


@pytest.mark.parametrize('field,value',[
    ('object_id','different'),('software_version','different'),('source_ref',''),('sha256','bad'),
    ('unit',''),('comparable',False),('comparable',1),('state','conflicting'),('state','not_applicable'),
    ('observed_at','2026-10-14T00:02:00'),('observed_at','2026-10-14T00:06:00+08:00'),
    ('window_start','2026-10-14T00:07:00+08:00')])
def test_invalid_identity_traceability_time_or_units_cannot_select_branch(field,value):
    s=next(iter(SPECS.values()));p=simulated_packet(s);p['records'][s[1][0][0]][field]=value
    assert preview(s,p)['state'] in {'stop_evidence','stop_conflict'}


def test_individually_valid_but_nonoverlapping_windows_stop_association():
    s=next(iter(SPECS.values()));p=simulated_packet(s)
    p['records'][s[1][0][0]].update(window_start='2026-10-14T01:00:00+08:00',
        window_end='2026-10-14T01:05:00+08:00',observed_at='2026-10-14T01:02:00+08:00')
    assert preview(s,p)['state']=='stop_evidence'


@pytest.mark.parametrize('field,value',[('manufacturer',''),('model',None),('software_version',''),
    ('deployment_matches',False),('deployment_matches',1),('authorized',1),('primary_supported',1),
    ('counter_supported','false')])
def test_missing_applicability_or_boolean_declaration_cannot_be_promoted(field,value):
    s=next(iter(SPECS.values()));p=simulated_packet(s);p[field]=value
    assert preview(s,p)['state'].startswith('stop_')


def test_twenty_specs_have_distinct_concrete_record_keys_and_two_branch_predicates():
    assert len(SPECS)==20
    for _,fields,positive,counter in SPECS.values():
        assert len(fields)==3 and len({k for k,_ in fields})==3
        assert len(positive)==len(counter)==2 and positive!=counter
    assert len({tuple(k for k,_ in s[1]) for s in SPECS.values()})==20
