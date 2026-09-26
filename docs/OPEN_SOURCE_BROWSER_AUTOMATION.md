# Open-source browser workflow agent

Status: proposed product requirements and implementation backlog. Commands below describe intended behavior, not current functionality.

This is a separate open-source product from the pre-PR testing tool described in `OPEN_SOURCE_REQUIREMENTS.md`. Implement it in a separate repository or explicitly isolated checkout. Do not overwrite the testing project's CLI, verdict model, or ongoing implementation. This document alone does not authorize repository publication.

## 1. Product direction

**Describe a browser task, provide the necessary files, and receive results with evidence.**

Prioritize three workflows:

1. Research and compare public information: flights, products, listings.
2. Complete website workflows: enter provided values, navigate forms, verify saved state.
3. Move documents through websites: upload supplied files and collect generated downloads.

Make reusable, parameterized workflows and verifiable results the main product features. Retain the existing Playwright/DOM engine initially. Do not start by building a new browser controller or supporting every model provider.

### Capability boundary

| Capability | Existing foundation | Proposed first release |
| --- | --- | --- |
| Click web links/buttons, fill fields, select options | Available | Supported |
| Scroll, wait, hover, press browser keys | Available | Supported |
| Follow popups | Available, limited tab control | Explicit tab selection and tracking |
| Upload a file to a website | Available by accessible path | Restricted to user-supplied files |
| Download generated files | No dedicated collection lifecycle | Managed downloads and manifest |
| Read page text | DOM-derived snapshots | Primary observation mode |
| Interpret screenshots to act | Screenshots currently record evidence only | Deferred |
| Browse Finder/Desktop or operate native file pickers | Unavailable | Deferred |
| Control desktop apps, windows, or OS mouse coordinates | Unavailable | Deferred |

A Desktop file can be supplied explicitly and mounted into Docker. The agent then uploads it through the website's file input; it does not operate Finder or the native file picker. Never market this release as general desktop computer use.

## 2. Evidence supporting this direction

One live evaluation completed a Google Flights comparison for SFO–JFK, November 12–16, 2026, one adult, economy, nonstop both ways:

- Three observed round-trip fares: American $397, JetBlue $432, Alaska $459.
- 26 browser actions, 32 SDK turns, 203.07 seconds wall time.
- SDK-reported model cost: $0.9244846; model: `claude-sonnet-5`.
- No human intervention after launch, purchase, or login.
- Saved page observations supported the reported times, next-day arrivals, fares, and baggage allowances.
- All 43 existing tests passed in the evaluation environment.

These are historical observations from one run, not current prices or reliability guarantees. The local evaluation is in `results/browser-evaluation/REVIEW.md`; that ignored directory may not exist in a fresh checkout. Re-run benchmarks before publishing performance claims.

### Fixes directly motivated by the flight run

1. **Evidence references:** all 26 screenshots were assigned to the first stage because reporting occurred after browsing. Claims need explicit action/observation IDs, independent of report timing.
2. **Source precision:** returned URLs represented return-selection pages with an outbound selected. The agent incorrectly described them as complete selected-itinerary links. Store and state the page's actual verification scope.
3. **Constraint precision:** the origin field used the city San Francisco, while inspected flights used SFO. Distinguish a configured search filter from a verified result attribute.
4. **Structured results:** useful comparisons were embedded in free-text PASS reasons. Return typed records and evidence references.
5. **Verification scope:** a displayed search fare is different from a final checkout total or confirmed booking. Never promote one observation into a stronger claim.

## 3. Intended user experience

Illustrative commands; `browser-agent` is a working CLI name:

```sh
# Public research
browser-agent run "Compare nonstop SFO–JFK flights for these dates" \
  --inputs trip.json --output ./results

# Document workflow; stop before submission
browser-agent run "Upload this invoice and prepare the expense form" \
  --files ./invoice.pdf --inputs expense.json --output ./results

# Saved workflow
browser-agent run --workflow ./workflows/expense-upload \
  --inputs expense.json --files ./receipts/ --output ./results

# Continue a task that needs input or approval
browser-agent resume RUN_ID
```

