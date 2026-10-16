"""Command-line interface for Issue Scout."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

from .discover import DEFAULT_LABELS, DiscoverOptions, discover
from .scout import EXCLUDED_POLICIES, Issue, RepoReport, scan_repo

PRESETS = {
    "python": [
        "pytest-dev/pytest", "pydantic/pydantic", "sqlfluff/sqlfluff", "Kludex/starlette",
        "encode/uvicorn", "Delgan/loguru", "Textualize/rich", "Textualize/textual",
        "streamlit/streamlit", "httpie/cli", "tox-dev/tox", "pre-commit/pre-commit",
        "PyCQA/pylint", "aio-libs/aiohttp", "tqdm/tqdm",
    ],
    "js": [
        "eslint/eslint", "vitejs/vite", "prettier/prettier", "date-fns/date-fns",
        "jupyterlab/jupyterlab", "sindresorhus/got",
    ],
}

POLICY_ORDER = {
    "welcome": 0, "mentions-ai": 1, "none": 2, "approval-first": 3,
    "strict": 4, "banned": 5, "closed": 6,
}

COLORS = {
    "welcome": "32", "mentions-ai": "36", "none": "37", "approval-first": "33",
    "strict": "35", "banned": "31", "closed": "31",
}

EPILOG = """\
modes:
  issue-scout                          search all of GitHub (default)
  issue-scout -l python -l rust        ...only these languages
  issue-scout --topic cli --min-stars 2000
  issue-scout owner/repo other/repo    scan just these repositories
  issue-scout --preset python          scan a built-in list of popular projects
