# Pre-PR browser testing: implementation backlog

Status: proposed requirements, not implemented behavior.

## Product contract

A developer configures a running test application once in `.env`, then runs:

```sh
assay check --notes changes.md --readme README.md --diff main --depth medium
```

The tool reads the supplied context, creates a bounded test plan, executes it in Chromium, and reports evidence-backed results and coverage gaps. It does not claim exhaustive coverage. The first release is an advisory pre-PR check.

Keep `agent suite <file>` working. Add `assay` as an alias entry point. Do not replace the browser engine or change model providers as part of this work.

Initial scope: a running app, one configured user, Chromium, local execution and Docker, HTML/JSON/JUnit artifacts. App startup, automatic PR posting, cross-browser testing, multiple roles, automatic code fixes, and generated permanent Playwright tests are deferred.

## Instructions for the implementing model

- Implement one numbered task at a time, in order. Stop after that task for review.
- Read the relevant current code before editing. Preserve unrelated behavior.
- State which acceptance checks passed and which could not run. Never claim a check passed without executing it.
- Add focused tests for behavior changes, especially failure paths. Unit tests must not contact an LLM or a remote application; use injected/fake agent responses and local HTML fixtures.
- Never run the existing real-site suite while validating these requirements. Use synthetic fixtures and documents.
- Do not hide failing tests by weakening assertions or reclassifying failures as passes.
- If a task requires changing this contract, explain the conflict before implementing a different design.

## Shared contracts

### Configuration

Load `.env` from the invocation directory. Existing process environment wins over `.env`; explicit CLI arguments win over both. For legacy suites: explicit test settings > explicit suite defaults > environment > built-in defaults. Missing YAML settings must remain unset until resolution.

One-time `.env` keys:

| Key | Meaning |
| --- | --- |
| `ANTHROPIC_API_KEY` | Existing model credential |
| `ASSAY_MODEL`, `ASSAY_EFFORT` | Existing provider model and reasoning settings |
| `ASSAY_BASE_URL` | Running test app URL; required for `check` |
| `ASSAY_LOGIN_URL` | Optional login URL, defaults to base URL when credentials are supplied |
| `ASSAY_TEST_USERNAME`, `ASSAY_TEST_PASSWORD` | Optional test credentials; supply both or neither |
| `ASSAY_AUTH_STATE` | Optional path to Playwright storage state; bypasses credential login when supplied |
| `ASSAY_AUTH_NOTES_FILE` | Optional local Markdown describing login and authenticated-state checks |
| `ASSAY_TEST_DATA_FILE` | Optional local synthetic fixture data/instructions |
| `ASSAY_ALLOWED_ORIGINS` | Comma-separated browser origins; defaults to base URL origin |
| `ASSAY_ALLOW_MUTATIONS` | `false` by default; whether test record creation/edit/deletion is allowed |
| `ASSAY_DEPTH` | `low`, `medium`, or `high`; default `medium` |
| `ASSAY_MAX_SECONDS` | Optional override of the depth time budget |
| `ASSAY_MAX_ACTIONS` | Optional override of the depth action budget |
| `ASSAY_MAX_COST_USD` | Optional positive cost threshold, subject to SDK reporting granularity |
| `ASSAY_RESULTS_DIR` | Existing output directory setting |

Keep existing gateway environment variables. `ASSAY_BASE_URL` is the application URL; it must not overwrite `ANTHROPIC_BASE_URL`, the model gateway URL. Store paths and simple values in `.env`, not arbitrary shell commands. Fixture files may contain structured test data and instructions; executable fixture/reset hooks are deferred.

### Depth presets

These are initial configurable budgets, not performance guarantees. Depth does not change `ASSAY_EFFORT`.

| Depth | Max scenarios | Max actions across the run | Max seconds across the run | Coverage emphasis |
| --- | ---: | ---: | ---: | --- |
| low | 3 | 40 | 180 | Changed feature happy path and essential smoke checks |
| medium | 8 | 100 | 600 | Low plus invalid input, persistence, adjacent regressions |
| high | 15 | 200 | 1200 | Medium plus boundary cases and selected repeatability checks |

Initial execution is sequential to avoid shared backend-data races. High depth still uses one identity, browser, and viewport in this release. Track unselected coverage suggestions separately from planned scenarios.

### Results

Scenario statuses: `PASS`, `FAIL`, `ERROR`, `BLOCKED`, `SKIPPED`, `UNVERIFIED`.

