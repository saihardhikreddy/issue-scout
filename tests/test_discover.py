from issue_scout.discover import DiscoverOptions, build_issue_queries, build_repo_queries, cap_per_repo
from issue_scout.scout import Issue


def test_repo_queries_include_filters():
    opts = DiscoverOptions(languages=["python", "C++"], topics=["machine learning"], min_stars=1000)
    queries = build_repo_queries(opts)
    assert len(queries) == 2
    for q in queries:
        assert "stars:>=1000" in q and "language:python" in q and "language:C++" in q
        assert 'topic:"machine learning"' in q and "archived:false" in q
    assert "good-first-issues:>0" in queries[0] and "help-wanted-issues:>0" in queries[1]


def test_issue_queries_one_per_label():
    opts = DiscoverOptions(labels=["good first issue", "bug"], max_comments=1, extra_query="org:psf")
    queries = build_issue_queries(opts)
    assert len(queries) == 2
    assert 'label:"good first issue"' in queries[0] and "label:bug" in queries[1]
    for q in queries:
        assert "-linked:pr" in q and "no:assignee" in q and "comments:<=1" in q and "org:psf" in q


def test_issue_query_without_labels():
    assert len(build_issue_queries(DiscoverOptions(labels=[]))) == 1


def _issue(repo, n):
    from datetime import datetime, timezone
    return Issue(repo, n, "t", f"u{repo}{n}", datetime.now(timezone.utc), 0, False, [])


def test_cap_per_repo_keeps_order():
    issues = [_issue("a/a", 1), _issue("a/a", 2), _issue("b/b", 3), _issue("a/a", 4)]
    assert [i.number for i in cap_per_repo(issues, 2)] == [1, 2, 3]
    assert len(cap_per_repo(issues, 0)) == 4


def test_mostly_latin():
    from issue_scout.discover import mostly_latin

    assert mostly_latin("Crash when opening settings")
    assert mostly_latin("Café crashes on résumé upload")
    assert not mostly_latin("Claude Desktop无法使用gemini模型")
    assert mostly_latin("1234 !!")
