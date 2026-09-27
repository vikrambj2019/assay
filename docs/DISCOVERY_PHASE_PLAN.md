# Adaptive app discovery plan

Assay should be able to test an application from behavioral requirements even
when the README is incomplete or the UI changes. The planner must not require
the user to copy headings, button labels, or other implementation details into
the notes.

## Goal

Before executing a planned scenario, Assay performs a bounded discovery pass on
the running application. It learns the available routes and interactive
controls, then uses those observations to choose actions. Requirements remain
the source of truth for verdicts; discovered UI details are execution guidance
and evidence.

## Proposed flow

1. **Collect context** from notes, README, diff, and fixture files.
2. **Generate behavioral scenarios** from the stated user outcomes. The planner
   may include model-evaluated assertions when a requirement has no objective
   deterministic check.
3. **Discover the app** from the configured start URL with a small action and
   time budget. Capture the URL, visible text, forms, links, buttons, and
   available navigation targets.
4. **Adapt execution** by giving the executor the discovery summary and the
   current page state. The executor may use observed labels and selectors to
   complete the scenario, but must not turn them into new required assertions.
5. **Evaluate evidence** against the original scenario. Report each assertion
   as PASS, FAIL, or UNVERIFIED with the observed evidence and confidence.

## Boundaries

- Discovery is read-only by default. It must not submit forms, create records,
  send messages, or perform irreversible actions.
- A scenario explicitly marked as requiring mutations may opt into actions that
  change state, subject to the existing mutation policy and confirmation rules.
- The agent may infer how to operate a control, but it may not invent a product
  requirement when the notes and README do not state one.
- Deterministic checks remain appropriate for URL patterns, exact value formats,
  persistence, and other objectively testable rules.
- If the app cannot be understood within the discovery budget, the report must
  explain what was observed and return UNVERIFIED instead of guessing.

## Initial implementation slices

1. Add a discovery result model containing the starting URL, visited URLs,
   visible text excerpts, and interactive element summaries.
2. Add a read-only discovery phase to the executor with separate action and
   time budgets.
3. Pass the discovery result into scenario execution and include it in the
   evidence report.
4. Update planner instructions to distinguish required, inferred, and
   exploratory information.
5. Add tests for read-only behavior, budget exhaustion, adaptive execution,
   and the rule that inferred UI details cannot become required assertions.
6. Add a Driftline example using behavioral notes only and verify that the
   booking flow completes without hardcoded UI strings.

## Acceptance criteria

- A behavioral notes file can drive a useful run against an unfamiliar app
  whose exact labels are absent from the notes.
- Discovery never mutates state unless the scenario explicitly permits it.
- Reports show which observations came from discovery and which requirements
  they support.
- Missing or ambiguous requirements produce UNVERIFIED with an explanation,
  rather than an invented PASS or FAIL.
- Existing deterministic checks, mutation policy, budgets, and video recording
  continue to work unchanged.
