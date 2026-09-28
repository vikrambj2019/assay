# Lightweight codebase understanding

Assay currently plans from behavioral notes, the README, and an optional git
diff. A useful next step is a small, bounded understanding pass before plan
generation. Its purpose is to give the planner enough architecture to choose
real flows without asking developers to describe every route or component.

## Proposed behavior

The pass should inspect only a bounded set of text files and produce a compact
context summary containing:

- application entry points and startup commands;
- route and page candidates;
- forms, links, buttons, and API clients found in source;
- persistence boundaries such as localStorage, cookies, or database calls;
- authentication and authorization boundaries;
- test fixtures and existing scripts that reveal supported flows.

The summary is planning context, not a test specification. Notes and README
requirements remain authoritative for required assertions. Code observations are
marked as inferred or exploratory and cannot silently become required behavior.

## Safety and cost limits

- Follow the existing secret-file and binary-file exclusions.
- Read a bounded number of files and cap total characters.
- Prefer likely entry points, routes, and package manifests over exhaustive
  repository indexing.
- Never execute application code, install dependencies, or run migrations during
  this phase.
- Redact credentials, tokens, private keys, and environment values before the
  summary reaches the planner.

## Suggested interface

Add an optional `assay understand` command that writes
`results/understanding.json` and `results/understanding.md`. `assay check` can
consume that artifact automatically when it exists, with a flag to disable it.
The command should also work independently so a developer can inspect what
Assay learned before paying for a plan or browser run.

## Acceptance criteria

- A fresh checkout produces a useful summary without an API key or browser.
- The summary identifies likely app entry points and auth/persistence boundaries
  for the fixture and Driftline examples.
- Secret and binary files never appear in the summary.
- Planning remains valid when the understanding artifact is absent or empty.
- The planner clearly distinguishes source-derived observations from behavioral
  requirements supplied by the developer.
