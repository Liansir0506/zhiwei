"""Offline commitments for new questions against a known, admitted corpus.

Shared corpus is intentional. This is not source-independent quality and cannot
authenticate custody, review gold, freeze a set, or authorize an evaluation.
"""
from collections import defaultdict
from datetime import datetime
import re

from qa_core.quality.independent_readiness import CASE_FIELDS, HEX, digest

POLICY = 'known_corpus_question_template_holdout'
SCOPE = 'default租户ops只读；现有已准入语料的新问题留出；未授权冻结、运行或新增模型外发'
ROLES = {'proposer_alias': '命题保管人 A', 'reviewer_alias': '真值审核人 B',
         'alias_kind': 'role_placeholder_not_authenticated_identity',
         'receipt_sha256': None, 'reviewed_at_utc': None, 'status': 'pending'}


def template(version, binding):
    return dict(protocol='known-corpus-question-intake-v1', policy=POLICY,
                target_kb_version=version, baseline_binding_sha256=binding,
                cases=[], custody=dict(ROLES), scope=SCOPE)


def roster(registry):
    """Only exact current asset identities; historical/pending aliases cannot qualify."""
    rows = []
    for s in registry['source_index']:
        if not s.get('is_current'):
            continue
        rows.append({k: s[k] for k in ('asset_id', 'source_id', 'filename', 'source_version',
                                     'doc_type', 'file_sha256', 'metadata_sha256',
                                     'canonical_body_sha256')})
    if any(not isinstance(r['asset_id'], str) or not r['asset_id'] for r in rows):
        raise ValueError('exact current asset identity required')
    if len({r['asset_id'] for r in rows}) != len(rows):
        raise ValueError('ambiguous current asset identity')
    return sorted(rows, key=lambda r: r['asset_id'])


def _custody(value):
    if not isinstance(value, dict) or set(value) != set(ROLES):
        raise ValueError('custody declarations required; self-approval is unsupported')
    if value['alias_kind'] != ROLES['alias_kind'] or value['status'] not in {'pending', 'submitted'}:
        raise ValueError('role aliases cannot authenticate or approve review')
    names = [value[k] for k in ('proposer_alias', 'reviewer_alias')]
    if any(not isinstance(n, str) or not n.strip() or len(n) > 100 for n in names) or names[0].strip().casefold() == names[1].strip().casefold():
        raise ValueError('two distinct role aliases required')
    receipt, date = value['receipt_sha256'], value['reviewed_at_utc']
    if receipt is not None and (not isinstance(receipt, str) or not HEX.fullmatch(receipt)):
        raise ValueError('invalid custody receipt commitment')
    if date is not None:
        try:
            parsed = datetime.fromisoformat(date.replace('Z', '+00:00'))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError('invalid custody review timestamp') from exc
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError('UTC custody timestamp required')
    if value['status'] == 'submitted' and (receipt is None or date is None):
        raise ValueError('submitted custody requires receipt and UTC timestamp')


