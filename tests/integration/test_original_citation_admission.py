"""Own disposable MySQL DB: real insert-only transaction and rollback checks."""
import copy
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from tests.integration.test_readonly_agent_domain import isolated_domain
from scripts.stage_original_citation_candidate import insert_missing_assets


def inputs(tmp_path):
    raw = tmp_path / 'raw'
    raw.mkdir()
    entries, metadata = [], {}
    for index in range(256):
        name = f'synthetic-{index}.md'
        (raw / name).write_text(f'Synthetic fixture {index}', encoding='utf-8')
        entries.append({'filename': name, 'existing_registration_action': 'insert_new_after_approval'})
        metadata[name] = {'asset_id': f'new-{index}', 'tenant_id': 'synthetic', 'dept_id': 'net',
            'doc_type': 'manual', 'source_system': 'synthetic-fixture', 'source_id': f'new-source-{index}',
            'source_version': 'fixture-r', 'security_level': 'internal', 'visibility': 'public', 'role_scope': [],
            'status': 'active', 'effective_from': '2026-01-01T01:00:00+01:00', 'effective_to': '2028-01-01T00:00:00+00:00'}
    # Existing registrations never enter INSERT or UPDATE.
    entries.append({'filename': 'must-not-be-read.md', 'existing_registration_action': 'preserve_unchanged'})
    return entries, metadata, {'recorded_at_utc': '2026-10-09T00:00:00+00:00'}


def test_insert_only_preserves_existing_registry_and_utc_storage(isolated_domain, tmp_path):
    _, dao = isolated_domain
    entries, metadata, receipt = inputs(tmp_path)
    with dao.engine.connect() as conn:
        before = [dict(r) for r in conn.exec_driver_sql('SELECT * FROM knowledge_assets ORDER BY asset_id').mappings()]
    with dao.engine.begin() as conn:
        assert len(insert_missing_assets(conn, entries, metadata, tmp_path, receipt)) == 256
    with dao.engine.connect() as conn:
        preserved = [dict(r) for r in conn.execute(sa.text("SELECT * FROM knowledge_assets WHERE asset_id NOT LIKE :prefix ORDER BY asset_id"),
                                                 {'prefix': 'new-%'}).mappings()]
        inserted = conn.exec_driver_sql("SELECT * FROM knowledge_assets WHERE asset_id='new-0'").mappings().one()
        assert preserved == before
        assert inserted['effective_from'] == datetime(2026, 1, 1)
        assert inserted['reviewed_by'] is None
        assert inserted['kb_version'] == 'local-citation-r1'


@pytest.mark.parametrize('fault', ['identity_conflict', 'foreign_key', 'wrong_count'])
def test_any_late_failure_rolls_back_all_insertions(isolated_domain, tmp_path, fault):
    _, dao = isolated_domain
    entries, metadata, receipt = inputs(tmp_path)
    expected = ValueError
    if fault == 'identity_conflict': metadata[entries[-2]['filename']]['asset_id'] = 'visible'
    elif fault == 'foreign_key':
        metadata[entries[-2]['filename']]['dept_id'] = 'missing-department'
        expected = sa.exc.IntegrityError
    else: entries.pop(255)
    with dao.engine.connect() as conn:
        before = [dict(r) for r in conn.exec_driver_sql('SELECT * FROM knowledge_assets ORDER BY asset_id').mappings()]
    with pytest.raises(expected):
        with dao.engine.begin() as conn:
            insert_missing_assets(conn, entries, metadata, tmp_path, receipt)
    with dao.engine.connect() as conn:
        assert [dict(r) for r in conn.exec_driver_sql('SELECT * FROM knowledge_assets ORDER BY asset_id').mappings()] == before
