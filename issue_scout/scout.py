"""Core logic: talk to GitHub, read contribution policies, parse and score issues."""

from __future__ import annotations

import json
import math
import os
import posixpath
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

POLICY_FILES = [
    "AI_POLICY.md",
    ".github/AI_POLICY.md",
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "CONTRIBUTING.rst",
    "docs/CONTRIBUTING.md",
]

# Order matters: the first verdict whose pattern matches wins.
POLICY_RULES = [
    (
        "closed",
        r"paused accepting pull requests|not accepting (outside |external )?(pull requests|contributions)(?! (that|which|for))",
    ),
    (
        "banned",
        r"(strictly )?forbids? the (usage|use) of (LLMs|AI)|no AI ?/ ?no LLM|"
        r"(do not|don't|cannot) accept (any )?(AI|LLM)[- ]generated|AI[- ]generated .{0,40}not (be )?accepted",
    ),
    (
        "approval-first",
        r"approved by a maintainer|must link to an? (issue|discussion) where",
    ),
    (
        "welcome",
        r"welcome[sd]? (the )?(thoughtful |responsible )?(use of )?(AI|LLM)|"
        r"(AI|LLM)[- ]assisted contributions are welcome|accepts? AI[- ]generated|"
        r"may use AI tools|AI tools .{0,60}(is|are) welcome|welcome the use of AI|welcomes? contributions .{0,80}(AI|Artificial Intelligence)",
    ),
    (
        "strict",
        r"unsupervised agentic|will be (permanently )?banned|discourage use of LLMs|"
        r"closed without (further )?(review|discussion)",
    ),
    ("mentions-ai", r"\bAI\b|\bLLMs?\b|generative|Copilot|ChatGPT"),
]

POLICY_NOTES = {
    "closed": "not taking outside PRs",
    "banned": "AI contributions banned",
    "approval-first": "needs maintainer sign-off on the approach before a PR",
    "strict": "AI tolerated but closely policed; low-effort PRs get closed",
    "welcome": "AI-assisted PRs welcome (you must understand and own the change)",
    "mentions-ai": "mentions AI; read the policy",
    "none": "no AI policy found",
}

# How much each verdict moves an issue up or down in a cross-project ranking.
POLICY_WEIGHT = {
    "welcome": 3, "mentions-ai": 0, "none": 0, "approval-first": -1,
    "strict": -3, "banned": -100, "closed": -100,
}
EXCLUDED_POLICIES = {"banned", "closed"}

SKIP_LABELS = re.compile(
    r"needs[- ]decision|needs[- ]design|proposal|rfc|question|discussion|wontfix|invalid|duplicate|"
    r"tracking|upstream|blocked|deferred|stale",
    re.I,
)
GOOD_LABELS = re.compile(r"good[- ]first|easy|help[- ]wanted|beginner|confirmed|low[- ]hanging", re.I)
BUG_LABELS = re.compile(r"\bbug\b|type: ?bug|regression", re.I)
FEATURE_LABELS = re.compile(r"enhancement|feature|feat\b", re.I)
DISCUSSION_TITLES = re.compile(r"feedback|\brfc\b|proposal|discussion|roadmap|tracking issue|^\[?feat", re.I)

# Fields fetched for every issue, wherever it comes from (a repo listing or a search).
ISSUE_FIELDS = """
  number
  title
  url
  createdAt
  comments { totalCount }
  assignees(first: 1) { totalCount }
  labels(first: 10) { nodes { name } }
  closedByPullRequestsReferences(first: 5, includeClosedPrs: false) { totalCount }
  timelineItems(first: 50, itemTypes: [CROSS_REFERENCED_EVENT, CONNECTED_EVENT]) {
    nodes {
      ... on CrossReferencedEvent { source { ... on PullRequest { number state url } } }
      ... on ConnectedEvent { subject { ... on PullRequest { number state url } } }
    }
  }
"""

REPO_FIELDS = "nameWithOwner stargazerCount isArchived isFork primaryLanguage { name }"

CACHE_DIR = Path(os.environ.get("ISSUE_SCOUT_CACHE", Path.home() / ".cache" / "issue-scout"))
POLICY_CACHE_DAYS = 7


class GhError(RuntimeError):
    pass


def log(message: str) -> None:
    if not os.environ.get("ISSUE_SCOUT_QUIET"):
        print(message, file=sys.stderr, flush=True)


