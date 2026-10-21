"""Merged-corpus revision: a device name alone does not identify optical scope."""
import re
from qa_core.retrieval import supported_evidence as base


def primary_topics(text,comparison=False):
    matches=[]
    for order,(topic,pattern) in enumerate(base.TOPICS):
        if topic=='optical':pattern=r'光功率|收光|发光|'+base.ascii_term('SPN')+r'.*(?:接收功率|dBm)'
        if topic=='ports':pattern=base.ascii_term(pattern)
        found=re.search(pattern,text,re.I)
        if found:matches.append((found.start(),order,topic))
    topics=[t for _,_,t in sorted(matches)]
    if not comparison and 'ng_setup' in topics:return ['ng_setup']
    return topics if comparison else topics[:1]


def relevant_docs(query,docs):
    docs=list(docs);comparison=bool(re.search(r'对比|比较|区别|差异|分别',query))
    requested=primary_topics(query,comparison)
    if requested:
        docs=[d for d in docs if d.extra.get('needs_clarification') or
            any(t in primary_topics(base.subject_text(d)) for t in requested)]
        if comparison:
            covered={t for d in docs for t in primary_topics(base.subject_text(d))}
            if any(t not in covered for t in requested):return []
    guarded=re.sub(base.ascii_term('VSWR'),'驻波比',query,flags=re.I)
    docs=base.legacy_relevant_docs(guarded,docs)
    if 'clock' in requested and re.search(r'备用|主备',query):
        docs=[d for d in docs if d.extra.get('needs_clarification') or
            re.search(r'备用|主备','\n'.join((d.text or '',d.question or '',d.answer or '')))]
    if re.search(r'间歇|瞬态|偶发',query):
        docs=[d for d in docs if d.extra.get('needs_clarification') or
            re.search(r'间歇|瞬态|偶发','\n'.join((d.text or '',d.question or '',d.answer or '')))]
    # A general clock card does not establish facts about a named timing
    # protocol. Keep the existing subject checks and require actual support.
    protocols=[p for p in ('PTP','SyncE') if re.search(base.ascii_term(p),query,re.I)]
    if protocols:
        def supports(doc,p):
            return bool(re.search(base.ascii_term(p),'\n'.join((doc.text or '',doc.question or '',doc.answer or '')),re.I))
        docs=[d for d in docs if d.extra.get('needs_clarification') or
            (any(supports(d,p) for p in protocols) if comparison else all(supports(d,p) for p in protocols))]
        if comparison and any(not any(supports(d,p) for d in docs) for p in protocols):return []
    return docs
