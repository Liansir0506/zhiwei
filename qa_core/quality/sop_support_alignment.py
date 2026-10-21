"""Literal, source-bound guide/SOP alignment; never site truth or action approval."""
from collections import Counter
import hashlib
from pathlib import Path
import re

from scripts.sop_evidence_draft_content import SPECS
from qa_core.quality.support_review import ACTIONS

LABELS={
    'handoff_only':'仅补交接记录，未确认影响或完成复盘',
    'records_requested':'补了具体取证项，现场记录尚未提供',
    'change_records_requested':'提及变更取证，未提供真实前后值与批准记录',
    'change_records_missing':'专题记录未明确列出变更核对项',
    'dependency_records_requested':'提及依赖或对端取证，仍需核对实际拓扑',
    'dependency_records_missing':'专题记录未明确列出上下游依赖核对项',
    'comparison_requested':'提及参数或配置比对，未证明配置一致',
    'comparison_missing':'专题记录未明确列出配置比对项',
    'alarm_records_requested':'提及相关告警取证，未证明告警因果',
    'alarm_records_missing':'专题记录未明确列出派生告警关系',
    'action_missing':'没有可执行处置依据，不授权现场动作',
    'recovery_missing':'恢复指标、测量方法、真实分母及合格值仍缺',
    'observation_missing':'观察时长与复发判据仍缺，不套用三个周期',
}


def pointer(text, filename, phrase):
    lines=[(i,line) for i,line in enumerate(text.splitlines(),1) if phrase in line]
    if len(lines)!=1:raise ValueError('one exact guide line required')
    line,quote=lines[0]
    return dict(source=filename,line=line,quote=quote,
                file_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())


def align_guide(entry, text):
    """Only recognized sealed guide prose; these flags describe text, not field evidence."""
    if hashlib.sha256(text.encode()).hexdigest()!=entry['body_sha256']:
        raise ValueError('guide exact bytes drift')
    spec=SPECS[entry['hypothesis']]
    records=[]
    for i,(ident,description) in enumerate(spec[1],1):
        expected=f'{i}. **{ident}**：{description}。'
        proof=pointer(text,entry['filename'],expected)
        if proof['quote']!=expected:raise ValueError('record prose differs from known guide')
        records.append(dict(id=ident,description=description,proof=proof,site_record_provided=False))
    primary=[pointer(text,entry['filename'],'- '+v+'。') for v in spec[2]]
    counter=[pointer(text,entry['filename'],'- '+v+'。') for v in spec[3]]
    handoff=pointer(text,entry['filename'],'交接应列：')
    action=pointer(text,entry['filename'],'逐动作授权与执行者')
    recovery=pointer(text,entry['filename'],'业务/告警/精度恢复指标')
    rows=[]
    for step,label in enumerate(ACTIONS,1):
        status,proof='records_requested',[r['proof'] for r in records]
        if step in {1,10}:status,proof='handoff_only',[handoff]
        elif step in {2,4,5,7}:
            pattern,yes,no={2:('变更','change_records_requested','change_records_missing'),
                4:('依赖|下游|上游|对端','dependency_records_requested','dependency_records_missing'),
                5:('配置|参数|规则|布局|订阅版本','comparison_requested','comparison_missing'),
                7:('派生告警','alarm_records_requested','alarm_records_missing')}[step]
            matches=[r['proof'] for r in records if re.search(pattern,r['description'])]
            status,proof=(yes,matches) if matches else (no,[action if step==2 else handoff])
        elif step==6:status,proof='action_missing',[action]
        elif step==8:status,proof='recovery_missing',[recovery]
        elif step==9:status,proof='observation_missing',[recovery]
        rows.append(dict(old_step=step,old_label=label,text_status=status,status_label=LABELS[status],
            guide_proofs=proof,site_evidence_verified=False,operational_supported=False))
    return dict(id=entry['id'],guide_source=entry['filename'],title=entry['title'],equipment=entry['equipment'],
        alarm=entry['alarm'],hypothesis=entry['hypothesis'],network_tech=entry['network_tech'],
        guide_sha256=entry['body_sha256'],records=records,primary_conditions=primary,counter_conditions=counter,
        steps=rows,source_scope='project_authored_literal_alignment_only',
        site_evidence_provided=False,root_cause_confirmed=False,action_authorized=False,full_sop_approved=False,
        manufacturer_document=None,independent_quality=None,
        unresolved_requirements=[
            dict(kind='manufacturer',label='厂家、型号、软件版本、手册章节与适用条件',status='missing'),
            dict(kind='site_evidence',label='有权读取且可比的真实现场原始记录',status='missing'),
            dict(kind='action',label='逐动作依据、授权执行者与影响边界',status='missing'),
            dict(kind='rollback',label='原配置备份、停止触发、逐动作回退与失败升级',status='missing'),
            dict(kind='recovery',label='恢复指标、合格值、真实分母、观察时长及复发判据',status='missing')])


def validate_matrix(matrix):
    entries=matrix.get('entries',[])
    if len(entries)!=20 or len({e['guide_source'] for e in entries})!=20:raise ValueError('exact twenty guides required')
    parents=[]
    for e in entries:
        if [r['old_step'] for r in e['steps']]!=list(range(1,11)) or len(e['records'])!=3:
            raise ValueError('ten steps and three records per family required')
        if len(e['primary_conditions'])!=2 or len(e['counter_conditions'])!=2:raise ValueError('both branches required')
        for key in ('root_cause_confirmed','action_authorized','full_sop_approved','site_evidence_provided'):
            if e[key] is not False:raise ValueError('literal alignment cannot grant field conclusions')
        if e['manufacturer_document'] is not None or e['independent_quality'] is not None:
            raise ValueError('no manufacturer or independent truth provided')
        if any(r['operational_supported'] is not False or r['site_evidence_verified'] is not False for r in e['steps']):
            raise ValueError('no operational support from template matching')
        if len(e['parent_references'])!=10:raise ValueError('ten existing references per family')
        parents.extend(p['filename'] for p in e['parent_references'])
        for item in [r['proof'] for r in e['records']]+e['primary_conditions']+e['counter_conditions']+[p for r in e['steps'] for p in r['guide_proofs']]:
            if item['source']!=e['guide_source'] or item['file_sha256']!=e['guide_sha256']:
                raise ValueError('guide provenance cross-wired')
    if len(parents)!=200 or len(set(parents))!=200:raise ValueError('exact unique 200 parent references')
    if any(matrix.get(k) is not False for k in ('publication_eligible','activated','human_semantic_approved')):
        raise ValueError('no publication or human review grant')
    if matrix.get('independent_denominator')!=0:raise ValueError('exposed templates are not independent samples')
    return True


def summarize(entries):
    counts=Counter(r['text_status'] for e in entries for r in e['steps'])
    return dict(guide_families=len(entries),parent_files=sum(len(e['parent_references']) for e in entries),
        step_alignment_rows=sum(len(e['steps']) for e in entries),record_contracts=sum(len(e['records']) for e in entries),
        primary_conditions=sum(len(e['primary_conditions']) for e in entries),counter_conditions=sum(len(e['counter_conditions']) for e in entries),
        unresolved_requirements=sum(len(e['unresolved_requirements']) for e in entries),text_status_counts=dict(counts),
        field_evidence_verified=0,operational_actions_approved=0,independent_denominator=0)
