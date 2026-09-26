# Running the browser automation

Everything runs through one command: **`agent suite <file.yaml>`**. The suite is
the *what to do* (goals + config + `needs:` order); the skills under
`harness/.claude/skills/` are the *how to do*.

## Run the whole suite

`suites/fixture-demo.yaml` defines every goal, its config, and its `needs:` deps.
A root test (login) runs first and writes the shared session; the rest run in
parallel up to `max_parallel`. Rebuild once after a `requirements.txt` change, then:

```bash
docker compose build                                   # once (deps changed)
docker compose run --rm agent suite suites/fixture-demo.yaml
```

## Watch it on screen (headed)

Mark the tests you want to watch with `headless: false` (and optionally
`slowmo_ms:`) in the YAML, then pass the X11 display:

```bash
xhost +local:
docker compose run --rm -e DISPLAY=$DISPLAY agent suite suites/fixture-demo.yaml
```

## Per-test config (set in the YAML, not the CLI)

- `headless: false` — show the browser for this test
- `slowmo_ms: 800` — pause between actions so a headed run is watchable
- `model:` / `effort:` — override the model or reasoning effort per test
- `skip: true` — park a test without deleting it
- `needs: [login]` — run only after those tests PASS (dependents get an isolated
  copy of the shared session, so parallel tests never clobber the login)
- `max_parallel:` (under `defaults:`) — how many tests run at once

## Output

One `results/<timestamp>/` per suite run: a subdir per test with its
`report.html` + `step-NN.png` screenshots, a suite `index.html` linking them all,
and a summed cost footer in the terminal.

## Unit tests (this codebase, no LLM / no network)

```bash
docker compose run --rm --entrypoint pytest agent -q
```

## Local install (outside Docker)

The real agent suite still requires Docker (it needs Node.js + the Claude Code CLI
installed in the image). But the unit tests and CLI help run locally:

```bash
pip install -e ".[dev]"       # installs runtime deps + pytest
playwright install chromium   # required by the browser integration tests
pytest -q                     # runs all tests (browser tests need chromium above)
agent --help                  # or: bta --help
```

Browser tests (`test_actions.py`, `test_settle.py`, `test_snapshot_pipeline.py`)
require a real Chromium and will fail, not skip, when it is absent — this is
intentional so missing browser deps are never silently hidden.
