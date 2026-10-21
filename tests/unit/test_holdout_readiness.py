from scripts.audit_holdout_readiness import canonical_query, exact_query_links


def test_query_normalization_detects_formatting_only_overlap():
    assert canonical_query("HTTP 429，等待 120 秒？") == canonical_query("http-429 等待１２０秒")


def test_exact_links_only_report_cross_suite_overlap():
    suites = {"old": [{"id": "A", "query": "HTTP 429？"},
                      {"id": "B", "query": "HTTP 429?"}],
              "new": [{"id": "C", "query": "http-429"},
                      {"id": "D", "query": "DNS 负缓存"}]}
    links = exact_query_links(suites)
    assert len(links) == 1
    assert {member["case_id"] for member in links[0]["members"]} == {"A", "B", "C"}
