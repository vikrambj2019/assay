# Task 8 review — `.env` configuration

Task 8 is complete.

Implemented and verified:

- Process environment takes precedence over `.env`; quoted values and comments
  are handled.
- Application URL and login URL validation, with login URL defaulting to the
  application URL.
- Strict boolean parsing for headless mode, evidence collection, and mutation
  policy.
- Positive validation for navigation/action timeouts and run budgets;
  non-negative validation for slow motion.
- Credential pairing, optional file checks, depth, origin, and gateway/app URL
  separation.
- Docker Compose forwards the complete configuration contract, including
  headless mode, timeout values, evidence collection, gateway variables, URLs,
  credentials, origins, mutation policy, depth, and budgets.
- `.env.example` documents the supported settings with placeholders only.

Validation:

- Task 8 configuration tests: **58 passed, 1 warning**
- Full suite: **485 passed, 59 skipped, 1 warning**

The warning is pytest’s existing collection warning for the dataclass named
`TestSpec`; it does not affect test results.