"""


def color(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="issue-scout",
        description="Find fresh, unclaimed GitHub issues in projects whose AI policy fits you.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("repos", nargs="*", help="repositories as owner/name (omit to search all of GitHub)")
    p.add_argument("--preset", choices=sorted(PRESETS), action="append",
                   help="add a built-in list of popular repos (repeatable)")

    g = p.add_argument_group("search all of GitHub")
    g.add_argument("-l", "--language", action="append", default=[],
                   help="repository language, e.g. python (repeatable)")
    g.add_argument("--topic", action="append", default=[], help="repository topic, e.g. cli (repeatable)")
    g.add_argument("--label", action="append",
                   help=f"issue label to search for (repeatable; default: {', '.join(DEFAULT_LABELS)}; "
                        "'any' for no label filter)")
    g.add_argument("--min-stars", type=int, default=500, help="skip smaller projects (default 500)")
    g.add_argument("--repo-limit", type=int, default=60, help="active repositories to scan (default 60)")
    g.add_argument("--search-limit", type=int, default=300, help="issues to pull from issue search (default 300)")
    g.add_argument("--strategy", choices=["both", "repos", "issues"], default="both",
                   help="repo-first, issue-first, or both (default both)")
    g.add_argument("--any-script", action="store_true",
                   help="keep issues whose titles are mostly in non-Latin scripts (skipped by default)")
    g.add_argument("--per-repo", type=int, default=3, help="max results from one project (default 3, 0 = no cap)")
    g.add_argument("--query", default="", help="extra GitHub search qualifiers, e.g. 'org:microsoft'")

    p.add_argument("--days", type=float, help="only issues newer than this (default 14 for search, 30 for repos)")
    p.add_argument("--max-comments", type=int, default=2, help="skip busier issues (default 2)")
    p.add_argument("--fetch", type=int, default=40, help="newest issues to look at per named repo (default 40)")
    p.add_argument("--top", type=int, help="results to show (default 25 for search, 5 per repo)")
    p.add_argument("--include-claimed", action="store_true", help="also show issues with open PRs")
    p.add_argument("--all-policies", action="store_true",
                   help="also show projects that ban AI or take no outside PRs")
    p.add_argument("--policy-only", action="store_true", help="only report each named repo's AI policy")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--no-color", action="store_true")
    return p


def policy_dict(policy) -> dict:
    return {"verdict": policy.verdict, "note": policy.note,
            "source": policy.source, "evidence": policy.evidence}


def issue_dict(i: Issue) -> dict:
    d = {"repo": i.repo, "number": i.number, "title": i.title, "url": i.url, "score": i.score,
         "age_days": round(i.age_days, 1), "comments": i.comments, "labels": i.labels,
         "stars": i.stars, "language": i.language, "open_prs": i.open_prs,
         "closed_prs": i.closed_prs, "reasons": i.reasons}
    if i.policy is not None:
        d["policy"] = policy_dict(i.policy)
    return d


def report_to_dict(r: RepoReport) -> dict:
    return {"repo": r.repo, "stars": r.stars, "error": r.error, "policy": policy_dict(r.policy),
            "issues": [issue_dict(i) for i in r.issues]}


def print_reports(reports: list[RepoReport], args: argparse.Namespace) -> None:
    use_color = not args.no_color and sys.stdout.isatty()
    width = shutil.get_terminal_size((100, 20)).columns
    top = args.top or 5
    for r in reports:
        if r.error:
            print(color(f"{r.repo}: {r.error}", "31", use_color))
            continue
        verdict = color(r.policy.verdict.upper(), COLORS[r.policy.verdict], use_color)
        print(f"\n{color(r.repo, '1', use_color)}  ({r.stars:,} stars)  {verdict}: {r.policy.note}")
        if r.policy.evidence:
            print(f"  policy: \"{r.policy.evidence[: width - 14]}\" [{r.policy.source}]")
        if args.policy_only:
            continue
        if not args.all_policies and r.policy.verdict in EXCLUDED_POLICIES:
            print("  (issues hidden; use --all-policies to show)")
            continue
        if r.closed_pr_count >= 3:
            print(color(f"  warning: {r.closed_pr_count} recent PRs on these issues were closed unmerged; "
                        "the project may be closing drive-by or agent-made PRs", "33", use_color))
        if not r.issues:
            print("  no fresh unclaimed issues")
            continue
        for i in r.issues[:top]:
            meta = f"{i.age_days:.0f}d old, {i.comments} comments"
            if i.reasons:
                meta += ", " + ", ".join(i.reasons)
            print(f"  {i.score:>5}  #{i.number} {i.title[: width - 20]}")
            print(f"         {meta}")
            print(f"         {i.url}")


def print_discoveries(issues: list[Issue], args: argparse.Namespace) -> None:
    use_color = not args.no_color and sys.stdout.isatty()
    width = shutil.get_terminal_size((100, 20)).columns
    shown = issues[: args.top or 25]
    if not shown:
        print("No fresh, unclaimed issues matched. Try more --days, a lower --min-stars, or --label any.")
        return
    closed_by_repo: dict[str, int] = {}
    for i in issues:
        closed_by_repo[i.repo] = closed_by_repo.get(i.repo, 0) + len(i.closed_prs)
    print(f"\nTop {len(shown)} of {len(issues)} fresh, unclaimed issues\n")
    for rank, i in enumerate(shown, 1):
        verdict = color(i.policy.verdict.upper(), COLORS[i.policy.verdict], use_color)
        lang = f", {i.language}" if i.language else ""
        print(f"{rank:>3}. {color(i.title[: width - 6], '1', use_color)}")
        print(f"     {i.repo} #{i.number}  ({i.stars:,} stars{lang})  score {i.score}  {verdict}")
        meta = f"{i.age_days:.0f}d old, {i.comments} comments"
        if i.reasons:
            meta += ", " + ", ".join(i.reasons)
        print(f"     {meta}")
        if closed_by_repo.get(i.repo, 0) >= 3:
            print(color(f"     warning: {closed_by_repo[i.repo]} recent PRs in this project were closed "
                        "unmerged; it may be closing drive-by or agent-made PRs", "33", use_color))
        print(f"     {i.url}")
    print("\nPolicy verdicts come from keyword rules. Read the project's guidelines before starting.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # titles can hold any character
    if not shutil.which("gh"):
        print("Issue Scout needs the GitHub CLI (gh), logged in with `gh auth login`.", file=sys.stderr)
        return 2

    repos = list(args.repos)
    for preset in args.preset or []:
        repos += PRESETS[preset]
    repos = list(dict.fromkeys(repos))

    if not repos:
        labels = args.label if args.label is not None else list(DEFAULT_LABELS)
        if any(label.lower() == "any" for label in labels):
            labels = []
        opts = DiscoverOptions(
            languages=args.language, topics=args.topic, labels=labels,
            min_stars=args.min_stars, max_age_days=args.days or 14,
            max_comments=args.max_comments, repo_limit=args.repo_limit,
            issue_search_limit=args.search_limit, include_claimed=args.include_claimed,
            include_excluded=args.all_policies, extra_query=args.query, per_repo=args.per_repo,
            latin_titles_only=not args.any_script,
            strategies=("repos", "issues") if args.strategy == "both" else (args.strategy,),
        )
        issues = discover(opts)
        if args.json:
            print(json.dumps([issue_dict(i) for i in issues[: args.top or len(issues)]], indent=2))
        else:
            print_discoveries(issues, args)
        return 0

    days = args.days or 30
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(
            lambda repo: scan_repo(
                repo, fetch=args.fetch, max_age_days=days,
                max_comments=args.max_comments, include_claimed=args.include_claimed,
            ),
            repos,
        ))
    reports.sort(key=lambda r: (r.error is not None, POLICY_ORDER[r.policy.verdict],
                                -(r.issues[0].score if r.issues else -99)))
    if args.json:
        print(json.dumps([report_to_dict(r) for r in reports], indent=2))
    else:
        print_reports(reports, args)
    return 1 if all(r.error for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
