# Task 12 review — authentication and execution policy

The policy layer is implemented and verified:

- Existing storage state takes precedence over credentials.
- A missing storage-state file falls back to configured credentials.
- Auth verification returns PASS only after an observable post-login state;
  failed credentials and MFA/CAPTCHA uncertainty are BLOCKED/UNVERIFIED.
- Missing login URL is UNVERIFIED rather than an assumed PASS.
- Mutation policy blocks undeclared-safe scenarios when mutations are disabled.
- Origin policy is passed to browser tools and enforced again for top-level
  redirects and popup navigations; unrelated subresources remain allowed.

Validation:

- Auth/policy tests: **61 passed**
- Full suite: **490 passed, 59 skipped, 1 warning**

The remaining production integration is the credential-login driver itself:
the current `bta check` CLI still uses the task-11 stub adapter until the
scenario/browser adapter work is completed in task 13. The auth decision and
verification contracts are ready for that adapter and do not silently report
unverified authentication as PASS.
