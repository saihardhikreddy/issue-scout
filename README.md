# 🔭 Issue Scout

**Find fresh, unclaimed GitHub issues in projects that actually want your help.**

Picking an open-source issue in 2026 is a race. Popular bugs get two or three pull requests
within hours, and more and more projects close AI-assisted PRs, or ban them outright.
Issue Scout checks both things before you write a line of code:

- 🆕 **Is anyone already on it?** It looks for open, linked or merged PRs and for assignees,
  and it flags issues where earlier PRs were closed unmerged.
- 🤖 **Will the project accept my PR?** It reads `AI_POLICY.md` and `CONTRIBUTING.*`
  (following symlinks) and sorts each project into a clear verdict.
- 📈 **Which issue first?** It ranks the remaining issues by freshness, activity and labels.

By default it searches **all of GitHub**, not a fixed list. It runs two searches and merges them:

1. **Repo-first:** find active, popular repositories (by language, topic and stars) that have
   good-first-issue or help-wanted issues, then read each one's newest issues.
2. **Issue-first:** search issues directly by label, language and age. This catches projects
   that the repository search misses.

Every candidate is then checked for open, linked or merged PRs, its project's AI policy is read
(and cached for a week), and all issues are ranked on one scale. Repositories are read a few at a
time over GraphQL, so a full search takes under a minute. It also skips issues in mostly non-Latin
scripts by default (`--any-script` keeps them) and shows at most 3 issues per project.

```text
$ issue-scout -l python --top 3

Top 3 of 92 fresh, unclaimed issues

  1. Pick categories for integrations the script can't place
     home-assistant/core #186846  (91,562 stars, Python)  score 20.0  WELCOME
     0d old, 0 comments, beginner-friendly label
  2. app crash down after select model in llm node
     langgenius/dify #44195  (158,645 stars, Python)  score 17.9  WELCOME
     0d old, 0 comments, bug
  3. issue: Adding someone in the Access dialog of a Public item makes it Private ...
     open-webui/open-webui #32539  (155,895 stars, Python)  score 17.5  NONE
     0d old, 1 comments, beginner-friendly label, bug
```

You can also scan specific projects, which groups results by project and quotes the policy line:

```text
$ issue-scout --preset python --top 1

sqlfluff/sqlfluff  (9,978 stars)  WELCOME: AI-assisted PRs welcome (you must understand and own the change)
  policy: "AI-assisted contributions are welcome when they meet the same quality," [CONTRIBUTING.md]
  warning: 10 recent PRs on these issues were closed unmerged; the project may be closing drive-by or agent-made PRs
    9.5  #8738 T-SQL: GROUP BY GROUPING SETS with more than one set fails to parse
         3d old, 0 comments, bug, 2 earlier PR(s) were closed

Textualize/rich  (57,550 stars)  APPROVAL-FIRST: needs maintainer sign-off on the approach before a PR
  policy: "- The Pull Request must link to a issue or discussion where a solution has been approv" [AI_POLICY.md]
    8.3  #4238 [BUG] Traceback.extract loops forever when __cause__ and __context__ form a cycle

streamlit/streamlit  (46,008 stars)  CLOSED: not taking outside PRs
```

## Policy verdicts

| Verdict | Meaning |
| --- | --- |
| `WELCOME` | AI-assisted PRs are accepted. You still have to understand and own the change. |
| `APPROVAL-FIRST` | A maintainer must approve the approach on the issue before a PR. |
| `STRICT` | AI is tolerated, but low-effort or agent-driven PRs are closed or the account banned. |
| `BANNED` | AI contributions are not allowed. Issues are hidden by default. |
| `CLOSED` | The project isn't taking outside PRs, or it's archived. Issues are hidden by default. |
| `MENTIONS-AI` / `NONE` | No clear rule was found, so read the contributing guide yourself. |

The verdict comes from keyword rules, so treat it as a pointer and read the quoted line before you start.

## Install

You need Python 3.10+ and the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login`.

```bash
git clone https://github.com/saihardhikreddy/issue-scout
cd issue-scout
pip install -e .
```

## Usage

```bash
issue-scout                                  # search all of GitHub
issue-scout -l python -l rust                # only these languages
issue-scout --topic cli --min-stars 2000     # by topic, bigger projects only
issue-scout --label "good first issue"       # only this label (default: good first issue, help wanted, bug)
issue-scout --label any --days 3             # any label, last 3 days
issue-scout --query "org:microsoft"          # any extra GitHub search qualifiers
issue-scout owner/repo other/repo            # just these projects
issue-scout --preset python                  # a built-in list of popular projects
issue-scout --preset python --policy-only    # just their AI policies
issue-scout -l go --json                     # machine-readable output
```

| Option | Default | What it does |
| --- | --- | --- |
| `-l`, `--language` | all | Repository language (repeatable) |
| `--topic` | none | Repository topic (repeatable) |
| `--label` | good first issue, help wanted, bug | Issue labels to search (repeatable, `any` for none) |
| `--min-stars` | 500 | Skip smaller projects |
| `--days` | 14 (search), 30 (named repos) | Only issues newer than this |
| `--max-comments` | 2 | Skip issues that already have more discussion |
| `--repo-limit` | 60 | Active repositories to read in the repo-first search |
| `--search-limit` | 300 | Issues to pull from the issue-first search |
| `--strategy` | both | `repos`, `issues` or `both` |
| `--per-repo` | 3 | Most results from one project (0 for no cap) |
| `--top` | 25 (search), 5 per repo | Results shown |
| `--include-claimed` | off | Also show issues that have open PRs |
| `--all-policies` | off | Also show issues from `BANNED` and `CLOSED` projects |
| `--any-script` | off | Keep issues whose titles are mostly in non-Latin scripts |

### How issues are ranked

Each issue starts at 10 points. It loses points for age (up to 10 at 60 days), for comments,
for looking like a discussion or feature request, for labels that need a maintainer decision,
and for earlier PRs that were closed unmerged. It gains points for beginner-friendly and bug
labels, a welcoming AI policy, and the project's popularity. Projects that ban AI or don't take
outside PRs are left out unless you pass `--all-policies`.

## Use it as a Claude Code skill

Copy `skills/issue-scout` into `~/.claude/skills/` (or your project's `.claude/skills/`).
Claude will then run the scan when you ask something like *"find me an issue I can contribute to"*.
The skill also tells Claude to re-check that an issue is free before coding, and never to open a PR
until you've reviewed the change yourself.

## Development

```bash
pip install pytest
python -m pytest
```

## License

MIT
