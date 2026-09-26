# Browser checks (assay)

<!--
Copy this section into your repository's AGENTS.md (or CLAUDE.md, .cursorrules,
or your coding agent's equivalent instructions file). It is the same contract
as skills/browser-check/SKILL.md, condensed for agents that do not load skills.
-->

After changing user-visible web behavior, verify it in a real browser with
`assay check` before opening a pull request.

**Setup (already done by a human):** the app is running at `ASSAY_BASE_URL`
(local or a trusted test deployment, never production); `ANTHROPIC_API_KEY`
is set; any test credentials are in `.env`. Do not ask for other credentials.
Mutations are blocked unless `ASSAY_ALLOW_MUTATIONS=true`.

**1. Describe the change** in `changes.md` as observable outcomes (what a user
sees), one per bullet.

**2. Run the check** (stdout is one JSON summary; logs go to stderr):

```bash
assay check --notes changes.md --diff main --depth low --format json --output results/check
```

**3. Act on the summary:**

- `exit_code` 0 / `status` `pass`: done at this depth.
- `exit_code` 1 / `status` `fail`: the app is wrong. Use
  `failures[].assertions[]` (`expected` vs `observed`) to fix the application,
  then rerun the same plan with the command in `rerun.failed`:
  `assay check --plan results/check/plan.json --only SCENARIO_ID --format json --output results/check`
- `exit_code` 2 / `status` `incomplete`: read `needs_attention`. ERROR is a
  harness problem, BLOCKED is a prerequisite or policy, UNVERIFIED is
  ambiguous. Do not change application code for these; tell the user.
- `exit_code` 2 / `status` `invalid_input` or `planning_failed`: fix the
  command or configuration as described in `error`.

**4. Before the PR**, rerun the full plan without `--only`:
`assay check --plan results/check/plan.json --format json --output results/check`.
Paste `results/check/summary.md` into the PR description.

**Rules:** at most two fix-and-rerun cycles, then stop and report. Never edit
`plan.json`, weaken `changes.md`, or regenerate the plan after a FAIL. Never
report UNVERIFIED, BLOCKED, SKIPPED, or a partial (`--only`) run as a pass.
Use `--depth low` while iterating and `medium` before the PR.