def screen(payload, registry, sources, version, binding):
    """Reject known question/template exposure; source/topic reuse is declared scope.

    source_ids contains exact asset_id values from this offline roster, rather
    than non-unique source-system aliases. Live ACL/withdrawal is still unverified.
    """
    expected = template(version, binding)
    if not isinstance(payload, dict) or set(payload) != set(expected):
        raise ValueError('hash-only manifest schema required')
    for field in ('protocol', 'policy', 'target_kb_version', 'baseline_binding_sha256', 'scope'):
        if payload[field] != expected[field]:
            raise ValueError('scope, policy or baseline changed')
    _custody(payload['custody'])
    cases = payload['cases']
    if not isinstance(cases, list) or len(cases) > 500:
        raise ValueError('bounded case roster required')
    # Caller must use the exact sealed current roster, not its own pending list.
    if sources != roster(registry):
        raise ValueError('current corpus roster changed')
    by_asset = {s['asset_id']: s for s in sources}
    known_raw = set(registry['raw_query_hashes'])
    known_canonical = set(registry['canonical_query_hashes'])
    known_templates = set(registry['blocking_link_index']['template_groups'])
    reasons, owners = {}, defaultdict(list)
    parent = list(range(len(cases)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, c in enumerate(cases):
        if not isinstance(c, dict) or set(c) != CASE_FIELDS:
            raise ValueError('hash commitments only; no question, gold or approval fields')
        if not isinstance(c['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', c['id']) or c['id'] in reasons:
            raise ValueError('unique bounded case ID required')
        for field in ('query_sha256', 'canonical_query_sha256', 'truth_sha256', 'evidence_manifest_sha256'):
            if not isinstance(c[field], str) or not HEX.fullmatch(c[field]):
                raise ValueError('exact hash commitments required')
        for field in ('source_ids', 'body_families', 'topic_groups', 'template_groups'):
            v = c[field]
            if not isinstance(v, list) or not v or len(v) > 100 or any(not isinstance(x, str) or not x.strip() or x != x.strip() or len(x) > 300 for x in v) or len(set(v)) != len(v):
                raise ValueError('complete unique source/body/topic/template keys required')
        if type(c['development_exposed']) is not bool or c['expected_kind'] not in {'answer', 'clarify', 'refuse'} or c['request_context'] != {'tenant_id': 'default', 'roles': ['ops']}:
            raise ValueError('unsupported exposure, outcome or role scope')
        why = []
        if c['development_exposed']:
            why.append('declared_development_exposed')
        if c['query_sha256'] in known_raw or c['canonical_query_sha256'] in known_canonical:
            why.append('known_exposed_question_hash')
        if set(c['template_groups']) & known_templates:
            why.append('known_exposed_question_template')
        if any(a not in by_asset for a in c['source_ids']):
            why.append('source_not_in_exact_current_corpus')
        else:
            bodies = {by_asset[a]['canonical_body_sha256'] for a in c['source_ids']}
            if set(c['body_families']) != bodies:
                why.append('source_body_binding_mismatch')
        reasons[c['id']] = why
        # Sharing a source, body or topic is permitted, so it must not propagate
        # exposure. Canonical questions and question-template groups do propagate.
        for key in [('question', c['canonical_query_sha256'])] + [('template', t) for t in c['template_groups']]:
            for prior in owners[key]:
                parent[find(i)] = find(prior)
            owners[key].append(i)
    for indices in owners.values():
        if len(indices) > 1 and len({cases[i]['canonical_query_sha256'] for i in indices}) == 1:
            for i in indices:
                if 'duplicate_question_commitment' not in reasons[cases[i]['id']]:
                    reasons[cases[i]['id']].append('duplicate_question_commitment')
    groups = defaultdict(list)
    for i in range(len(cases)):
        groups[find(i)].append(cases[i]['id'])
    for members in groups.values():
        if any(reasons[ident] for ident in members):
            for ident in members:
                if not reasons[ident]:
                    reasons[ident].append('connected_to_blocked_question_template_group')
    blocked = [dict(id=ident, reasons=why) for ident, why in reasons.items() if why]
    shared = dict(source_body_reuse_permitted=True, topic_reuse_permitted=True,
                  source_independent=False, source_body_topic_groups_do_not_propagate_question_exposure=True)
    return dict(protocol='known-corpus-question-screen-v1', policy=POLICY,
                case_count=len(cases), question_template_groups=len(groups), blocked_cases=blocked,
                static_screen_passed=bool(cases) and not blocked,
                state='blocked' if not cases or blocked else 'needs_human_custody_truth_and_semantic_review',
                blockers=(['no_new_human_cases'] if not cases else []) + (['exposed_or_invalid_question_groups'] if blocked else []) +
                         ['human_custody_not_verified', 'human_truth_not_verified', 'semantic_question_template_overlap_not_verified',
                          'exposure_coverage_incomplete', 'live_acl_withdrawal_and_run_binding_not_verified', 'freeze_not_authorized'],
                scope_declaration=shared, verified_question_holdout_frozen=0,
                verified_source_independent_frozen=0, quality_metrics=None,
                metrics_eligible=False, run_authorized=False,
                declaration_sha256=digest(payload))
