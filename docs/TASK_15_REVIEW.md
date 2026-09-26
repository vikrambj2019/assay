# Task 15 review — reports and exit semantics

Task 15 is complete.

- `results.json`, self-contained HTML, and JUnit are generated from the same
  `RunResult` and remain parseable.
- Reports include verdicts, assertion kinds, assertion evidence, coverage
  suggestions, input provenance, exclusions, truncation details, warnings,
  budgets, completeness, and exit code.
- HTML escapes application text and applies the shared secret redactor;
  JUnit messages and JSON values receive the same redaction pass.
- FAIL remains exit code 1; incomplete, ERROR, BLOCKED, and UNVERIFIED runs
  remain exit code 2; clean complete runs and skipped-only runs are 0.

Validation:

- Report tests: **49 passed**
- Full suite: **490 passed, 59 skipped, 1 warning**
