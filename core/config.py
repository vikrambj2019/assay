"""Runtime configuration, loaded from environment variables.

Load order (highest priority first):
  1. Explicit CLI arguments
  2. Process environment (already set before invocation)
  3. .env file in the invocation directory (load_dotenv())
  4. Built-in defaults

Call load_dotenv() once at startup (harness/cli.py) before any Config.from_env()
call so .env values are visible when from_env() reads os.environ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Process environment already set is never overwritten — the existing process
    env always wins. Silent when the file does not exist.
    If *path* is None, looks for .env in the current working directory.
    """
    target = path if path is not None else Path.cwd() / ".env"
    if not target.is_file():
        return
    for raw_line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip matching outer quotes (single or double)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


def legacy_env_warnings() -> list[str]:
    """Return a warning for each pre-rename ``BTA_*`` variable still set.

    The project was renamed from bta to assay before its first public release.
    Legacy variables are ignored (never silently honored) so a stale .env
    cannot change behavior; this makes the ignored setting visible instead.
    """
    warnings = []
    for key in sorted(os.environ):
        if key.startswith("BTA_"):
            new = "ASSAY_" + key[len("BTA_"):]
            warnings.append(f"{key} is ignored; the variable is now named {new}")
    return warnings


# ---------------------------------------------------------------------------
# Parsing helpers (raise ValueError with actionable messages)
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _strict_env_bool(name: str, default: bool) -> bool:
    """Like _env_bool but raises for unrecognised values."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name}={raw!r}: must be a boolean value "
        f"(true/false/yes/no/1/0/on/off)"
    )


def _parse_url(val: str, name: str) -> str:
    """Validate an http/https URL; raise ValueError with an actionable message."""
    p = urlparse(val)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(
            f"{name}={val!r}: must be an http or https URL "
            f"(e.g. http://localhost:3000)"
        )
    return val


def _parse_origin(val: str, name: str) -> str:
    """Validate and normalise a bare origin (scheme://host[:port], no path)."""
    p = urlparse(val)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError(
            f"{name}: {val!r} is not a valid origin — "
            f"must be scheme://host[:port] (e.g. http://localhost:3000)"
        )
    if p.path not in ("", "/"):
        raise ValueError(
            f"{name}: {val!r} must be a bare origin with no path "
            f"(e.g. http://localhost:3000, not http://localhost:3000/app)"
        )
    return f"{p.scheme}://{p.netloc}"


def _parse_positive_int(val: str, name: str) -> int:
    try:
        n = int(val)
    except ValueError:
        raise ValueError(f"{name}={val!r}: must be a positive integer")
    if n <= 0:
        raise ValueError(f"{name}={n}: must be a positive integer (> 0)")
    return n


def _parse_positive_float(val: str, name: str) -> float:
    try:
        f = float(val)
    except ValueError:
        raise ValueError(f"{name}={val!r}: must be a positive number")
    if f <= 0:
        raise ValueError(f"{name}={f}: must be a positive number (> 0)")
    return f


def _parse_nonnegative_int(val: str, name: str) -> int:
    try:
        n = int(val)
    except ValueError:
        raise ValueError(f"{name}={val!r}: must be a non-negative integer")
    if n < 0:
        raise ValueError(f"{name}={n}: must be a non-negative integer (>= 0)")
    return n


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class Config:
    # ── Browser substrate ────────────────────────────────────────────────
    headless: bool = True
    nav_timeout_ms: int = 30_000
    action_timeout_ms: int = 10_000  # Playwright auto-wait cap for clicks/fills
    slow_mo_ms: int = 0

    # Fold console errors + failed requests into the page snapshot the model reads
    # after each action, so it can react to a broken action. Off = DOM only.
    show_evidence: bool = True

    # Record a browser video per check scenario (ASSAY_RECORD_VIDEO / --record-video).
    # record_video_dir is set per scenario by the check runner, not from env.
    record_video: bool = False
    record_video_dir: Path | None = None

    # ── Agent model ──────────────────────────────────────────────────────
    # Driven by the Claude Agent SDK (which runs the Claude Code CLI).
    # The SDK reads ANTHROPIC_API_KEY from the environment; `effort` maps to
    # the SDK's reasoning-effort control.
    model: str = "claude-sonnet-5"
    effort: str = "high"

    # Optional third-party gateway. Point the SDK at any Anthropic-compatible
    # endpoint — e.g. OpenRouter's at https://openrouter.ai/api — and `model`
    # names a provider slug there. Both None (default) = talk to Anthropic
    # directly with ANTHROPIC_API_KEY. See sdk_env().
    base_url: str | None = None         # ANTHROPIC_BASE_URL (gateway)
    auth_token: str | None = None       # ANTHROPIC_AUTH_TOKEN (gateway)

    # ── Application/test configuration ──────────────────────────────────
    # ASSAY_BASE_URL is the *application* URL — distinct from ANTHROPIC_BASE_URL
    # (the model gateway). Required for `assay check`; used for goal interpolation
    # in suites.
    app_url: str = ""                   # ASSAY_BASE_URL
    login_url: str = ""                 # ASSAY_LOGIN_URL (defaults to app_url)

    # Optional test credentials — supply both or neither.
    test_username: str = ""             # ASSAY_TEST_USERNAME
    test_password: str = ""             # ASSAY_TEST_PASSWORD

    # Optional auth-state / context files.
    auth_state: Path | None = None      # ASSAY_AUTH_STATE (Playwright storage state)
    auth_notes_file: Path | None = None # ASSAY_AUTH_NOTES_FILE (Markdown)
    test_data_file: Path | None = None  # ASSAY_TEST_DATA_FILE (fixture data/instructions)

    # Browser origins the agent is allowed to navigate to.
    # Defaults to the ASSAY_BASE_URL origin when not explicitly set.
    allowed_origins: list[str] = field(default_factory=list)  # ASSAY_ALLOWED_ORIGINS

    allow_mutations: bool = False       # ASSAY_ALLOW_MUTATIONS
    depth: str = "medium"              # ASSAY_DEPTH: low | medium | high

    # Independently re-review each reported FAIL with a fresh-eyes model call
    # before it is recorded as a confirmed application failure.
    # ASSAY_ADJUDICATE_FAILS=true (or --adjudicate-fails).
    adjudicate_fails: bool = False

    # Optional budget overrides (None = use depth preset defaults).
    max_seconds: int | None = None      # ASSAY_MAX_SECONDS
    max_actions: int | None = None      # ASSAY_MAX_ACTIONS
    max_cost_usd: float | None = None   # ASSAY_MAX_COST_USD

    # ── Session persistence ──────────────────────────────────────────────
    # Set per-test by the suite runner. When set, the browser loads cookies +
    # storage from this file on start and writes them back on close.
    storage_state: Path | None = None

    # ── Output ──────────────────────────────────────────────────────────
    results_root: Path = field(default_factory=lambda: Path("results"))

    @classmethod
    def from_env(cls) -> "Config":
        # ── Application URL ──────────────────────────────────────────────
        app_url_raw = os.environ.get("ASSAY_BASE_URL", "")
        app_url = _parse_url(app_url_raw, "ASSAY_BASE_URL") if app_url_raw else ""

        login_url_raw = os.environ.get("ASSAY_LOGIN_URL", "")
        login_url = _parse_url(login_url_raw, "ASSAY_LOGIN_URL") if login_url_raw else app_url

        # ── Depth ────────────────────────────────────────────────────────
        depth = os.environ.get("ASSAY_DEPTH", "medium")
        if depth not in ("low", "medium", "high"):
            raise ValueError(
                f"ASSAY_DEPTH={depth!r}: must be 'low', 'medium', or 'high'"
            )

        # ── Budget overrides ─────────────────────────────────────────────
        max_seconds_raw = os.environ.get("ASSAY_MAX_SECONDS")
        max_seconds = (_parse_positive_int(max_seconds_raw, "ASSAY_MAX_SECONDS")
                       if max_seconds_raw else None)

        max_actions_raw = os.environ.get("ASSAY_MAX_ACTIONS")
        max_actions = (_parse_positive_int(max_actions_raw, "ASSAY_MAX_ACTIONS")
                       if max_actions_raw else None)

        max_cost_raw = os.environ.get("ASSAY_MAX_COST_USD")
        max_cost_usd = (_parse_positive_float(max_cost_raw, "ASSAY_MAX_COST_USD")
                        if max_cost_raw else None)

        # ── Allowed origins ──────────────────────────────────────────────
        origins_raw = os.environ.get("ASSAY_ALLOWED_ORIGINS", "").strip()
        if origins_raw:
            allowed_origins = [
                _parse_origin(o.strip(), "ASSAY_ALLOWED_ORIGINS")
                for o in origins_raw.split(",")
                if o.strip()
            ]
        elif app_url:
            p = urlparse(app_url)
            allowed_origins = [f"{p.scheme}://{p.netloc}"]
        else:
            allowed_origins = []

        # ── Optional file paths (stored as Path; existence checked in validate) ─
        auth_state_raw = os.environ.get("ASSAY_AUTH_STATE")
        auth_notes_raw = os.environ.get("ASSAY_AUTH_NOTES_FILE")
        test_data_raw = os.environ.get("ASSAY_TEST_DATA_FILE")

        return cls(
            headless=_strict_env_bool("ASSAY_HEADLESS", True),
            nav_timeout_ms=_parse_positive_int(
                os.environ.get("ASSAY_NAV_TIMEOUT_MS", "30000"), "ASSAY_NAV_TIMEOUT_MS"
            ),
            action_timeout_ms=_parse_positive_int(
                os.environ.get("ASSAY_ACTION_TIMEOUT_MS", "10000"), "ASSAY_ACTION_TIMEOUT_MS"
            ),
            slow_mo_ms=_parse_nonnegative_int(
                os.environ.get("ASSAY_SLOWMO_MS", "0"), "ASSAY_SLOWMO_MS"
            ),
            show_evidence=_strict_env_bool("ASSAY_SHOW_EVIDENCE", True),
            record_video=_strict_env_bool("ASSAY_RECORD_VIDEO", False),
            model=os.environ.get("ASSAY_MODEL", "claude-sonnet-5"),
            effort=os.environ.get("ASSAY_EFFORT", "high"),
            # Gateway (ANTHROPIC_*) — kept separate from app URL.
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
            auth_token=os.environ.get("ANTHROPIC_AUTH_TOKEN") or None,
            # Application
            app_url=app_url,
            login_url=login_url,
            test_username=os.environ.get("ASSAY_TEST_USERNAME", ""),
            test_password=os.environ.get("ASSAY_TEST_PASSWORD", ""),
            auth_state=Path(auth_state_raw) if auth_state_raw else None,
            auth_notes_file=Path(auth_notes_raw) if auth_notes_raw else None,
            test_data_file=Path(test_data_raw) if test_data_raw else None,
            allowed_origins=allowed_origins,
            allow_mutations=_strict_env_bool("ASSAY_ALLOW_MUTATIONS", False),
            adjudicate_fails=_strict_env_bool("ASSAY_ADJUDICATE_FAILS", False),
            depth=depth,
            max_seconds=max_seconds,
            max_actions=max_actions,
            max_cost_usd=max_cost_usd,
            results_root=Path(os.environ.get("ASSAY_RESULTS_DIR", "results")),
        )

    def validate_for_check(self) -> None:
        """Validate that all inputs required for `assay check` are present and sane.

        Raises ValueError with an actionable multi-line message listing every
        problem found, so the user can fix them all in one pass. Must be called
        before any browser launch or paid model call.
        """
        errors: list[str] = []

        if not self.app_url:
            errors.append(
                "ASSAY_BASE_URL is required for `assay check` — "
                "set it to the running application's URL "
                "(e.g. http://localhost:3000) in .env"
            )

        # Credentials must be supplied together or not at all.
        if bool(self.test_username) != bool(self.test_password):
            errors.append(
                "ASSAY_TEST_USERNAME and ASSAY_TEST_PASSWORD must both be set "
                "or both be unset"
            )

        # Optional files must exist when specified. auth_state is excluded:
        # it may not exist yet (created on the first login run) and its absence
        # just means the agent will start a fresh browser session.
        for path, name in [
            (self.auth_notes_file, "ASSAY_AUTH_NOTES_FILE"),
            (self.test_data_file, "ASSAY_TEST_DATA_FILE"),
        ]:
            if path is not None and not path.exists():
                errors.append(f"{name}={path}: file not found")

        if errors:
            raise ValueError(
                "Configuration errors for `assay check`:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

    def sdk_env(self) -> dict[str, str]:
        """Env overrides handed to the Agent SDK subprocess (the Claude Code CLI).

        A base_url redirects the CLI to a gateway like OpenRouter; that gateway
        authenticates by ANTHROPIC_AUTH_TOKEN, so we also blank ANTHROPIC_API_KEY
        to stop the CLI preferring a stale first-party key. Empty when no gateway
        is set — the CLI then inherits ANTHROPIC_API_KEY and talks to Anthropic.
        """
        env: dict[str, str] = {}
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
            env["ANTHROPIC_API_KEY"] = ""
        if self.auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = self.auth_token
        return env

    def new_run_dir(self) -> Path:
        """Create and return a fresh, collision-resistant per-run results directory.

        Uses results/<ts>/ and falls back to results/<ts>_1/, results/<ts>_2/, …
        so simultaneous runs always get distinct directories even when they start
        within the same second.
        """
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.results_root / stamp
        run_dir = base
        n = 1
        while True:
            try:
                run_dir.mkdir(parents=True, exist_ok=False)
                return run_dir
            except FileExistsError:
                run_dir = self.results_root / f"{stamp}_{n}"
                n += 1
