"""Scope refusal and projection tamper checks; never write a live database."""
import copy

import pytest

from qa_core.config.settings import Settings
from qa_core.quality.review_contract import bind_pending
from scripts import original_citation_candidate as candidate
from scripts import stage_original_citation_candidate as stage
from scripts.prepare_legacy_projection import read, write, seal


@pytest.fixture
def synthetic_review(tmp_path, monkeypatch):
    review = tmp_path / 'review'
    package = tmp_path / 'package'
    review.mkdir()
    (package / 'raw').mkdir(parents=True)
    entries, sources = [], []
    for index in range(276):
        name = f'source-{index}.md'
        path = package / 'raw' / name
        path.write_text(f'Synthetic source {index}', encoding='utf-8')
        reference = index >= 256
        meta = {'tenant_id': 'default', 'dept_id': '' if reference else 'public', 'source_system': 'synthetic_review',
                'source_id': f'source-{index}', 'asset_id': f'asset-{index}', 'source_version': 'source-revision',
                'synthetic': not reference, 'security_level': 'internal', 'visibility': 'public',
                'role_scope': [], 'allowed_roles': [], 'status': 'active', 'review_status': 'auto_validated'}
        entries.append({'filename': name, 'source_sha256': candidate.digest(path), 'proposed_metadata': meta})
        sources.append({'filename': name, 'legacy_ledger': {'doc_type': 'manual' if reference else 'guide'}})
    write(package / 'source-proposals.json', {'entries': sources})
    plan = {'target': copy.deepcopy(candidate.review_module.TARGET), 'scope': 'original_local_demo_staged_only',
            'department_resolution': {'choice': 'use_domain_empty'},
            'shared_withdrawal_groups': [{'group_binding_sha256': 'synthetic-shared-group'}],
            'entries': entries, 'acceptance_limits': ['synthetic test only'], 'not_authorized': ['release']}
    write(review / 'proposal.json', plan)
    seal(review)
    receipt = bind_pending({'protocol': 'original-citation-receipt-v1', 'proposal_sha256': candidate.digest(review / 'proposal.json'),
        'review_manifest_sha256': candidate.digest(review / 'artifact-hashes.json'), 'target': plan['target'],
        'scope': plan['scope'], 'reviewer_kind': 'human_user_chat_confirmation', 'user_message': 'synthetic test decision',
        'department_resolution': plan['department_resolution'], 'acknowledged_withdrawal_groups': ['synthetic-shared-group'],
        'approved_filenames': [row['filename'] for row in entries], 'original_release_authorized': False})
    receipt['approval'].update(status='approved', proposer='synthetic-proposer', reviewer='synthetic-reviewer', evidence='synthetic fixture only')
    receipt_path = tmp_path / 'receipt.json'
    write(receipt_path, receipt)
    monkeypatch.setattr(candidate.review_module, 'validate', lambda root: None)
    monkeypatch.setattr(candidate.review_module, 'PACKAGE', package)
    return review, receipt_path, tmp_path / 'candidate'


def test_projection_preserves_identity_bytes_department_and_never_publishes(synthetic_review):
    review, receipt_path, output = synthetic_review
    result = candidate.project(review, receipt_path, output)
    assert result['original_import_authorized'] and not result['original_release_authorized']
    metadata = read(output / 'raw/meta.json')
    assert metadata['source-275.md']['asset_id'] == 'asset-275'
    assert metadata['source-275.md']['dept_id'] == ''
    assert metadata['source-0.md']['dept_id'] == 'public'
    assert all(meta['review_status'] == 'human_chat_approved' for meta in metadata.values())
    assert not read(output / 'candidate-intent.json')['database_version_created']
    with pytest.raises(ValueError, match='new and separate'):
        candidate.project(review, receipt_path, output)


@pytest.mark.parametrize('change', ['pending', 'self_review', 'missing_evidence', 'binding', 'target', 'scope',
    'proposal_hash', 'manifest_hash', 'department', 'shared_groups', 'release', 'partial_roster', 'duplicate_roster'])
