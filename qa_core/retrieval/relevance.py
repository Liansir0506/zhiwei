"""Conservative subject checks supplement ranking, never replace access filters."""
import re

# Aliases describe the same requested subject; shared verbs such as '处理' do not.
SUBJECTS = (
    (r'乒乓(?:切换)?|ping[- ]pong', r'乒乓|ping[- ]pong'),
    (r'掉电|断电|下电', r'掉电|断电|下电|电源故障|供电中断'),
    (r'驻波比|\bVSWR\b', r'驻波比|\bVSWR\b'),
    (r'失锁', r'失锁'),
    (r'光功率', r'光功率'),
    (r'发射功率', r'发射功率'),
    (r'传输中断', r'传输中断|传输链路中断'),
    (r'NG\s*Setup', r'NG\s*Setup|NG建立|NG接口建立'),
    *((rf'(?<![A-Za-z0-9]){term}(?![A-Za-z0-9])',
       rf'(?<![A-Za-z0-9]){term}(?![A-Za-z0-9])')
      for term in ('AMF','SMF','UPF','NRF','UDM','AUSF','PCF','BBU','GPS','SPN','N2','N4',
                   'PFCP','SCTP','FR1','FR2','CA')),
)


def relevant_docs(query, docs):
    anchors = [evidence for requested, evidence in SUBJECTS if re.search(requested, query, re.I)]
    # Explicit bands, power classes and numeric ports must occur in evidence;
    # a shared network acronym alone cannot support a different interface.
    entities = re.findall(r'(?<![A-Za-z0-9])(?:n\d+|[Pp][Cc]\d+)(?![A-Za-z0-9])', query)
    if re.search(r'端口|PFCP|SCTP', query, re.I):
        entities.extend(re.findall(r'(?<![A-Za-z0-9])\d{4,5}(?![A-Za-z0-9])', query))
    for term in entities:
        alias = (rf'(?:PC\s*{term[2:]}|Power\s+Class\s+{term[2:]})'
                 if term.upper().startswith('PC') else re.escape(term))
        anchors.append(r'(?<![A-Za-z0-9])' + alias + r'(?![A-Za-z0-9])')
    if not anchors:
        return list(docs)
    result = []
    comparison = bool(re.search(r'对比|比较|区别|差异|和|与', query))
    for doc in docs:
        if doc.extra.get('needs_clarification'):
            result.append(doc)
            continue
        text = '\n'.join((doc.text or '', doc.question or '', doc.answer or ''))
        matches = [bool(re.search(anchor, text, re.I)) for anchor in anchors]
        if (any(matches) if comparison else all(matches)):
            result.append(doc)
    if comparison:
        combined = '\n'.join('\n'.join((doc.text or '', doc.question or '', doc.answer or '')) for doc in result)
        if not all(re.search(anchor, combined, re.I) for anchor in anchors):
            return []
    return result
