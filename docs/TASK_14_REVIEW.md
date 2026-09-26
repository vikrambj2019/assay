# Task 14 review — run budgets and partial results

Task 14 is complete for the budget lifecycle.

- The monotonic budget starts before planning and is checked before and after
  scenario execution.
- Browser tool attempts are counted before execution, including failed and
  origin-blocked actions.
- SDK-reported scenario costs are recorded at usage boundaries; cost-limit
  messages disclose that delayed SDK reporting cannot be a hard cap.
- Budget exhaustion propagates through the browser adapter, closes owned
  sessions, preserves completed PASS/FAIL results, and marks current and
  remaining scenarios UNVERIFIED with the budget reason.
- Incomplete budget-limited runs remain incomplete for exit-code handling.

Validation:

- Budget and executor tests: **89 passed**
- Full suite: **490 passed, 59 skipped, 1 warning**
