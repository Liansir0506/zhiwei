"""Subject-bound evidence selection, without widening retrieval or ACL scope.

The released host keeps its frozen import. Candidate verification imports this
module explicitly; its integration patch is reviewed with the next release.
"""
import re

from qa_core.retrieval.relevance import relevant_docs as legacy_relevant_docs


def ascii_term(term):
    return r'(?<![A-Za-z0-9])(?:'+term+r')(?![A-Za-z0-9])'


TOPICS = (
    ('power', r'发射功率|功率等级|'+ascii_term(r'FR1|FR2|PC\d+|Power\s+Class')),
    ('ng_setup', r'NG\s*Setup|NG建立|NG接口建立'),
    ('ports', r'N2|N4|PFCP|SCTP'),
    ('vswr', r'驻波|回波损耗|'+ascii_term('VSWR|DTF')),
    ('gps', r'失锁|'+ascii_term('GPS|GNSS')),
    ('optical', r'光功率|收光|发光|'+ascii_term('SPN')),
    ('bbu', ascii_term('BBU')+r'|基带.*(?:掉电|供电|电源)'),
    ('handover', r'乒乓|ping[- ]?pong|'+ascii_term('A3|hysteresis|time.?to.?trigger')),
    ('interference', r'干扰|扫频|频谱|瀑布图'),
    ('transport', r'传输(?:链路)?中断|链路中断|'+ascii_term('LOS')),
    ('clock', r'时钟|同步异常|'+ascii_term('PTP|SyncE')),
)


def topics(text):
    matches=[]
    for order,(topic,pattern) in enumerate(TOPICS):
        # N2/N4/etc use ASCII boundaries too; Chinese adjacency is permitted.
        if topic=='ports':pattern=ascii_term(pattern)
        found=re.search(pattern,text,re.I)
        if found:matches.append((found.start(),order,topic))
    return [topic for _,_,topic in sorted(matches)]


def primary_topics(text, *, comparison=False):
    found=topics(text)
    # A named procedure is the subject of "SCTP failure means NG SETUP
    # FAILURE?"; its transport mention is supporting context, not a port query.
    if not comparison and 'ng_setup' in found:return ['ng_setup']
    return found if comparison else found[:1]


def subject_text(doc):
    if doc.doc_type=='faq':return doc.question or doc.text or ''
    # A clause/title can carry the source subject; body cross-references alone
    # do not establish that a fragment is about the requested subject.
    heading=getattr(doc,'clause','') or ''
    if topics(heading):return heading
    declared=re.search(r'本卡关注[“"\']([^”"\']+)[”"\']',doc.text or '')
    if declared:return declared.group(1)
    return (doc.text or '').split('\n',1)[0].split('。',1)[0]


def relevant_docs(query, docs):
    """Keep substantive subject matches before the legacy entity/coverage gates."""
    docs=list(docs)
    comparison=bool(re.search(r'对比|比较|区别|差异|分别',query))
    requested=primary_topics(query,comparison=comparison)
    if requested:
        kept=[]
        for doc in docs:
            if doc.extra.get('needs_clarification'):
                kept.append(doc);continue
            own=primary_topics(subject_text(doc))
            if any(topic in own for topic in requested):kept.append(doc)
        docs=kept
        if comparison:
            covered={t for d in docs for t in primary_topics(subject_text(d))}
            if any(t not in covered for t in requested):return []
    # Fix ASCII word boundaries for VSWR adjacent to Chinese, without changing
    # the stored text or weakening protocol/band/port evidence requirements.
    guarded=re.sub(ascii_term('VSWR'), '驻波比', query, flags=re.I)
    return legacy_relevant_docs(guarded,docs)
