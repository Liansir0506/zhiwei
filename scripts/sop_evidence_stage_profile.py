"""Exact proposed test profile; merely constructs configuration, never applies it."""
import sqlalchemy as sa

from scripts.prepare_sop_evidence_admission import ROOT
from scripts import readonly_workspace_runtime as ro
from qa_core.quality.sop_admission import TARGET
from qa_core.config.environment import validate_isolated_environment


def profile():
    base=ro.profile()
    selected=base.model_copy(update=dict(app_env='test',
        mysql_dsn=sa.engine.make_url(base.mysql_dsn).set(database=TARGET['database']).render_as_string(hide_password=False),
        milvus_uri=TARGET['milvus_uri'],doc_collection=TARGET['doc_collection'],faq_collection=TARGET['faq_collection'],
        data_dir=str(ROOT/'.runtime/test/sop-evidence-stage-r1/candidate'),
        reports_dir=str(ROOT/'.runtime/test/sop-evidence-stage-r1/reports'),
        redis_url='redis://127.0.0.1:16379/14',generation_enabled=False,agent_enabled=False,
        auth_required=True,allow_header_roles=False,semantic_cache_enabled=False,
        llm_api_key='',llm_base_url='http://127.0.0.1:1/disabled',embedding_device='cpu',rerank_device='cpu'))
    validate_profile(selected);return selected


def validate_profile(settings):
    url=sa.engine.make_url(settings.mysql_dsn)
    if (url.host not in {'127.0.0.1','localhost'} or url.port!=TARGET['mysql_port'] or url.database!=TARGET['database'] or
        settings.milvus_uri!=TARGET['milvus_uri'] or settings.doc_collection!=TARGET['doc_collection'] or
        settings.faq_collection!=TARGET['faq_collection'] or settings.generation_enabled or settings.agent_enabled or
        not settings.auth_required or settings.allow_header_roles or settings.semantic_cache_enabled or settings.llm_api_key or
        settings.llm_base_url!='http://127.0.0.1:1/disabled' or validate_isolated_environment(settings,'test')):
        raise ValueError('exact isolated disabled-model stage profile required')
    return True