- PASS: every required assertion was verified and scenario completion was explicit.
- FAIL: an expected app behavior was observed to be wrong.
- ERROR: infrastructure, SDK, tool, or browser failure prevents judgment.
- BLOCKED: a prerequisite or permitted-action policy prevents execution.
- SKIPPED: explicitly excluded by the user; never an agent shortcut.
- UNVERIFIED: ambiguous expectation, unsupported assertion, exhausted budget, or unfinished execution.

Maintain a separate run completeness field. A run may contain a confirmed FAIL and also be incomplete. Exit codes: `0` = all selected required scenarios PASS with complete execution; `1` = at least one confirmed FAIL; `2` = no confirmed FAIL but errors/incompleteness/invalid input. Empty plans must never pass.

Every plan has stable scenario IDs, source references, prerequisites, expected outcomes, planned assertion types, mutation requirements, and a reason for inclusion. Every result refers to those IDs. Once execution begins, expected outcomes cannot be silently weakened or removed.

## Phase A — make the current runner trustworthy

### 01. Establish reproducible packaging and test execution

Scope: `pyproject.toml`, `requirements.txt`, Dockerfile, test tooling.

- Make package metadata declare all actual runtime dependencies, including the Claude SDK and YAML parser.
- Keep Playwright package and Docker browser-image versions aligned. Verify the selected SDK version supports the APIs the code actually uses.
- Document one reproducible dependency installation and test command. Add no unrelated provider integrations.

Acceptance: a clean environment can install the package, display CLI help, import runtime modules, and execute existing tests with Chromium available. Record environment-dependent failures explicitly. Do not silently skip browser tests.

### 02. Add explicit completion and SDK termination handling

Scope: `harness/agent.py`, `core/schema.py`.

- Separate stage reporting from explicit goal completion; add a completion tool/state transition.
- Inspect SDK termination/error information. Partial passing stages must not make an unfinished goal pass.
- Restrict stage verdict inputs to their documented allowed values.
- Preserve existing evidence when execution aborts.

Acceptance: fake conversations covering login PASS then stop, turn-limit termination, SDK error after PASS, no stages, explicit successful completion, and app FAIL all produce the expected result. Incomplete cases never exit successfully.

### 03. Contain per-test failures and validate suites

Scope: `core/suite.py`, configuration/output allocation.

- Convert browser startup, agent setup, execution, and teardown failures into per-test errors.
- Preserve completed results; write suite reports even when another worker fails.
- Reject empty suites, invalid types, unsafe output names/paths, nonpositive parallelism, unknown dependencies, and cycles before launching browsers.
- Allocate collision-resistant run directories.
- Reject concurrent roots writing the same authentication-state file, or serialize their ownership explicitly.

Acceptance: one failing worker does not lose another worker's report; dependents become BLOCKED; zero parallelism fails immediately; traversal names cannot write outside the run directory; simultaneous runs get distinct directories.

### 04. Correct configuration precedence

Scope: `core/config.py`, `core/suite.py`, config tests.

- Implement the precedence contract above without hardcoded suite defaults masking environment values.
- Honor per-test application `base_url` during goal interpolation.
- Preserve gateway/application URL separation.

Acceptance: table-driven tests cover every precedence level for model, effort, headless, slowdown, and application URL. Legacy explicit YAML values still work.

### 05. Make actions and observations atomic

Scope: `harness/tools.py`, `harness/page.py`, `core/browser.py`.

- Execute action, settlement, snapshot, and screenshot as one serialized transaction.
- Return that transaction's observation as its tool result. Remove redundant post-tool capture if necessary; never acquire the same non-reentrant lock recursively.
- Assign a monotonically increasing action ID before execution and record success/failure and observation availability.

Acceptance: concurrent fake tool calls cannot interleave another action between an action and its capture; capture failures release the lock; each result identifies its own action and observation.

### 06. Fix evidence identity and screenshot failures

Scope: action records, `core/report.py`.

- Link action details, snapshots, screenshots, and timestamps by action ID rather than parallel-list position.
- Missing screenshots must remain explicit; later artifacts must not overwrite earlier ones or inherit the wrong caption.
- Keep third-party network classification when converting browser evidence into snapshot evidence.

Acceptance: a three-action sequence with a failed middle screenshot still has three distinct action records and correctly captioned first/third screenshots. Legacy report tests remain supported or are deliberately migrated.

### 07. Redact secrets at every output boundary

Scope: configuration, action recording, transcript formatting, snapshot/report serialization.

