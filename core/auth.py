"""Authentication setup for `bta check` runs.

Determines which auth strategy to use from the configuration, and provides
an injectable ``AuthVerifyAdapter`` so the post-login state check can be
tested without a real browser.

Auth strategy (in priority order):
  1. BTA_AUTH_STATE file exists  → load Playwright storage state (no login step)
  2. BTA_TEST_USERNAME / PASSWORD → credential login via browser tools
  3. Neither configured           → proceed without authentication

After any login attempt the caller should call ``verify_authenticated()`` to
confirm the browser is in an authenticated state.  Uncertain or clearly failed
logins must surface as UNVERIFIED / BLOCKED rather than PASS.

Credentials must never appear in generated plans (enforced in the planner).
MFA / CAPTCHA that cannot be automated produces UNVERIFIED; the run does not
silently pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config


# ── Auth mode ─────────────────────────────────────────────────────────────────

class AuthMode(str, Enum):
    """How the run will establish an authenticated browser session."""
    NONE         = "none"           # no auth configured
    STORAGE_STATE = "storage_state" # load a Playwright storage state file
    CREDENTIALS  = "credentials"    # credential login via browser tools


@dataclass
class AuthSetup:
    """Decision record: how auth will be performed for this run."""
    mode: AuthMode
    reason: str                         # human-readable explanation
    storage_state_path: Path | None = None  # set when mode=STORAGE_STATE


# ── Auth result ───────────────────────────────────────────────────────────────

@dataclass
class AuthResult:
    """Outcome of an authentication attempt or verification."""
    status: str   # "pass" | "blocked" | "unverified"
    reason: str


# ── Pure decision logic ───────────────────────────────────────────────────────

def resolve_auth_setup(cfg: "Config") -> AuthSetup:
    """Determine the authentication strategy from config (no browser required).

    Precedence:
    1. ``auth_state`` path configured and file exists → STORAGE_STATE
    2. ``auth_state`` configured but file missing     → NONE (first run warning)
    3. Both ``test_username`` + ``test_password`` set → CREDENTIALS
    4. Nothing                                        → NONE
    """
    if cfg.auth_state is not None:
        if cfg.auth_state.exists():
            return AuthSetup(
                mode=AuthMode.STORAGE_STATE,
                reason=f"will load Playwright storage state from {cfg.auth_state}",
                storage_state_path=cfg.auth_state,
            )
        # File not found — fall back to credentials when available; otherwise
        # proceed unauthenticated with an explicit first-run warning.
        if cfg.test_username and cfg.test_password:
            target = cfg.login_url or cfg.app_url or "(no URL configured)"
            return AuthSetup(
                mode=AuthMode.CREDENTIALS,
                reason=(
                    f"BTA_AUTH_STATE={cfg.auth_state} not found; will log in as "
                    f"{cfg.test_username!r} at {target}"
                ),
            )
        return AuthSetup(
            mode=AuthMode.NONE,
            reason=f"BTA_AUTH_STATE={cfg.auth_state} not found — no credentials available",
        )

    if cfg.test_username and cfg.test_password:
        target = cfg.login_url or cfg.app_url or "(no URL configured)"
        return AuthSetup(
            mode=AuthMode.CREDENTIALS,
            reason=f"will log in as {cfg.test_username!r} at {target}",
        )

    return AuthSetup(
        mode=AuthMode.NONE,
        reason="no authentication configured (no storage state or credentials)",
    )


# ── Browser-level verification (injectable for tests) ─────────────────────────

class AuthVerifyAdapter(Protocol):
    """Injectable browser interface for auth-state verification.

    Use ``FakeAuthVerifyAdapter`` in unit tests; wire a real Playwright page
    in integration tests or the executor.
    """

    async def current_url(self) -> str:
        """Return the page's current URL."""
        ...

    async def page_text(self) -> str:
        """Return visible text content of the current page."""
        ...


async def verify_authenticated(
    adapter: AuthVerifyAdapter,
    login_url: str,
) -> AuthResult:
    """Verify that the browser is in an authenticated state.

    Checks observable browser state after a login attempt.  A login is
    considered successful when the browser has navigated away from the
    login URL AND the page does not contain obvious failure indicators.

    Outcomes:
    - PASS       — navigated away from login URL; no failure text visible
    - BLOCKED    — clear failure text on page (invalid credentials, etc.)
    - UNVERIFIED — still on login URL, or ambiguous page content (MFA,
                   CAPTCHA, or unknown auth form still present)

    Args:
        adapter:   Provides current_url() and page_text() from the browser.
        login_url: The URL the login form was on.  Empty string = unknown.
    """
    current = await adapter.current_url()
    text = await adapter.page_text()
    text_lower = text.lower()

    # Clear failure indicators (wrong password, account lock, etc.)
    _FAILURE_PHRASES = (
        "invalid password",
        "invalid credentials",
        "incorrect password",
        "login failed",
        "authentication failed",
        "account locked",
        "access denied",
        "unauthorized",
    )
    for phrase in _FAILURE_PHRASES:
        if phrase in text_lower:
            return AuthResult(
                status="blocked",
                reason=f"login failed — page contains {phrase!r}",
            )

    # If still on the login URL, auth is uncertain
    if login_url and current.startswith(login_url):
        # Could be MFA, CAPTCHA, or just a slow redirect
        return AuthResult(
            status="unverified",
            reason=(
                "still on the login page after auth attempt — possible MFA, "
                "CAPTCHA, or login did not complete"
            ),
        )

    # Page contains a password field → we may still be on an auth screen
    if "password" in text_lower and ("[password]" in text_lower or 'type="password"' in text_lower):
        return AuthResult(
            status="unverified",
            reason="page still contains a password field — auth may not have completed",
        )

    if not login_url:
        # No login URL means there is no observed authenticated-state boundary.
        # Never turn an uncheckable state into PASS.
        return AuthResult(
            status="unverified",
            reason="authentication cannot be verified because no login_url is configured",
        )

    return AuthResult(
        status="pass",
        reason=f"authenticated — navigated to {current}",
    )
