# Task 9 review — `bta check` context collection

Task 9 is complete for the CLI and context-loading scope.

Implemented and verified:

- `bta check` accepts required notes, optional README/diff/depth, URL, and
  budget overrides; README defaults to `README.md`.
- Notes are required, binary notes fail clearly, and missing README/diff inputs
  are reported without requiring Git when no diff is requested.
- Git references are validated and committed-since-merge-base, staged, and
  unstaged tracked changes are collected with argument-array subprocess calls.
- Untracked files are reported by filename only; `.env`, auth-state, obvious
  secret files, and binary diff files are excluded with provenance.
- Context size limits, truncation, exclusions, and warnings survive loading.

Validation:

- Context tests: **45 passed**
- Full suite before this task: **485 passed, 59 skipped, 1 warning**

The existing pytest collection warning for the `TestSpec` dataclass remains
non-functional.
