from datetime import datetime, timedelta, timezone

from issue_scout.scout import classify_policy, looks_like_symlink, parse_issue, score_issue


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def node(number=1, days_ago=1.0, comments=0, labels=(), timeline=(), linked=0, assignees=0):
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "createdAt": iso(days_ago),
        "comments": {"totalCount": comments},
        "assignees": {"totalCount": assignees},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "closedByPullRequestsReferences": {"totalCount": linked},
        "timelineItems": {"nodes": list(timeline)},
    }


def pr(number, state):
    return {"source": {"number": number, "state": state, "url": f"https://github.com/o/r/pull/{number}"}}


# --- policy classification ---------------------------------------------------

def test_policy_banned():
    text = "# NO AI / NO LLM POLICY\nThis project strictly forbids the usage of LLMs, agents, or any other AI tools."
    assert classify_policy({"CONTRIBUTING.md": text}).verdict == "banned"


def test_policy_closed_beats_welcome():
    text = ("We welcome the thoughtful use of AI tools.\n"
            "We have **paused accepting pull requests from outside the maintainer team.**")
    assert classify_policy({"CONTRIBUTING.md": text}).verdict == "closed"


def test_policy_partial_pr_restriction_is_not_closed():
    text = "Prettier is not accepting pull requests that add new formatting options."
    assert classify_policy({"CONTRIBUTING.md": text}).verdict == "none"


def test_policy_approval_first():
    text = "- The Pull Request must link to a issue or discussion where a solution has been approved by a maintainer."
    policy = classify_policy({"AI_POLICY.md": text})
    assert policy.verdict == "approval-first"
    assert policy.source == "AI_POLICY.md"


def test_policy_welcome_beats_strict_wording():
    text = ("We welcome contributions from all developers, including those who use AI/LLM tools\n"
            "With the advent of unsupervised agentic tools like OpenClaw, we ask for effort.")
    assert classify_policy({"CONTRIBUTING.rst": text}).verdict == "welcome"


def test_policy_strict():
    text = "- Absolutely no unsupervised agentic tools like OpenClaw.\n- Accounts will be permanently banned."
    assert classify_policy({"AI_POLICY.md": text}).verdict == "strict"


def test_policy_welcome_phrasings():
    for text in (
        "We welcome the use of AI when contributing to Pydantic.",
        "The use of AI tools to interact with this project is welcome under the following principles:",
        "You may use AI tools when contributing to Starlette.",
        "ESLint welcomes contributions from everyone, including those created with the assistance "
        "of Artificial Intelligence (AI) tools.",
    ):
        assert classify_policy({"CONTRIBUTING.md": text}).verdict == "welcome", text


def test_symlink_detection():
    assert looks_like_symlink("docs/contributing.md")
    assert looks_like_symlink("../CONTRIBUTING.rst\n")
    assert not looks_like_symlink("# Contributing\n\nThanks for helping.md")
    assert not looks_like_symlink("")


def test_policy_none_and_mentions():
    assert classify_policy({}).verdict == "none"
    assert classify_policy({"CONTRIBUTING.md": "Run the tests first."}).verdict == "none"
    assert classify_policy({"CONTRIBUTING.md": "Please disclose Copilot usage."}).verdict == "mentions-ai"


# --- issue parsing -----------------------------------------------------------

def test_open_pr_marks_issue_claimed():
    issue = parse_issue("o/r", node(timeline=[pr(5, "OPEN"), pr(6, "CLOSED")]))
    assert issue.claimed
    assert issue.closed_prs == ["https://github.com/o/r/pull/6"]


def test_merged_pr_counts_as_claimed():
    assert parse_issue("o/r", node(timeline=[pr(5, "MERGED")])).claimed


def test_only_closed_prs_is_unclaimed():
    issue = parse_issue("o/r", node(timeline=[pr(7, "CLOSED"), pr(7, "CLOSED")]))
    assert not issue.claimed
    assert len(issue.closed_prs) == 1  # duplicates collapsed


def test_linked_pr_or_assignee_claims():
    assert parse_issue("o/r", node(linked=1)).claimed
    assert parse_issue("o/r", node(assignees=1)).claimed


def test_non_pr_cross_reference_ignored():
    issue = parse_issue("o/r", node(timeline=[{"source": {}}, {}]))
    assert not issue.claimed


# --- scoring -----------------------------------------------------------------

def test_scoring_prefers_fresh_quiet_beginner_bugs():
    good = parse_issue("o/r", node(days_ago=1, labels=["good first issue", "bug"]))
    old = parse_issue("o/r", node(days_ago=40, comments=2))
    decision = parse_issue("o/r", node(days_ago=1, labels=["needs-decision"]))
    for issue in (good, old, decision):
        score_issue(issue)
    assert good.score > old.score
    assert good.score > decision.score
    assert "needs a maintainer decision" in decision.reasons


