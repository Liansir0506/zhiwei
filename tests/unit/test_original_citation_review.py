"""Synthetic review inputs only; no database/model access or corpus approvals."""
import copy
import json

import pytest

from scripts import prepare_original_citation_review as module


@pytest.fixture
def proposal_inputs(tmp_path, monkeypatch):
    package = tmp_path / 'package'
    review = tmp_path / 'group-review'
    package.mkdir()
    review.mkdir()
    (package / 'raw').mkdir()
    for root in (package, review):
        module.write(root / 'artifact-hashes.json', {})
    entries = []
    for i in range(276):
        reference = i >= 256
        name = f'ref_{i}.md' if reference else f'sim_{i}.csv'
        meta = {'dept_id': 'public', 'tenant_id': 'default', 'synthetic': not reference,
                'security_level': 'internal', 'role_scope': [], 'status': 'active',
                'source_version': module.VERSION, 'license_status': 'link_only_no_redistribution' if reference else 'synthetic'}
        (package / 'raw' / name).write_text('<script>alert(1)</script>', encoding='utf-8')
        entries.append({'filename': name, 'sha256': 'a' * 64, 'original_scope_metadata': meta,
                        'proposed_asset_id': f'asset-{i}', 'proposed_source_id': f'source-{i}',
                        'existing_registration': {'asset_id': f'asset-{i}'} if reference else None,
                        'existing_source_version': module.VERSION, 'exact_traced_rows': 1})
    module.write(package / 'source-proposals.json', {'entries': entries})
    receipt = tmp_path / 'receipt.json'
    module.write(receipt, {'scope': 'isolated_local_demo_only', 'target_environment': 'test', 'approval': {'status': 'approved'},
                           'approved_filenames': [row['filename'] for row in entries[:256]]})
    monkeypatch.setattr(module, 'PACKAGE', package)
    monkeypatch.setattr(module, 'GROUP_REVIEW', review)
    monkeypatch.setattr(module, 'RECEIPT', receipt)
    monkeypatch.setattr(module, 'validate_package', lambda path: None)
    groups = [{'source_id': 'shared', 'filenames': ['ref_256.md', 'ref_257.md'],
               'meaning': 'withdrawal applies to both'}]
    monkeypatch.setattr(module, 'load_plan', lambda package, review: {'shared_withdrawal_groups': groups})
    audit = {'target': copy.deepcopy(module.TARGET), 'read_only': True,
             'protected_state_unchanged': True, 'source_files': 276,
             'trace_status': {'exact_trace': 5300}, 'sql_before': {'fixture': 'same'}, 'sql_after': {'fixture': 'same'}}
    return audit, entries


def test_candidate_never_expands_historical_receipt_or_publishes(proposal_inputs):
    audit, entries = proposal_inputs
    before = module.RECEIPT.read_bytes()
    plan = module.proposal(audit)
    assert plan['status'] == 'pending'
    assert plan['scope'] == 'original_local_demo_staged_only'
    assert len(plan['entries']) == 276
    assert all(row['review_status'] == 'pending' for row in plan['entries'])
    assert sum(row['historical_isolated_approval'] for row in plan['entries']) == 256
    assert 'original ACTIVE/config/port switch or release' in plan['not_authorized']
    assert module.RECEIPT.read_bytes() == before


def test_reference_identity_department_and_joint_withdrawal(proposal_inputs):
    audit, entries = proposal_inputs
    plan = module.proposal(audit)
    for original, proposed in zip(entries[256:], plan['entries'][256:]):
        assert proposed['asset_id'] == original['proposed_asset_id']
        assert proposed['source_id'] == original['proposed_source_id']
        assert proposed['proposed_metadata']['dept_id'] == ''
        assert proposed['existing_registration_action'] == 'preserve_unchanged'
        assert proposed['proposed_metadata']['license_status'] == 'link_only_no_redistribution'
    assert plan['department_resolution']['existing_sql_update'] is False
    assert plan['shared_withdrawal_groups'][0]['filenames'] == ['ref_256.md', 'ref_257.md']
    assert all(row['proposed_metadata']['dept_id'] == 'public' for row in plan['entries'][:256])


@pytest.mark.parametrize('change', ['target', 'read_only', 'unchanged', 'roster', 'trace', 'sql'])
def test_reject_unverified_or_changed_original_state(proposal_inputs, change):
    audit, _ = proposal_inputs
    if change == 'target':
        audit['target']['database'] = 'test_database'
    elif change == 'read_only':
        audit['read_only'] = False
    elif change == 'unchanged':
        audit['protected_state_unchanged'] = False
    elif change == 'roster':
        audit['source_files'] = 275
    elif change == 'trace':
        audit['trace_status'] = {'exact_trace': 5299, 'ambiguous': 1}
    else:
        audit['sql_after'] = {'fixture': 'changed'}
    with pytest.raises(ValueError, match='verified original'):
        module.proposal(audit)


@pytest.mark.parametrize('change', ['environment', 'scope', 'extra_reference', 'pending'])
def test_reject_historical_receipt_expansion(proposal_inputs, change):
    audit, entries = proposal_inputs
    receipt = module.read(module.RECEIPT)
    if change == 'environment':
        receipt['target_environment'] = 'dev'
    elif change == 'scope':
        receipt['scope'] = 'original_local_demo_staged_only'
    elif change == 'extra_reference':
        receipt['approved_filenames'].append(entries[-1]['filename'])
    else:
        receipt['approval']['status'] = 'pending'
    module.write(module.RECEIPT, receipt)
    with pytest.raises(ValueError, match='historical isolated'):
        module.proposal(audit)


def test_review_escapes_body_and_does_not_hide_pending(proposal_inputs):
    audit, _ = proposal_inputs
    page = module.render(module.proposal(audit))
    assert '<script>' not in page
    assert '&lt;script&gt;' in page
    assert page.count('<details>') == 20
    assert '待批准' in page and '不切换当前ACTIVE' in page


def test_resealed_scope_change_is_rejected(proposal_inputs, tmp_path):
    audit, _ = proposal_inputs
    output = tmp_path / 'output'
    output.mkdir()
    plan = module.proposal(audit)
    module.write(output / 'live-audit.json', audit)
    module.write(output / 'proposal.json', plan)
    (output / 'review.html').write_text(module.render(plan), encoding='utf-8')
    module.seal(output)
    assert module.validate(output)['original_import_authorized'] is False
    plan['approval_authorizes'].append('switch original ACTIVE')
    module.write(output / 'proposal.json', plan)
    module.seal(output)
    with pytest.raises(ValueError, match='differs from verified inputs'):
        module.validate(output)


def test_explicit_runtime_profile_ignores_shell_and_rejects_other_store(monkeypatch):
    import dotenv
    env = {'APP_ENV': 'dev', 'MYSQL_DSN': 'mysql+pymysql://fixture:fixture@127.0.0.1:3306/rag_kb',
           'MILVUS_URI': 'http://localhost:19530', 'DOC_COLLECTION': 'doc_collection', 'FAQ_COLLECTION': 'faq_collection'}
    monkeypatch.setattr(dotenv, 'dotenv_values', lambda path: env)
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.setenv('MYSQL_DSN', 'mysql+pymysql://fixture:fixture@localhost:13306/rag_kb_test')
    assert module.runtime_settings().app_env == 'dev'
    env['MYSQL_DSN'] = 'mysql+pymysql://fixture:fixture@localhost:13306/rag_kb_test'
    with pytest.raises(ValueError, match='original read-only target'):
        module.runtime_settings()
