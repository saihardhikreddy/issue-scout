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


def _issue_node(url, topics):
    return {
        "url": url, "number": 1, "title": "Fix the thing", "createdAt": "2026-01-01T00:00:00Z",
        "repository": {
            "nameWithOwner": "o/r", "stargazerCount": 5000, "isArchived": False, "isFork": False,
            "primaryLanguage": {"name": "Python"},
            "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]},
        },
    }


def test_issue_search_respects_topics(monkeypatch):
    from issue_scout import discover

    seen = {}

    def fake_search(query, kind, limit, node_fields):
        seen["fields"] = node_fields
        return [_issue_node("https://x/1", ["cli", "python"]), _issue_node("https://x/2", ["web"])]

    monkeypatch.setattr(discover, "_paged_search", fake_search)
    monkeypatch.setattr(discover, "parse_issue", lambda repo, node: Issue(
        repo=repo, number=1, title="t", url=node["url"], created=None, comments=0, assigned=False, labels=[]))
    issues = discover.search_issues(DiscoverOptions(topics=["CLI"], labels=[]))
    assert [i.url for i in issues] == ["https://x/1"]
    assert "repositoryTopics" in seen["fields"]


def test_has_topics_without_filter():
    from issue_scout.discover import has_topics

    assert has_topics({}, [])
    assert not has_topics({}, ["cli"])


def _labelled(url, labels):
    from datetime import datetime, timezone
    return Issue("o/r", 1, "Fix the thing", url, datetime.now(timezone.utc), 0, False, labels)


def _discover_with(monkeypatch, opts):
    from issue_scout import discover
    from issue_scout.scout import Policy

    repo_first = [_labelled("https://x/1", ["Good First Issue"]), _labelled("https://x/2", []),
                  _labelled("https://x/3", ["enhancement"])]
    monkeypatch.setattr(discover, "search_repos", lambda o: ["o/r"])
    monkeypatch.setattr(discover, "scan_repos_batched", lambda repos, o: repo_first)
    monkeypatch.setattr(discover, "search_issues", lambda o: [])
    monkeypatch.setattr(discover, "fetch_policies", lambda repos: {r: Policy("none") for r in repos})
    return sorted(i.url for i in discover.discover(opts))


def test_explicit_labels_also_filter_repo_first_results(monkeypatch):
    opts = DiscoverOptions(labels=["good first issue"], require_labels=True, per_repo=0)
    assert _discover_with(monkeypatch, opts) == ["https://x/1"]


def test_default_labels_keep_repo_first_results(monkeypatch):
    assert len(_discover_with(monkeypatch, DiscoverOptions(per_repo=0))) == 3
    assert len(_discover_with(monkeypatch, DiscoverOptions(labels=[], require_labels=True, per_repo=0))) == 3
