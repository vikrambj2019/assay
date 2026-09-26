# Public demo and coding-agent integration plan

## Goal

Make the project understandable in five minutes and usable from both a human
terminal and a coding agent. The CLI remains the product interface; agent
skills provide thin instructions around that interface.

## Milestones

### 1. Runnable synthetic demo — first implementation

- Keep the local Flask fixture synthetic and self-contained.
- Add a canonical notes example under `examples/`.
- Add one documented command sequence that starts the app, runs a plan-only
  check, runs mocked evaluation, and explains generated artifacts.
- Keep all demo data, credentials, and URLs local.

### 2. Portable coding-agent skill — first implementation

- Add `skills/browser-check/SKILL.md`.
- Teach an agent when to run `assay check`, how to select depth, and how to read
  verdicts and reports.
- Require local or trusted fixture apps, preserve mutation policy, and never
  request production credentials.
- Keep the skill independent of any one coding-agent vendor.

### 3. Stable machine interface — implemented

- Add `--format json` and `--output PATH` to `assay check`.
- Add frozen-plan reruns (`--plan PATH`, `--only SCENARIO_ID`) so a coding
  agent's fix-and-verify loop cannot drift its expectations.
- Preserve the existing report schemas and exit codes.
- Add a small JSON summary suitable for CI and coding-agent consumption.

### 4. Demo packaging and video

- Hosted synthetic demo: Driftline (https://driftline-demo.onrender.com),
  a separate repository, used as the realistic demo target.
- Add a one-command Docker demo service for the fixture app.
- Record short synthetic videos: install/demo, failure report, and coding-agent
  invocation.
- Do not record private applications, credentials, or real user data.

## Acceptance checks

- A fresh checkout can follow the README demo without private services.
- The mocked fixture evaluation runs without a model key.
- A coding agent can invoke the skill and receive a report path plus exit code.
- JSON output is stable enough for CI parsing.
- Full unit and fixture tests remain green.
