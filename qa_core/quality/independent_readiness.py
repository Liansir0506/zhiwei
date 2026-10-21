"""Conservative, hash-only exposure inventory and offline intake screening.

This module cannot authenticate a custodian, approve truth, freeze questions or
run evaluation. Passing a hash/link screen never establishes semantic independence.
"""
from collections import defaultdict
import hashlib
import json
import re
from scripts.audit_holdout_readiness import canonical_query

LINKS=('source_ids','body_families','topic_groups','template_groups')
QUERY_FIELDS=('query','question','original_question','counter')


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def digest(value):
    return sha(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False))


def extract_records(payload, input_id, input_sha):
    """Read known public developer material; store hashes and pointers, not prose.

    Every found query is considered exposed even if the old JSON calls it frozen
    or claims development_exposed=false. Missing group declarations stay visible.
    """
    records=[]
    def walk(value,pointer):
        if isinstance(value,dict):
            for field in QUERY_FIELDS:
                query=value.get(field)
                if not isinstance(query,str) or not canonical_query(query):continue
                groups={k:sorted(set(v for v in value.get(k,[]) if isinstance(v,str) and v))
                        if isinstance(value.get(k),list) else [] for k in LINKS}
                # Exact source references are only conservative blocking keys.
                for key in ('source','expect_hit','filename','source_id','source_id_proposal'):
                    if isinstance(value.get(key),str) and value[key]:groups['source_ids'].append(value[key])
                for key in ('source_body_sha256','body_sha256','card_sha256'):
                    if isinstance(value.get(key),str) and value[key]:groups['body_families'].append(value[key])
                groups={k:sorted(set(v)) for k,v in groups.items()}
                location=pointer+'/'+field
                records.append(dict(id=digest([input_id,location])[:32],input=input_id,input_sha256=input_sha,
                    json_pointer=location,legacy_case_id=str(value.get('id') or value.get('case_id') or value.get('faq_id') or ''),
                    query_sha256=sha(query),canonical_query_sha256=sha(canonical_query(query)),
                    declared_split=value.get('split'),declared_exposure=value.get('development_exposed'),
                    disposition='development_exposed_or_developer_accessible_not_independent',
                    group_links=groups,missing_declared_links=[k for k in LINKS if not value.get(k)],
                    developer_exposed=True))
            for key,child in value.items():
                escaped=str(key).replace('~','~0').replace('/','~1')
                if isinstance(child,(dict,list)):walk(child,pointer+'/'+escaped)
        elif isinstance(value,list):
            for index,child in enumerate(value):walk(child,pointer+'/'+str(index))
    walk(payload,'')
    return records


def connected_groups(records):
    parent=list(range(len(records)));owners={}
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,record in enumerate(records):
        keys=[('canonical_query_sha256',record['canonical_query_sha256'])]
        keys.extend((field,value) for field in LINKS for value in record['group_links'][field])
        for key in keys:
            if key in owners:parent[find(i)]=find(owners[key])
            else:owners[key]=i
    groups=defaultdict(list)
    for i,r in enumerate(records):groups[find(i)].append(r)
    return [dict(id=digest(sorted(r['id'] for r in members))[:32],
        record_ids=sorted(r['id'] for r in members),record_count=len(members),
        developer_exposed=any(r['developer_exposed'] for r in members),
        declared_splits=sorted({str(r['declared_split']) for r in members}),
        may_split_into_frozen=False) for members in groups.values()]


def inventory(records, inputs, source_index, *, coverage_notes):
    if len({r['id'] for r in records})!=len(records):raise ValueError('duplicate exposure occurrence ID')
    aliases=defaultdict(list)
    for source in source_index:
        for alias in source['blocking_aliases']:aliases[alias].append(source)
    for r in records:
        for ref in list(r['group_links']['source_ids']):
            if ref in aliases:
                for source in aliases[ref]:
                    r['group_links']['source_ids'].extend(source['blocking_aliases'])
                    r['group_links']['body_families'].append(source['canonical_body_sha256'])
        r['group_links']={k:sorted(set(v)) for k,v in r['group_links'].items()}
    groups=connected_groups(records)
    duplicate=defaultdict(list)
    for r in records:duplicate[r['canonical_query_sha256']].append(r['id'])
    links={k:sorted({v for r in records for v in r['group_links'][k]}) for k in LINKS}
    links['source_ids']=sorted(set(links['source_ids'])|{v for s in source_index for v in s['blocking_aliases']})
    links['body_families']=sorted(set(links['body_families'])|{s['canonical_body_sha256'] for s in source_index})
    return dict(protocol='independent-exposure-inventory-v1',inputs=inputs,records=records,groups=groups,
        source_index=source_index,blocking_link_index=links,
        canonical_query_hashes=sorted(duplicate),raw_query_hashes=sorted({r['query_sha256'] for r in records}),
        summary=dict(input_files=len(inputs),query_occurrences=len(records),unique_canonical_queries=len(duplicate),
            connected_groups=len(groups),exact_duplicate_groups=sum(len(v)>1 for v in duplicate.values()),
            legacy_declared_frozen_occurrences=sum(r['declared_split']=='frozen' for r in records),
            incomplete_declared_link_occurrences=sum(bool(r['missing_declared_links']) for r in records),
            current_sources=sum(s.get('is_current',False) for s in source_index),
            current_body_families=len({s['canonical_body_sha256'] for s in source_index if s.get('is_current',False)}),
            public_source_occurrences=len(source_index),
            verified_independent_frozen=0),
        duplicate_examples=[dict(canonical_query_sha256=k,record_ids=v[:12],occurrences=len(v))
            for k,v in sorted(duplicate.items()) if len(v)>1][:20],
        coverage_notes=coverage_notes,exposure_coverage_complete=False,semantic_independence_verified=False)


