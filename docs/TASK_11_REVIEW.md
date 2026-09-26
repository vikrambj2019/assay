# Task 11 review — planner and depth policies

Task 11 is complete.

The planner now produces validated plans through an injectable adapter, enforces
the configured depth rather than trusting model output, caps scenarios, retains
trimmed ideas as coverage suggestions, preserves requirement source references,
labels mutation scenarios, and makes at most one structured-output repair
attempt. Planning time is included in the run budget, including `--plan-only`.

Validation:

- Planner tests plus plan-only CLI test: **51 passed**
- Full suite: **489 passed, 59 skipped, 1 warning**
