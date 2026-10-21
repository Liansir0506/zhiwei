"""Offline simulation of evidence completeness, never a live SOP/action gate."""
from datetime import datetime
import re

STATES={'recorded','missing','conflicting','not_applicable'}


def preview(spec,packet):
    """Caller supplies hypothetical declarations; source/authority remain unverified."""
    result=dict(operational_ready=False,root_cause_confirmed=False,action_authorized=False,
                authority_verified=False,source_content_verified=False,independent_quality=False)
    def finish(state,reason):return dict(result,state=state,reason=reason)
    if packet.get('authorized') is not True:return finish('stop_access','没有现有授权记录可读声明')
    if not all(isinstance(packet.get(k),str) and packet[k].strip() for k in ('object_id','manufacturer','model','software_version')):
        return finish('stop_applicability','对象/厂家/型号/版本缺失')
    if packet.get('deployment_matches') is not True:return finish('stop_applicability','部署或制式不匹配，停止套用')
    records=packet.get('records')
    if not isinstance(records,dict):return finish('stop_evidence','未提供专题记录')
    windows=[]
    for ident,_ in spec[1]:
        r=records.get(ident,{})
        if not isinstance(r,dict) or r.get('state') not in STATES:return finish('stop_evidence','记录状态未知')
        if r['state']=='conflicting':return finish('stop_conflict','记录矛盾，交接复核')
        if r['state']!='recorded':return finish('stop_evidence','缺失/不适用项须另审部署与分支，不能当作零')
        if (r.get('object_id')!=packet['object_id'] or r.get('software_version')!=packet['software_version'] or
            not isinstance(r.get('source_ref'),str) or not r['source_ref'].strip() or
            not isinstance(r.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',r['sha256']) or
            not isinstance(r.get('unit'),str) or not r['unit'].strip() or r.get('comparable') is not True):
            return finish('stop_evidence','身份/版本/出处/单位/可比性不足')
        try:
            start,end,at=[datetime.fromisoformat(r[k]) for k in ('window_start','window_end','observed_at')]
            if any(t.utcoffset() is None for t in (start,end,at)) or not start<=at<=end:raise ValueError()
            windows.append((start,end))
        except (KeyError,ValueError,TypeError):return finish('stop_evidence','时间/时区或观察窗无效')
    if max(s for s,_ in windows)>min(e for _,e in windows):return finish('stop_evidence','专题记录时间窗不重叠，不能作同窗关联')
    primary,counter=packet.get('primary_supported'),packet.get('counter_supported')
    if type(primary) is not bool or type(counter) is not bool:return finish('stop_evidence','分支关联尚未复核')
    if primary and counter:return finish('stop_conflict','支持与相反记录同时成立，停止选分支')
    if primary:return finish('hypothesis_review','只交接该假设供复核，不确认原因或执行动作')
    if counter:return finish('alternative_review','交接相反记录，停止沿用模板原因')
    return finish('no_supported_branch','已有记录不支持所列分支，保留未知并升级复核')
