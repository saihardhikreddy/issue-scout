"""Search all of GitHub for promising issues, not just a fixed list of projects.

Two searches run and their results are merged:

* Repo-first: find active, popular repositories (by language, topic and stars) that have
  beginner-friendly or help-wanted issues, then read their newest issues.
* Issue-first: search issues directly by label, language and freshness. This reaches
  smaller projects the repo search misses; results below the star threshold are dropped.

Every candidate is then checked for claims (open/linked/merged PRs, assignees), its project's
AI policy is read (cached on disk), and everything is ranked on one scale.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .scout import (
    EXCLUDED_POLICIES,
    ISSUE_FIELDS,
    REPO_FIELDS,
    GhError,
    Issue,
    Policy,
    _policy_file_fields,
    fetch_policies,
    graphql,
    keep_issue,
    lit,
    log,
    parse_issue,
    policy_from_repo_data,
    score_issue,
)

DEFAULT_LABELS = ["good first issue", "help wanted", "bug"]


@dataclass
class DiscoverOptions:
    languages: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=lambda: list(DEFAULT_LABELS))
    min_stars: int = 500
    max_age_days: float = 14
    max_comments: int = 2
    repo_limit: int = 60
    issue_search_limit: int = 300
    issues_per_repo: int = 25
    include_claimed: bool = False
    include_excluded: bool = False
    per_repo: int = 3
    latin_titles_only: bool = True
    extra_query: str = ""
    strategies: tuple[str, ...] = ("repos", "issues")


def _since(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def build_repo_queries(opts: DiscoverOptions) -> list[str]:
    base = [f"stars:>={opts.min_stars}", f"pushed:>={_since(opts.max_age_days)}",
            "archived:false", "fork:false", "is:public"]
    base += [f"language:{_quote(lang)}" for lang in opts.languages]
    base += [f"topic:{_quote(topic)}" for topic in opts.topics]
    if opts.extra_query:
        base.append(opts.extra_query)
    # Repo search can't OR these two qualifiers, so ask twice.
    return [" ".join(base + [q, "sort:stars"]) for q in ("good-first-issues:>0", "help-wanted-issues:>0")]


def build_issue_queries(opts: DiscoverOptions) -> list[str]:
    base = ["is:issue", "is:open", "no:assignee", "-linked:pr", "archived:false",
            f"created:>={_since(opts.max_age_days)}", f"comments:<={opts.max_comments}"]
    base += [f"language:{_quote(lang)}" for lang in opts.languages]  # several are OR'ed
    if opts.extra_query:
        base.append(opts.extra_query)
    base.append("sort:created-desc")
    if not opts.labels:
        return [" ".join(base)]
    # One query per label keeps each label's results from crowding out the others.
    return [" ".join(base + [f"label:{_quote(label)}"]) for label in opts.labels]


def _paged_search(query: str, kind: str, limit: int, node_fields: str) -> list[dict]:
    gql = (
        "query($q: String!, $first: Int!, $after: String) {"
        f" search(query: $q, type: {kind}, first: $first, after: $after) {{"
        " pageInfo { hasNextPage endCursor }"
        f" nodes {{ {node_fields} }} }} }}"
    )
    nodes: list[dict] = []
    after = None
    while len(nodes) < limit:
        page = min(50 if kind == "ISSUE" else 100, limit - len(nodes))
        try:
            data = graphql(gql, {"q": query, "first": page, "after": after})["search"]
        except GhError as exc:
            log(f"  search failed ({query}): {exc}")
            break
        nodes += [n for n in data["nodes"] if n]
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return nodes


def search_repos(opts: DiscoverOptions) -> list[str]:
    repos: dict[str, None] = {}
    per_query = max(1, opts.repo_limit // 2)
    for query in build_repo_queries(opts):
        log(f"searching repos: {query}")
        for node in _paged_search(query, "REPOSITORY", per_query, "... on Repository { nameWithOwner }"):
            repos[node["nameWithOwner"]] = None
    return list(repos)[: opts.repo_limit]


def _scan_chunk(chunk: list[str], opts: DiscoverOptions, since: str) -> list[Issue]:
    log(f"reading issues: {', '.join(chunk)}")
    parts = []
    for i, repo in enumerate(chunk):
        owner, _, name = repo.partition("/")
        parts.append(
            f"r{i}: repository(owner: {lit(owner)}, name: {lit(name)}) {{ {REPO_FIELDS} "
            f"{_policy_file_fields()} "
            f"issues(states: OPEN, first: {opts.issues_per_repo}, "
            "orderBy: {field: CREATED_AT, direction: DESC}, "
            f"filterBy: {{ since: {lit(since)} }}) {{ nodes {{ {ISSUE_FIELDS} }} }} }}"
        )
    try:
        data = graphql("{ " + " ".join(parts) + " }")
    except GhError as exc:
        log(f"  skipped {', '.join(chunk)}: {exc}")
        return []
    issues = []
    for i in range(len(chunk)):
        repo_data = data.get(f"r{i}")
        if not repo_data:
            continue
        repo = repo_data["nameWithOwner"]
        policy = policy_from_repo_data(repo, repo_data)
        for node in repo_data["issues"]["nodes"]:
            issue = parse_issue(repo, node)
            issue.stars = repo_data["stargazerCount"]
            issue.language = (repo_data.get("primaryLanguage") or {}).get("name")
            issue.policy = policy
            issues.append(issue)
    return issues


def scan_repos_batched(repos: list[str], opts: DiscoverOptions, batch: int = 5) -> list[Issue]:
    """Newest issues plus the policy for many repos, a few repos per request, in parallel."""
    since = (datetime.now(timezone.utc) - timedelta(days=opts.max_age_days)).isoformat()
    chunks = [repos[i:i + batch] for i in range(0, len(repos), batch)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = pool.map(lambda chunk: _scan_chunk(chunk, opts, since), chunks)
    return [issue for chunk_issues in results for issue in chunk_issues]


def search_issues(opts: DiscoverOptions) -> list[Issue]:
    node_fields = f"... on Issue {{ {ISSUE_FIELDS} repository {{ {REPO_FIELDS} }} }}"
    queries = build_issue_queries(opts)
    per_query = max(1, opts.issue_search_limit // len(queries))
    issues: list[Issue] = []
    for query in queries:
        log(f"searching issues: {query}")
        for node in _paged_search(query, "ISSUE", per_query, node_fields):
            repo = node["repository"]
            if repo["stargazerCount"] < opts.min_stars or repo["isArchived"] or repo["isFork"]:
                continue
            issue = parse_issue(repo["nameWithOwner"], node)
            issue.stars = repo["stargazerCount"]
            issue.language = (repo.get("primaryLanguage") or {}).get("name")
            issues.append(issue)
    return issues


def discover(opts: DiscoverOptions) -> list[Issue]:
    found: dict[str, Issue] = {}
    if "repos" in opts.strategies:
        repos = search_repos(opts)
        log(f"found {len(repos)} active repositories")
        for issue in scan_repos_batched(repos, opts):
            found.setdefault(issue.url, issue)
    if "issues" in opts.strategies:
        for issue in search_issues(opts):
            found.setdefault(issue.url, issue)

    candidates = [
        i for i in found.values()
        if keep_issue(i, max_age_days=opts.max_age_days, max_comments=opts.max_comments,
                      include_claimed=opts.include_claimed)
    ]
    if opts.latin_titles_only:
        candidates = [i for i in candidates if mostly_latin(i.title)]
    missing = sorted({i.repo for i in candidates if i.policy is None})
    if missing:
        log(f"reading AI policies for {len(missing)} more repositories")
        policies = fetch_policies(missing)
        for issue in candidates:
            if issue.policy is None:
                issue.policy = policies.get(issue.repo, Policy("none"))

    if not opts.include_excluded:
        candidates = [i for i in candidates if i.policy.verdict not in EXCLUDED_POLICIES]
    for issue in candidates:
        score_issue(issue)
    candidates.sort(key=lambda i: i.score, reverse=True)
    return cap_per_repo(candidates, opts.per_repo)


def mostly_latin(text: str) -> bool:
    """True when the title is mostly Latin script, so the issue is likely in English."""
    latin = other = 0
    for ch in text:
        if not ch.isalpha():
            continue
        if ch.isascii() or "À" <= ch <= "ɏ":
            latin += 1
        else:
            other += 3  # one CJK character carries about as much as a short word
    return latin + other == 0 or latin / (latin + other) >= 0.7


def cap_per_repo(issues: list[Issue], per_repo: int) -> list[Issue]:
    """Keep the list varied: at most per_repo issues from any one project (0 = no cap)."""
    if per_repo <= 0:
        return issues
    counts: dict[str, int] = {}
    kept = []
    for issue in issues:
        counts[issue.repo] = counts.get(issue.repo, 0) + 1
        if counts[issue.repo] <= per_repo:
            kept.append(issue)
    return kept
