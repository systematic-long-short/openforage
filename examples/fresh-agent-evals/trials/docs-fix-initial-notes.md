# `docs-fix` initial trial — retained false negative

The first real `docs-fix` trial (session
`ses_f35cbc52fffejERhlvEF47gADs`, 2026-09-22 17:40:14–17:40:55Z) is kept
deliberately as a **grader false negative**, not as an agent failure.

## What actually happened

- Every task check passed: the onboarding page was repaired, it matched
  `invite_code=`, the fixture markers were gone, the real `truthfulness`
  validator exited `0`, and `trial-output/evidence.json` carried the required
  fields and at least two exact commands.
- The verdict still failed because the isolation comparison saw
  `__pycache__/*.pyc` files the validator run had created under frozen
  `.github/scripts/validation/` directories, and because the parent Git
  context leaked into the child session (commands such as `git status`,
  reading `<REPO>/docs/...`, and a workspace-sibling trial-record read).
- The runner was then repaired: the child environment sets
  `PYTHONDONTWRITEBYTECODE=1`, the prepared snapshot is its own throwaway
  local Git repository, and the child prompt is workspace-scoped.
- The `docs-fix` rerun (separate session `ses_f35c4d44fffe2EyS64ZobFsMNM`,
  17:47:49–17:48:19Z) is a **separate observation** and passed; it is archived
  next to this note. The original false negative is never deleted or silently
  converted to a pass.

## Why it is retained

A grader bug that marks correct work as failing is part of the evidence
record. Keeping it visible is how the packet distinguishes "the agent failed"
from "the harness/grader failed", and it is why the deterministic grader
regressions now cover bytecode suppression and Git isolation directly.
