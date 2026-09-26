# Contributing

## Requirements

- Python 3.11+
- Node.js 22+ (for the Docker-based browser runner only)
- [Docker](https://docs.docker.com/get-docker/) (optional — needed for real browser runs)

## Local setup (unit tests only)

Unit tests run without a browser or API key.

```bash
git clone <repo>
cd browser-automation-main
pip install -e ".[dev]" flask
pytest tests/test_plan.py tests/test_planner.py tests/test_auth_policy.py \
       tests/test_executor.py tests/test_budget.py tests/test_check_report.py \
       tests/test_context.py tests/test_env_config.py \
       tests/test_fixture_ground_truth.py -v
```

## Local setup (browser integration tests)

```bash
pip install -e ".[dev]" flask
playwright install chromium
pytest tests/ -v --ignore=tests/test_skills.py
```

Browser tests (`test_actions.py`, `test_settle.py`, `test_snapshot_pipeline.py`)
**fail** (not skip) when Chromium is absent. This is intentional — missing
browser deps must not be silently hidden.

## Five-minute fixture demo

Run `assay check --plan-only` against a synthetic fixture app to see plan
generation without executing browser scenarios (no credentials needed beyond
an Anthropic API key):

```bash
# 1. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Start the clean fixture app
flask --app fixture.app.factory:create_app run --port 5173 &

# 3. Create a minimal notes file
echo "# Changes\n- Added record creation form with name/qty/price fields" > demo-notes.md

# 4. Run plan-only (plans but does not execute — no browser mutations)
assay check --notes demo-notes.md --depth low --url http://localhost:5173 --plan-only
```

This writes `results/check/plan.json` without opening a browser.

For the mocked evaluation (no API key needed):

```bash
python -c "
import asyncio, json
from fixture.eval.bugs import BUGS
from fixture.eval.harness import run_mocked_eval
from fixture.eval.report import write_eval_report
from pathlib import Path

report = asyncio.run(run_mocked_eval(BUGS))
write_eval_report(report, Path('eval-out'))
print(f'Detected: {report.detected_count}/{report.sample_size - 1} bugs')
print(f'False positives: {report.false_positive_count}')
"
```

## Running the synthetic demo suite

Start the fixture app and run the suite:

```bash
flask --app fixture.app.factory:create_app run --port 5173 &
agent suite suites/fixture-demo.yaml
```

(Requires Docker for the full browser runner; see `README.md`.)

## Code style

- No formatter is enforced; match the existing style.
- New modules must have a module docstring.
- New public functions must have a short docstring.
- Tests go in `tests/`. No LLM or remote app calls in tests — use fake adapters.

## Adding a bug to the fixture

1. Add the deliberate regression to `fixture/app/factory.py` behind a new
   `BUG-NNN` ID in `active_bugs`.
2. Add a `BugSpec` entry to `fixture/eval/bugs.py` with the stable ID, flow,
   observable failure, detection check, and both clean + broken page states.
3. Add Flask test-client tests to `tests/test_fixture_ground_truth.py`
   covering the clean and broken behavior.
4. Run `pytest tests/test_fixture_ground_truth.py` to confirm all pass.

## Live-model evaluation (opt-in)

Real-model evaluation is triggered manually via the `live-eval` GitHub Actions
workflow. It requires:

- `ANTHROPIC_API_KEY` secret in the repository settings
- A manual workflow dispatch

Results MUST include model ID, configuration, and run count in any published
result. Do not publish percentages from a single run.

## Before opening a PR

```bash
# All unit tests must pass
pytest tests/test_plan.py tests/test_planner.py tests/test_auth_policy.py \
       tests/test_executor.py tests/test_budget.py tests/test_check_report.py \
       tests/test_context.py tests/test_env_config.py \
       tests/test_fixture_ground_truth.py -q

# CLI help must work without credentials
assay --help
assay check --help
```
