# assay — browser checks with evidence-backed verdicts

Write a goal in plain English; the agent executes it against a running web app
and returns a **per-stage verdict with evidence**. A testing agent, not a
browsing agent: it asks "did what was supposed to happen actually happen?",
judges each step by the page's actual end state, and keeps two things strictly
separate:

- **FAIL** — the app is broken (the valuable output)
- **ERROR** — the harness got confused (our bug, not the app's)

Built on the [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk).
The name comes from an *assay*: a test of what is actually there, as opposed
to what is claimed.

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env   # then set ANTHROPIC_API_KEY and ASSAY_BASE_URL
```

Describe the change in a notes file, then run the check against your running app:

```bash
cat > changes.md <<'EOF'
# Record editing
- Users can rename a record.
- Saving and reloading keeps the new name.
- An empty name is rejected with a visible error.
EOF

assay check --notes changes.md --diff main --depth medium
```

Open `results/check/report.html` for the evidence, or `results/check/summary.md`
for the short version. That's the whole product: **notes in, verdicts out.**

## The workflow: check → fix → rerun

```bash
assay check --notes changes.md --diff main --depth medium   # 1. check
# ... read report.html, fix the app ...
assay check --plan results/check/plan.json --only s-002     # 2. rerun the same plan
```

`--plan` replays the saved plan byte-for-byte (its sha256 is recorded), so
expectations can't drift toward whatever the app now does. `--only` reruns one
scenario plus its prerequisites.

Rules of the loop:

- At most two fix-and-rerun cycles per failure, then escalate to a human.
- Never weaken the notes or edit the plan to make a FAIL go away.
- Never report UNVERIFIED, BLOCKED, ERROR, or a partial run as a pass.
- A FAIL you can't reproduce is a flake until `--flake-retries` says otherwise.

## For coding agents

The CLI is the stable interface — every harness calls it the same way.
A portable skill lives at [`skills/browser-check/SKILL.md`](skills/browser-check/SKILL.md);
agents that read `AGENTS.md` instead of skills get the same loop from
[`integrations/AGENTS.md`](integrations/AGENTS.md).

Give the agent this contract:

```bash
assay check --notes changes.md --diff main --depth low --format json --output results/check
```

- `--format json` prints exactly one JSON document to stdout (schema
  `assay.check.summary`, v1) and all logs to stderr — always parseable.
- The same document is saved as `summary.json`; `summary.md` is PR-ready.
- Exit codes: **0** pass · **1** confirmed app failure · **2** incomplete /
  blocked / error. Only 0 is a green light.
- `needs_attention` in the summary lists ERROR / BLOCKED / UNVERIFIED scenarios
  with hints. `rerun.failed` gives the exact frozen-rerun command.

## CI

There is a composite action (`.github/actions/assay-check/action.yml`) that
installs assay, runs the check, uploads all artifacts, posts `summary.md` as a
PR comment, and gates on the exit code. See [`docs/CI.md`](docs/CI.md) for the
copy-paste workflow and the merge policy (0 = merge, 1 = don't, 2 = human look).

## Command reference

| Option | Purpose |
|---|---|
| `--notes PATH` | Markdown notes describing the change (required, unless `--plan`) |
| `--plan PATH` | Rerun a saved `plan.json` exactly — no re-planning |
| `--only ID` | With `--plan`: run one scenario + prerequisites (repeatable) |
| `--diff REF` / `--readme PATH` | Extra planning context (diff = committed + staged + unstaged vs merge-base) |
| `--depth low\|medium\|high` | Caps: 3/8/15 scenarios, 40/200/300 actions, 180/900/1800s |
| `--fail-fast` | Stop after the first confirmed FAIL |
| `--flake-retries N` | Re-run FAILED scenarios N times; a passing retry reports PASS annotated FLAKY |
| `--adjudicate-fails` | Fresh-eyes model review of each FAIL before it counts (extra model cost) |
| `--plan-only` | Write the plan without executing (planning still calls the model) |
| `--record-video` | Save per-scenario `video.webm`, embedded in `report.html` |
| `--url URL` / `--output DIR` | Override app URL / artifact directory |
| `--format text\|json` | `json`: one summary document on stdout, logs on stderr |
| `--max-seconds` / `--max-actions` / `--max-cost-usd` | Override the depth budget |

## Configuration

`.env` (never commit it — it's gitignored). Environment wins over `.env`; CLI
flags win over both.

```
ANTHROPIC_API_KEY=sk-ant-...          # required: planning + agent + adjudication
ASSAY_BASE_URL=http://localhost:3000  # required: your running app
# ASSAY_LOGIN_URL=...                 # defaults to ASSAY_BASE_URL
# ASSAY_TEST_USERNAME=... / ASSAY_TEST_PASSWORD=...   # credential login
# ASSAY_AUTH_STATE=./auth-state.json  # or: Playwright storage state, skips login
# ASSAY_DEPTH=medium                  # low | medium | high
# ASSAY_ALLOW_MUTATIONS=false         # true lets the agent create/edit/delete
# ASSAY_ADJUDICATE_FAILS=false        # true: independent review of each FAIL
# ASSAY_ALLOWED_ORIGINS=...           # navigation allowlist (defaults to app origin)
```

Unsupported MFA/CAPTCHA → BLOCKED. Uncertain login → UNVERIFIED. Neither ever
silently passes.

## Understanding results

| Verdict | Meaning |
|---|---|
| PASS | Expected outcome observed, evidence attached |
| FAIL | App misbehaved — the valuable output |
| ERROR | Harness problem (our bug, not the app's) |
| BLOCKED | A prerequisite failed, or policy prevented execution |
| SKIPPED | Explicitly excluded; never an agent shortcut |
| UNVERIFIED | Ambiguous requirement or unfinished run — needs a human |

Artifacts in `--output DIR` (written on every run, including partial ones):
`plan.json`, `results.json`, `report.html` (with per-action screenshots),
`junit.xml` (real durations; BLOCKED/UNVERIFIED → `<skipped>`),
`summary.json`, `summary.md`. Credentials are redacted from all text artifacts;
review screenshots/recordings before sharing.

Two honest limitations: `ASSAY_MAX_COST_USD` is enforced at SDK usage-report
boundaries, not as a hard cap — a single large call can overshoot it. And assay
is advisory: it improves signal before review but doesn't prove coverage.

## Demos

**Driftline** (hosted synthetic flight-booking site, [repo](https://github.com/vikrambj2019/Driftline)):
every fresh browser gets seeded data, so mutations are safe there. `clean` is
the reference; `b1`–`b6` have planted defects.

```bash
# in .env: ASSAY_BASE_URL=https://driftline-demo.onrender.com/v/clean (+ login URL,
# allowed origins, demo@example.com / demo1234, ASSAY_ALLOW_MUTATIONS=true)
assay check --notes examples/driftline-profile.md --depth low --format json
```

**Local fixture** (no API key, no browser — deterministic eval):

```bash
.venv/bin/python -c "
import asyncio
from fixture.eval.bugs import BUGS
from fixture.eval.harness import run_mocked_eval
report = asyncio.run(run_mocked_eval(BUGS))
print('Detected:', report.detected_count, '| False positives:', report.false_positive_count)
"
```

## Contributing

See `CONTRIBUTING.md` for setup, the fixture, and how to add regression bugs.

## License

[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).
Copyright © 2026 Vikram Bandugula and Biplob Das. Commercial use is not permitted.