def graphql(query: str, variables: dict | None = None, *, retries: int = 3) -> dict:
    """Run a GraphQL query through the gh CLI, backing off on rate limits."""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in (variables or {}).items():
        flag = "-F" if isinstance(value, (int, bool)) or value is None else "-f"
        if value is not None:
            cmd += [flag, f"{key}={value}"]
    for attempt in range(retries + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        out = proc.stdout.strip()
        data = json.loads(out) if out.startswith("{") else {}
        if proc.returncode == 0 or data.get("data"):
            return data.get("data") or {}
        message = proc.stderr.strip() or out
        if "rate limit" in message.lower() and attempt < retries:
            wait = 30 * (attempt + 1)
            log(f"  GitHub rate limit hit, waiting {wait}s...")
            time.sleep(wait)
            continue
        raise GhError(message)
    raise GhError("unreachable")


def lit(value: str) -> str:
    """A GraphQL string literal."""
    return json.dumps(value)


# --- contribution policies ---------------------------------------------------

@dataclass
class Policy:
    verdict: str
    source: str | None = None
    evidence: str | None = None

    @property
    def note(self) -> str:
        return POLICY_NOTES[self.verdict]

    @property
    def weight(self) -> int:
        return POLICY_WEIGHT[self.verdict]


def classify_policy(files: dict[str, str]) -> Policy:
    """Pick the strongest verdict found across the policy files."""
    for verdict, pattern in POLICY_RULES:
        rx = re.compile(pattern, re.I)
        for path, text in files.items():
            for line in text.splitlines():
                if rx.search(line):
                    return Policy(verdict, path, line.strip()[:200])
    return Policy("none")


def looks_like_symlink(text: str) -> bool:
    """Symlinked files come back from the API as just the target path."""
    stripped = text.strip()
    return (
        0 < len(stripped) < 200
        and not any(ch.isspace() for ch in stripped)
        and stripped.lower().endswith((".md", ".rst", ".txt"))
    )


def _policy_file_fields(prefix: str = "f") -> str:
    return " ".join(
        f'{prefix}{i}: object(expression: {lit("HEAD:" + path)}) {{ ... on Blob {{ text }} }}'
        for i, path in enumerate(POLICY_FILES)
    )


def _files_from(data: dict) -> dict[str, str]:
    return {
        path: data[f"f{i}"]["text"]
        for i, path in enumerate(POLICY_FILES)
        if data.get(f"f{i}") and data[f"f{i}"].get("text")
    }


def _resolve_symlinks(repo: str, files: dict[str, str]) -> dict[str, str]:
    targets = {}
    for path, text in files.items():
        if looks_like_symlink(text):
            targets[path] = posixpath.normpath(posixpath.join(posixpath.dirname(path), text.strip()))
    if not targets:
        return files
    owner, _, name = repo.partition("/")
    fields = " ".join(
        f"t{i}: object(expression: {lit('HEAD:' + target)}) {{ ... on Blob {{ text }} }}"
        for i, target in enumerate(targets.values())
    )
    try:
        data = graphql(f"{{ repository(owner: {lit(owner)}, name: {lit(name)}) {{ {fields} }} }}")
    except GhError:
        data = {}
    repo_data = data.get("repository") or {}
    resolved = dict(files)
    for i, path in enumerate(targets):
        obj = repo_data.get(f"t{i}")
        resolved[path] = obj.get("text", "") if obj else ""
    return resolved


def policy_from_repo_data(repo: str, data: dict) -> Policy:
    if data.get("isArchived"):
        return Policy("closed", None, "repository is archived")
    return classify_policy(_resolve_symlinks(repo, _files_from(data)))


def _load_cache() -> dict:
    try:
        return json.loads((CACHE_DIR / "policies.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "policies.json").write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def fetch_policies(repos: list[str], *, batch: int = 10, use_cache: bool = True) -> dict[str, Policy]:
    """Policies for many repos, a batch per request, cached on disk for a week."""
    cache = _load_cache() if use_cache else {}
    now = time.time()
    result: dict[str, Policy] = {}
    todo = []
    for repo in dict.fromkeys(repos):
        entry = cache.get(repo.lower())
        if entry and now - entry["at"] < POLICY_CACHE_DAYS * 86400:
            result[repo] = Policy(entry["verdict"], entry.get("source"), entry.get("evidence"))
        else:
            todo.append(repo)
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        parts = []
        for i, repo in enumerate(chunk):
            owner, _, name = repo.partition("/")
            parts.append(
                f"r{i}: repository(owner: {lit(owner)}, name: {lit(name)}) "
                f"{{ isArchived {_policy_file_fields()} }}"
            )
        try:
            data = graphql("{ " + " ".join(parts) + " }")
        except GhError as exc:
            log(f"  could not read policies for {', '.join(chunk)}: {exc}")
            data = {}
        for i, repo in enumerate(chunk):
            repo_data = data.get(f"r{i}")
            policy = policy_from_repo_data(repo, repo_data) if repo_data else Policy("none")
            result[repo] = policy
            cache[repo.lower()] = {"at": now, "verdict": policy.verdict,
                                   "source": policy.source, "evidence": policy.evidence}
    if use_cache:
        _save_cache(cache)
    return result


# --- issues ------------------------------------------------------------------

@dataclass
class Issue:
    repo: str
    number: int
    title: str
    url: str
    created: datetime
    comments: int
    assigned: bool
    labels: list[str]
    open_prs: list[str] = field(default_factory=list)
    closed_prs: list[str] = field(default_factory=list)
    stars: int = 0
    language: str | None = None
    policy: Policy | None = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def age_days(self) -> float:
        return (datetime.now(timezone.utc) - self.created).total_seconds() / 86400

    @property
    def claimed(self) -> bool:
        return self.assigned or bool(self.open_prs)


def parse_issue(repo: str, node: dict) -> Issue:
    issue = Issue(
        repo=repo,
        number=node["number"],
        title=node["title"],
        url=node["url"],
        created=datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")),
        comments=node["comments"]["totalCount"],
        assigned=node["assignees"]["totalCount"] > 0,
        labels=[label["name"] for label in node["labels"]["nodes"]],
    )
    seen = set()
    for item in node["timelineItems"]["nodes"]:
        pr = item.get("source") or item.get("subject") or {}
        if "number" not in pr or pr["url"] in seen:
            continue
        seen.add(pr["url"])
        if pr["state"] == "CLOSED":
            issue.closed_prs.append(pr["url"])
        else:  # OPEN, or MERGED (the fix may already be in): someone is on it
            issue.open_prs.append(pr["url"])
    if node["closedByPullRequestsReferences"]["totalCount"] and not issue.open_prs:
        issue.open_prs.append("(linked PR)")
    return issue


def score_issue(issue: Issue) -> None:
    """Score how promising an issue is. Policy and stars only count when known."""
    score = 10.0
    reasons = []
    score -= min(issue.age_days, 60) / 6  # fresher is better, down to -10 at 60 days
    score -= issue.comments * 1.5
    if any(GOOD_LABELS.search(label) for label in issue.labels):
        score += 4
        reasons.append("beginner-friendly label")
    if any(BUG_LABELS.search(label) for label in issue.labels):
        score += 2
        reasons.append("bug")
    if any(SKIP_LABELS.search(label) for label in issue.labels):
        score -= 8
        reasons.append("needs a maintainer decision")
    if DISCUSSION_TITLES.search(issue.title):
        score -= 4
        reasons.append("looks like a discussion or feature request")
    elif any(FEATURE_LABELS.search(label) for label in issue.labels):
        score -= 1
        reasons.append("feature request")
    if issue.closed_prs:
        score -= 2
        reasons.append(f"{len(issue.closed_prs)} earlier PR(s) were closed")
    if issue.policy is not None:
        score += issue.policy.weight
    if issue.stars:
        score += min(math.log10(issue.stars), 5) * 0.6  # +1.8 at 1k stars, +3 at 100k
    issue.score = round(score, 1)
    issue.reasons = reasons


# --- scanning named repos ----------------------------------------------------

@dataclass
class RepoReport:
    repo: str
    stars: int
    policy: Policy
    issues: list[Issue]
    error: str | None = None

    @property
    def closed_pr_count(self) -> int:
        return sum(len(i.closed_prs) for i in self.issues)


def keep_issue(issue: Issue, *, max_age_days: float, max_comments: int, include_claimed: bool) -> bool:
    if issue.age_days > max_age_days or issue.comments > max_comments:
        return False
    return include_claimed or not issue.claimed


def scan_repo(
    full_name: str,
    *,
    fetch: int = 40,
    max_age_days: float = 30,
    max_comments: int = 2,
    include_claimed: bool = False,
) -> RepoReport:
    owner, _, name = full_name.partition("/")
    query = (
        "query($owner: String!, $name: String!, $first: Int!) {"
        f" repository(owner: $owner, name: $name) {{ {REPO_FIELDS} {_policy_file_fields()}"
        " issues(states: OPEN, first: $first, orderBy: {field: CREATED_AT, direction: DESC})"
        f" {{ nodes {{ {ISSUE_FIELDS} }} }} }} }}"
    )
    try:
        data = graphql(query, {"owner": owner, "name": name, "first": fetch}).get("repository")
    except GhError as exc:
        return RepoReport(full_name, 0, Policy("none"), [], error=str(exc))
    if data is None:
        return RepoReport(full_name, 0, Policy("none"), [], error="repository not found")

    repo = data["nameWithOwner"]
    policy = policy_from_repo_data(repo, data)
    issues = []
    for node in data["issues"]["nodes"]:
        issue = parse_issue(repo, node)
        if not keep_issue(issue, max_age_days=max_age_days, max_comments=max_comments,
                          include_claimed=include_claimed):
            continue
        score_issue(issue)  # within one repo, policy and stars are the same for every issue
        issue.stars, issue.policy = data["stargazerCount"], policy
        issues.append(issue)
    issues.sort(key=lambda i: i.score, reverse=True)
    return RepoReport(repo, data["stargazerCount"], policy, issues)
