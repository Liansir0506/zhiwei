"""Optional, content-bound runtime selection; absent selection keeps old profile."""
from pathlib import Path
from scripts.prepare_legacy_projection import read,digest,verify_seal

PROJECT=Path(__file__).resolve().parents[1]
REVIEW=PROJECT/'.runtime/test/reports/merged-protocol-release-20261010'
SELECTION=PROJECT/'.runtime/original-local/merged-release-selection.json'
VERSION='local-merged-r2'


def validate_selection(receipt,plan,plan_sha):
    if (receipt.get('decision')!='approved' or receipt.get('reviewer_kind')!='human_user_chat_confirmation'
            or not str(receipt.get('user_message','')).strip() or receipt.get('plan_sha256')!=plan_sha
            or receipt.get('version')!=VERSION or receipt.get('scope')!='local_demo_publication'
            or receipt.get('new_faq_outbound_approved') is not True
            or plan.get('version')!=VERSION or plan.get('previous_active')!='local-release-r1'
            or plan.get('generation_validation_requests')!=0):
        raise ValueError('exact merged local publication and new FAQ outbound approval required')


def selected_settings(base):
    if not SELECTION.exists():return base
    verify_seal(REVIEW)
    plan=read(REVIEW/'release-plan.json');receipt=read(SELECTION)
    validate_selection(receipt,plan,digest(REVIEW/'release-plan.json'))
    for name,sha in plan['code_bindings'].items():
        path=(PROJECT/name).resolve()
        if not path.is_relative_to(PROJECT) or digest(path)!=sha:raise ValueError('merged publication code binding changed')
    candidate=PROJECT/'data/candidates/merged-protocol-release-r2'
    verify_seal(candidate)
    if digest(candidate/'projection.json')!=plan['projection_sha256']:raise ValueError('merged source projection changed')
    return base.model_copy(update={'data_dir':str(candidate),'reports_dir':str(PROJECT/'.runtime/test/reports')})


def allowed_active_versions():
    if not SELECTION.exists():return ()
    # Approval/bindings are rechecked by build_profile before this is called.
    return (VERSION,)
