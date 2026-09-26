# Review of E2E requirements tasks 1–5

Verdict: changes are directionally correct, but tasks 1–5 should not yet be marked accepted. Fix and rerun the cases below before building more behavior on this foundation. Task 6 and later requirements are outside this review.

## Validation performed

- Current repository tests in the existing Docker runtime: **82 passed, 3 failed**, 1 collection warning, 5.49 seconds.
- Eight independent local acceptance probes: **8 failed**, confirming six categories of missing behavior below. These probes intentionally assert the requirements, not the current implementation.
- Built a wheel from an isolated copy, unpacked it outside the checkout, and attempted runtime imports: **failed**, because the wheel omits the DOM JavaScript asset. It also contains no skill instruction files.
- No live model calls, real website mutations, or production code changes were made.
- This checkout does not contain Git metadata; review is against the requirements and current files, not a commit diff.

## Findings and fixes

### R1 — P1 — SDK error results can still produce PASS (task 2)

Location: `harness/agent.py:340`, `_make_stage_tools.finalize`.

Only the Python `exc` argument is examined. `ResultMessage.is_error`, `subtype`, and `errors` are ignored. Reproduction: call `complete_goal(PASS)`, then finalize with `is_error=True` and `subtype=error_during_execution`; the final verdict is PASS. Without completion, the same infrastructure failure is classified only as generic UNVERIFIED and its diagnostic information is lost.

Fix: inspect structured SDK termination information as well as raised exceptions. Preserve prior stages, append the appropriate ERROR/UNVERIFIED termination record, and prevent successful completion on an unsuccessful SDK termination. Distinguish budget/turn-limit exits from execution faults.

Acceptance: use fake ResultMessages with the real SDK fields for success, execution error, turn-limit, budget-limit, and missing result, both before and after a completion call. Current tests model an SDK error only as a Python exception and therefore miss this path.

### R2 — P1 — Packaged runtime cannot import (task 1)

Location: `pyproject.toml:33`; runtime read at `core/snapshot/capture.py:20`.

The built wheel has no `core/snapshot/dom_walk.js`. Importing `core.snapshot` from the unpacked wheel raises FileNotFoundError. Editable installs and Docker source copies conceal the defect. The wheel also contains zero `SKILL.md` files, so shipped skill behavior is not preserved.

Fix: explicitly include required runtime package data. Decide which synthetic/public skill resources are intended to ship; do not indiscriminately package the existing private document library. Add a wheel-install smoke test outside the checkout. Keep the documented Docker Playwright dependency alignment; the comment in pyproject currently says pinned but its constraint uses `>=`.

Acceptance: build/install a wheel in an isolated environment, import the runtime, load the DOM asset, and verify intended skill resources. A clean package install must not depend on source files remaining next to it.

### R3 — P1 — Report preservation is not passing its own acceptance tests (task 3)

Location: `core/suite.py:173`, `core/suite.py:290`, `core/report.py:70`.

Three current tests fail with FileNotFoundError writing a per-test report because the report directory does not exist:

- `test_failing_worker_does_not_lose_passing_worker`
- `test_dependent_blocked_when_upstream_fails`
- `test_crash_becomes_error_and_blocks_dependents`

The mocks bypass `_run_test` directory creation, so these failures do not prove that every ordinary browser startup failure loses reports. They do reveal a fragile ownership contract: the recovery/report path assumes execution already created its output directory. In production the deferred agent import occurs before that creation; failure there similarly leaves no per-test directory and the error report can crash finalization.

Fix: make recovery/report generation safely establish writable report directories and contain individual report-write failures so the suite summary survives where possible. Do not just weaken the three assertions or teach the mocks to hide the dependency.

Acceptance: make the existing tests pass and add a failure before directory creation; successful sibling reports and a suite index must survive.

### R4 — P1 — Teardown errors erase completed evidence (task 3)

Location: `core/suite.py:190`, `core/suite.py:226`.

Returning the stage logs from inside `async with BrowserSession(...)` means a failing `__aexit__` prevents those logs from reaching the worker. The catch then substitutes a new one-entry ERROR list. A local probe returning a confirmed app FAIL with specific evidence, followed by a teardown exception, produced a report containing only the teardown error.

Fix: retain completed logs independently of cleanup. Append teardown failure as another record rather than replacing the app results. Ensure resource cleanup itself proceeds as far as possible after individual close failures.

