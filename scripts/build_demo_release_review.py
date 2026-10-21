"""Build the sealed demo-release review package; never publishes or activates."""
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import demo_release_runtime as release
from scripts import stage_demo_release as stage
from scripts.prepare_legacy_projection import digest, read, seal, verify_seal, write

REVIEW = release.REVIEW
CANDIDATE = stage.CANDIDATE
CODE_BINDINGS = [
    'scripts/stage_demo_release.py',
    'scripts/build_demo_release_package.py',
    'scripts/demo_release_runtime.py',
    'scripts/demo_release_local_server.py',
    'scripts/execute_demo_release.py',
    'scripts/start_demo_release_background.ps1',
    'scripts/start_content_quality_background_v2.ps1',
    'scripts/original_local_runtime.py',
    'scripts/original_local_server.py',
    'scripts/init_collections.py',
    'scripts/prepare_legacy_projection.py',
    'scripts/migrate_legacy_collections.py',
    'qa_core/quality/gate.py',
    'qa_core/quality/publication.py',
    'qa_core/retrieval/supported_searcher.py',
    'qa_core/retrieval/supported_evidence_v2.py',
    'qa_core/application/qa_usecase.py',
    'qa_core/storage/version_manifest.py',
]


def build():
    if REVIEW.exists():
        raise ValueError('review exists; refuse to rewrite')
    REVIEW.mkdir(parents=True)
    receipt, bodies, meta = stage.validate()
    verify_seal(CANDIDATE)
    if not (stage.REPORT / 'artifact-hashes.json').exists():
        seal(stage.REPORT)
    verify_seal(stage.REPORT)
    new_files = sorted(name for name in meta if name.startswith(('manual_device-', 'manual_public-')))
    outbound = dict(
        protocol='demo-release-outbound-review-v1', version=release.VERSION,
        decision='project_authored_simulated_and_public_summary_only',
        summary=('新增10份资料：8份设备取证记录单为项目自编演示拟真数据（SIMULATED），'
                 '2份为公开规范/RF工程资料的中文自编摘要；均不复制第三方全文，不含真实客户数据。'),
        files=[dict(filename=name, synthetic=meta[name]['synthetic'],
                    origin_type=meta[name]['origin_type'], license_status=meta[name]['license_status'],
                    source_system=meta[name]['source_system'], source_id=meta[name]['source_id'],
                    body_sha256=digest(CANDIDATE / 'raw' / name)) for name in new_files],
        third_party_full_text_redistribution=False,
        real_customer_data=False,
        inherited_content_unchanged=True,
        generation_mode_note=('generation模式下新增演示资料可参与DeepSeek生成；全部为项目自编/公开摘要，'
                              '无第三方全文再分发风险。'))
    write(REVIEW / 'outbound-review.json', outbound)
    profiles = {}
    for mode in release.MODES:
        selected = release.profile(mode)
        from scripts import original_local_runtime as owned
        public = owned.public_profile(selected)
        write(REVIEW / ('profile-' + mode + '.json'), public)
        profiles[mode] = digest(REVIEW / ('profile-' + mode + '.json'))
    plan = dict(version=release.VERSION, previous_active=release.OLD,
                candidate_projection_sha256=digest(CANDIDATE / 'projection.json'),
                candidate_report_seal_sha256=digest(stage.REPORT / 'artifact-hashes.json'),
                outbound_review_sha256=digest(REVIEW / 'outbound-review.json'),
                profile_sha256=profiles, validation_paid_calls=0, agent_enabled=False,
                new_admissions=10, inherited_files=326,
                gate_report=read(stage.REPORT / 'gate.json')['report'],
                code_bindings={name: digest(release.PROJECT / name) for name in CODE_BINDINGS},
                independent_quality_approved=False)
    write(REVIEW / 'release-plan.json', plan)
    seal(REVIEW)
    verify_seal(REVIEW)
    print(json.dumps(dict(version=release.VERSION, sealed=True, profiles=profiles,
                          plan_sha256=digest(REVIEW / 'release-plan.json')), ensure_ascii=False))


if __name__ == '__main__':
    build()
