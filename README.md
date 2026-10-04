# assay — browser checks with evidence-backed verdicts

Write a goal in plain English; the agent executes it against a running web
app and returns a **trustworthy per-stage verdict and evidence report**.

This is a **testing agent, not a browsing agent**. A browsing agent asks "what
should I do next?"; a testing agent asks "did what was supposed to happen
actually happen?" The agent **judges each step by the page's actual end state**,
and distinguishes **FAIL** (the app is broken — the valuable output) from
**ERROR** (the harness got confused — our bug).

Built on the **[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk)**
(default model `claude-sonnet-5`, configurable with `ASSAY_MODEL`). The SDK runs
the agentic loop; the harness supplies the browser tools, live-page delivery,
and the verdict/report machinery.

The name comes from an *assay*: a test of what is actually there, as opposed
to what is claimed.

## What problem this solves

Web applications can pass unit tests while a real user flow is broken: a link
can point to the wrong route, a success message can disappear after reload, a
form can accept invalid input, or a login redirect can lead to a dead page.
Those failures are difficult to cover from a code diff alone because the
expected behavior is spread across the change notes, README, and running app.

`assay` turns that information into a bounded browser test run before a pull
request is submitted. It reads the notes and optional README/diff, proposes
scenarios and observable assertions, executes them in Chromium, and writes
evidence-backed PASS, FAIL, BLOCKED, ERROR, or UNVERIFIED results. It is an
advisory testing agent: it improves feedback before review, but it does not
prove exhaustive coverage or replace normal unit and integration tests.

It is useful when you want to:

- check a feature branch against the behavior described in its notes;
- catch navigation, validation, persistence, and authentication regressions;
- run repeatable browser checks against a local or private application without
  adopting a separate hosted testing service (the configured model provider
  still receives the planner context required for the run);
- inspect screenshots, page evidence, assertion details, and JUnit output when
  a scenario fails; or
- evaluate the harness itself against the included synthetic clean and broken
  fixture app, without a real application or model key.

## How it works

1. You provide a Markdown notes file describing the change. A README and git
   diff are optional context.
2. The planner extracts testable flows and records where each requirement came
   from. It caps the plan using the selected `low`, `medium`, or `high` depth.
3. The executor opens a controlled Chromium session, performs the planned
   actions, waits for the page to settle, and evaluates observable assertions.
4. The policy layer limits navigation origins, blocks mutations by default, and
   enforces time, action, and reported-cost budgets.
5. The reporter preserves partial results and evidence even when a run is
   interrupted. An ambiguous requirement remains `UNVERIFIED` instead of being
   guessed as a pass.

The model supplies planning and interaction decisions. The harness owns browser
actions, origin and mutation policy, budgets, redaction, assertion evaluation,
and final exit semantics.

