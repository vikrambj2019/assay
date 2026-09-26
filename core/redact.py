"""Shared credential redactor — applied at every text output boundary.

Scrubs configured credentials and provider tokens from text before writing to
step files, reports, or the terminal transcript. Construct via make_redactor()
to pick up the current process environment automatically.

Image-redaction limitation: screenshots are not post-processed. A password
typed into a visible text field, a credential echoed by the application, or
any secret displayed on-screen will appear in the PNG step screenshots.
Screenshots are stored alongside redacted text artifacts; the limitation must
be communicated to anyone sharing the results directory.
"""

from __future__ import annotations

import os

_PLACEHOLDER = "[REDACTED]"

# Environment variables whose values are treated as secrets.
_CREDENTIAL_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ASSAY_TEST_PASSWORD",
    "ASSAY_TEST_USERNAME",
)


class Redactor:
    """Replaces registered secret strings with '[REDACTED]'.

    Secrets are deduplicated and sorted longest-first so a prefix never
    shadows a longer match (e.g. "abc" won't match inside "abcdef" first).
    Thread-safe for reads; scrub() is a pure transformation.
    """

    def __init__(self, secrets: "list[str]"):
        seen: set[str] = set()
        self._secrets: list[str] = []
        for s in sorted((s for s in secrets if s and s.strip()), key=len, reverse=True):
            if s not in seen:
                seen.add(s)
                self._secrets.append(s)

    def scrub(self, text: str) -> str:
        """Return *text* with every registered secret replaced by '[REDACTED]'."""
        for s in self._secrets:
            if s in text:
                text = text.replace(s, _PLACEHOLDER)
        return text


def make_redactor() -> "Redactor":
    """Build a Redactor from the current process environment.

    Reads ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ASSAY_TEST_PASSWORD, and
    ASSAY_TEST_USERNAME. Only non-empty values are registered as secrets.
    """
    return Redactor([os.environ.get(v, "") for v in _CREDENTIAL_VARS])