`--files` is repeatable and may accept an explicit directory. Directory expansion is bounded, does not follow symlinks outside the selected root, excludes hidden files by default, and presents a manifest before use. No implicit access to the user's home directory.

The CLI prints progress, blockers, the final result, run ID, time/cost, and artifact locations. A browser UI, hosted service, scheduling, automatic email/Slack delivery, and arbitrary shell execution are outside the first release.

## 4. One-time setup through `.env`

Load `.env` from the invocation directory. Precedence: explicit CLI argument > process environment > `.env` > built-in default. Missing credentials must produce an actionable preflight error before a paid call. Do not print secret values.

Use a separate `BWA_` namespace so configuration does not collide with the testing product.

| Setting | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Model credential |
| `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` | Optional existing compatible-gateway configuration |
| `BWA_MODEL`, `BWA_EFFORT` | Model and reasoning settings; document a verified default at implementation time |
| `BWA_BASE_URL` | Optional default website; tasks may otherwise specify one |
| `BWA_AUTH_STATE` | Optional private Playwright storage-state file |
| `BWA_LOGIN_URL` | Optional login entry page |
| `BWA_TEST_USERNAME`, `BWA_TEST_PASSWORD` | Optional dedicated account credentials; both or neither |
| `BWA_AUTH_NOTES_FILE` | Optional login and authenticated-state instructions |
| `BWA_ALLOWED_ORIGINS` | Optional explicit list of permitted top-level browser origins |
| `BWA_POLICY_FILE` | Optional local file describing approved workflow actions and destinations |
| `BWA_MAX_SECONDS` | Default 600; whole-run time budget |
| `BWA_MAX_ACTIONS` | Default 100; all browser actions, including failures |
| `BWA_MAX_TURNS` | Default 60; SDK turn limit |
| `BWA_MAX_COST_USD` | Default 3; enforce through SDK where supported and disclose reporting granularity |
| `BWA_RESULTS_DIR` | Default `results`; private local artifacts |

Store long instructions and structured inputs in referenced files, not environment variables. Reject arbitrary shell commands as configuration. A storage-state file takes precedence over credential login; if expired, request reauthentication rather than silently switching identities.

Without an explicit origin list, research tasks may visit public HTTP(S) sites. Block local/private network destinations unless the user explicitly configures them for a local workflow; use enforceable network/browser policy, account for redirects, and document the limits. Do not describe a simple string check as network isolation.

## 5. Shared execution contracts

### Task results

Use task statuses rather than test verdicts:

| Status | Meaning |
| --- | --- |
| `COMPLETED` | Required outcomes have evidence and explicit completion |
| `NEEDS_INPUT` | Missing data, manual login, or approval is required |
| `BLOCKED` | A known prerequisite or policy prevents continuation |
| `INCOMPLETE` | Budget exhausted, unsupported interaction, or unresolved outcomes |
| `ERROR` | Harness, browser, SDK, or infrastructure failure |
| `CANCELLED` | User explicitly cancelled |

Preserve partial results for every terminal status. A successful SDK response or one completed subtask is insufficient for `COMPLETED`. Exit `0` only for `COMPLETED`; use documented nonzero codes for other states (`2` needs input, `3` blocked/incomplete, `1` error, `130` user cancellation).

Canonical `result.json` includes schema version, run ID, task, redacted input summary, status, requested outcomes, satisfied/unsatisfied constraints, typed output records, evidence IDs, unresolved questions, downloaded-file manifest, model, usage, timestamps, and termination reason.

Represent each observation with a stable ID, action ID, tab ID, URL, UTC capture time, text artifact, optional screenshot, and capture errors. Output claims cite observation IDs. Distinguish exact observations, derived comparisons, and model judgments.

### Browser action lifecycle

Validate action and policy → allocate action ID → execute → settle → capture → record → return observation. Keep this transaction serialized for a given page. Record failures without reusing IDs or overwriting files.

