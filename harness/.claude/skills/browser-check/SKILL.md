---
name: browser-check
description: Run the local assay pre-PR browser check from a coding agent using change notes, README context, and a bounded depth.
---

# Browser check

Use this skill after implementing a user-visible web change and before opening
a pull request. The check is advisory and complements unit and integration
tests.

## Procedure

1. Find the repository's change notes. If none exists, create a short Markdown
   file describing the intended behavior and observable outcomes.
2. Confirm that the application under test is local, a trusted test deployment,
   or the repository's synthetic fixture. Never use a production URL or ask for
   a user's personal credentials.
3. Run the existing CLI from the repository root:

   ```bash
   assay check --notes PATH --readme README.md --diff main --depth medium
   ```

   Use `--depth low` for a smoke check, `medium` for normal changes, and
   `high` only when boundary and repeatability checks justify the extra cost.
4. Use `--plan-only` first when the plan needs review or the application is not
   ready for browser mutations.
5. Read `results/check/results.json`, `report.html`, and `junit.xml`. Report the
   result and artifact paths to the user.

## Verdict handling

- `PASS`: the required observable outcome was confirmed.
- `FAIL`: the application did not meet the requirement; summarize the evidence.
- `BLOCKED`: a prerequisite or mutation policy prevented the scenario.
- `ERROR`: the harness failed; do not describe it as an application failure.
- `UNVERIFIED`: the requirement was ambiguous or unsupported; do not convert it
  to PASS.

Exit code `0` means all required scenarios passed, `1` means a confirmed app
failure, and `2` means the run was incomplete, blocked, or errored.

## Safety and privacy

Keep `ASSAY_ALLOW_MUTATIONS=false` unless the user explicitly authorized a local
test mutation. Do not weaken origin policy, expose secrets in notes, or paste
auth-state contents into a report. Review screenshots before sharing them.
