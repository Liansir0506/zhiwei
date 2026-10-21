# -*- coding: utf-8 -*-
"""统计窗口与案例导出的纯函数契约（不依赖数据库）。"""
import hashlib
from datetime import datetime

import pytest
from fastapi import HTTPException

from qa_core.api.route_ops_resources import _parse_time_bound
from qa_core.api.route_stats import window_bounds, window_payload
from qa_core.api.route_tickets import case_document, case_filename, case_source_id


def test_window_7d_is_inclusive_of_anchor_day():
    anchor = datetime(2026, 9, 19, 8, 30)
    start, end = window_bounds(anchor, 7)
    assert start == datetime(2026, 9, 13, 0, 0)
    assert end == anchor
    # 首尾都算：13/14/15/16/17/18/19 = 7 个日历日
    assert (end.date() - start.date()).days + 1 == 7


def test_window_30d_uses_same_rule():
    start, end = window_bounds(datetime(2026, 9, 19, 8, 30), 30)
    assert start.date().isoformat() == "2026-08-21"


def test_window_none_when_no_data():
    assert window_bounds(None, 7) == (None, None)
    assert window_payload(None, None, 7) is None


def test_window_payload_labels_match_bounds():
    start, end = window_bounds(datetime(2026, 9, 19, 8, 30), 7)
    payload = window_payload(start, end, 7)
    assert payload["from_date"] == "2026-09-13"
    assert payload["to_date"] == "2026-09-19"
    assert payload["days"] == 7


def test_date_only_end_covers_whole_day():
    start = _parse_time_bound("2026-09-13")
    end = _parse_time_bound("2026-09-19", end=True)
    assert start == datetime(2026, 9, 13, 0, 0)
    assert end == datetime(2026, 9, 19, 23, 59, 59, 999999)


def test_invalid_time_bound_is_422():
    with pytest.raises(HTTPException) as exc:
        _parse_time_bound("2026/09/19")
    assert exc.value.status_code == 422


def test_case_source_and_filename_are_stable():
    assert case_source_id(12, "OPS-TT-20261020120000") == "case:OPS-TT-20261020120000:12"
    name = case_filename(12, "OPS-TT-20261020120000")
    assert name.startswith("fault_case_") and name.endswith(".md")
    assert all(ch.isalnum() or ch in "_.-" for ch in name)


def test_case_document_has_front_matter_and_matching_sha():
    row = {"id": 12, "ticket_id": "OPS-TT-1", "alarm_id": "SIM-ALM-000002",
           "content": "# 案例\n\n处理过程", "source_id": "case:OPS-TT-1:12",
           "reviewed_by": "admin", "reviewed_at": "2026-10-20 12:00:00"}
    content, sha = case_document(row)
    assert content.startswith("---\n")
    assert "source_id: case:OPS-TT-1:12" in content
    assert "reviewed_by: admin" in content
    assert "doc_type: fault_case" in content
    assert sha == hashlib.sha256(content.encode("utf-8")).hexdigest()
