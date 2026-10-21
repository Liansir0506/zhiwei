from datetime import datetime

import pytest
from pydantic import ValidationError

from qa_core.domain.models import KnowledgeAsset, normalize_knowledge_metadata
from scripts.migrate_domain_model import SQL_PATH, split_sql_statements


def test_legacy_metadata_normalizes_to_enterprise_fields():
    meta = normalize_knowledge_metadata({
        "equipment_type": "BBU3900",
        "allowed_roles": ["ops"],
        "ticket_id": "TT-2026-001",
        "source": "fault_case_tickets.csv",
    })
    assert meta["tenant_id"] == "default"
    assert meta["dept_id"] == "public"
    assert meta["device_type"] == "BBU3900"
    assert meta["role_scope"] == ["ops"]
    assert meta["source_id"] == "TT-2026-001"
    assert meta["security_level"] == "internal"
    assert meta["status"] == "active"


def test_role_restricted_asset_requires_role_scope():
    with pytest.raises(ValidationError, match="role_scope"):
        KnowledgeAsset(
            asset_id="asset-1", tenant_id="default", filename="a.md",
            doc_type="manual", source_system="wiki", visibility="role_restricted",
        )


def test_asset_rejects_reverse_effective_period():
    with pytest.raises(ValidationError, match="effective_to"):
        KnowledgeAsset(
            asset_id="asset-1", tenant_id="default", filename="a.md",
            doc_type="manual", source_system="wiki",
            effective_from=datetime(2026, 2, 1), effective_to=datetime(2026, 1, 1),
        )


def test_domain_migration_file_is_non_destructive_and_splitable():
    statements = split_sql_statements(SQL_PATH.read_text(encoding="utf-8"))
    assert len(statements) >= 13
    normalized = " ".join(statements).upper()
    assert "CREATE TABLE IF NOT EXISTS DEVICES" in normalized
    assert "CREATE TABLE IF NOT EXISTS KNOWLEDGE_RELATIONS" in normalized
    assert "DROP TABLE" not in normalized
    assert "TRUNCATE" not in normalized
    assert "DELETE FROM" not in normalized
