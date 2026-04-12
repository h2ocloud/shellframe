# ShellFrame Review Workflow

This is the playbook for triaging incoming PRs, issues, and tracking project
health (stars, forks). Followed manually by Howard, and by AI sessions when
asked to "check shellframe inbox" or run on a schedule.

## Daily / on-demand check

```bash
# Repo health snapshot
gh repo view h2ocloud/shellframe --json stargazerCount,forkCount,pushedAt,latestRelease

# Open PRs
gh pr list --repo h2ocloud/shellframe --state open --json number,title,author,createdAt,additions,deletions

# Open issues
gh issue list --repo h2ocloud/shellframe --state open --json number,title,author,createdAt,labels

# Recently closed (last 7 days)
gh pr list --repo h2ocloud/shellframe --state closed --search "closed:>$(date -v-7d +%Y-%m-%d)" --limit 20
gh issue list --repo h2ocloud/shellframe --state closed --search "closed:>$(date -v-7d +%Y-%m-%d)" --limit 20
```

Report format:
```
ShellFrame inbox — YYYY-MM-DD
  Stars: 8 (Δ +0)    Forks: 2 (Δ +0)
  Open PRs: 0
  Open issues: 0
  Last release tag: v0.9.0    GitHub Release: v0.3.0  ← stale
```

If stars/forks are unchanged, surface only as a one-line summary. If there
are new PRs/issues, drop into the per-item review below.

## PR review checklist

Run for each open PR. Don't merge anything without Howard's explicit approval.

```bash
gh pr view <num> --repo h2ocloud/shellframe
gh pr diff <num> --repo h2ocloud/shellframe
gh pr checks <num> --repo h2ocloud/shellframe
```

### Hard blockers (auto-flag, do not merge)

- **Network calls or subprocess to unknown hosts** — Especially in
  `bridge_telegram.py` and `main.py`. Anything that talks to a server
  not in the user's config is suspicious.
- **Token / credential capture** — Look for code that reads `~/.config`,
  environment variables, or files outside the repo and sends them
  anywhere.
- **`os.system` / `subprocess` with user-controlled strings** — Shell
  injection risk.
- **`eval` / `exec` of remote content** — Never.
- **New dependencies with no clear justification** — `requirements.txt`
  changes need a reason.
- **Modifies `install.sh` / `install.ps1` to fetch code from non-GitHub
  URLs** — Supply chain risk.
- **Touches `filters.json` to disable security-relevant patterns** —
  Manual review only.

### Soft signals (weigh, may still merge)

- **Aligned with project direction**: ShellFrame is a personal multi-tab
  GUI terminal. Cross-platform improvements, UX polish, bug fixes, AI CLI
  tool integrations are aligned. Adding generic terminal features
  unrelated to AI workflows is borderline.
- **Code quality**: follows existing patterns, no over-engineering, no
  speculative abstractions, comments explain *why* not *what*.
- **Scope creep**: a PR titled "fix typo" that also rewrites the bridge
  is a red flag.
- **Test coverage**: ShellFrame doesn't have a formal test suite yet, so
  this is N/A. Once we add tests this becomes a hard requirement.
- **First-time contributor**: more friendly tone in review, more
  patience with code style nits.

### Recommendation categories

- **Approve & merge** — Trivial fixes (typos, docs), small bug fixes
  with obvious correctness. Howard still gets the final click.
- **Approve with nits** — Mostly good, a couple of comments. Howard
  decides whether to fix in this PR or follow-up.
- **Request changes** — Substantive issues (correctness, design,
  scope). Leave concrete actionable feedback.
- **Close politely** — Not aligned with project direction, or
  superseded by other work. Always explain why.
- **Spam / low-effort** — `--lock --reason spam` and close. No reply.

### Posting a review

```bash
gh pr review <num> --repo h2ocloud/shellframe --approve --body "..."
gh pr review <num> --repo h2ocloud/shellframe --request-changes --body "..."
gh pr review <num> --repo h2ocloud/shellframe --comment --body "..."
```

For comments on specific lines, use the GitHub web UI — `gh` doesn't
support inline review comments well.

## Issue triage checklist

```bash
gh issue view <num> --repo h2ocloud/shellframe
```

### Categorize

- **bug** — Reproducible, with steps. If reporter didn't include
  reproduction, ask first; don't guess.
- **feature request** — Evaluate against project scope. Howard's
  philosophy: build what HE needs, accept community PRs that align,
  don't take on features for hypothetical users.
- **question** — Answer if quick, otherwise point to README / WINDOWS.md.
- **bug, but our env** — Tell reporter we'll look at it, log to
  `feedback_*.md` memory if it's a recurring footgun.
- **spam / off-topic** — Close with one polite line.

### Decision tree

```
Is it a security report?
  → Treat as urgent. Acknowledge within 24h. Do NOT discuss
    publicly. Coordinate fix + disclosure.

Is it a clear bug with a repro?
  → Label `bug`. Estimate effort. If <30 min, fix in next session.
    If larger, add to backlog (a `feedback_*` memory or new issue).

Is it a feature request?
  → Check against project scope (multi-tab GUI terminal for AI CLIs,
    cross-platform, lightweight). If aligned, label `enhancement`.
    If not, close politely with reasoning.

Is it a question?
  → Answer briefly. Update README / WINDOWS.md if it'd help others.

Is it abuse / spam?
  → Lock + close. No reply.
```

### Posting a comment / closing

```bash
gh issue comment <num> --repo h2ocloud/shellframe --body "..."
gh issue close <num> --repo h2ocloud/shellframe --reason completed
gh issue close <num> --repo h2ocloud/shellframe --reason "not planned" --comment "..."
gh issue edit <num> --repo h2ocloud/shellframe --add-label bug,enhancement
```

## Stars / forks tracking

Stars are loose social signal — track them but don't optimize for them.
If stars cross thresholds (10, 25, 50, 100), it's a moment to:
- Tighten release process (smaller, more frequent versions; better release notes)
- Add CI (currently none)
- Add a basic test suite
- Review CONTRIBUTING.md for clarity

Howard's default: ship freely while small, tighten when there's an audience.

## When AI sessions run this

If you're a Claude session asked to "check shellframe inbox":

1. Run the daily check commands above.
2. If nothing changed since the last report (saved at `~/.config/shellframe/last_review.json`), one-line summary and stop.
3. If there are new PRs / issues, do the review checklist for each and produce a structured report:
   ```
   PR #12 — "fix tmux capture timeout" by @some-user
     Author: first-time contributor (2 prior commits in other repos)
     Scope: tmux capture-pane subprocess timeout handling
     Diff: +8 -3 in bridge_telegram.py
     Security: ✓ no network, no subprocess injection, no new deps
     Quality: ✓ follows existing pattern
     Recommendation: APPROVE with one nit (rename var for clarity)

   Issue #5 — "TG bridge crash on empty voice" by @another-user
     Type: bug
     Repro: included, looks valid
     Severity: medium (crash)
     Effort: ~20min
     Recommendation: FIX in next session, label `bug`
   ```
4. Update `last_review.json` with the current state so the next run can
   diff against it.
5. Never merge or close anything without Howard's explicit OK.