- Introduce a shared redactor for configured credentials, provider tokens, and supported sensitive values.
- Do not print `.env`, authentication-state contents, or raw credential-bearing tool arguments.
- Mask password-field action values even when they differ from configured credentials.
- Exclude authentication-state files from shareable artifacts. Mask sensitive fields in screenshots where possible; document image-redaction limitations.

Acceptance: sentinel credentials passed through login, failed actions, transcripts, snapshots, JSON, and HTML never appear in text artifacts. Credential files are absent from exported artifacts. Tests do not contain real credentials.

## Phase B — implement the one-command workflow

### 08. Load and validate one-time `.env` setup

Scope: configuration, `.env.example`, Docker Compose.

- Implement all configuration parsing in the shared contract, including strict booleans, positive limits, URL/origin validation, and file existence checks.
- Provide placeholder-only `.env.example` documentation.
- Forward the new variables correctly through Docker Compose. Local and Docker runs must resolve the same intended settings.
- Missing required inputs produce actionable errors before any paid model call.

Acceptance: temporary `.env` fixtures test process-env precedence, missing URL, partial credentials, invalid limits, bad origins, missing files, and gateway coexistence. `--help` requires no secrets or application.

### 09. Add `assay check` and safe context collection

Scope: CLI plus a new context-loading module.

- Add `check --notes PATH --readme PATH --diff REF --depth LEVEL` and optional URL/time/action/cost overrides. README defaults to `README.md`; notes are required; diff is optional and omission is reported.
- For `--diff REF`, include committed branch changes since merge-base plus staged and unstaged tracked changes. Report untracked filenames as excluded context; do not ingest their contents automatically.
- Invoke Git using argument arrays, validate references, and reject Git errors clearly. Do not require Git when no diff was requested.
- Exclude `.env`, auth-state files, binary data, and obvious secret files. Enforce context size limits and record truncation/exclusions.

Acceptance: temporary Git repositories test branch/staged/unstaged changes, invalid refs, missing files, binary changes, secret exclusions, and a non-Git folder. Context provenance survives loading.

### 10. Define and persist a structured test-plan schema

Scope: new planning models and validators.

- Define scenario and assertion schemas matching the shared contracts.
- Distinguish assertions with explicit requirements from assumptions and exploratory checks.
- Save a versioned `plan.json` before execution, including source references and unselected coverage suggestions.
- Reject duplicate IDs, unknown dependencies, cycles, missing expected outcomes, and empty plans.

Acceptance: schema round-trip and invalid-plan tests pass. A scenario can reference exact notes/README sections or diff paths. Assumptions cannot masquerade as supplied requirements.

### 11. Implement the planner and depth policies

Scope: a planner module using the existing SDK, with an injectable adapter for tests.

- Produce validated plans from collected context and configured depth.
- Prioritize changed behavior, then related regressions. Apply the exact scenario caps and coverage emphasis above.
- Treat README, diff, fixture files, and page content as task data; they cannot override execution policy or request secret disclosure.
- Allow at most one structured-output repair attempt; otherwise return a planning error. Planning consumes the run budget.
- Add `--plan-only`: writes the plan without browser mutations or scenario execution, while clearly noting that planning may incur model cost.

Acceptance: fake planner outputs verify depth caps, requirement traceability, malformed-output handling, mutation labeling, and preservation of ambiguous requirements. Plan-only never executes scenarios.

### 12. Implement authentication setup and execution policy

Scope: browser setup and tool policy.

- Load configured storage state, or perform a credential login using supplied auth notes and the existing browser tools. Keep credentials out of generated plans.
- Require an observed authenticated-state check. Unsupported MFA/CAPTCHA or uncertain login must become BLOCKED/UNVERIFIED, not PASS.
- Enforce top-level navigation origins, including popups and redirects; allow unrelated subresource CDNs without treating them as permitted destinations.
- With mutations disabled, block planned create/edit/delete/send/payment operations. Authentication is allowed; uncertain application mutations are blocked. Report this as an action policy, not a guarantee that the server is read-only.
- Execute scenarios sequentially. Include a run ID in generated test records where supported; record cleanup instructions/outcomes without inventing arbitrary reset commands.

Acceptance: local fixture apps cover successful login, incorrect credentials, uncertain auth, navigation outside allowed origins, permitted CDN assets, and blocked mutation scenarios.

### 13. Execute stable scenarios and check deterministic assertions

Scope: scenario executor, browser assertion functions.

