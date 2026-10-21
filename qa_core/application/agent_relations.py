"""Derive retrieval targets exclusively from the freshly authorized context."""
import re
from qa_core.retrieval.constraints import EvidenceConstraint


class RelationSelectionError(Exception):
    def __init__(self, reason, status='NO_EVIDENCE', question=''):
        self.reason, self.status, self.question = reason, status, question


def select_targets(context, version, query, fingerprint):
    if context.get('truncated') or context.get('_relations_truncated'):
        raise RelationSelectionError('relationship_scope_incomplete')
    assets = context.get('knowledge', [])
    if len(assets) > 20:
        raise RelationSelectionError('relationship_scope_incomplete')
    approved = {}
    for row in assets:
        if row.get('kb_version') != version:
            continue
        if not row.get('asset_id') or not row.get('source_version') or not re.fullmatch(r'[a-f0-9]{64}', row.get('checksum') or ''):
            raise RelationSelectionError('relationship_scope_incomplete')
        approved[(row['asset_id'], row['source_version'])] = row
    edges = context.get('_relations', [])
    identities = {row['ticket_id'] for row in context.get('work_orders', [])}
    pairs = {tuple(edge.get(k, '') for k in ('asset_id', 'source_version')) for edge in edges}
    pairs &= set(approved)
    checksums = tuple((*pair, approved[pair]['checksum']) for pair in pairs)
    exact = bool(re.search(r'(?:工单.*(?:原文|原始记录)|(?:原文|原始记录).*工单)', query))
    if not exact:
        if not pairs:
            raise RelationSelectionError('related_evidence_unavailable')
        return EvidenceConstraint(tuple(pairs), revision=fingerprint, checksums=checksums)
    linked = {(e['asset_id'], e['source_version'], e['relation_id']) for e in edges
              if e.get('relation_type') == 'work_order' and e.get('relation_id') in identities
              and (e.get('asset_id'), e.get('source_version')) in pairs}
    if not linked:
        raise RelationSelectionError('related_evidence_unavailable')
    # Explicit identities cannot be replaced by the sole authorized work order.
    selector = query.split('工单', 1)[1]
    selector = re.split(r'原文|原始记录', selector, maxsplit=1)[0].strip()
    selector = re.sub(r'^(?:编号|ID|号)\s*[:：]?\s*', '', selector, flags=re.I)
    requested = re.findall(r'[A-Za-z0-9_.:-]+', selector)
    if not requested and selector.strip('的 ：:，、。'):
        raise RelationSelectionError('ambiguous_work_order', 'NEEDS_CLARIFICATION', '请使用明确的关联工单ID，或直接核对关联工单原文。')
    if len(set(requested)) > 1:
        raise RelationSelectionError('ambiguous_work_order', 'NEEDS_CLARIFICATION', '请指定一个关联工单ID以核对完整原文。')
    authorized = {row[2] for row in linked}
    if requested:
        if requested[0] not in authorized:
            raise RelationSelectionError('linked_record_unavailable')
        selected = requested[0]
    elif len(authorized) != 1:
        raise RelationSelectionError('ambiguous_work_order', 'NEEDS_CLARIFICATION', '存在多个关联工单，请指定一个工单ID。')
    else:
        selected = next(iter(authorized))
    triples = tuple(row for row in linked if row[2] == selected)
    return EvidenceConstraint(tuple(pairs), triples=triples, revision=fingerprint, checksums=checksums)


def explanation(constraint):
    return {'strategy': 'linked_work_order' if constraint.exact else 'related_assets',
            'asset_count': len({row[:2] for row in constraint.triples}) if constraint.exact else len(constraint.pairs),
            'ticket_id': constraint.triples[0][2] if constraint.exact else None,
            'description': ('已核对授权关联工单的完整原文；未确认现场根因。' if constraint.exact else
                            '检索限定于授权关联文件及修订；文件关联不表示命中特定工单记录。')}
