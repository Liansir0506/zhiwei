"""Exact, scoped admission declarations. No DB, publication, or reviewer authentication."""
from copy import deepcopy
from datetime import datetime

from qa_core.quality.review_contract import bind_pending,check_approval,review_digest

TARGET=dict(version='test-sop-evidence-r1',database='test_sop_evidence_r1',mysql_port=13306,
    milvus_uri='http://127.0.0.1:29530',doc_collection='test_sop_evidence_r1_doc',
    faq_collection='test_sop_evidence_r1_faq',status='STAGED',generation_enabled=False,
    agent_enabled=False,auth_required=True,allow_header_roles=False,semantic_cache_enabled=False)
MESSAGE='批准20份指南，仅限隔离本地演示STAGED'
SCOPE='twenty_sop_evidence_guides_new_isolated_staged_only'


def make_plan(package,source_seal):
    if package.get('review_status')!='pending' or len(package.get('entries',[]))!=20:
        raise ValueError('exact twenty pending guides required')
    entries=[]
    for e in package['entries']:
        if e['review_status']!='pending' or e['identity_registered'] or e['outward_authorized'] or e['full_procedure_supported']:
            raise ValueError('draft scope changed')
        identity=deepcopy(e['source_identity_proposal'])
        access=dict(tenant_id='default',roles=['ops','admin'],visibility='internal',security_level='internal')
        if e['access_proposal']!=access or e['doc_type_proposal']!='guide':raise ValueError('exact draft scope required')
        metadata=dict(tenant_id='default',dept_id='',visibility='role_restricted',allowed_roles=['ops','admin'],
            role_scope=['ops','admin'],security_level='internal',**identity,status='reviewing',
            effective_from=None,effective_to=None,doc_type='guide',equipment_type=e['equipment'],device_type=e['equipment'],
            network_tech=e['network_tech'],alarm_code=e['alarm'],dataset_id='sop-evidence-local-demo',
            origin_type='project_authored_evidence_guide',synthetic=False,generator_version='sop-evidence-draft-r1',
            generation_seed='',review_status='pending',license_status='pending',quality='local_demo',evidence_refs=[])
        entries.append(bind_pending(dict(id=e['id'],filename=e['filename'],body_sha256=e['body_sha256'],
            source_identity=identity,access_proposal=access,metadata_pending=metadata,title=e['title'],
            original_parent_bindings=deepcopy(e['parent_members']),source_revision=e['source_identity_proposal']['source_version'],
            source_body_unchanged=True,full_procedure_supported=False,manufacturer_document=None)))
    if len({e['id'] for e in entries})!=20 or len({e['source_identity']['asset_id'] for e in entries})!=20:
        raise ValueError('source identities not unique')
    return dict(protocol='sop-evidence-admission-plan-v1',source_review_seal_sha256=source_seal,
        target=deepcopy(TARGET),entries=entries,scope=SCOPE,decision='pending',
        publication_authorized=False,outbound_authorized=False,independent_quality_approved=False,
        operational_sop_approved=False,original_and_readonly_stores_untouched=True)


def receipt_template(plan):
    return dict(protocol='sop-evidence-admission-receipt-v1',decision='pending',reviewer_kind='human_user_chat_confirmation',
        user_message=None,recorded_at_utc=None,plan_sha256=review_digest(plan),
        source_review_seal_sha256=plan['source_review_seal_sha256'],target=deepcopy(TARGET),scope=SCOPE,
        exact_entry_bindings=[dict(id=e['id'],binding_sha256=e['approval']['binding_sha256']) for e in plan['entries']],
        publication_authorized=False,outbound_authorized=False,independent_quality_approved=False,operational_sop_approved=False)


def validate_receipt(plan,receipt):
    if plan['target']!=TARGET or plan['scope']!=SCOPE or plan['decision']!='pending':raise ValueError('prepared target/scope changed')
    for flag in ('publication_authorized','outbound_authorized','independent_quality_approved','operational_sop_approved'):
        if plan.get(flag) is not False:raise ValueError('admission cannot widen operational or outbound scope')
    if len(plan['entries'])!=20:raise ValueError('exact twenty-item scope required')
    for e in plan['entries']:
        check_approval(e)
        m=e['metadata_pending']
        if (m['tenant_id']!='default' or m['role_scope']!=['ops','admin'] or m['allowed_roles']!=['ops','admin'] or
            m['visibility']!='role_restricted' or m['security_level']!='internal' or m['dept_id']!='' or
            m['doc_type']!='guide' or m['review_status']!='pending' or m['status']!='reviewing' or
            e['full_procedure_supported'] is not False or e['approval']['status']!='pending' or
            any(m[k]!=v for k,v in e['source_identity'].items())):
            raise ValueError('prepared identity, guide type or role scope changed')
    expected=receipt_template(plan)
    expected.update(decision='approved',user_message=MESSAGE,recorded_at_utc=receipt.get('recorded_at_utc'))
    if receipt!=expected:raise ValueError('specific content-bound chat admission required; next-step is not a receipt')
    try:
        stamp=datetime.fromisoformat(receipt['recorded_at_utc'])
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds()!=0:raise ValueError()
    except (ValueError,TypeError):raise ValueError('UTC decision timestamp required') from None
    return True


def admitted_material(plan,receipt):
    """Pure projection after exact receipt, not authentication or actual ingestion."""
    validate_receipt(plan,receipt);metadata={};mapping=[]
    for e in plan['entries']:
        m=deepcopy(e['metadata_pending']);m.update(status='active',review_status='human_chat_approved',
            license_status='project_authored_local_demo')
        metadata[e['filename']]=m
        mapping.append(dict(filename=e['filename'],sha256=e['body_sha256'],tenant_id='default',
            source_system=m['source_system'],source_id=m['source_id'],approval=dict(status='approved',
                proposer='codex_authored_evidence_guides',reviewer='human_user_chat_confirmation',
                evidence='Exact admission receipt binding='+review_digest(receipt))))
    return metadata,dict(protocol='reviewed-source-map-v1',entries=mapping)
