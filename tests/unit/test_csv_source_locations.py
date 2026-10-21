import copy
import csv
import pytest
from qa_core.indexing.parsers.locations import locate_chunks
from qa_core.indexing.parsers.parsers import parse_dispatch
from qa_core.indexing.chunkers.registry import chunk_faq, chunk_ticket


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def test_faq_multiline_record_and_answer_are_bound(tmp_path):
    path = tmp_path / "faq_example.csv"
    write_csv(path, [{"source": "faq-1", "question": "同一问题？", "answer": "答案一\n第二行"},
                     {"source": "faq-2", "question": "同一问题？", "answer": "另一答案"}])
    chunks = chunk_faq(parse_dispatch("faq", path))
    positions = locate_chunks(path, chunks)
    first = positions[0]["matches"][0][0]
    assert first == {"kind": "csv_record", "record": 1, "line_start": 2, "line_end": 3, "columns": ["question", "answer"]}
    assert positions[1]["matches"][0][0]["line_start"] == 4
    forged = copy.deepcopy(chunks[0]); forged.metadata["answer"] = "另一答案"
    assert locate_chunks(path, [forged]) == [None]


def test_ticket_sanitization_and_reconstruction(tmp_path):
    path = tmp_path / "fault_case_example.csv"
    write_csv(path, [{"ticket_id": "SIM-1", "title": "故障", "phenomenon": "地址192.0.2.1不可达",
                     "root_cause": "模拟链路断开", "resolution": "仅模拟恢复"}])
    chunks = chunk_ticket(parse_dispatch("fault_case", path))
    assert "{IP}" in chunks[0].text
    assert locate_chunks(path, chunks)[0]["matches"][0][0]["record"] == 1
    forged = copy.deepcopy(chunks[0]); forged.text += "没有的结论"
    assert locate_chunks(path, [forged]) == [None]


def test_duplicate_csv_records_are_ambiguous(tmp_path):
    path = tmp_path / "faq_example.csv"
    row = {"source": "faq-1", "question": "问？", "answer": "答"}
    write_csv(path, [row, row])
    result = locate_chunks(path, chunk_faq(parse_dispatch("faq", path)))
    assert all(r["ambiguous"] and len(r["matches"]) == 2 for r in result)


def test_missing_row_source_uses_the_actual_file(tmp_path):
    path = tmp_path / "faq_example.csv"
    write_csv(path, [{"question": "问？", "answer": "答"}])
    chunks = chunk_faq(parse_dispatch("faq", path))
    chunks[0].metadata["source"] = path.name
    assert locate_chunks(path, chunks)[0]["matches"][0][0]["record"] == 1


@pytest.mark.parametrize("body", ['question,question,answer\na,b,c\n', 'question,answer\na,b,c\n'])
def test_malformed_csv_is_not_assigned_a_guessed_location(tmp_path, body):
    path = tmp_path / "faq_example.csv"; path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError): locate_chunks(path, [])
