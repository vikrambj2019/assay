# Running assay in CI

`assay check` is built to gate pull requests: it plans from the change notes,
runs bounded browser scenarios, and exits with a machine-readable code. The
composite action at `.github/actions/assay-check/action.yml` wraps the whole
flow — install, run, artifact upload, PR comment, and gating.

## Minimal PR workflow

```yaml
name: Browser check

on:
  pull_request:
    branches: ["main"]

permissions:
  contents: read
  pull-requests: write   # for the verdict comment

jobs:
  assay:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Start the app
        run: |
          # whatever your app needs — the check runs against a live URL
          npm ci && npm run build
          npm start -- --port 5173 &
          npx wait-on http://localhost:5173

      - name: Assay browser check
        uses: ./​.github/actions/assay-check
        with:
          app-url: http://localhost:5173
          notes: changes.md
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The action installs assay from the repo the action itself lives in, installs
Playwright Chromium, runs the check, uploads `assay-results/` (report.html,
junit.xml, per-action screenshots, video), posts `summary.md` as a PR comment,
and fails the job on a non-zero exit code.

## What the exit code means for merging

| Exit | Meaning | Merge guidance |
|------|---------|----------------|
| 0 | All scenarios passed | Safe to merge |
| 1 | **Confirmed application failure** | Do not merge — the report names the defect with evidence |
| 2 | Incomplete / blocked / error | Needs a human look — the harness couldn't finish, which is not a green light |

This maps directly onto the verdict taxonomy: only FAIL blocks; ERROR and
UNVERIFIED route to a person instead of silently passing or failing.

## Recommended settings for PR checks

- **`flake-retries: 1`** (the default) — a scenario that fails then passes on
  retry is reported `PASS` annotated `FLAKY`, with both attempts' evidence.
  Browser checks flake; merges shouldn't die for it, but flakes stay visible.
- **`fail-fast: true`** — stop at the first confirmed FAIL. Most PR runs are
  "did I break the thing I touched"; there is no point running the rest.
- **`adjudicate-fails: true`** — each FAIL gets a fresh-eyes model review
  before it blocks a merge. Costs extra model calls; worth it once the check
  gates merging, because a false FAIL that blocks a PR is how teams learn to
  ignore the tool.

## Notes

- The app must be running and reachable at `app-url` before the action runs —
  starting it is the workflow's job (see the example above).
- `ANTHROPIC_API_KEY` must be a repository or environment secret. Fork PRs
  cannot access secrets: run this workflow only on branches from the repo
  itself, or accept that fork PRs skip the browser check.
- The PR comment needs `pull-requests: write`. Without it, set
  `comment-on-pr: false` — the artifacts and the job status still work.
- junit.xml is written with real per-scenario durations, so CI dashboards that
  render test timings work out of the box.
