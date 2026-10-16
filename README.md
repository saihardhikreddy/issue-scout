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

One GraphQL request per repository, so a scan of 15 projects takes about 10 seconds.

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
issue-scout --preset python                 # popular Python projects
issue-scout --preset js                     # popular JavaScript projects
issue-scout owner/repo other/repo --days 7  # your own list, last week only
issue-scout --preset python --policy-only   # just the AI policies
issue-scout --preset python --json          # machine-readable output
```

| Option | Default | What it does |
| --- | --- | --- |
| `--days` | 30 | Only issues newer than this |
| `--max-comments` | 2 | Skip issues that already have more discussion |
| `--top` | 5 | Issues shown per repo |
| `--fetch` | 40 | Newest issues inspected per repo |
| `--include-claimed` | off | Also show issues that have open PRs |
| `--all-policies` | off | Also show issues from `BANNED` and `CLOSED` projects |

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
