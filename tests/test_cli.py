import argparse
from datetime import datetime, timedelta, timezone

from issue_scout.cli import build_parser, color, wants_color
from issue_scout.scout import Issue, keep_issue, lit


def _args(**kw):
    base = dict(no_color=False, color="auto")
    base.update(kw)
    return argparse.Namespace(**base)


def test_color_wraps_only_when_enabled():
    assert color("hi", "32", False) == "hi"
    assert color("hi", "32", True) == "\033[32mhi\033[0m"


def test_wants_color_flags():
    assert wants_color(_args(color="always"))
    assert not wants_color(_args(color="never"))
    assert not wants_color(_args(color="always", no_color=True))


def test_lit_escapes_graphql_strings():
    assert lit('say "hi"') == '"say \\"hi\\""'
    assert lit("a\nb") == '"a\\nb"'


def _issue(days_old=1, comments=0, assigned=False, open_prs=None):
    created = datetime.now(timezone.utc) - timedelta(days=days_old)
    return Issue("o/r", 1, "t", "u", created, comments, assigned, [], open_prs=open_prs or [])


def test_keep_issue_filters_age_and_comments():
    kw = dict(max_age_days=7, max_comments=2, include_claimed=False)
    assert keep_issue(_issue(), **kw)
    assert not keep_issue(_issue(days_old=10), **kw)
    assert not keep_issue(_issue(comments=3), **kw)


def test_keep_issue_claimed_only_when_asked():
    claimed = _issue(assigned=True)
    assert not keep_issue(claimed, max_age_days=7, max_comments=2, include_claimed=False)
    assert keep_issue(claimed, max_age_days=7, max_comments=2, include_claimed=True)
    assert _issue(open_prs=["#5"]).claimed


def test_parser_accepts_repos_and_repeatable_languages():
    a = build_parser().parse_args(["owner/repo", "-l", "python", "-l", "rust"])
    assert a.repos == ["owner/repo"]
    assert a.language == ["python", "rust"]