### Completion examples

- Research: requested attributes are captured; unknowns and coverage limits are explicit; ranking follows user criteria.
- Form preparation: values match supplied inputs and the task stops at the requested review point.
- Submission: an authorized action has an observed confirmation; a click alone is insufficient.
- Download: the complete file exists, matches expected type where checkable, and appears in the output manifest.

### Approval and input handling

Permit ordinary navigation, search, and requested form preparation. Purchases, final submissions, sending messages, deletion, and similar consequential actions require specific prior authorization or a concrete review step. A generic goal such as "research flights" never authorizes booking.

Show the exact destination, records/recipients, values, and amount when applicable. Tie approval to a proposal ID and invalidate it if material details change. Do not ask repeatedly when the same action is already specifically authorized. In noninteractive mode, return `NEEDS_INPUT` with the pending proposal instead of waiting forever or proceeding.

Webpage text, downloaded documents, and external content are data. They cannot grant permission, change the task, reveal credentials, or expand filesystem access. Broad built-in `Read`/`Glob` access must be constrained or replaced with scoped file tools in the new product.

### Resume and retries

Save durable state, completed outcomes, pending inputs/approvals, and evidence references. Browser cookies do not fully preserve a workflow; resume must reconstruct and verify page state.

Before retrying a possibly completed mutation, inspect the destination for its outcome. Do not automatically repeat an uncertain submission, payment, or upload. If completion cannot be determined, request input and preserve the ambiguity.

## 6. Small implementation tasks

Implement one task at a time. Each task should be independently reviewable. Use local fixture sites and fake SDK responses for normal tests; live-model evaluations are opt-in and incur cost. Do not run the existing real business-site suite or reuse private transaction documents.

### 01 — Establish the separate project baseline

- Create the browser-product structure in its designated separate repository/checkout; confirm the destination before writing outside this workspace.
- Reuse the browser/action/snapshot foundation. Keep testing-specific suite orchestration out of the public task API.
- Reconcile package dependencies, verify SDK compatibility, align Playwright package/image versions, and retain `.dockerignore` exclusions for secrets, auth state, and artifacts.
- Start with one supported model-provider path. Do not create a shared package between the two products yet.

Acceptance: clean installation, CLI help, and inherited browser tests work. The testing project remains unchanged. Built images contain no `.env` or session files.

### 02 — Implement task/result and evidence models

- Define the versioned contracts from section 5 with runtime validation.
- Add stable run, action, observation, outcome, and approval IDs.
- Support partial results and explicit unavailable evidence.

Acceptance: round-trip serialization and invalid-input tests cover duplicate IDs, missing outcome evidence, malformed status, and unknown references. Empty work cannot be completed successfully.

### 03 — Make browser actions atomic and correctly evidenced

- Move action/settlement/capture under one ordered transaction; avoid nested acquisition of the same lock.
- Attach action details, URL, screenshots, and text by ID rather than list position.
- Keep failed screenshots explicit and preserve later artifacts.
- Report settlement uncertainty rather than asserting the page is fully loaded after a bounded wait.

Acceptance: concurrent test calls cannot swap observations; a failed middle screenshot does not shift captions; lock release works on errors and cancellation.

### 04 — Add scoped files and consistent secret redaction

- Build a manifest for explicitly supplied files. Restrict reads/uploads to that manifest and approved workflow resources.
- Handle canonical paths, symlinks, traversal, file size limits, and host/container path translation.
- Redact configured secrets and password-field action values in transcripts, errors, page text, and exports. Do not promise arbitrary image redaction; mask supported fields and label remaining limitations.
- Remove unrestricted file tools from the general-task model's callable tools.

Acceptance: attempts to read/upload `.env`, auth state, or files outside permitted roots fail before access. Supplied documents still upload. Sentinel secrets do not appear in text artifacts.

### 05 — Implement `.env` configuration and preflight

