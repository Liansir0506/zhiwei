"""Content-bound admission declarations for r2; no identity authentication or writes."""
from copy import deepcopy
from datetime import datetime

from qa_core.quality.review_contract import bind_pending, check_approval, review_digest

TARGET = dict(version='test-sop-records-r2', database='test_sop_records_r2', mysql_port=13306,
    milvus_uri='http://127.0.0.1:29530', doc_collection='test_sop_records_r2_doc',
    faq_collection='test_sop_records_r2_faq', status='STAGED', generation_enabled=False,
    agent_enabled=False, auth_required=True, allow_header_roles=False, semantic_cache_enabled=False)
SCOPE = 'twenty_draft_r2_record_guides_new_isolated_staged_only'
MESSAGE = '批准20份draft-r2修订指南，仅限新隔离本地STAGED'
FLAGS = ('publication_authorized', 'outbound_authorized', 'independent_quality_approved', 'operational_sop_approved')
ACCESS = dict(tenant_id='default', roles=['ops', 'admin'], visibility='internal', security_level='internal')


def make_plan(package, previous_plan, source_seal, previous_seal):
    if package.get('protocol') != 'sop-record-revision-r2' or len(package['entries']) != 20:
        raise ValueError('exact r2 revision package required')
    previous = {e['filename']: e for e in previous_plan['entries']}
    if len(previous) != 20 or {e['filename'] for e in package['entries']} != set(previous):
        raise ValueError('revision roster differs from previous reviewed sources')
    entries = []
    for e in package['entries']:
        old = previous[e['filename']]
        expected_identity = deepcopy(old['source_identity']); expected_identity['source_version'] = 'draft-r2'
        if (e['source_identity_proposal'] != expected_identity or e['access_proposal'] != ACCESS or
                e['previous_body_sha256'] != old['body_sha256'] or e['id'] != old['id'] or
                e['title'] != old['title'] or e['review_status'] != 'pending' or e['license_status'] != 'pending' or
                any(e.get(k) is not False for k in ('identity_registered', 'operational_ready',
                    'root_cause_confirmed', 'action_authorized', 'full_sop_approved'))):
            raise ValueError('exact pending revision identity and scope required')
        metadata = deepcopy(old['metadata_pending'])
        metadata.update(**expected_identity, generator_version='sop-record-revision-r2')
        entries.append(bind_pending(dict(id=e['id'], filename=e['filename'], title=e['title'],
            body_sha256=e['body_sha256'], previous_body_sha256=e['previous_body_sha256'],
            source_identity=expected_identity, access_proposal=deepcopy(ACCESS), metadata_pending=metadata,
            extensions=deepcopy(e['extensions']), original_parent_bindings=deepcopy(old['original_parent_bindings']),
            source_revision='draft-r2', original_paragraphs_preserved=True, full_procedure_supported=False,
            manufacturer_document=None)))
    if sum(len(e['extensions']) for e in entries) != 49:
        raise ValueError('exact forty-nine record extensions required')
    plan = dict(protocol='sop-record-admission-plan-v1', source_review_seal_sha256=source_seal,
        previous_review_seal_sha256=previous_seal, target=deepcopy(TARGET), scope=SCOPE, decision='pending',
        entries=entries, publication_authorized=False, outbound_authorized=False,
        independent_quality_approved=False, operational_sop_approved=False,
        replaces_previous_version=False, original_and_readonly_stores_untouched=True)
    validate_plan(plan)
    return plan


def validate_plan(plan):
    if (plan['protocol'] != 'sop-record-admission-plan-v1' or plan['target'] != TARGET or
            plan['scope'] != SCOPE or plan['decision'] != 'pending' or
            plan.get('replaces_previous_version') is not False or
            plan.get('original_and_readonly_stores_untouched') is not True or
            any(plan.get(k) is not False for k in FLAGS)):
        raise ValueError('new isolated guide scope cannot widen')
    entries = plan['entries']
    if len(entries) != 20 or len({e['id'] for e in entries}) != 20 or len({e['filename'] for e in entries}) != 20:
        raise ValueError('twenty distinct sources required')
    if len({e['source_identity']['asset_id'] for e in entries}) != 20:
        raise ValueError('twenty distinct stable asset identities required')
    counts = {2: 0, 4: 0, 5: 0, 7: 0}
    for e in entries:
        check_approval(e)
        m = e['metadata_pending']
        if (e['approval']['status'] != 'pending' or e['access_proposal'] != ACCESS or
                e['source_revision'] != 'draft-r2' or e['source_identity']['source_version'] != 'draft-r2' or
                e['full_procedure_supported'] is not False or e['original_paragraphs_preserved'] is not True or
                e['manufacturer_document'] is not None or
                any(m.get(k) != v for k, v in dict(tenant_id='default', dept_id='', visibility='role_restricted',
                    allowed_roles=['ops','admin'], role_scope=['ops','admin'], security_level='internal',
                    status='reviewing', doc_type='guide', review_status='pending', license_status='pending',
                    quality='local_demo', synthetic=False, generator_version='sop-record-revision-r2').items()) or
                any(m.get(k) != v for k, v in e['source_identity'].items())):
            raise ValueError('identity, permission, provenance or guide type changed')
        for x in e['extensions']:
            if (x['old_step'] not in counts or x['value'] is not None or x['site_evidence_verified'] is not False or
                    x['action_authorized'] is not False or len(set(x['requested_fields'])) != 5):
                raise ValueError('record contract grants no field truth or action')
            counts[x['old_step']] += 1
    if counts != {2:11, 4:9, 5:10, 7:19}:
        raise ValueError('record extension scope changed')
    return True


def receipt_template(plan):
    validate_plan(plan)
    return dict(protocol='sop-record-admission-receipt-v1', decision='pending',
        reviewer_kind='human_user_chat_confirmation', user_message=None, recorded_at_utc=None,
        plan_sha256=review_digest(plan), source_review_seal_sha256=plan['source_review_seal_sha256'],
        previous_review_seal_sha256=plan['previous_review_seal_sha256'], target=deepcopy(TARGET), scope=SCOPE,
        exact_entry_bindings=[dict(id=e['id'], binding_sha256=e['approval']['binding_sha256']) for e in plan['entries']],
        publication_authorized=False, outbound_authorized=False, independent_quality_approved=False,
        operational_sop_approved=False)


def validate_receipt(plan, receipt):
    expected = receipt_template(plan)
    expected.update(decision='approved', user_message=MESSAGE, recorded_at_utc=receipt.get('recorded_at_utc'))
    if receipt != expected:
        raise ValueError('specific r2 content-bound chat decision required; old receipt or next-step is insufficient')
    try:
        stamp = datetime.fromisoformat(receipt['recorded_at_utc'])
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('actual UTC decision timestamp required') from None
    return True


def admitted_material(plan, receipt):
    validate_receipt(plan, receipt)
    metadata, mapping = {}, []
    for e in plan['entries']:
        m = deepcopy(e['metadata_pending'])
        m.update(status='active', review_status='human_chat_approved', license_status='project_authored_local_demo')
        metadata[e['filename']] = m
        mapping.append(dict(filename=e['filename'], sha256=e['body_sha256'], tenant_id='default',
            source_system=m['source_system'], source_id=m['source_id'], approval=dict(status='approved',
                proposer='codex_authored_record_revisions', reviewer='human_user_chat_confirmation',
                evidence='Exact r2 admission receipt binding='+review_digest(receipt))))
    return metadata, dict(protocol='reviewed-source-map-v1', entries=mapping)
