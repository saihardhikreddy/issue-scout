---
name: issue-scout
description: Find fresh, unclaimed GitHub issues to contribute to, in projects whose AI-contribution policy allows it. Use when the user wants an open-source issue to work on, asks "what can I contribute to", or wants to check a project's AI policy before opening a PR.
---

# Issue Scout

Issue Scout scans GitHub repositories for recent open issues that nobody is working on yet,
and reads each project's AI-contribution policy so you don't prepare a fix that will be closed.

## Requirements

- GitHub CLI (`gh`) logged in (`gh auth status`).
- Python 3.10+ and this package installed (`pip install -e <path-to-issue-scout>`),
  or run it in place with `python -m issue_scout` from the repo root.

## Steps

1. Run the scan. Use the user's repos if they named any, otherwise a preset:

   ```bash
   issue-scout --preset python --top 3
   issue-scout owner/repo another/repo --days 14
   issue-scout --preset python --json   # when you need to process the results
   ```

2. Read each repo's verdict before suggesting its issues:
   - `WELCOME`: AI-assisted PRs are accepted; the person submitting must understand and own the change.
   - `APPROVAL-FIRST`: a maintainer has to approve the approach on the issue before any PR.
     Draft that comment for the user to post themselves.
   - `STRICT`: AI is tolerated but low-effort or agent-driven PRs get closed or the account banned.
   - `BANNED` / `CLOSED`: do not prepare PRs for these projects.
   - `NONE` / `MENTIONS-AI`: open the policy file and read it yourself.

3. Treat a "N recent PRs were closed unmerged" warning as a sign the project auto-closes
   drive-by or agent-made PRs. Mention it to the user before doing any work there.

4. Before writing code for an issue, re-check it is still unclaimed (`gh issue view N -R owner/repo`
   and `gh pr list -R owner/repo --search N`); popular issues often get a PR within hours.

5. Never open a pull request on the user's behalf until they have read the diff and said they
   stand behind it. Their account carries the reputation, and several projects ban accounts
   that send agent-made PRs. Disclose AI assistance in the PR body whenever the policy asks for it.
