"""Approval-bound process adapter; immutable previous release remains intact."""
import argparse
import json
from datetime import datetime
from pathlib import Path
import sqlalchemy as sa
from scripts import original_local_runtime as owned
from scripts import stage_content_quality_merge as merge
from scripts.prepare_legacy_projection import read,digest,verify_seal

PROJECT=owned.PROJECT
REVIEW=PROJECT/'.runtime/test/reports/content-quality-release-review-20261011-r2'
SELECTION=owned.ROOT/'content-quality-release-selection.json'
VERSION=merge.VERSION
OLD='local-merged-r2'
BASE=owned.build_profile()
ORIGINAL_SERVER=owned.SERVER_MODULE
ORIGINAL_VALIDATE=owned.validate_profile
ORIGINAL_BUILD=owned.build_profile
ORIGINAL_VERIFY_ACTIVE=owned.verify_active
MODES={'generation':'批准切换并允许新增资料用于DeepSeek生成',
       'citation':'仅批准引用模式切换，新增资料不外发'}


def profile(mode):
    if mode not in MODES:raise ValueError('explicit mode required')
    generating=mode=='generation'
    settings=BASE.model_copy(update=dict(doc_collection=merge.DOC,faq_collection=merge.FAQ,
        data_dir=str(merge.CANDIDATE),reports_dir=str(merge.REPORT/'quality'),
        semantic_cache_enabled=False,generation_enabled=generating,agent_enabled=False))
    if not generating:settings=settings.model_copy(update=dict(llm_api_key='',llm_base_url='http://127.0.0.1:1/disabled'))
    return settings


def validate_receipt(receipt,plan,plan_sha):
    mode=receipt.get('mode')
    expected=dict(decision='approved',reviewer_kind='human_user_chat_confirmation',user_message=MODES.get(mode),
        scope='content_quality_local_demo_publication',plan_sha256=plan_sha,version=VERSION,mode=mode,
        new_material_outbound_approved=mode=='generation',independent_quality_approved=False,
        recorded_at_utc=receipt.get('recorded_at_utc'))
    if (mode not in MODES or receipt!=expected or not expected['recorded_at_utc']
        or plan.get('version')!=VERSION or plan.get('previous_active')!=OLD
        or plan.get('validation_paid_calls')!=0 or plan.get('agent_enabled') is not False):
        raise ValueError('exact publication and chosen outbound scope receipt required')
    stamp=datetime.fromisoformat(expected['recorded_at_utc'])
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds()!=0:
        raise ValueError('UTC approval timestamp required')


def validate_review():
    verify_seal(REVIEW);verify_seal(merge.CANDIDATE);verify_seal(merge.REPORT)
    plan=read(REVIEW/'release-plan.json')
    if (plan['candidate_projection_sha256']!=digest(merge.CANDIDATE/'projection.json')
        or plan['candidate_report_seal_sha256']!=digest(merge.REPORT/'artifact-hashes.json')
        or plan['outbound_review_sha256']!=digest(REVIEW/'outbound-review.json')):
        raise ValueError('published content binding changed')
    for name,checksum in plan['code_bindings'].items():
        path=(PROJECT/name).resolve()
        if not path.is_relative_to(PROJECT) or digest(path)!=checksum:raise ValueError('release code binding changed')
    return plan


def selected_settings():
    if not SELECTION.exists():return BASE
    plan=validate_review();receipt=read(SELECTION)
    validate_receipt(receipt,plan,digest(REVIEW/'release-plan.json'))
    selected=profile(receipt['mode'])
    if digest(REVIEW/('profile-'+receipt['mode']+'.json'))!=plan['profile_sha256'][receipt['mode']]:
        raise ValueError('reviewed mode profile changed')
    if owned.public_profile(selected)!=read(REVIEW/('profile-'+receipt['mode']+'.json')):
        raise ValueError('current mode profile differs')
    return selected


def validate_profile(settings):
    if settings.model_dump()!=selected_settings().model_dump():raise ValueError('exact approved runtime profile required')


def verify_active(settings,engine):
    if not SELECTION.exists():return ORIGINAL_VERIFY_ACTIVE(settings,engine)
    from qa_core.quality.publication import verify_publication
    with engine.connect() as conn:
        rows=conn.execute(sa.text("SELECT version,quality_report_path FROM kb_versions WHERE status='ACTIVE'")).all()
    if len(rows)!=1 or rows[0][0]!=VERSION:raise ValueError('approved candidate must be unique ACTIVE')
    verify_publication(settings,*rows[0]);return VERSION


def install_adapter():
    """Dedicated CLI/server process only; never edit the frozen source files."""
    selected_settings()
    owned.build_profile=selected_settings
    owned.validate_profile=validate_profile
    owned.verify_active=verify_active
    owned.SERVER_MODULE='scripts.content_quality_local_server' if SELECTION.exists() else ORIGINAL_SERVER


def main():
    install_adapter();owned.main()


if __name__=='__main__':
    try:main()
    except Exception as error:
        import sys
        print('Content runtime command failed ('+type(error).__name__+'); inspect approved bindings and owned state.',file=sys.stderr)
        sys.exit(1)
