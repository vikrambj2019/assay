# Review of task 6: evidence identity and screenshot failures

Task 6 is not ready to accept yet. The basic action-record/report mapping works for successful captures and a missing middle screenshot, but there are three blockers.

## Validation

- Full Docker test suite: **83 passed, 3 failed**, 1 collection warning.
- Task 6 probes: **9 passed, 5 failed**.
- No live model calls or remote-site mutations were made.

## Blockers

### 1. `run_goal()` raises before the model starts

`harness/agent.py:397` still calls `page_hooks(session, out_dir, shots)`, but task 6 removed the `shots` list and changed the state to `records`. Any real `run_goal()` invocation raises `NameError: name 'shots' is not defined` while building SDK options.

Fix the stale argument and add an integration-style unit test that patches `drive()` and confirms `run_goal()` reaches it. This is a release-blocking regression because the browser suite cannot execute.

### 2. Artifact-write failures still lose action records

`harness/tools.py:97` writes the snapshot text file before `records.append(...)`. If writing `step-01.txt` fails, the action happened but no `ActionRecord` is retained. A later action then starts with record 1, breaking the task 6 guarantee.

Create and append the record in a `finally` path, preserve an explicit observation/artifact failure status, and ensure the transaction does not silently present a successful action as fully evidenced. Decide whether the tool should return an error result or a normal action result with `observation unavailable`; document that choice.

### 3. Third-party network classification is discarded in snapshots

`core/snapshot/capture.py:47` reconstructs `NetworkFailure` without copying `third_party`. A third-party failure shown in browser evidence loses that classification when passed through the snapshot/report path.

Copy the field into the snapshot model and add a test that captures a third-party failure and verifies it remains third-party in the rendered/report evidence.

## Existing task 6 behavior that is correct

- `ActionRecord` carries a stable action ID and screenshot path.
- Every completed normal action gets a record, including `screenshot=None`.
- Returned tool output includes `[step-NN]`, so repeated identical calls are distinguishable.
- Reports iterate `ActionRecord` objects directly, so a missing middle screenshot does not shift later captions.
- The new failed-middle-screenshot report test passes.

## Related regressions still present from tasks 1–5

- The full suite still has three report-preservation failures because mocked workers do not create per-test report directories before `write_report()`.
- The isolated wheel still omits `core/snapshot/dom_walk.js`; importing `core.snapshot` from the wheel raises `FileNotFoundError`.
- Root session conflict detection uses `Path` normalization only; relative and absolute aliases still are not canonicalized against one invocation directory.
- Teardown errors are swallowed and only emitted as text. The completed result is preserved, but the suite can still exit PASS after teardown failed. Cleanup failure needs to affect the final outcome.

## Recommended order

1. Remove the stale `shots` reference and add the `run_goal()` smoke test.
2. Make action-record creation and artifact failure handling exception-safe.
3. Preserve `third_party` through snapshot conversion.
4. Fix the three existing suite-report failures.
5. Make teardown failure visible in the final verdict.
6. Fix wheel package data and canonical session paths.
7. Rerun the full suite and all task 6 probes.