Acceptance: return PASS and FAIL stages, then fail teardown. Both original stage records and evidence remain, along with the cleanup ERROR. Confirmed app failures must not disappear.

### R5 — P2 — Application URL environment precedence is missing (task 4)

Location: `core/suite.py:80`; environment loading in `core/config.py`.

Setting `BTA_BASE_URL=http://fixture.local` with no YAML application URL turns `Open {base_url}/login` into `Open /login`. The parser only checks YAML. Existing precedence tests cover environment values for other settings but omit this URL case.

Fix: implement application URL resolution as explicit test > suite defaults > environment > built-in, separately from `ANTHROPIC_BASE_URL`. Test every level. Full `.env` loading remains task 8; respecting an already-set process environment value is part of task 4.

### R6 — P2 — Invalid YAML types silently change execution (task 3)

Location: `core/suite.py:69`, `core/suite.py:85`.

The parser coerces instead of validating: quoted `skip: "false"` becomes True, quoted `headless: "false"` becomes True, and `goal: null` becomes the literal goal `None`. Other structural types are not validated before `.get`/iteration. Silent skipping can allow a suite to exit successfully without running intended tests.

Fix: validate mappings/lists and exact field types before conversion, including booleans, nonempty goals/names, dependency lists, integer limits, and string paths. Reject malformed input with field-specific errors before browser launch.

Acceptance: add the three reproduced cases and invalid root/defaults/tests/needs types; no malformed value should be silently interpreted as a valid goal or skip instruction.

### R7 — P2 — Session-write conflict detection misses equivalent paths (task 3)

Location: `core/suite.py:134`.

Two roots with `session: shared.json` and `session: ./shared.json` pass validation even though both write the same file. The current check compares raw strings. Absolute/relative aliases and symlinks create similar cases.

Fix: canonicalize session paths against the documented invocation directory before conflict detection and use the same resolved paths during execution.

Acceptance: equivalent relative, absolute, and supported symlink paths are rejected as conflicting roots; genuinely distinct files remain permitted.

### R8 — P2 — Action IDs/status are not returned or recorded as required (task 5)

Location: `harness/tools.py:78` through `harness/tools.py:99`.

The counter names snapshot files, but tool output and the trail contain only `detail` and page text. `ActionResult.ok` is discarded, and screenshot availability is not explicit in the returned record. Two separate clicks on the same element yielding the same page return identical objects; the consumer cannot identify them by action ID. Exceptions before the final trail append can also lose the attempted action record.

Fix: include action ID, action success/failure, and observation availability in the action result/record; record attempted actions even when execution or artifact writing fails. Keep the new full-transaction lock. Full report mapping and screenshot-caption migration can remain in task 6, but task 5 must expose the IDs/status that task 6 will consume.

Acceptance: repeated identical actions have different IDs; failed actions and captures carry explicit status; a subsequent action still runs; consumers can associate each returned observation with its attempt.

## What is working

- Runtime dependencies now include the SDK and YAML parser.
- An explicit completion tool prevents the common intermediate-PASS-then-stop case from passing.
- Stage verdict restrictions are implemented.
- Empty suites, nonpositive parallelism, obvious path traversal, duplicate names, unknown dependencies, and cycles have checks.
- Collision-resistant output directory allocation is present.
- Model, effort, headless, and slowdown settings now preserve environment fallback when YAML values are absent.
- The action/settlement/capture sequence is under one lock, and the new concurrency/capture-failure tests pass.

## Reproduction commands

```sh
docker compose run --rm --entrypoint pytest agent -q -p no:cacheprovider
docker compose run --rm --entrypoint pytest agent -q -p no:cacheprovider results/task-1-5-review/test_review_regressions.py --tb=short
docker compose run --rm --entrypoint python agent results/task-1-5-review/check_wheel.py
```

The independent probes and XML results are under the ignored local `results/task-1-5-review/` directory. Promote appropriate regression cases into `tests/` when implementing fixes; the review artifacts alone are not distributed with the repository.

Recommended repair order: R1, R2, R3/R4, R5/R6/R7, R8. Then rerun the full suite and independent probes. Do not call tasks 1–5 complete based solely on the original tests; several test doubles omit the state needed to expose these failure paths.
