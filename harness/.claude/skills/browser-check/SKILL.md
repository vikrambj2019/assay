---
name: browser-check
description: Verify a user-visible web change in a real browser before opening a pull request, using the assay CLI. Use after implementing UI, form, navigation, auth, or persistence changes to a web app that is running locally or in a trusted test deployment.
---

# Browser check with assay

`assay check` plans a bounded browser test from change notes, runs it in
Chromium against a running app, and returns evidence-backed verdicts. The CLI
is the source of truth; this skill only tells you how to drive it. The check
is advisory and complements unit and integration tests.

## Before you run it

1. **The app must already be running.** assay does not start or stop it. Use
   the URL of a local dev server, a trusted test deployment, or a synthetic
   demo such as Driftline. Never point it at production, and never ask the
   user for personal or production credentials.
2. **Configuration comes from the environment or `.env`.** At minimum
   `ASSAY_BASE_URL` and `ANTHROPIC_API_KEY`. `--url` overrides the base URL.
   Only use test accounts the user has already configured
   (`ASSAY_TEST_USERNAME` / `ASSAY_TEST_PASSWORD` or `ASSAY_AUTH_STATE`).
3. **Mutations are off by default** (`ASSAY_ALLOW_MUTATIONS=false`), so
   create/edit/delete scenarios come back BLOCKED. Only enable mutations for a
   local or synthetic app, and only when the user has agreed.
4. **Write the notes.** Create a short Markdown file (for example
   `changes.md`) that states the intended behavior as observable outcomes:

   ```markdown
   # Profile editing
   - A signed-in user can change their phone number on the Account page.
   - After saving, reloading the page still shows the new number.
   - A phone number with fewer than 10 digits is rejected with an error.
   ```

   Describe what a user should see, not how the code works. Keep ambiguous
   requirements ambiguous; assay reports them as UNVERIFIED instead of guessing.

## The loop

1. **First run** (generates and saves the plan):

   ```bash
   assay check --notes changes.md --readme README.md --diff main \
     --depth low --format json --output results/check
   ```

   stdout is one JSON summary; logs go to stderr. Read `status`, `exit_code`,
   `failures`, `needs_attention`, and `rerun` from it. The same document is in
   `results/check/summary.json`.

2. **Act on the result:**

   | Exit | status | What to do |
   |---|---|---|
   | 0 | `pass` | Done at this depth. |
   | 1 | `fail` | The app is wrong. Read `failures[].assertions[]` (`expected` vs `observed`), fix the application code, then rerun the **same plan**. |
   | 2 | `incomplete` | Check `needs_attention`. ERROR = harness problem, BLOCKED = prerequisite or policy, UNVERIFIED = ambiguous or unverifiable. **Do not change application code to make these go away.** Report them to the user. |
   | 2 | `invalid_input` / `planning_failed` | Fix the command, configuration, or notes as the `error` field says. |

3. **Rerun the frozen plan after a fix.** Never regenerate the plan to get a
   different answer:

   ```bash
   assay check --plan results/check/plan.json --format json \
     --output results/check --only SCENARIO_ID
   ```

   The summary's `rerun.failed` field contains this command ready to use.
   `--only` also runs the scenario's prerequisites. Compare `plan.sha256`
   before and after to confirm the expectations did not change.

4. **Before opening the PR**, rerun the full plan without `--only`:

   ```bash
   assay check --plan results/check/plan.json --format json --output results/check
   ```

   A partial run (`"scope": "partial"`) is never a full pass. For a
   substantive change, prefer a fresh `--depth medium` run from the notes.

## Rules

- At most **two** fix-and-rerun cycles. If it still fails, stop and report
  what you tried and what the evidence shows.
- **Never weaken the notes, edit `plan.json`, or drop scenarios after a FAIL.**
  Changing expectations to get a pass is the same as deleting a failing test.
- Never describe UNVERIFIED, BLOCKED, or SKIPPED as passing, and never
  describe ERROR as an application failure.
- Use `--depth low` while iterating, `medium` before the PR, and `high` only
  when boundary and repeatability checks justify the time and cost.
- Use `--plan-only` when you want to review the scenarios before any browser
  interaction. Planning still calls the model. Show the user the scenario list from the
  summary (`scenarios`) and ask whether to run all of it or only some
  (`--plan … --only ID`).
- Add `--record-video` when the user wants a recording of each scenario.
- Do not paste secrets, auth-state contents, or `.env` values into notes or
  reports. Screenshots can contain sensitive data.

## Reporting back

Paste `results/check/summary.md` into the PR description or your reply. It
already contains the verdict counts, failures with expected vs observed,
items needing attention, the plan hash, and the advisory disclaimer. Mention
the path to `report.html` for full evidence.

Exit codes: `0` all selected scenarios passed and the run completed; `1` at
least one confirmed application failure; `2` incomplete, blocked, errored, or
invalid input.
