"""Literal admitted alarm/device targets narrow recall and reread, never grant access."""
import re
from qa_core.retrieval.constraints import EvidenceConstraint


def constraint_for_query(query,matrix,plan):
    entries=matrix['entries'];approved={e['filename']:e for e in plan['entries']}
    devices={e['equipment'] for e in entries}
    alarm_pattern=r'(?<![A-Za-z0-9])ALM-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*'
    alarms={a.upper() for a in re.findall(alarm_pattern,query,re.I)}
    device_text=re.sub(alarm_pattern,' ',query,flags=re.I)
    mentioned={name for name in devices if re.search(r'(?<![A-Za-z0-9])'+re.escape(name)+r'(?![A-Za-z0-9])',device_text,re.I)}
    if alarms:
        targets=[e for e in entries if e['alarm'].upper() in alarms]
        if len({e['alarm'].upper() for e in targets})!=len(alarms):
            return EvidenceConstraint(pairs=()),dict(kind='alarm',labels=sorted(alarms),reason='unknown_alarm')
        if mentioned and any(e['equipment'] not in mentioned for e in targets):
            return EvidenceConstraint(pairs=()),dict(kind='alarm_device_conflict',labels=sorted(alarms),reason='topic_conflict')
        kind,labels='alarm',sorted(alarms)
    elif mentioned:
        targets=[e for e in entries if e['equipment'] in mentioned]
        kind,labels='equipment',sorted(mentioned)
    else:
        return None,dict(kind='exploratory',labels=[],reason='no_explicit_admitted_topic')
    pairs=[]
    for e in targets:
        source=approved[e['guide_source']]
        if source['source_revision']!='draft-r2' or source['body_sha256']!=e['guide_sha256']:
            raise ValueError('admitted topic binding changed')
        pairs.append((source['source_identity']['asset_id'],'draft-r2'))
    return EvidenceConstraint(pairs=tuple(pairs)),dict(kind=kind,labels=labels,reason='',sources=[e['guide_source'] for e in targets])
