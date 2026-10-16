"""Command-line interface for Issue Scout."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

from .scout import RepoReport, scan_repo

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
HIDDEN_POLICIES = {"banned", "closed"}

COLORS = {
    "welcome": "32", "mentions-ai": "36", "none": "37", "approval-first": "33",
    "strict": "35", "banned": "31", "closed": "31",
}


def color(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="issue-scout",
        description="Find fresh, unclaimed GitHub issues in projects whose AI policy fits you.",
    )
    p.add_argument("repos", nargs="*", help="repositories as owner/name")
    p.add_argument("--preset", choices=sorted(PRESETS), action="append",
                   help="add a built-in list of popular repos (repeatable)")
    p.add_argument("--days", type=float, default=30, help="only issues newer than this (default 30)")
    p.add_argument("--max-comments", type=int, default=2, help="skip busier issues (default 2)")
    p.add_argument("--fetch", type=int, default=40, help="newest issues to look at per repo (default 40)")
    p.add_argument("--top", type=int, default=5, help="issues to show per repo (default 5)")
    p.add_argument("--include-claimed", action="store_true", help="also show issues with open PRs")
    p.add_argument("--all-policies", action="store_true",
                   help="also show repos that ban AI or take no outside PRs")
    p.add_argument("--policy-only", action="store_true", help="only report each repo's AI policy")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--no-color", action="store_true")
    return p


def report_to_dict(r: RepoReport) -> dict:
    return {
        "repo": r.repo,
        "stars": r.stars,
        "error": r.error,
        "policy": {"verdict": r.policy.verdict, "note": r.policy.note,
                   "source": r.policy.source, "evidence": r.policy.evidence},
        "issues": [
            {"number": i.number, "title": i.title, "url": i.url, "score": i.score,
             "age_days": round(i.age_days, 1), "comments": i.comments, "labels": i.labels,
             "open_prs": i.open_prs, "closed_prs": i.closed_prs, "reasons": i.reasons}
            for i in r.issues
        ],
    }


def print_text(reports: list[RepoReport], args: argparse.Namespace) -> None:
    use_color = not args.no_color and sys.stdout.isatty()
    width = shutil.get_terminal_size((100, 20)).columns
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
        if not args.all_policies and r.policy.verdict in HIDDEN_POLICIES:
            print("  (issues hidden; use --all-policies to show)")
            continue
        if r.closed_pr_count >= 3:
            print(color(f"  warning: {r.closed_pr_count} recent PRs on these issues were closed unmerged; "
                        "the project may be closing drive-by or agent-made PRs", "33", use_color))
        if not r.issues:
            print("  no fresh unclaimed issues")
            continue
        for i in r.issues[: args.top]:
            meta = f"{i.age_days:.0f}d old, {i.comments} comments"
            if i.reasons:
                meta += ", " + ", ".join(i.reasons)
            print(f"  {i.score:>5}  #{i.number} {i.title[: width - 20]}")
            print(f"         {meta}")
            print(f"         {i.url}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repos = list(args.repos)
    for preset in args.preset or []:
        repos += PRESETS[preset]
    repos = list(dict.fromkeys(repos))
    if not repos:
        print("Give one or more owner/name repos, or --preset python / --preset js.", file=sys.stderr)
        return 2
    if not shutil.which("gh"):
        print("Issue Scout needs the GitHub CLI (gh), logged in with `gh auth login`.", file=sys.stderr)
        return 2

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(
            lambda repo: scan_repo(
                repo, fetch=args.fetch, max_age_days=args.days,
                max_comments=args.max_comments, include_claimed=args.include_claimed,
            ),
            repos,
        ))
    reports.sort(key=lambda r: (r.error is not None, POLICY_ORDER[r.policy.verdict],
                                -(r.issues[0].score if r.issues else -99)))

    if args.json:
        print(json.dumps([report_to_dict(r) for r in reports], indent=2))
    else:
        print_text(reports, args)
    return 1 if all(r.error for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
