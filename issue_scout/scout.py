"""Core logic: fetch issues and contribution policies from GitHub and rank them."""

from __future__ import annotations

import json
import posixpath
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

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

SKIP_LABELS = re.compile(
    r"needs[- ]decision|needs[- ]design|proposal|rfc|question|discussion|wontfix|invalid|duplicate|"
    r"tracking|upstream|blocked|deferred",
    re.I,
)
GOOD_LABELS = re.compile(r"good[- ]first|easy|help[- ]wanted|beginner|confirmed", re.I)
BUG_LABELS = re.compile(r"\bbug\b|type: ?bug|regression", re.I)

QUERY = """
query($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    stargazerCount
    isArchived
    %(files)s
    issues(states: OPEN, first: $first, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
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
      }
    }
  }
}
"""


class GhError(RuntimeError):
    pass


def _file_fields() -> str:
    parts = []
    for i, path in enumerate(POLICY_FILES):
        parts.append(f'f{i}: object(expression: "HEAD:{path}") {{ ... on Blob {{ text }} }}')
    return "\n    ".join(parts)


def run_graphql(owner: str, name: str, first: int) -> dict:
    query = QUERY % {"files": _file_fields()}
    cmd = [
        "gh", "api", "graphql",
        "-f", f"query={query}",
        "-F", f"owner={owner}",
        "-F", f"name={name}",
        "-F", f"first={first}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise GhError(proc.stderr.strip() or proc.stdout.strip())
    data = json.loads(proc.stdout)
    if data.get("errors") and not data.get("data", {}).get("repository"):
        raise GhError("; ".join(e.get("message", "") for e in data["errors"]))
    return data["data"]["repository"]


def fetch_blob(owner: str, name: str, path: str) -> str | None:
    query = (
        "query($owner: String!, $name: String!, $expr: String!) {"
        " repository(owner: $owner, name: $name) { object(expression: $expr) { ... on Blob { text } } } }"
    )
    cmd = ["gh", "api", "graphql", "-f", f"query={query}",
           "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"expr=HEAD:{path}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        return None
    obj = (json.loads(proc.stdout).get("data") or {}).get("repository", {}).get("object")
    return obj.get("text") if obj else None


def looks_like_symlink(text: str) -> bool:
    """Symlinked files come back from the API as just the target path."""
    stripped = text.strip()
    return (
        0 < len(stripped) < 200
        and not any(ch.isspace() for ch in stripped)
        and stripped.lower().endswith((".md", ".rst", ".txt"))
    )


@dataclass
class Policy:
    verdict: str
    source: str | None = None
    evidence: str | None = None

    @property
    def note(self) -> str:
        return POLICY_NOTES[self.verdict]


def classify_policy(files: dict[str, str]) -> Policy:
    """Pick the strongest verdict found across the policy files."""
    for verdict, pattern in POLICY_RULES:
        rx = re.compile(pattern, re.I)
        for path, text in files.items():
            for line in text.splitlines():
                if rx.search(line):
                    return Policy(verdict, path, line.strip()[:200])
    return Policy("none")


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
        if pr["state"] == "OPEN":
            issue.open_prs.append(pr["url"])
        elif pr["state"] == "CLOSED":
            issue.closed_prs.append(pr["url"])
        else:  # MERGED: the fix may already be in, treat as claimed
            issue.open_prs.append(pr["url"])
    if node["closedByPullRequestsReferences"]["totalCount"] and not issue.open_prs:
        issue.open_prs.append("(linked PR)")
    return issue


def score_issue(issue: Issue) -> None:
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
    if issue.closed_prs:
        score -= 2
        reasons.append(f"{len(issue.closed_prs)} earlier PR(s) were closed")
    issue.score = round(score, 1)
    issue.reasons = reasons


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


def scan_repo(
    full_name: str,
    *,
    fetch: int = 40,
    max_age_days: float = 30,
    max_comments: int = 2,
    include_claimed: bool = False,
) -> RepoReport:
    owner, _, name = full_name.partition("/")
    try:
        data = run_graphql(owner, name, fetch)
    except GhError as exc:
        return RepoReport(full_name, 0, Policy("none"), [], error=str(exc))
    if data is None:
        return RepoReport(full_name, 0, Policy("none"), [], error="repository not found")

    files = {
        path: data[f"f{i}"]["text"]
        for i, path in enumerate(POLICY_FILES)
        if data.get(f"f{i}") and data[f"f{i}"].get("text")
    }
    for path, text in list(files.items()):
        if looks_like_symlink(text):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(path), text.strip()))
            files[path] = fetch_blob(owner, name, target) or ""
    policy = classify_policy(files)
    if data.get("isArchived"):
        policy = Policy("closed", None, "repository is archived")

    issues = []
    for node in data["issues"]["nodes"]:
        issue = parse_issue(data["nameWithOwner"], node)
        if issue.age_days > max_age_days or issue.comments > max_comments:
            continue
        if issue.claimed and not include_claimed:
            continue
        score_issue(issue)
        issues.append(issue)
    issues.sort(key=lambda i: i.score, reverse=True)
    return RepoReport(data["nameWithOwner"], data["stargazerCount"], policy, issues)
