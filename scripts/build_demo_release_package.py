"""Build the demo-release candidate package: inherited content + simulated device
records + public-reference summaries. Never publishes or activates."""
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from scripts.prepare_legacy_projection import digest, read, seal, verify_seal

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'local-demo-release-r1'
DOC, FAQ = 'local_demo_release_r1_doc', 'local_demo_release_r1_faq'
PARENT = ROOT / 'data/candidates/content-quality-merged-r2'
R2_STAGE = ROOT / '.runtime/test/sop-records-stage-r2/candidate'
NEW_FILES = ROOT / '.test-tmp/demo-device'
CANDIDATE = ROOT / 'data/candidates/demo-release-r1'
RECEIPT = CANDIDATE / 'approval-receipt.json'

USER_MESSAGE = ('由于此项目为个人demo，未拥有真实数据，所以你来编造一些数据或从网上采用公开数据；'
                '并拟真数据校验、治理发布最后实现')
SCOPE = 'simulated_device_records_and_public_reference_summaries_isolated_local_staged_new_version_only'

DEVICE_TOPICS = {
    'manual_device-bbu-01.md': ('device-bbu-01', 'BBU', '5G'),
    'manual_device-clock-01.md': ('device-clock-01', '时钟同步', '5G'),
    'manual_device-gps-01.md': ('device-gps-01', 'GNSS授时', '5G'),
    'manual_device-handover-01.md': ('device-handover-01', '切换', '5G'),
    'manual_device-interference-01.md': ('device-interference-01', '干扰', '5G'),
    'manual_device-spn-01.md': ('device-spn-01', 'SPN传输', '5G'),
    'manual_device-transport-01.md': ('device-transport-01', '传输', '5G'),
    'manual_device-vswr-01.md': ('device-vswr-01', '天馈', '5G'),
}
PUBLIC_TOPICS = {
    'manual_public-ngap-causes.md': ('public-ngap-causes', '协议', '5G',
        ['https://www.etsi.org/deliver/etsi_ts/138400_138499/138413/']),
    'manual_public-vswr-rf.md': ('public-vswr-rf', '天馈', '5G',
        ['公开RF工程教材与公开天线/馈线厂家安装维护文档（可公开检索，不复制全文）']),
}
VENDORS = {'bbu': '演示厂家A', 'clock': '演示厂家A', 'gps': '演示厂家B', 'handover': '演示厂家B',
           'interference': '演示厂家B', 'spn': '演示厂家C', 'transport': '演示厂家C', 'vswr': '演示厂家D'}


