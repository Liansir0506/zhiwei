"""Revision literal coverage; requested records never become field truth or actions."""
from copy import deepcopy
from collections import Counter
from qa_core.quality.sop_support_alignment import align_guide, pointer, LABELS, summarize

REQUESTED={2:'change_records_requested',4:'dependency_records_requested',5:'comparison_requested',7:'alarm_records_requested'}


def revise_alignment(original_entry, revision_entry, text, parent_references):
    spec=deepcopy(original_entry)
    spec['body_sha256']=revision_entry['body_sha256']
    result=align_guide(spec,text)
    result['parent_references']=deepcopy(parent_references)
    result['source_revision']='draft-r2'
    result['previous_guide_sha256']=revision_entry['previous_body_sha256']
    result['previous_step_statuses']={str(s['old_step']):s['text_status'] for s in result['steps']}
    for record in result['records']:record.update(kind='original_record',requested_fields=[])
    for extension in revision_entry['extensions']:
        proof=pointer(text,result['guide_source'],extension['description'])
        record=dict(id=extension['id'],description=extension['description'],proof=proof,
            site_record_provided=False,kind='revision_extension',requested_fields=deepcopy(extension['requested_fields']),
            subject=extension['subject'],boundary=extension['boundary'],old_step=extension['old_step'],
            value=None,site_evidence_verified=False,action_authorized=False)
        result['records'].append(record)
        step=result['steps'][extension['old_step']-1]
        if not step['text_status'].endswith('_missing') and step['text_status']!='comparison_missing':
            raise ValueError('revision must address a recognized literal omission')
        step.update(text_status=REQUESTED[extension['old_step']],status_label=LABELS[REQUESTED[extension['old_step']]],
            guide_proofs=[proof],revision_record_ids=[extension['id']])
    for step in result['steps']:
        step['previous_text_status']=result['previous_step_statuses'][str(step['old_step'])]
        step['text_changed']=step['text_status']!=step['previous_text_status']
    return result


def validate_matrix(matrix):
    entries=matrix['entries'];parents=[];record_ids=[];changes=Counter()
    if len(entries)!=20 or len({e['guide_source'] for e in entries})!=20:
        raise ValueError('exact twenty revised guides required')
    for e in entries:
        if e['source_revision']!='draft-r2' or [s['old_step'] for s in e['steps']]!=list(range(1,11)):
            raise ValueError('exact revision and ten steps required')
        if len(e['primary_conditions'])!=2 or len(e['counter_conditions'])!=2 or len(e['parent_references'])!=10:
            raise ValueError('both condition branches and original parent bindings required')
        if any(e.get(k) is not False for k in ('site_evidence_provided','root_cause_confirmed','action_authorized','full_sop_approved')):
            raise ValueError('literal coverage grants no field conclusion')
        if e['manufacturer_document'] is not None or e['independent_quality'] is not None:
            raise ValueError('no manufacturer or independent truth supplied')
        parents.extend(p['filename'] for p in e['parent_references'])
        if len([r for r in e['records'] if r['kind']=='original_record'])!=3:
            raise ValueError('three original contracts per guide required')
        for r in e['records']:
            record_ids.append(r['id'])
            if r['site_record_provided'] is not False:raise ValueError('record request is not an observation')
            if r['kind']=='revision_extension' and (r['value'] is not None or r['site_evidence_verified'] is not False or
                    r['action_authorized'] is not False or len(set(r['requested_fields']))!=5):
                raise ValueError('specific extension cannot supply facts or actions')
        for s in e['steps']:
            if s['site_evidence_verified'] is not False or s['operational_supported'] is not False:
                raise ValueError('operational gap cannot be promoted by text')
            if s['text_changed']:changes[s['old_step']]+=1
            if s['old_step'] in REQUESTED and s['text_status']!=REQUESTED[s['old_step']]:
                raise ValueError('requested record coverage differs')
            if s['old_step'] in {6,8,9} and not s['text_status'].endswith('_missing'):
                raise ValueError('action, recovery and observation gaps must remain')
        proofs=[r['proof'] for r in e['records']]+e['primary_conditions']+e['counter_conditions']+[p for s in e['steps'] for p in s['guide_proofs']]
        if any(p['source']!=e['guide_source'] or p['file_sha256']!=e['guide_sha256'] for p in proofs):
            raise ValueError('revision evidence cross-wired')
    if len(parents)!=200 or len(set(parents))!=200 or len(record_ids)!=109 or len(set(record_ids))!=109:
        raise ValueError('exact parent and record rosters required')
    if dict(changes)!={2:11,4:9,5:10,7:19}:
        raise ValueError('exact forty-nine literal changes required')
    if any(matrix.get(k) is not False for k in ('publication_eligible','activated','human_semantic_approved')) or matrix['independent_denominator']!=0:
        raise ValueError('no publication or independent quality grant')
    summary=summarize(entries)
    summary.update(baseline_record_contracts=60,record_extensions=49,literal_gaps_addressed=dict(changes))
    if matrix['summary']!=summary:raise ValueError('summary denominator differs from actual entries')
    return True


def revision_summary(entries):
    result=summarize(entries)
    changes=Counter(s['old_step'] for e in entries for s in e['steps'] if s['text_changed'])
    result.update(baseline_record_contracts=60,record_extensions=49,literal_gaps_addressed=dict(changes))
    return result
