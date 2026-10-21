"""Construct a new disabled-model profile; never applies settings or starts a Worker."""
from pathlib import Path
import sqlalchemy as sa
from scripts.sop_evidence_stage_profile import profile as previous_profile
from scripts.prepare_sop_record_revision import ROOT
from qa_core.quality.sop_record_admission import TARGET
from qa_core.config.environment import validate_isolated_environment

STAGE = ROOT/'.runtime/test/sop-records-stage-r2'
CANDIDATE = STAGE/'candidate'


def profile():
    base = previous_profile()
    selected = base.model_copy(update=dict(
        mysql_dsn=sa.engine.make_url(base.mysql_dsn).set(database=TARGET['database']).render_as_string(hide_password=False),
        doc_collection=TARGET['doc_collection'], faq_collection=TARGET['faq_collection'],
        data_dir=str(CANDIDATE), reports_dir=str(STAGE/'reports'), redis_url='redis://127.0.0.1:16379/13',
        api_host='127.0.0.1', api_port=18025))
    validate_profile(selected)
    return selected


def validate_profile(settings):
    url = sa.engine.make_url(settings.mysql_dsn)
    if (url.host not in {'localhost','127.0.0.1'} or url.port != 13306 or url.database != TARGET['database'] or
            settings.app_env != 'test' or settings.milvus_uri != TARGET['milvus_uri'] or
            settings.doc_collection != TARGET['doc_collection'] or settings.faq_collection != TARGET['faq_collection'] or
            settings.redis_url != 'redis://127.0.0.1:16379/13' or settings.api_host != '127.0.0.1' or settings.api_port != 18025 or
            Path(settings.data_dir) != CANDIDATE or Path(settings.reports_dir) != STAGE/'reports' or
            settings.generation_enabled or settings.agent_enabled or not settings.auth_required or
            settings.allow_header_roles or settings.semantic_cache_enabled or settings.llm_api_key or
            settings.llm_base_url != 'http://127.0.0.1:1/disabled' or validate_isolated_environment(settings,'test')):
        raise ValueError('exact new r2 isolated profile required')
    return True