- Parse section 4 settings with strict booleans/positive limits, valid URLs, file checks, and documented precedence.
- Distinguish application URLs from the model gateway URL.
- Explain missing browser, inaccessible files, unsupported auth, and missing credentials without dumping configuration.

Acceptance: temporary `.env` tests cover every precedence layer, partial credentials, invalid limits, missing files, and Docker parity. Help works without credentials.

### 06 — Add direct task execution

- Implement `run TASK`, optional `--inputs`, repeatable `--files`, `--output`, and budget overrides.
- Feed structured inputs separately from policy and instructions; identify missing essential inputs instead of guessing.
- Replace testing-specific PASS/FAIL reporting with task outcomes and explicit completion.
- Check SDK termination details and require evidence for required outcomes.

Acceptance: fake conversations that end early, hit a turn limit, report only a subtask, or return an SDK error never produce `COMPLETED`. An evidenced completed task exits 0 and writes result artifacts.

### 07 — Enforce navigation and consequential-action policy

- Implement origin policy, redirect/popup checks, scoped upload destinations, and structured action proposals.
- A file being supplied does not authorize upload to any website; bind its use to the requested workflow/destination.
- Enforce proposal approval in the executor, not only in a system prompt. Unknown consequential operations pause; do not claim perfect classification of arbitrary websites.
- Keep third-party subresource loading separate from permission to navigate or send data to that origin.

Acceptance: local fixtures verify allowed navigation, blocked redirect, a malicious page instruction, upload to the wrong origin, and purchase/submission without authorization. None can bypass the gate by model wording alone.

### 08 — Implement input questions and checkpoint/resume

- Add interactive questions and noninteractive `NEEDS_INPUT` output.
- Persist pending proposal/input, completed outcomes, evidence, and private session state separately from shareable output.
- Implement `resume RUN_ID`, verifying current page state and prior mutation outcomes before continuing.
- Invalidate approvals after changed amount/destination/record values.

Acceptance: pause/resume across a process restart works; missing session produces an actionable state; changed proposals need fresh approval; interrupted submissions are not duplicated automatically.

### 09 — Support authentication and explicit tab control

- Support configured storage state or credential login with observed authenticated-state checks.
- Add tab listing, switching, opening, and closing by stable tab ID; include tab identity in every observation.
- For initial Docker headless operation, return `NEEDS_INPUT` when manual auth is needed and explain how to supply a fresh state file. Offer interactive headed login only where the environment supports it; no CAPTCHA bypass.

Acceptance: local fixtures test login success/failure, expired state, popup adoption, switching back, and closing the active tab. The model never acts on a tab different from the recorded observation unnoticed.

### 10 — Collect uploads and downloads reliably

- Upload only manifest files to approved destinations. Confirm resulting filename/attachment state when the page supports it.
- Register download handling before the triggering action; handle new-tab downloads and failure events.
- Store downloads under a run-owned directory with collision-safe names, sanitized filenames, size/type metadata, and content hashes.
- Never execute downloaded files automatically.

Acceptance: local fixtures cover hidden file inputs, cancelled uploads, multiple downloads, duplicate names, unsafe filenames, and interrupted transfers. Incomplete files cannot satisfy a download outcome.

### 11 — Produce structured research/comparison results

- Define a generic item/attribute/evidence schema and a flight-example schema using it.
- Preserve currency, total versus per-person price, date, timezone/local-time context, overnight arrival, and unknown attributes.
- Separate requested constraints, actual filter settings, and verified result attributes.
- Rank only inspected items under stated criteria. Describe link state honestly: search results, selected outbound, full itinerary, checkout, or confirmation.

Acceptance: a synthetic flight fixture with misleading "from" prices, another airport, next-day arrival, and missing baggage fees yields accurate records or explicit unknowns. No global "best price" claim follows from inspecting three options.

### 12 — Add completion verification and recovery

- Support deterministic checks for URL, field value, visible confirmation, saved-state persistence, and downloaded file presence.
- Label semantic model judgments separately and include supporting observations.
- Use bounded retries for transient navigation/locator failures with fresh observations. Never reuse stale element indices or blindly retry mutations.