def test_receipt_cannot_expand_or_launder_scope(synthetic_review, change):
    review, path, _ = synthetic_review
    receipt = read(path)
    if change == 'pending': receipt['approval']['status'] = 'pending'
    elif change == 'self_review': receipt['approval']['reviewer'] = receipt['approval']['proposer']
    elif change == 'missing_evidence': receipt['approval']['evidence'] = ''
    elif change == 'binding': receipt['user_message'] += 'changed'
    else:
        if change == 'target': receipt['target']['database'] = 'other'
        elif change == 'scope': receipt['scope'] = 'production'
        elif change == 'proposal_hash': receipt['proposal_sha256'] = '0' * 64
        elif change == 'manifest_hash': receipt['review_manifest_sha256'] = '0' * 64
        elif change == 'department': receipt['department_resolution'] = {'choice': 'use_raw_public'}
        elif change == 'shared_groups': receipt['acknowledged_withdrawal_groups'] = []
        elif change == 'release': receipt['original_release_authorized'] = True
        elif change == 'partial_roster': receipt['approved_filenames'].pop()
        else: receipt['approved_filenames'][-1] = receipt['approved_filenames'][0]
        approval = receipt['approval']
        receipt = bind_pending(receipt)
        receipt['approval'].update({key: approval[key] for key in ('status', 'proposer', 'reviewer', 'evidence')})
    write(path, receipt)
    with pytest.raises(ValueError):
        candidate.approved_inputs(review, path)


@pytest.mark.parametrize('change', ['body', 'metadata', 'map', 'intent', 'extra_file'])
def test_resealed_projection_tamper_still_fails(synthetic_review, change):
    review, path, output = synthetic_review
    candidate.project(review, path, output)
    if change == 'body': (output / 'raw/source-275.md').write_text('changed', encoding='utf-8')
    elif change == 'extra_file': (output / 'raw/extra.md').write_text('extra', encoding='utf-8')
    else:
        name = {'metadata': 'raw/meta.json', 'map': 'source-map.json', 'intent': 'candidate-intent.json'}[change]
        value = read(output / name)
        if change == 'metadata': value['source-275.md']['dept_id'] = 'public'
        elif change == 'map': value['entries'][-1]['source_id'] = 'renamed'
        else: value['published'] = True
        write(output / name, value)
    seal(output)
    with pytest.raises(ValueError): candidate.validate_candidate(output, review)


@pytest.mark.parametrize('change', ['original_collection', 'test_store', 'paid', 'no_auth', 'original_port', 'agent'])
def test_execution_profile_rejects_other_targets_or_unsafe_defaults(change):
    values = {name: field.default for name, field in Settings.model_fields.items()}
    values.update(app_env='dev', mysql_dsn='mysql+pymysql://fixture:fixture@127.0.0.1:3306/rag_kb',
                  milvus_uri='http://localhost:19530', doc_collection='local_citation_r1_doc', faq_collection='local_citation_r1_faq',
                  generation_enabled=False, llm_api_key='', auth_required=True, workflow_evidence_enabled=True,
                  api_host='127.0.0.1', api_port=18013, redis_url='redis://127.0.0.1:6379/14', agent_enabled=False)
    settings = Settings(_env_file=None, **values)
    stage.validate_target(settings)
    if change == 'original_collection': settings.doc_collection = 'doc_collection'
    elif change == 'test_store': settings.mysql_dsn = 'mysql+pymysql://fixture:fixture@127.0.0.1:13306/rag_kb_test'
    elif change == 'paid': settings.generation_enabled = True
    elif change == 'no_auth': settings.auth_required = False
    elif change == 'original_port': settings.api_port = 8010
    else: settings.agent_enabled = True
    with pytest.raises(ValueError, match='exact approved original'):
        stage.validate_target(settings)
