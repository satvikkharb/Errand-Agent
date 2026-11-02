---
description: Commit and push to a branch, then open a PR to main
allowed-tools: Bash(git:*), Bash(gh:*)
---

Commit and push my changes fast. Use as few git calls as possible; no extra exploration.

Rules:
- Remote `origin` (github.com/satvikkharb, account satvikkharb26@gmail.com). Use the repo's existing git user config; don't change it.
- Never push to `main`/`master`. The only exception: the repo has no commits yet (initial commit) — then commit and push to `main`, and stop.
- Otherwise: if on `main`, create a short kebab-case branch from the change (e.g. `fix-login-bug`); if already on a feature branch, reuse it.
- Stage relevant files only (never `.env`, secrets, `.venv`, caches).
- Commit message: one short line, imperative, ≤50 chars. No body.
- NO Claude attribution anywhere: no `Co-Authored-By`, no "Generated with Claude Code", in commits, PR titles, or PR bodies.
- Push with `git push -u origin <branch>`, then `gh pr create --base main --fill` (or reuse the open PR if one exists). Keep the PR title/body short, also with no Claude mention.
- Finish by printing the PR URL.

$ARGUMENTS (optional) = hint for the commit message.
