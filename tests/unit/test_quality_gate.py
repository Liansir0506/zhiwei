from qa_core.indexing.chunkers.base import make_chunk
from qa_core.quality.gate import check_duplicates, check_sop_integrity
from scripts.rebuild_kb import dedup_key


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def test_duplicate_gate_compares_content_not_alarm_code():
    client = FakeClient([
        {"id": "a", "text": "ALM-1 first resolution", "alarm_code": "ALM-1"},
        {"id": "b", "text": "ALM-1 second resolution", "alarm_code": "ALM-1"},
    ])
    ok, detail = check_duplicates(client, "v4")
    assert ok is True
    assert detail["count"] == 0
    assert "text" in client.calls[0]["output_fields"]


def test_ingest_dedup_key_does_not_merge_same_alarm_code():
    first = make_chunk("first resolution", "fault_case", alarm_code="ALM-1")
    second = make_chunk("second resolution", "fault_case", alarm_code="ALM-1")
    assert dedup_key(first) != dedup_key(second)


def test_duplicate_gate_rejects_exact_content_copy():
    client = FakeClient([
        {"id": "a", "text": "same content"},
        {"id": "b", "text": "same content"},
    ])
    ok, detail = check_duplicates(client, "v4")
    assert ok is False
    assert detail["duplicate_chunk_ids"] == ["b"]


def test_sop_gate_groups_steps_by_source_and_ignores_intro():
    client = FakeClient([
        {"id": "intro", "source": "sop-1", "step_index": 0},
        {"id": "one", "source": "sop-1", "step_index": 1},
        {"id": "two", "source": "sop-1", "step_index": 2},
    ])
    ok, detail = check_sop_integrity(client, "v4")
    assert ok is True
    assert detail["sop_sources"] == 1


def test_sop_gate_rejects_missing_step():
    client = FakeClient([
        {"id": "one", "source": "sop-1", "step_index": 1},
        {"id": "three", "source": "sop-1", "step_index": 3},
    ])
    ok, detail = check_sop_integrity(client, "v4")
    assert ok is False
    assert detail["broken"] == ["sop-1"]