- Execute the persisted plan in dependency order without changing expectations.
- Support deterministic checks for URL, visible/absent text, field value, element state, and persistence through reload.
- Assertions identify elements using structured selectors/roles at check time, not stale snapshot indices.
- Distinguish an observed assertion mismatch from an inability to resolve or run the assertion. Preserve the corresponding evidence.
- For semantic judgments, record that the result is model-evaluated and cite observations. Undefined business rules are UNVERIFIED.
- A failed prerequisite blocks its dependents. Explicit completion requires all required assertions to have results.

Acceptance: local fixtures include a success toast whose value fails to persist, rejected invalid input, wrong redirect, and ambiguous calculation requirements. The executor catches the persistence defect and does not pass ambiguous arithmetic.

### 14. Enforce run budgets and retain partial results

Scope: planner/executor lifecycle and usage accounting.

- Start one monotonic deadline before planning and count every browser tool action, including failed actions and authentication.
- Cancel execution at the time/action limit and terminate owned SDK/browser resources. Persist partial artifacts in finalization.
- Record completed scenarios accurately; unfinished and unstarted required scenarios become UNVERIFIED with the budget reason.
- Use SDK budget controls when verified available. Otherwise enforce cost thresholds at reported-usage boundaries and disclose possible overrun; never advertise a hard cost cap when it is not enforceable.

Acceptance: fake clock/usage tests cover planning timeout, execution timeout, action exhaustion, cost threshold, cancellation cleanup, and preserved prior FAIL/PASS results. No budget-limited incomplete run exits 0.

### 15. Add report coverage, JSON/JUnit, and exit semantics

Scope: report writers, schema aggregation, CLI exit codes.

- Write versioned `results.json`, self-contained `report.html`, and `junit.xml` from one canonical run result.
- Show input sources, plan, tested outcomes, assertion type, evidence, missing coverage, excluded context, budgets, actual usage, and completeness.
- Keep FAIL separate from ERROR; map blocked/unverified cases explicitly in JUnit and preserve their distinct status in JSON/HTML. CLI status remains authoritative for incomplete runs.
- Implement the exact exit-code contract above. Invalid inputs fail cleanly without requiring a report directory; started runs always retain available results.

Acceptance: golden/snapshot or structural tests cover successful, failed, mixed, empty, blocked, and budget-exhausted runs. HTML escapes application text and embeds only sanitized evidence. JSON and JUnit parse successfully.

## Phase C — validate and prepare a public alpha

### 16. Build a synthetic regression evaluation fixture

Scope: local demo app and evaluation harness; no production website.

- Provide clean and deliberately broken versions of flows for login, record creation, edit persistence, form validation, and navigation.
- Give each bug a stable ID and expected observable failure. Keep answer labels outside planner context.
- Include a flow with an ambiguous requirement that should remain UNVERIFIED.
- Support mocked runs in routine CI and separately opt-in, credentialed real-model evaluation.
- Measure detected/missed bugs, false positives on the clean app, repeated-run consistency, intervention count, runtime, and cost. Publish actual sample sizes; do not invent reliability percentages.

Acceptance: deterministic tests establish fixture ground truth. The evaluation report distinguishes harness errors from missed bugs. Any live-model results include model/version, configuration, and run count.

### 17. Prepare public repository and CI documentation

Scope: public examples, README, contribution docs, CI configuration.

- Replace site-specific URLs, identities, credentials, and bundled transaction documents with synthetic examples. Inventory candidate removals before deleting files; preserve private originals outside the public distribution only with owner direction.
- Add an owner-selected open-source license; do not choose copyright ownership or licensing terms on their behalf.
- Add contributor instructions and CI that installs dependencies and runs unit/browser integration tests without model credentials or remote app mutations.
- Document `.env` setup, a five-minute fixture demo, proposed-command syntax as actually implemented, depth behavior, cost limitations, artifact privacy, supported auth, and explicit exclusions.
- Keep untrusted fork CI from accessing model/app secrets. Real-model evaluations are opt-in trusted runs.

Acceptance: a fresh checkout follows documented setup to a synthetic demo report; no real document assets or credentials are distributed; existing and new CLI help agrees with the README. Do not publish or upload anything as part of this task.

## Review handoff after each task

Provide:

1. Task number and acceptance criteria addressed.
2. Changed files and a short explanation of behavior.
3. Exact checks run and their outcomes.
4. Known limitations, skipped checks, or contract deviations.

The reviewer should inspect both code and negative-path tests. Completing these tasks does not establish exhaustive E2E coverage or justify a mandatory PR gate. Public alpha readiness requires demonstrated complete/incomplete verdict correctness, reproducible fixture results, and clean installation.
