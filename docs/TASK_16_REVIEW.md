# Task 16 review — synthetic regression evaluation fixture

## Result

Task 16 is implemented. The fixture covers clean and deliberately broken login, record creation, edit persistence, form validation, and navigation flows. Bug IDs are stable, answer labels stay in `fixture/eval/bugs.py`, and `AMBIGUOUS-001` remains `UNVERIFIED`.

## Fixes applied

- Added `fixture/eval/live.py`, an explicitly gated (`BTA_EVAL_LIVE=1`) live evaluation adapter. Trusted runners provide the real browser/model callback; reports require model metadata and record run count, verdicts, interventions, cost, runtime, and repeated-run consistency.
- Added raw operational metrics to `EvalReport` and `eval_report.json`. Mocked runs report measured runtime and zero model cost/interventions; no percentages are invented.
- Added Flask to `requirements.txt` and removed the fixture test skip, so the supported Docker installation executes the tests and missing dependencies fail loudly. The trusted workflow now runs the fixture suite instead of printing a placeholder.

## Verification

- `python3 -m pytest -q -p no:cacheprovider tests/test_fixture_ground_truth.py` → **59 passed**.
- `BTA_EVAL_LIVE=1 python3` callback smoke test → live report produced with `sample_size=12`, intervention count, cost, and consistency counts.
- Rebuild the Docker image before running its fixture command; the old image predates the Flask dependency and reported skips.