def hashed(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def build():
    import shutil as _shutil
    if CANDIDATE.exists():
        _shutil.rmtree(CANDIDATE)
    verify_seal(PARENT)
    verify_seal(R2_STAGE)
    (CANDIDATE / 'raw').mkdir(parents=True)
    inherited = read(PARENT / 'raw/meta.json')
    for name in inherited:
        _shutil.copyfile(PARENT / 'raw' / name, CANDIDATE / 'raw' / name)
    meta = {name: dict(value) for name, value in inherited.items()}
    source_maps = {}
    parent_seal = digest(PARENT / 'artifact-hashes.json')
    for name, value in inherited.items():
        source_maps[name] = dict(filename=name, sha256=digest(CANDIDATE / 'raw' / name),
                                 tenant_id=value['tenant_id'], source_system=value['source_system'],
                                 source_id=value['source_id'],
                                 approval=dict(status='approved', proposer='inherited_admitted_source',
                                               reviewer='human_user_chat_confirmation',
                                               evidence='Inherited admitted merged package sha256=' + parent_seal))
    r2_meta = read(R2_STAGE / 'raw/meta.json')
    r2_map = {e['filename']: e for e in read(R2_STAGE / 'source-map.json')['entries']}
    r2_seal = digest(R2_STAGE / 'artifact-hashes.json')
    r2_receipt_sha = digest(R2_STAGE / 'approval-receipt.json')
    for name, value in r2_meta.items():
        _shutil.copyfile(R2_STAGE / 'raw' / name, CANDIDATE / 'raw' / name)
        meta[name] = dict(value)
        entry = r2_map[name]
        source_maps[name] = dict(filename=name, sha256=digest(CANDIDATE / 'raw' / name),
                                 tenant_id=value['tenant_id'], source_system=entry['source_system'],
                                 source_id=entry['source_id'],
                                 approval=dict(status='approved', proposer='inherited_admitted_source',
                                               reviewer='human_user_chat_confirmation',
                                               evidence='Inherited admitted r2 stage package sha256=' + r2_seal
                                                        + ' receipt=' + r2_receipt_sha))
    receipt = dict(decision='approved', reviewer_kind='human_user_chat_confirmation', user_message=USER_MESSAGE,
                   scope=SCOPE, recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                   publication_authorized=False, outbound_authorized=False, independent_quality_approved=False)
    (CANDIDATE / 'approval-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    receipt_sha = digest(RECEIPT)
    entries = []
    for name in sorted(NEW_FILES.glob('*.md')):
        shutil.copyfile(name, CANDIDATE / 'raw' / name.name)
        body_sha = digest(CANDIDATE / 'raw' / name.name)
        if name.name in DEVICE_TOPICS:
            source_id, equipment, tech = DEVICE_TOPICS[name.name]
            system, dataset = 'project_authored_device_record', 'device-evidence-demo'
            synthetic, origin = True, 'synthetic'
            license_status = 'project_authored_local_demo'
            evidence_refs = []
            vendor = next(v for k, v in VENDORS.items() if k in name.name)
        else:
            source_id, equipment, tech, evidence_refs = PUBLIC_TOPICS[name.name]
            system, dataset = 'public_reference_summary', 'public-reference-demo'
            synthetic, origin = False, 'public_reference_derived'
            license_status = 'public_summary_no_full_text'
            vendor = ''
        asset_id = 'kb-' + hashed([VERSION, source_id])[:32]
        meta[name.name] = dict(tenant_id='default', dept_id='public', visibility='role_restricted',
                               allowed_roles=['ops', 'admin'], role_scope=['ops', 'admin'],
                               security_level='internal', source_system=system, source_id=source_id,
                               source_version='demo-r1' if synthetic else '2026-10-15', asset_id=asset_id,
                               status='active', effective_from=None, effective_to=None, doc_type='manual',
                               network_tech=tech, equipment_type=equipment, vendor=vendor, dataset_id=dataset,
                               origin_type=origin, synthetic=synthetic,
                               generator_version='project_authored_demo-r1', generation_seed='',
                               review_status='human_chat_approved', license_status=license_status,
                               evidence_refs=evidence_refs)
        source_maps[name.name] = dict(filename=name.name, sha256=body_sha, tenant_id='default',
                                      source_system=system, source_id=source_id,
                                      approval=dict(status='approved', proposer='workbuddy_authored_drafts',
                                                    reviewer='human_user_chat_confirmation',
                                                    evidence='Explicit chat receipt sha256=' + receipt_sha))
        entries.append(dict(id=source_id, filename=name.name, body_sha256=body_sha, synthetic=synthetic,
                            source_identity_proposal=dict(source_system=system, source_id=source_id,
                                                          source_version=meta[name.name]['source_version']),
                            access_proposal=dict(tenant_id='default', roles=['ops', 'admin'],
                                                 visibility='internal', security_level='internal'),
                            approval=dict(status='approved', proposer='workbuddy_authored_drafts',
                                          reviewer='human_user_chat_confirmation',
                                          evidence='Explicit chat receipt sha256=' + receipt_sha)))
    (CANDIDATE / 'raw/meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    (CANDIDATE / 'source-map.json').write_text(json.dumps(
        dict(protocol='reviewed-source-map-v1', entries=list(source_maps.values())), ensure_ascii=False, indent=1), encoding='utf-8')
    (CANDIDATE / 'approved-bodies.json').write_text(json.dumps(dict(entries=entries), ensure_ascii=False, indent=1), encoding='utf-8')
    (CANDIDATE / 'projection.json').write_text(json.dumps(dict(
        version=VERSION, status='STAGED', collections=[DOC, FAQ], files=len(meta),
        parent_versions=['local-content-quality-r1', 'test-sop-records-r2'], new_admissions=len(entries),
        inherited_guides=20, old_versions_preserved=True, publication_authorized=False,
        outbound_authorized=False, manual_rebuild_policy='tokenizer-paragraph-v1',
        independent_quality_approved=False),
        ensure_ascii=False, indent=1), encoding='utf-8')
    from qa_core.ingest.source_identity import validate_mapping
    validate_mapping(CANDIDATE / 'raw', json.loads((CANDIDATE / 'source-map.json').read_text(encoding='utf-8')), meta)
    seal(CANDIDATE)
    verify_seal(CANDIDATE)
    print(json.dumps(dict(version=VERSION, files=len(meta), new_admissions=len(entries),
                          inherited=len(inherited), inherited_guides=len(r2_meta),
                          receipt_sha256=receipt_sha), ensure_ascii=False))


if __name__ == '__main__':
    build()
