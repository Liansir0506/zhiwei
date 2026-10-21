"""Release target/configuration denial tests; no original database or approval."""
import copy

import pytest

from qa_core.config.settings import Settings
from qa_core.ingest.binding import version_binding
from scripts import prepare_original_citation_release as release


@pytest.fixture
def original_base(monkeypatch):
    values = {name: field.default for name, field in Settings.model_fields.items()}
    values.update(app_env='dev', mysql_dsn='mysql+pymysql://fixture:fixture@127.0.0.1:3306/rag_kb',
                  milvus_uri='http://localhost:19530', redis_url='redis://127.0.0.1:6379/0',
                  embedding_model_path='F:/fixture/models/bge-m3', llm_api_key='synthetic-original-key')
    base = Settings(_env_file=None, **values)
    monkeypatch.setattr(release, 'runtime_settings', lambda: base)
    return base


def test_host_binding_matches_staged_while_original_config_is_unchanged(original_base):
    before = original_base.model_dump()
    settings = release.build_profile()
    staged = settings.model_copy(update={'api_port': 18013, 'redis_url': 'redis://127.0.0.1:6379/14'})
    assert version_binding(settings) == version_binding(staged)
    assert original_base.model_dump() == before
    assert settings.api_port == 8010 and settings.auth_required
    assert not settings.generation_enabled and not settings.agent_enabled
    assert not settings.llm_api_key


@pytest.mark.parametrize('changes', [
    {'milvus_uri': 'http://milvus:19530'}, {'embedding_model_path': '/app/models/bge-m3'},
    {'milvus_uri': 'http://127.0.0.1:19530'}, {'doc_collection': 'doc_collection'},
    {'faq_collection': 'faq_collection'}, {'app_env': 'test'}, {'api_host': '0.0.0.0'},
    {'api_port': 18013}, {'redis_url': 'redis://127.0.0.1:16379/15'},
    {'generation_enabled': True}, {'agent_enabled': True}, {'auth_required': False},
    {'workflow_evidence_enabled': False}, {'semantic_cache_enabled': True},
    {'llm_api_key': 'synthetic-new-key'}, {'llm_base_url': 'https://example.invalid'},
    {'data_dir': 'unreviewed-data'}, {'reports_dir': 'unreviewed-reports'},
    {'mysql_dsn': 'mysql+pymysql://fixture:fixture@127.0.0.1:13306/rag_kb_test'},
])
def test_host_runtime_cannot_adopt_other_target_or_enable_models(original_base, changes):
    with pytest.raises(ValueError, match='exact reviewed'):
        release.validate_profile(release.build_profile().model_copy(update=changes))


def test_host_environment_replaces_inherited_paid_and_foreign_settings(original_base, monkeypatch):
    monkeypatch.setenv('MYSQL_DSN', 'mysql+pymysql://foreign/other')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic-poisoned-key')
    monkeypatch.setenv('GENERATION_ENABLED', 'true')
    monkeypatch.setenv('APP_SCENARIOS_DIR', 'unreviewed-scenarios')
    settings = release.build_profile()
    env = release.profile_environment(settings)
    assert env['MYSQL_DSN'] == settings.mysql_dsn and env['LLM_API_KEY'] == ''
    assert env['GENERATION_ENABLED'] == env['AGENT_ENABLED'] == 'false'
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
    assert env['APP_SCENARIOS_DIR'] == str(release.ROOT / 'scenarios')
    assert all(name.upper() in env for name in Settings.model_fields)


def test_public_profile_has_no_database_credentials_or_original_paid_key(original_base):
    profile = release.public_profile(release.build_profile())
    assert 'mysql_dsn' not in profile and 'llm_api_key' not in profile
    assert 'synthetic-original-key' not in str(profile)
    assert profile['mysql_target']['database'] == 'rag_kb'


@pytest.mark.parametrize('change', ['none', 'docker', 'collection', 'path'])
def test_manifest_mismatch_is_not_aliased_or_rebound(original_base, change):
    settings = release.build_profile()
    manifest = {'binding': version_binding(settings)}
    if change == 'none':
        manifest = None
    else:
        altered = {'docker': {'milvus_uri': 'http://milvus:19530'},
                   'collection': {'doc_collection': 'doc_collection_schema_r1'},
                   'path': {'embedding_model_path': '/app/models/bge-m3'}}[change]
        manifest = {'binding': version_binding(settings.model_copy(update=altered))}
    with pytest.raises(ValueError, match='binding differs'):
        release.check_binding(settings, manifest)


@pytest.mark.parametrize('change', ['old_missing', 'multiple_active', 'candidate_active', 'candidate_missing', 'pending_job'])
def test_release_preflight_refuses_pointer_drift_or_unfinished_candidate(change):
    rows = [{'version': release.OLD_ACTIVE, 'status': 'ACTIVE'}, {'version': release.VERSION, 'status': 'STAGED'}]
    release.require_states(copy.deepcopy(rows), 0)
    if change == 'old_missing': rows[0]['status'] = 'ARCHIVED'
    elif change == 'multiple_active': rows.append({'version': 'unreviewed', 'status': 'ACTIVE'})
    elif change == 'candidate_active': rows[1]['status'] = 'ACTIVE'
    elif change == 'candidate_missing': rows.pop()
    with pytest.raises(ValueError):
        release.require_states(rows, 1 if change == 'pending_job' else 0)