Coding agents drive the same CLI. A portable skill at
[`skills/browser-check/SKILL.md`](skills/browser-check/SKILL.md) and an
[`AGENTS.md` snippet](integrations/AGENTS.md) for agents that don't load skills
teach the check → fix → rerun loop. They contain orchestration guidance only;
the CLI and harness remain the source of truth. See
[For coding agents and CI](#for-coding-agents-and-ci).

---

## Quick start

```bash
pip install -e ".[dev]" flask
assay --help
assay check --help
```

No browser or API key is needed for unit tests:

```bash
pytest tests/test_plan.py tests/test_executor.py tests/test_budget.py \
       tests/test_check_report.py tests/test_fixture_ground_truth.py -q
```

---

## From a fresh checkout to a full Driftline check

This is the complete developer flow for the hosted synthetic Driftline demo.

```bash
git clone https://github.com/vikrambj2019/assay.git
cd assay
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
docker compose build
```

Set the model key and hosted Driftline values in `.env`:

```env
ASSAY_BASE_URL=https://driftline-demo.onrender.com/v/clean
ASSAY_LOGIN_URL=https://driftline-demo.onrender.com/v/clean/login
ASSAY_ALLOWED_ORIGINS=https://driftline-demo.onrender.com
ASSAY_TEST_USERNAME=demo@example.com
ASSAY_TEST_PASSWORD=demo1234
ASSAY_ALLOW_MUTATIONS=true
```

Create change notes, then run the full check with browser recording:

```bash
cat > changes.md <<'EOF'
# Profile editing
- A signed-in user can update their phone number.
- Saving shows a success message.
- Reloading keeps the new phone number.
- Invalid values show a visible validation error.
EOF

assay check --notes changes.md --readme README.md --diff main \
  --depth high --record-video --output results/driftline-check
```

Open `results/driftline-check/summary.md`, `report.html`, and the
scenario-level `video.webm` files in VS Code. The Docker equivalent is:

```bash
docker compose run --rm agent check --notes changes.md --readme README.md \
  --diff main --depth high --record-video \
  --output /app/results/driftline-check
```

---

## `assay check` — pre-PR browser check

One command reads your changes, generates a bounded test plan, executes it in
Chromium, and writes evidence-backed results:

```bash
assay check \
  --notes changes.md \
  --readme README.md \
  --diff main \
  --depth medium
```

The tool **does not claim exhaustive coverage**. It is an advisory pre-PR check.

### Typical workflow

```bash
# 1. Start the application locally.
# 2. Describe the change and expected behavior.
cat > changes.md <<'EOF'
# Record editing
- Users can rename a record.
- Saving and reloading keeps the new name.
- An empty name is rejected with an error.
EOF

# 3. Configure credentials and the application URL in .env.
cp .env.example .env

# 4. Generate and execute a bounded browser plan.
assay check --notes changes.md --readme README.md --diff main --depth medium
```

Use `--plan-only` when you want to review the generated scenarios before any
browser interaction. Use `--depth low` for a quick smoke check, `medium` for
normal feature work, and `high` when boundary and repeatability checks justify
the additional time and model cost.

The command exits `0` only when all required scenarios pass. It exits `1` for a
confirmed application failure and `2` for an incomplete, blocked, or harness
error run. This makes the result usable in a local pre-PR script while keeping
uncertainty visible.

### Command options

| Option | Purpose |
|---|---|
| `--notes PATH` | Markdown notes describing the change. Required unless `--plan` is given. |
| `--plan PATH` | Rerun a saved `plan.json` exactly as written, without re-planning. |
| `--only ID` | With `--plan`: run only this scenario and its prerequisites. Repeatable. |
| `--readme PATH` / `--diff REF` | Extra planning context. `--diff` includes committed, staged, and unstaged changes since the merge-base. |
| `--depth low\|medium\|high` | Scenario, action, and time caps (see [Depth presets](#depth-presets)). |
| `--url URL` | Application URL; overrides `ASSAY_BASE_URL`. |
| `--output DIR` | Artifact directory (default `results/check`). |
| `--format text\|json` | `json` prints one summary document to stdout and all logs to stderr. |
| `--plan-only` | Write the plan without executing scenarios (planning still calls the model). Prints a scenario table and the commands to run all or some of it. |
| `--record-video` | Save a browser recording per scenario as `<output>/<scenario-id>/video.webm`, embedded in `report.html` (also `ASSAY_RECORD_VIDEO=true`). |
| `--max-seconds`, `--max-actions`, `--max-cost-usd` | Override the depth budget. |

### For coding agents and CI

The CLI is the stable interface; every harness (Claude Code, Codex, Cursor, CI
scripts) calls it the same way.

```bash
assay check --notes changes.md --diff main --depth low --format json --output results/check
```

With `--format json`, stdout carries exactly one JSON document (schema
`assay.check.summary`, version 1) and nothing else, so `| jq` always parses. The
same document is saved as `summary.json`, and a PR-ready `summary.md` is written
next to it. Abridged:

```json
{
  "schema": "assay.check.summary",
  "schema_version": "1",
  "status": "fail",
  "exit_code": 1,
  "exit_meaning": "at least one confirmed application failure",
  "complete": true,
  "scope": "full",
  "plan": {"path": "results/check/plan.json", "sha256": "9f2c…", "source": "generated"},
  "counts": {"PASS": 2, "FAIL": 1, "total": 3},
  "failures": [{
    "scenario_id": "s-002",
    "title": "Profile change survives reload",
    "reason": "'(555) 999-4321' not visible after reload",
    "assertions": [{"id": "a-003", "expected": "Reloading shows the new phone number",
                    "check": {"type": "persistence", "text": "(555) 999-4321"},
                    "observed": "…"}]
  }],
  "needs_attention": [],
  "artifacts": {"report": "results/check/report.html", "summary_md": "results/check/summary.md"},
  "rerun": {"failed": "assay check --plan results/check/plan.json --format json --output results/check --only s-002"}
}
```

`status` is one of `pass`, `fail`, `incomplete`, `planned`, `invalid_input`, or
`planning_failed`. `needs_attention` lists ERROR, BLOCKED, and UNVERIFIED
scenarios with a hint about what to do. Fields are only added within a schema
version.

**Frozen reruns.** After fixing a failure, rerun the *same* plan rather than
planning again, so expectations can't drift toward whatever the app now does:

```bash
assay check --plan results/check/plan.json --only s-002 --format json --output results/check
```

`--plan` copies the plan byte-for-byte and records its `sha256` in
`results.json` and the summary. `--only` also runs prerequisites; other
scenarios are reported as SKIPPED with the reason "not selected", and the
summary's `scope` becomes `partial`. A partial run is never a full pass.

The [browser-check skill](skills/browser-check/SKILL.md) and the
[`AGENTS.md` snippet](integrations/AGENTS.md) encode the full loop and its
rules: at most two fix-and-rerun cycles, never weaken notes or edit the plan
after a FAIL, and never report UNVERIFIED, BLOCKED, or a partial run as a pass.

### `.env` setup

Copy `.env.example` to `.env` and fill in the values. The process environment
always wins over `.env`; CLI arguments win over both.

The project was renamed from `bta` before its first release. Old `BTA_*`
variables are ignored, and each one still set prints a warning naming its
`ASSAY_*` replacement.

```
ANTHROPIC_API_KEY=sk-ant-...        # required for planning and execution

ASSAY_BASE_URL=http://localhost:3000  # required: URL of the running app
# ASSAY_LOGIN_URL=http://localhost:3000/login   # optional, defaults to ASSAY_BASE_URL

# Optional test credentials (supply both or neither)
# ASSAY_TEST_USERNAME=testuser@example.com
# ASSAY_TEST_PASSWORD=changeme

# Optional: path to a Playwright storage-state file (bypasses login)
# ASSAY_AUTH_STATE=./auth-state.json

# Depth: low | medium | high (default: medium)
# ASSAY_DEPTH=medium

# Budget overrides (defaults come from depth preset)
# ASSAY_MAX_SECONDS=600
# ASSAY_MAX_ACTIONS=100
# ASSAY_MAX_COST_USD=1.00
```

Do not commit `.env` — it contains credentials. The file is listed in `.gitignore`.

### Depth presets

| Depth  | Max scenarios | Max actions | Max seconds | Coverage emphasis |
|--------|----------:|----------:|----------:|---|
| low    | 3  | 40  | 180   | Changed feature happy path and essential smoke checks |
| medium | 8  | 200 | 900   | Low plus invalid input, persistence, adjacent regressions |
| high   | 15 | 200 | 1 200 | Medium plus boundary cases and selected repeatability checks |

Override any limit with `--max-seconds`, `--max-actions`, or `--max-cost-usd`.
Depth does not change `ASSAY_EFFORT`.

### Supported auth modes

| Mode | How to activate | What the harness does |
|---|---|---|
| None | No credentials or state file | Runs unauthenticated |
| Storage state | `ASSAY_AUTH_STATE=./auth-state.json` | Loads Playwright cookies/localStorage; bypasses login form |
| Credentials | `ASSAY_TEST_USERNAME` + `ASSAY_TEST_PASSWORD` | Performs a credential login using the login URL |

Unsupported MFA or CAPTCHA becomes `BLOCKED`, not `PASS`. Uncertain login state
becomes `UNVERIFIED`. Neither ever silently passes.

### Output artifacts

These files are written to `results/check/` (or `--output DIR`) on every run, including partial runs:

| File | Format | Contents |
|---|---|---|
| `plan.json` | JSON (versioned) | Full test plan with scenarios, assertions, source references |
| `results.json` | JSON (versioned) | Per-scenario verdicts, assertion kinds, budget summary |
| `report.html` | Self-contained HTML | Evidence, goals, assertion types, coverage suggestions |
| `junit.xml` | JUnit XML | CI-compatible; BLOCKED/UNVERIFIED map to `<skipped>` with status in message |
| `summary.json` | JSON (`assay.check.summary` v1) | Compact machine summary; identical to `--format json` stdout |
| `summary.md` | Markdown | PR-ready summary: counts, failures with expected vs observed, items needing attention |
| `<scenario-id>/video.webm` | WebM | Browser recording per scenario, only with `--record-video` |

### Artifact privacy

- Auth-state files (`*.session.json`, `*-auth-state.json`) are listed in
  `.gitignore` and are never included in shareable artifacts.
- Configured credentials (username, password, API keys) are redacted from all
  text artifacts before writing.
- Application-sourced text in HTML reports is HTML-escaped.
- Screenshots and `--record-video` recordings may capture sensitive on-screen data — review before sharing.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | All required scenarios PASS; run is complete |
| 1 | At least one confirmed FAIL (app misbehaved) |
| 2 | No confirmed FAIL but run is incomplete, errored, or blocked |

Empty plans and budget-exhausted runs always exit non-zero.

### Cost limitations

`ASSAY_MAX_COST_USD` enforces a cost threshold at SDK-reported usage boundaries.
SDK usage reporting may be batched — a single large model call can push the
total above the threshold before the next check. This is disclosed in the
`reason` field of any UNVERIFIED scenario caused by the cost limit, and in the
budget summary in `results.json`. **Do not rely on this as a hard cost cap.**

### Explicit exclusions (current release)

- App startup and teardown are not managed by the tool.
- Automatic PR posting is not implemented.
- Cross-browser testing (Firefox, WebKit) is not supported; Chromium only.
- Multiple test identities or roles are not supported.
- Parallel scenario execution is not implemented; scenarios run sequentially.
- Executable fixture/reset hooks are not supported.

---

## Five-minute fixture demo

A synthetic demo app ships in `fixture/`. It includes clean and deliberately
broken flows for login, record creation, edit persistence, form validation, and
navigation — with a pre-built evaluation harness.

The canonical demo notes are in [`examples/shop-change.md`](examples/shop-change.md).
The roadmap for the demo, agent skill, machine-readable output, and videos is
in [`docs/PUBLIC_DEMO_AND_AGENT_PLAN.md`](docs/PUBLIC_DEMO_AND_AGENT_PLAN.md).

### Mocked evaluation (no API key, no browser)

```bash
python -c "
import asyncio, json
from fixture.eval.bugs import BUGS
from fixture.eval.harness import run_mocked_eval
from fixture.eval.report import write_eval_report
from pathlib import Path

report = asyncio.run(run_mocked_eval(BUGS))
write_eval_report(report, Path('eval-out'))
print('Detected:', report.detected_count, '/', report.sample_size - 1, 'bugs')
print('False positives:', report.false_positive_count)
print('Ambiguous (UNVERIFIED):', report.ambiguous_unverified_count)
"
```

### Browser demo (requires Anthropic API key)

```bash
# 1. Copy and configure .env
cp .env.example .env
# (set ANTHROPIC_API_KEY)

# 2. Start the fixture app
flask --app fixture.app.factory:create_app run --port 5173 &

# 3. Run plan-only (no browser interaction, verifies planning works)
assay check --notes examples/shop-change.md --depth low \
          --url http://localhost:5173 --plan-only

# 4. View the generated plan
cat results/check/plan.json | python3 -m json.tool | head -60
```

### Hosted demo: Driftline

[Driftline](https://github.com/vikrambj2019/Driftline) is a synthetic flight-booking
site built for demos and evaluation. It is hosted at
<https://driftline-demo.onrender.com/>, runs entirely in the browser, and gives
every fresh browser its own seeded data, so mutation-enabled runs are safe
there. Variants live under `/v/<id>`: `clean` is the reference site and
`b1`–`b6` each contain planted defects (deliberately undescribed on the site).

```bash
# .env for the hosted demo — ASSAY_ALLOW_MUTATIONS=true is safe here only
ASSAY_BASE_URL=https://driftline-demo.onrender.com/v/clean
ASSAY_LOGIN_URL=https://driftline-demo.onrender.com/v/clean/login
ASSAY_ALLOWED_ORIGINS=https://driftline-demo.onrender.com
ASSAY_TEST_USERNAME=demo@example.com
ASSAY_TEST_PASSWORD=demo1234
ASSAY_ALLOW_MUTATIONS=true
```

```bash
assay check --notes examples/driftline-profile.md --depth low --format json
# then point at a defect variant and compare
assay check --notes examples/driftline-profile.md --depth low --format json \
  --url https://driftline-demo.onrender.com/v/b2
```

Keep the local Flask fixture for fast, deterministic evaluation; use Driftline
for realistic demos and opt-in live-model evaluation.

---

## `agent suite` — NL goal runner

`agent suite <file.yaml>` drives a set of goals concurrently against a running
app. Each goal runs in its own browser; `needs:` orders them (e.g. login before
the rest).

```bash
# Start the fixture app, then:
agent suite suites/fixture-demo.yaml
```

```yaml
# suites/fixture-demo.yaml
defaults:
  base_url: http://localhost:5173
  model: claude-sonnet-4-6
  max_parallel: 1
tests:
  - name: login
    goal: "Open {base_url} and log in using the fixture-login skill."
  - name: record-creation
    needs: [login]
    goal: "Open {base_url}/records and add a new record. Confirm the saved banner."
```

Skills are loaded from `harness/.claude/skills/<name>/SKILL.md`. A synthetic
login skill is provided at `harness/.claude/skills/fixture-login/SKILL.md`.

The real browser runner requires Docker (Node.js + Claude Code CLI in the image):

```bash
docker compose build
docker compose run --rm agent suite suites/fixture-demo.yaml
```

### Verdict taxonomy

| Verdict | Meaning |
|---|---|
| PASS | Expected outcome observed; evidence attached |
| FAIL | App misbehaved (error banner, wrong redirect, missing data) — the valuable output |
| ERROR | Harness problem (infra failure, unresolvable element) — our bug, not the app's |
| BLOCKED | Prerequisite failed or mutation policy prevents execution |
| SKIPPED | Explicitly excluded by `skip: true`; never an agent shortcut |
| UNVERIFIED | Ambiguous requirement, unsupported assertion, or exhausted budget |

---

## Project layout

```
core/               # browser substrate — no agent, no LLM
├── plan.py         # Plan/Scenario/Assertion schema + validation + persistence
├── planner.py      # run_planner: context → validated Plan (injectable adapter)
├── assertions.py   # evaluate_assertion: url_contains / text_visible / persistence / …
├── executor.py     # run_plan: topological execution + budget enforcement
├── budget.py       # RunBudget: time / action / cost limits + FakeClock for tests
├── check_report.py # write_results_json / write_check_html / write_junit_xml / exit_code
├── check_summary.py # summary.json / summary.md, --only selection (agent + CI contract)
├── policy.py       # OriginPolicy + MutationPolicy
├── auth.py         # AuthMode / resolve_auth_setup / verify_authenticated
├── run.py          # ScenarioResult / execution_order / pre_check_scenario
├── context.py      # collect_context: notes + README + git diff (secret-scrubbed)
├── redact.py       # Redactor: masks credentials in all text artifacts
├── config.py       # Config: ASSAY_* env vars with validation
├── browser.py      # BrowserSession + passive evidence capture (console + network)
├── actions.py      # typed Playwright actions by data-assay-index
├── settle.py       # network-idle + DOM-quiet settlement detector
├── snapshot/       # DOM → indexed text (dom_walk.js · capture · render)
├── suite.py        # YAML suite runner: parse → needs-graph → concurrent run_goal
├── schema.py       # Verdict + StepLog + ActionRecord — shared vocabulary
└── report.py       # HTML evidence report for agent suite runs

harness/            # thinking layer — Claude Agent SDK (imports core only)
├── cli.py          # assay / agent entrypoint: suite + check subcommands
├── tools.py        # browser actions as MCP tools with origin policy
├── agent.py        # Claude Agent SDK loop + live transcript + run_goal
├── page.py         # PostToolUse hook: settle → render → deliver live page
└── .claude/skills/ # Agent Skills (<name>/SKILL.md + optional bundled files)
    ├── browser-check/SKILL.md   # portable coding-agent skill (also at skills/)
    └── fixture-login/SKILL.md   # synthetic demo login skill

integrations/
└── AGENTS.md       # browser-check loop for agents that read AGENTS.md instead of skills

fixture/            # synthetic regression evaluation fixture
├── app/factory.py  # Flask demo app — clean + broken variants (BUG-001…005)
└── eval/
    ├── bugs.py     # BugSpec registry (answer labels — never passed to planner)
    ├── harness.py  # run_mocked_eval + EvalReport (no LLM, no browser)
    └── report.py   # write_eval_report (counts, no invented percentages)

suites/             # goal suites (YAML) — the "what to do", one per environment
└── fixture-demo.yaml   # synthetic local demo suite

tests/              # pytest unit tests (no LLM, no network required)
results/            # per-run output: plan, results, report, junit, summary
                    # gitignored — not distributed
```

---

## Docker setup

The image includes Playwright Chromium + Node.js + the Claude Code CLI:

```bash
docker compose build
cp .env.example .env
# (fill in ANTHROPIC_API_KEY and ASSAY_BASE_URL)
docker compose run --rm agent suite suites/fixture-demo.yaml
docker compose run --rm --entrypoint pytest agent -q
```

> **Version pinning:** the Playwright pip package and the `mcr.microsoft.com/playwright/python`
> base image tag must stay on the same version (`1.61.0`), or the package and the
> preinstalled browser binaries drift apart. Both are pinned in `requirements.txt`
> and `Dockerfile`.

---

## Security

- Never commit `.env`. It is listed in `.gitignore`.
- Auth-state files (`*.session.json`) are gitignored and excluded from all artifacts.
- The origin policy (`ASSAY_ALLOWED_ORIGINS`) blocks top-level navigations to
  unlisted origins, including redirects and popups. CDN subresources are always
  allowed. This is a tool-level policy; it is not a server-side guarantee.
- Mutation scenarios (`requires_mutations: true`) are blocked by default
  (`ASSAY_ALLOW_MUTATIONS=false`). This blocks planned create/edit/delete operations
  at the agent tool level; it is not a guarantee the server is read-only.
- Fork CI workflows cannot access repository secrets (GitHub Actions default).
  Live-model evaluation requires `workflow_dispatch` from a trusted committer.

---

## Contributing

See `CONTRIBUTING.md` for setup instructions, the five-minute demo, and how to
add bugs to the regression fixture.

## Harness trust options

PR checks can enable independent failure review and flake handling:

```bash
assay check --notes changes.md --adjudicate-fails --flake-retries 1
```

The check can also stop after the first confirmed failure with `--fail-fast`.
Screenshots, videos, JUnit durations, and the machine-readable summary are
written to the output directory. See [`docs/CI.md`](docs/CI.md) for the
composite GitHub Action.

## License

This project is licensed under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).
Copyright © 2026 Vikram Bandugula and Biplob Das. Commercial use is not permitted under this license.