def test_discussion_titles_rank_lower():
    bug = parse_issue("o/r", node(days_ago=1, labels=["bug"]))
    talk = parse_issue("o/r", node(days_ago=1, labels=["bug"]))
    talk.title = "Return type inference: your feedback"
    for issue in (bug, talk):
        score_issue(issue)
    assert bug.score > talk.score


def test_policy_and_stars_raise_score():
    from issue_scout.scout import Policy

    plain = parse_issue("o/r", node())
    popular = parse_issue("o/r", node())
    popular.stars, popular.policy = 50_000, Policy("welcome")
    strict = parse_issue("o/r", node())
    strict.policy = Policy("strict")
    for issue in (plain, popular, strict):
        score_issue(issue)
    assert popular.score > plain.score > strict.score


# --- policy cache ------------------------------------------------------------

def test_failed_policy_lookup_is_not_cached(tmp_path, monkeypatch):
    from issue_scout import scout

    monkeypatch.setattr(scout, "CACHE_DIR", tmp_path)

    def failing(query, variables=None, **kw):
        raise scout.GhError("HTTP 502")

    monkeypatch.setattr(scout, "graphql", failing)
    assert scout.fetch_policies(["o/r"])["o/r"].verdict == "none"
    assert "o/r" not in scout._load_cache()

    welcome = {"r0": {"isArchived": False, "f0": {"text": "AI-assisted contributions are welcome."}}}
    monkeypatch.setattr(scout, "graphql", lambda query, variables=None, **kw: welcome)
    assert scout.fetch_policies(["o/r"])["o/r"].verdict == "welcome"
    assert scout._load_cache()["o/r"]["verdict"] == "welcome"


def test_stale_or_broken_cache_entries_are_refetched(tmp_path, monkeypatch):
    import json
    import time

    from issue_scout import scout

    monkeypatch.setattr(scout, "CACHE_DIR", tmp_path)
    (tmp_path / "policies.json").write_text(json.dumps({
        "o/old": {"at": time.time(), "verdict": "renamed-in-a-later-version"},
        "o/hand": {"verdict": "welcome"},          # hand-edited, no timestamp
        "o/ok": {"at": time.time(), "verdict": "welcome", "source": "AI.md", "evidence": "yes"},
    }))
    asked = []

    def fake(query, variables=None, **kw):
        asked.append(query)
        return {"r0": {"isArchived": False}, "r1": {"isArchived": False}}

    monkeypatch.setattr(scout, "graphql", fake)
    got = scout.fetch_policies(["o/old", "o/hand", "o/ok"])  # used to raise KeyError
    assert got["o/ok"].verdict == "welcome" and got["o/old"].verdict == "none"
    assert len(asked) == 1 and '"o"' in asked[0] and '"old"' in asked[0] and '"ok"' not in asked[0]
    assert set(scout._load_cache()) == {"o/old", "o/hand", "o/ok"}
    assert not (tmp_path / "policies.json.tmp").exists()

    (tmp_path / "policies.json").write_text("[]")  # valid JSON, wrong shape
    assert scout._load_cache() == {}


# --- gh CLI wrapper ----------------------------------------------------------

def _fake_run(returncode, stdout, stderr=""):
    import subprocess

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return run


def test_graphql_returns_data(monkeypatch):
    from issue_scout import scout

    monkeypatch.setattr(scout.subprocess, "run", _fake_run(0, '{"data": {"viewer": {"login": "me"}}}'))
    assert scout.graphql("{ viewer { login } }") == {"viewer": {"login": "me"}}


def test_graphql_garbled_output_raises_gh_error(monkeypatch):
    import pytest

    from issue_scout import scout

    monkeypatch.setattr(scout.subprocess, "run", _fake_run(1, '{"data": {"repo', "connection reset"))
    with pytest.raises(scout.GhError, match="connection reset"):
        scout.graphql("{ viewer { login } }", retries=0)


def test_negated_labels_do_not_count():
    plain = parse_issue("o/r", node())
    unconfirmed = parse_issue("o/r", node(labels=["status: unconfirmed"]))
    unblocked = parse_issue("o/r", node(labels=["unblocked"]))
    confirmed = parse_issue("o/r", node(labels=["confirmed"]))
    for issue in (plain, unconfirmed, unblocked, confirmed):
        score_issue(issue)
    assert unconfirmed.score == plain.score
    assert unblocked.score == plain.score
    assert confirmed.score > plain.score