def intake_template(version,binding):
    return dict(protocol='independent-intake-manifest-v1',target_kb_version=version,
        baseline_binding_sha256=binding,policy='source_body_topic_template_group_holdout',cases=[],
        custody=dict(proposer_id=None,reviewer_id=None,reviewer_kind='human',
                     receipt_sha256=None,reviewed_at_utc=None,status='pending'),
        scope='default租户ops只读；未获准模型调用、发布、资料准入或独立冻结')


CASE_FIELDS={'id','query_sha256','canonical_query_sha256','truth_sha256','evidence_manifest_sha256',
             *LINKS,'development_exposed','expected_kind','request_context'}
HEX=re.compile(r'[a-f0-9]{64}')


def screen_intake(payload,registry,version,binding):
    """Only screen opaque commitments. Editable approval flags cannot authorize anything."""
    template=intake_template(version,binding)
    if not isinstance(payload,dict) or set(payload)!=set(template) or payload['protocol']!=template['protocol'] or payload['policy']!=template['policy']:
        raise ValueError('unsupported intake schema or policy; approval flags are not accepted')
    if payload['target_kb_version']!=version or payload['baseline_binding_sha256']!=binding:
        raise ValueError('current version/baseline binding changed')
    if payload['scope']!=template['scope'] or not isinstance(payload['custody'],dict) or set(payload['custody'])!=set(template['custody']):
        raise ValueError('custody or access scope schema changed')
    if payload['custody']['reviewer_kind']!='human' or payload['custody']['status'] not in {'pending','submitted'}:
        raise ValueError('offline custody declaration cannot self-approve')
    cases=payload['cases']
    if not isinstance(cases,list) or len(cases)>500:raise ValueError('invalid bounded case roster')
    ids=[];provisional=[];direct={}
    for c in cases:
        if not isinstance(c,dict) or set(c)!=CASE_FIELDS:raise ValueError('case must contain hash commitments only')
        if not isinstance(c['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',c['id']):raise ValueError('invalid case ID')
        ids.append(c['id'])
        for k in ('query_sha256','canonical_query_sha256','truth_sha256','evidence_manifest_sha256'):
            if not isinstance(c[k],str) or not HEX.fullmatch(c[k]):raise ValueError('missing exact hash commitments')
        for field in LINKS:
            values=c[field]
            if (not isinstance(values,list) or not values or len(values)>100 or
                any(not isinstance(v,str) or not v.strip() or len(v)>300 for v in values)):
                raise ValueError('all source/body/topic/template links must be declared')
        if type(c['development_exposed']) is not bool:raise ValueError('explicit exposure declaration required')
        if c['expected_kind'] not in {'answer','clarify','refuse'} or c['request_context']!={'tenant_id':'default','roles':['ops']}:
            raise ValueError('unsupported outcome or role scope')
        reasons=[]
        if c['development_exposed']:reasons.append('declared_development_exposed')
        if c['query_sha256'] in registry['raw_query_hashes'] or c['canonical_query_sha256'] in registry['canonical_query_hashes']:
            reasons.append('known_exposed_question_hash')
        for field in LINKS:
            if set(c[field])&set(registry['blocking_link_index'][field]):reasons.append('known_development_'+field)
        direct[c['id']]=reasons
        provisional.append(dict(id=c['id'],canonical_query_sha256=c['canonical_query_sha256'],
            group_links={k:c[k] for k in LINKS},developer_exposed=bool(reasons),declared_split='frozen_proposed'))
    if len(set(ids))!=len(ids):raise ValueError('duplicate case ID')
    for group in connected_groups(provisional):
        if group['developer_exposed']:
            for ident in group['record_ids']:
                if not direct[ident]:direct[ident].append('connected_to_exposed_group')
    blocked=[dict(id=ident,reasons=reasons) for ident,reasons in direct.items() if reasons]
    return dict(protocol='independent-intake-screen-v1',case_count=len(cases),blocked_cases=blocked,
        static_link_screen_passed=bool(cases) and not blocked,
        state='blocked' if not cases or blocked else 'needs_verified_custody_and_semantic_review',
        blockers=(['no_new_human_cases'] if not cases else [])+(['exposed_connected_groups'] if blocked else [])+
            ['authenticated_independent_custody_not_verified','human_truth_and_scope_not_verified',
             'semantic_overlap_not_verified','complete_exposure_inventory_not_established','freeze_not_authorized'],
        verified_independent_frozen=0,metrics_eligible=False,run_authorized=False,
        note='Hash/link screening does not authenticate reviewers or grant freezing/evaluation permission')