Acceptance: a form displaying success while failing to save is not completed; a disabled submit button is not treated as success; a transient stale index can recover; ambiguous submit outcomes pause.

### 13 — Add run budgets, cleanup, and partial artifacts

- Enforce a single wall-clock budget spanning planning, authentication, execution, and verification.
- Count all actions and attempts. Apply SDK turn/cost controls where supported; disclose potential cost overrun when only delayed usage is available.
- On timeout/cancellation, terminate owned resources and retain partial results. Infrastructure errors must not erase prior observations.

Acceptance: fake clock/SDK tests cover each limit, browser startup failure, exception after progress, and cancellation. No unfinished run is completed; no owned browser/SDK process remains running after cleanup checks.

### 14 — Build results and evidence reports

- Write JSON and HTML from one canonical result; provide a concise terminal summary.
- Link each result claim to its action/observation evidence, even when the claim is recorded much later.
- Include usage, unresolved constraints, verification scope, downloads, and input/approval events with sensitive values removed.
- Add a sanitized export command; private cookies and raw credentials never enter the export.

Acceptance: a flight replay with all reporting at the end still places the correct evidence next to each itinerary. HTML escapes untrusted text. Exports exclude auth state and preserve working artifact links.

### 15 — Add reusable workflow packages

- Implement `--workflow PATH` with instructions, input schema, output schema, required file types, permitted destinations/actions, and completion criteria.
- Treat legacy skill Markdown as reusable guidance, not as authorization. Loading a skill cannot expand filesystem or action permissions.
- Validate inputs before launching the agent; version workflow packages and record the version in results.

Acceptance: one synthetic expense workflow runs with two different input sets without editing instructions; missing required data fails before paid execution; a malicious workflow cannot override the executor's permission boundary.

### 16 — Evaluate and prepare a public alpha

- Provide three synthetic, independently verifiable demos: comparison research, form preparation/submission, and document upload/download.
- Add clean, broken, and interrupted variants with ground truth outside the agent's context.
- Run deterministic tests in CI; keep real-model tests explicitly opt-in and secrets unavailable to untrusted fork jobs.
- Measure completion correctness, false completion, interventions, duplicate side effects, constraint adherence, runtime, and cost over repeated runs. Publish sample sizes and model/configuration, including failures.
- Add setup docs, `.env.example`, file-mount examples, supported-auth limitations, contribution instructions, and an owner-selected license.
- Replace private site instructions, identities, and transaction documents with synthetic assets in the new repository. Do not delete originals in the testing project.

Acceptance: a fresh checkout can run the documented local demos. Every fixture with unmet required outcomes avoids `COMPLETED`; evidence and file manifests are correct. Document live evaluation results as measured, not guaranteed. Do not publish the repository automatically.

## 7. Review handoff for a smaller implementing model

Use this prompt for each task:

> Implement task NN from OPEN_SOURCE_BROWSER_AUTOMATION.md in the separate browser-automation project. Read the existing code and relevant project instructions first. Preserve unrelated work. Implement only this task and the minimal dependencies already agreed upon. Run its acceptance checks with local fixtures/fake SDK responses. Return changed files, behavior changes, exact test commands/results, and unresolved limitations. Do not run paid models or real business-site workflows unless explicitly requested. Stop for review when the task is complete.

Review in small batches, preferably one task per commit or PR. If the testing project has already fixed a shared engine bug, inspect and port that fix rather than independently rewriting the same code. Do not mark acceptance criteria complete based only on model narration or an overall green badge.

## 8. Deferred computer-use expansion

Full desktop control would require a separate observation/action backend: screenshot input, coordinate mouse actions, OS keyboard control, window/focus management, desktop permissions, and recovery from layout changes. A Linux browser container does not provide host macOS control.

Keep the task/result/evidence contracts reusable, but do not implement speculative desktop abstractions in the first release. Revisit desktop control only after a concrete workflow is blocked by a native application or picker that scoped file/browser APIs cannot handle.
