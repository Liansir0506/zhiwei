"""六类切分器纯逻辑测试（阶段4前置，不打真实服务）。"""
import pytest

from qa_core.indexing.chunkers.base import estimate_tokens, split_by_window
from qa_core.indexing.chunkers.registry import (
    chunk_faq,
    chunk_manual,
    chunk_sop,
    chunk_standard,
    chunk_table,
    chunk_ticket,
)


@pytest.mark.unit
def test_ticket_one_row_one_chunk():
    rows = [{"ticket_id": "T1", "alarm_code": "ALM-1234", "title": "BBU 掉电",
             "phenomenon": "网管告警 BBU 掉电", "root_cause": "电源模块故障",
             "resolution": "更换电源模块", "equipment_type": "BBU",
             "vendor": "华为", "network_tech": "5G"}]
    chunks = chunk_ticket(rows)
    assert len(chunks) == 1
    assert chunks[0].metadata["alarm_code"] == "ALM-1234"
    assert "电源模块" in chunks[0].text


@pytest.mark.unit
def test_ticket_long_row_splits_with_parent_id():
    long_text = "细节" * 1500  # ~3000 token，超过 1200 上限
    rows = [{"ticket_id": "T2", "alarm_code": "ALM-5678",
             "phenomenon": long_text, "root_cause": long_text, "resolution": long_text}]
    chunks = chunk_ticket(rows)
    assert len(chunks) == 3
    assert all(c.metadata["parent_id"] == "T2" for c in chunks)
    assert [c.metadata["part"] for c in chunks] == ["phenomenon", "root_cause", "resolution"]


@pytest.mark.unit
def test_manual_heading_path_and_window():
    section = "小节内容句子。" * 400  # 超过窗口上限
    text = f"# 手册\n## 安装\n开头段\n### 电源\n{section}\n## 调试\n调试内容"
    chunks = chunk_manual(text, source="manual.pdf")
    paths = {c.metadata["chapter_path"] for c in chunks}
    assert "手册/安装" in paths or "手册/安装/电源" in paths
    assert any(c.metadata["chapter_path"] == "手册/调试" for c in chunks)
    long_chunks = [c for c in chunks if estimate_tokens(c.text) > 200]
    assert long_chunks  # 超长 section 被窗口切分


@pytest.mark.unit
def test_manual_no_heading_single_chunk():
    chunks = chunk_manual("纯文本手册内容", source="m.pdf")
    assert len(chunks) == 1 and chunks[0].metadata["chapter_path"] == ""


@pytest.mark.unit
def test_standard_clause_split():
    text = "5G 传输标准前言\n3.1.1 光功率要求 光功率应满足 X\n3.1.2 告警上报 告警应在 1s 内上报"
    chunks = chunk_standard(text, standard_no="3GPP TS 38.101", version="v17")
    clauses = [c.metadata["clause"] for c in chunks]
    assert "preface" in clauses and "3.1.1" in clauses and "3.1.2" in clauses
    assert all(c.metadata["standard_no"] == "3GPP TS 38.101" for c in chunks)


@pytest.mark.unit
def test_sop_step_level_split():
    """设计报告 §4.2.3：告警 SOP 按步骤分块，每步一个 chunk（标题上下文携带）。"""
    sop = "1. 前置检查\n步骤 1 确认告警\n步骤 2 定位传输段\n步骤 3 环回测试\n步骤 4 更换模块"
    chunks = chunk_sop(sop, scene="传输中断", network_tech="传输")
    assert len(chunks) == 5  # 1 导语块（适用范围）+ 4 步骤块
    assert [c.metadata["step_index"][0] for c in chunks] == [0, 1, 2, 3, 4]
    assert all("步骤" in c.text for c in chunks[1:])
    assert "前置检查" in chunks[0].text  # 导语块保留范围说明


def test_sop_fallback_single_when_no_steps():
    chunks = chunk_sop("无步骤结构的普通说明文档内容", scene="传输中断", network_tech="传输")
    assert len(chunks) == 1


@pytest.mark.unit
def test_faq_skips_empty():
    rows = [{"question": "5G基站传输中断排查步骤？", "answer": "按 SOP 排查…", "network_tech": "5G"},
            {"question": "", "answer": "无效行"}]
    chunks = chunk_faq(rows)
    assert len(chunks) == 1
    assert chunks[0].text == "5G基站传输中断排查步骤？"
    assert chunks[0].metadata["answer"].startswith("按 SOP")


@pytest.mark.unit
def test_table_split_repeats_header():
    header = "| 设备 | 参数 | 值 |\n|---|---|---|"
    body = "\n".join(f"| BBU{i} | p | v |" for i in range(75))
    chunks = chunk_table(f"{header}\n{body}", source="cfg.md")
    assert len(chunks) == 3  # 75 行 / 30 → 3 块
    assert all(c.text.startswith("| 设备 | 参数 | 值 |") for c in chunks)


@pytest.mark.unit
def test_window_overlap():
    text = "句子。" * 600
    pieces = split_by_window(text, max_tokens=100, overlap_tokens=10)
    assert len(pieces) > 1
    assert all(estimate_tokens(p) <= 130 for p in pieces)  # 上限+overlap 余量
