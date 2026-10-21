import json

from scripts.screen_holdout_source_overlap import normalized, screen, tree_digest, windows


def test_text_normalization_removes_urls_but_keeps_protocol_terms():
    assert normalized("ＢＦＤ  https://example.com/a  检测时间") == "bfd 检测时间"


def test_windows_cover_short_and_long_text():
    assert windows("BFD") == [(0, "bfd")]
    parts = windows("检测时间" * 100)
    assert parts[0][0] == 0
    assert parts[1][0] == 300
    assert parts[-1][1]


def test_tree_digest_is_stable_and_content_bound(tmp_path):
    first = tmp_path / "a.md"
    first.write_text("first", encoding="utf-8")
    (tmp_path / "meta.json").write_text("ignored", encoding="utf-8")
    before = tree_digest(tmp_path)
    assert before[0] == 1
    first.write_text("changed", encoding="utf-8")
    assert tree_digest(tmp_path)[1] != before[1]


def test_screen_surfaces_direct_reuse_without_calling_a_model(tmp_path):
    source_dir = tmp_path / "new"
    raw_dir = tmp_path / "raw"
    protocol_dir = tmp_path / "protocol"
    for directory in (source_dir, raw_dir, protocol_dir):
        directory.mkdir()
    (source_dir / "bfd.md").write_text("# BFD\n\n依据\n\nBFD 检测时间由对端倍数决定。\n异步模式。\n", encoding="utf-8")
    (source_dir / "netconf.md").write_text("# NETCONF\n\n依据\n\nNETCONF 候选配置锁。\n提交配置。\n", encoding="utf-8")
    (raw_dir / "same.md").write_text("BFD 检测时间由对端倍数决定。异步模式。", encoding="utf-8")
    (raw_dir / "other.md").write_text("HTTP 503 暂时无法处理。", encoding="utf-8")
    for name in ("v3.json", "r3.json"):
        (tmp_path / name).write_text(json.dumps({"cases": [{"id": "1", "query": "测试"}]}), encoding="utf-8")
    result = screen(source_dir, raw_dir, protocol_dir, tmp_path / "v3.json", tmp_path / "r3.json", top_k=1)
    assert result["matches"]["bfd.md"][0]["source"] == "same.md"
    assert result["matches"]["bfd.md"][0]["cosine"] > 0
