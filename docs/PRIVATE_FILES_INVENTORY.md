# Public-release privacy checklist

The repository release tree contains only synthetic examples. Before publishing,
confirm that no local-only files have been copied back into the checkout:

- `.env`, auth-state files, and run output remain ignored.
- `suites/fixture-demo.yaml` is the only shipped suite.
- `harness/.claude/skills/fixture-login/` is the only shipped application skill.
- No real application URLs, identities, credentials, transaction documents, or
  browser transcripts are present.
- Review any new fixture or example for secrets before committing.
- The project license is PolyForm Noncommercial 1.0.0; it is source-available and
  does not qualify as OSI open source because commercial use is restricted.

Private source material removed during this cleanup was preserved outside the
repository by the release operator and must not be re-added to the public tree.
