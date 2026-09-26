# Task 17 review — public repository and CI

## Result

Release hygiene and documentation are implemented for the synthetic public
tree. Private production suites, credentials-bearing skills, transaction PDFs,
and recorded browser transcripts were removed from the checkout and preserved
outside it. The public examples now use only `fixture-demo.yaml` and
`fixture-login`.

CI now has a manual `workflow_dispatch` trigger for trusted live evaluation and
runs browser tests on pull requests as well as pushes. Flask is installed by
the normal requirements file, and the skill tests assert the public fixture
skill rather than private application skills.

## License

The repository now uses the PolyForm Noncommercial License 1.0.0 with the
required notice naming Vikram Bandugula and Biplob Das. This is source-available,
not OSI open source, because commercial use is restricted.

## Verification

- Private artifact scan found no production URL, identity, or document in the
  public examples; remaining password references are synthetic tests/fixtures.
- Full Docker pytest suite: **549 passed, 1 warning** before the Task 17 skill
  assertion update; rerun after the final cleanup.
- CLI documentation and `agent --help` / `bta check --help` remain credential-free.
