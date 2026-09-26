"""Tests for authentication setup and execution policy (Task 12).

All pure unit / async-unit tests — no browser, no LLM, no network, no git.
FakeAuthVerifyAdapter injects canned page text and URL responses so every
auth-verification path is exercised without touching Playwright.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.auth import (
    AuthMode,
    AuthSetup,
    AuthResult,
    AuthVerifyAdapter,
    resolve_auth_setup,
    verify_authenticated,
)
from core.plan import (
    Assertion,
    AssertionKind,
    Plan,
    Scenario,
    SourceRef,
    make_plan,
)
from core.policy import MutationPolicy, OriginPolicy
from core.run import (
    ScenarioResult,
    execution_order,
    generate_run_id,
    pre_check_scenario,
)
from core.schema import Verdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(**kwargs):
    """Minimal fake Config."""
    defaults = dict(
        auth_state=None,
        test_username="",
        test_password="",
        login_url="",
        app_url="http://localhost:3000",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _src() -> SourceRef:
    return SourceRef(kind="notes", path="changes.md", excerpt="feature added")


def _assertion(aid="a-001") -> Assertion:
    return Assertion(
        id=aid,
        description="Works correctly",
        kind=AssertionKind.REQUIRED,
        source=_src(),
    )


def _scenario(sid="s-001", *, skip=False, mutations=False,
              prerequisites=None) -> Scenario:
    return Scenario(
        id=sid,
        title=f"Scenario {sid}",
        goal="Verify the feature",
        prerequisites=prerequisites or [],
        assertions=[_assertion()],
        requires_mutations=mutations,
        reason="Changed behavior",
        source=_src(),
        skip=skip,
    )


class FakeAuthVerifyAdapter:
    """Fake AuthVerifyAdapter for unit tests."""

    def __init__(self, url: str, text: str) -> None:
        self._url = url
        self._text = text

    async def current_url(self) -> str:
        return self._url

    async def page_text(self) -> str:
        return self._text


# ── OriginPolicy ──────────────────────────────────────────────────────────────

def test_origin_policy_no_restrictions_allows_any_url():
    policy = OriginPolicy(allowed_origins=[])
    allowed, _ = policy.check_navigation("http://example.com/page")
    assert allowed is True


def test_origin_policy_allows_matching_origin():
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    allowed, _ = policy.check_navigation("http://localhost:3000/dashboard")
    assert allowed is True


def test_origin_policy_blocks_different_origin():
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    allowed, reason = policy.check_navigation("http://evil.example.com/phish")
    assert allowed is False
    assert "evil.example.com" in reason


def test_origin_policy_reason_mentions_allowed_origins():
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    _, reason = policy.check_navigation("http://other.example.com/")
    assert "localhost:3000" in reason


def test_origin_policy_blocks_different_port():
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    allowed, reason = policy.check_navigation("http://localhost:9999/page")
    assert allowed is False
    assert "9999" in reason


def test_origin_policy_blocks_different_scheme():
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    allowed, _ = policy.check_navigation("https://localhost:3000/page")
    assert allowed is False


def test_origin_policy_multiple_allowed_origins():
    policy = OriginPolicy(allowed_origins=[
        "http://localhost:3000",
        "http://localhost:4000",
    ])
    assert policy.check_navigation("http://localhost:3000/a")[0] is True
    assert policy.check_navigation("http://localhost:4000/b")[0] is True
    assert policy.check_navigation("http://localhost:9999/c")[0] is False


def test_origin_policy_cdn_subresources_always_allowed():
    """CDN assets loaded by the page are not blocked by OriginPolicy."""
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    assert policy.is_subresource_allowed("https://cdn.example.com/script.js") is True
    assert policy.is_subresource_allowed("https://fonts.googleapis.com/css") is True


def test_origin_policy_bad_url_blocked():
    policy = OriginPolicy(allowed_origins=["http://localhost:3000"])
    allowed, _ = policy.check_navigation("not-a-url")
    assert allowed is False


# ── MutationPolicy ────────────────────────────────────────────────────────────

def test_mutation_policy_non_mutation_scenario_always_allowed():
    policy = MutationPolicy(allow_mutations=False)
    s = _scenario(mutations=False)
    allowed, _ = policy.check_scenario(s)
    assert allowed is True


def test_mutation_policy_blocks_mutation_when_disabled():
    policy = MutationPolicy(allow_mutations=False)
    s = _scenario(mutations=True)
    allowed, reason = policy.check_scenario(s)
    assert allowed is False
    assert "ASSAY_ALLOW_MUTATIONS" in reason


def test_mutation_policy_allows_mutation_when_enabled():
    policy = MutationPolicy(allow_mutations=True)
    s = _scenario(mutations=True)
    allowed, _ = policy.check_scenario(s)
    assert allowed is True


def test_mutation_policy_reason_notes_policy_not_guarantee():
    policy = MutationPolicy(allow_mutations=False)
    s = _scenario(mutations=True)
    _, reason = policy.check_scenario(s)
    assert "policy" in reason.lower() or "guarantee" in reason.lower()


def test_mutation_policy_blocked_includes_scenario_id():
    policy = MutationPolicy(allow_mutations=False)
    s = _scenario("s-007", mutations=True)
    _, reason = policy.check_scenario(s)
    assert "s-007" in reason


# ── resolve_auth_setup ────────────────────────────────────────────────────────

def test_resolve_no_auth_when_nothing_configured():
    cfg = _cfg(auth_state=None, test_username="", test_password="")
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.NONE


def test_resolve_storage_state_when_file_exists(tmp_path):
    state_file = tmp_path / "auth.json"
    state_file.write_text("{}")
    cfg = _cfg(auth_state=state_file)
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.STORAGE_STATE
    assert setup.storage_state_path == state_file


def test_resolve_storage_state_path_in_result(tmp_path):
    state_file = tmp_path / "auth.json"
    state_file.write_text("{}")
    cfg = _cfg(auth_state=state_file)
    setup = resolve_auth_setup(cfg)
    assert setup.storage_state_path == state_file


def test_resolve_none_when_auth_state_file_missing(tmp_path):
    cfg = _cfg(auth_state=tmp_path / "nonexistent.json")
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.NONE


def test_resolve_none_missing_file_reason_mentions_path(tmp_path):
    missing = tmp_path / "ghost.json"
    cfg = _cfg(auth_state=missing)
    setup = resolve_auth_setup(cfg)
    assert str(missing) in setup.reason or "ghost.json" in setup.reason


def test_missing_storage_state_falls_back_to_credentials(tmp_path):
    cfg = _cfg(
        auth_state=tmp_path / "missing.json",
        test_username="alice",
        test_password="fixture-password",
    )
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.CREDENTIALS


def test_resolve_credentials_when_both_set():
    cfg = _cfg(auth_state=None, test_username="alice", test_password="secret")
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.CREDENTIALS


def test_resolve_credentials_reason_includes_username():
    cfg = _cfg(auth_state=None, test_username="alice", test_password="secret")
    setup = resolve_auth_setup(cfg)
    assert "alice" in setup.reason


def test_resolve_storage_state_takes_priority_over_credentials(tmp_path):
    state_file = tmp_path / "auth.json"
    state_file.write_text("{}")
    cfg = _cfg(auth_state=state_file, test_username="alice", test_password="s")
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.STORAGE_STATE


def test_resolve_none_with_only_username():
    """Supply username but not password → no-auth (validated by Config.validate_for_check)."""
    cfg = _cfg(auth_state=None, test_username="alice", test_password="")
    setup = resolve_auth_setup(cfg)
    assert setup.mode is AuthMode.NONE


# ── verify_authenticated ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_pass_when_navigated_away():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/dashboard",
        text="Welcome, Alice! Manage your account here.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert result.status == "pass"


@pytest.mark.asyncio
async def test_verify_unverified_when_still_on_login_url():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/login",
        text="Please enter your username and password.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert result.status == "unverified"


@pytest.mark.asyncio
async def test_verify_unverified_reason_mentions_login_page():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/login",
        text="Login form here.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert "login" in result.reason.lower()


@pytest.mark.asyncio
async def test_verify_blocked_on_invalid_credentials():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/login",
        text="Invalid credentials. Please try again.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_verify_blocked_on_incorrect_password():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/login",
        text="Incorrect password. Account locked after 3 more attempts.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_verify_blocked_on_authentication_failed():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/",
        text="Authentication failed. Please contact your administrator.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_verify_unverified_when_mfa_required():
    """MFA prompt: still on login URL but different text → UNVERIFIED."""
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/login/mfa",
        text="Enter the verification code sent to your phone.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert result.status == "unverified"


@pytest.mark.asyncio
async def test_verify_unverified_with_no_login_url():
    """Without a login_url there is no observed auth boundary."""
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/app",
        text="Dashboard — hello!",
    )
    result = await verify_authenticated(adapter, "")
    assert result.status == "unverified"


@pytest.mark.asyncio
async def test_verify_pass_reason_includes_current_url():
    adapter = FakeAuthVerifyAdapter(
        url="http://localhost:3000/dashboard",
        text="Welcome back.",
    )
    result = await verify_authenticated(adapter, "http://localhost:3000/login")
    assert "dashboard" in result.reason


# ── generate_run_id ───────────────────────────────────────────────────────────

def test_generate_run_id_returns_nonempty_string():
    rid = generate_run_id()
    assert isinstance(rid, str)
    assert len(rid) > 0


def test_generate_run_id_unique_each_call():
    ids = {generate_run_id() for _ in range(20)}
    assert len(ids) == 20


def test_run_id_in_scenario_result():
    rid = generate_run_id()
    result = ScenarioResult(
        scenario_id="s-001",
        scenario_title="Login",
        verdict=Verdict.PASS,
        reason="ok",
        run_id=rid,
    )
    assert result.run_id == rid


# ── execution_order ───────────────────────────────────────────────────────────

def test_execution_order_single_scenario():
    plan = make_plan("medium", [_scenario("s-001")])
    order = execution_order(plan)
    assert [s.id for s in order] == ["s-001"]


def test_execution_order_independent_scenarios():
    plan = make_plan("medium", [_scenario("s-001"), _scenario("s-002")])
    order = execution_order(plan)
    assert set(s.id for s in order) == {"s-001", "s-002"}
    assert len(order) == 2


def test_execution_order_prerequisite_before_dependent():
    s1 = _scenario("s-001")
    s2 = _scenario("s-002", prerequisites=["s-001"])
    plan = make_plan("medium", [s2, s1])   # intentionally reversed in input
    order = execution_order(plan)
    ids = [s.id for s in order]
    assert ids.index("s-001") < ids.index("s-002")


def test_execution_order_chain():
    """s-001 → s-002 → s-003 must come out in that order."""
    s1 = _scenario("s-001")
    s2 = _scenario("s-002", prerequisites=["s-001"])
    s3 = _scenario("s-003", prerequisites=["s-002"])
    plan = make_plan("medium", [s3, s1, s2])  # shuffled input
    order = execution_order(plan)
    ids = [s.id for s in order]
    assert ids.index("s-001") < ids.index("s-002") < ids.index("s-003")


def test_execution_order_diamond():
    """s-001 → {s-002, s-003} → s-004."""
    s1 = _scenario("s-001")
    s2 = _scenario("s-002", prerequisites=["s-001"])
    s3 = _scenario("s-003", prerequisites=["s-001"])
    s4 = _scenario("s-004", prerequisites=["s-002", "s-003"])
    plan = make_plan("high", [s4, s3, s2, s1])
    order = execution_order(plan)
    ids = [s.id for s in order]
    assert ids.index("s-001") < ids.index("s-002")
    assert ids.index("s-001") < ids.index("s-003")
    assert ids.index("s-002") < ids.index("s-004")
    assert ids.index("s-003") < ids.index("s-004")


# ── pre_check_scenario ────────────────────────────────────────────────────────

def _mp(allow: bool = False) -> MutationPolicy:
    return MutationPolicy(allow_mutations=allow)


def test_pre_check_returns_none_for_normal_scenario():
    result = pre_check_scenario(_scenario(), _mp(), {})
    assert result is None


def test_pre_check_skipped_scenario():
    s = _scenario(skip=True)
    verdict, reason = pre_check_scenario(s, _mp(), {})
    assert verdict is Verdict.SKIPPED
    assert "skip" in reason.lower()


def test_pre_check_mutation_blocked():
    s = _scenario(mutations=True)
    verdict, reason = pre_check_scenario(s, _mp(allow=False), {})
    assert verdict is Verdict.BLOCKED


def test_pre_check_mutation_allowed():
    s = _scenario(mutations=True)
    result = pre_check_scenario(s, _mp(allow=True), {})
    assert result is None


def test_pre_check_blocked_when_prerequisite_failed():
    s = _scenario("s-002", prerequisites=["s-001"])
    verdict, reason = pre_check_scenario(
        s, _mp(), {"s-001": Verdict.FAIL}
    )
    assert verdict is Verdict.BLOCKED
    assert "s-001" in reason


def test_pre_check_blocked_when_prerequisite_errored():
    s = _scenario("s-002", prerequisites=["s-001"])
    verdict, _ = pre_check_scenario(s, _mp(), {"s-001": Verdict.ERROR})
    assert verdict is Verdict.BLOCKED


def test_pre_check_blocked_when_prerequisite_unverified():
    s = _scenario("s-002", prerequisites=["s-001"])
    verdict, _ = pre_check_scenario(
        s, _mp(), {"s-001": Verdict.UNVERIFIED}
    )
    assert verdict is Verdict.BLOCKED


def test_pre_check_allowed_when_prerequisite_passed():
    s = _scenario("s-002", prerequisites=["s-001"])
    result = pre_check_scenario(s, _mp(), {"s-001": Verdict.PASS})
    assert result is None


def test_pre_check_blocked_when_prerequisite_not_yet_run():
    s = _scenario("s-002", prerequisites=["s-001"])
    verdict, reason = pre_check_scenario(s, _mp(), {})  # s-001 absent
    assert verdict is Verdict.BLOCKED
    assert "s-001" in reason


def test_pre_check_multiple_prerequisites_all_must_pass():
    s = _scenario("s-003", prerequisites=["s-001", "s-002"])
    # Only s-001 passed — s-002 failed
    result = pre_check_scenario(
        s, _mp(),
        {"s-001": Verdict.PASS, "s-002": Verdict.FAIL},
    )
    verdict, _ = result
    assert verdict is Verdict.BLOCKED


def test_pre_check_skip_takes_priority_over_mutation_block():
    """skip=True should produce SKIPPED even if mutation policy would block."""
    s = _scenario(skip=True, mutations=True)
    verdict, _ = pre_check_scenario(s, _mp(allow=False), {})
    assert verdict is Verdict.SKIPPED


# ── tools.py: origin_policy in open_url ──────────────────────────────────────

_sdk_available = pytest.mark.skipif(
    __import__("importlib").util.find_spec("claude_agent_sdk") is None,
    reason="claude_agent_sdk not installed",
)


@_sdk_available
def test_browser_tools_accepts_origin_policy_param():
    """browser_tools() must accept origin_policy keyword without error."""
    import inspect
    from harness.tools import browser_tools
    sig = inspect.signature(browser_tools)
    assert "origin_policy" in sig.parameters


@_sdk_available
def test_browser_tools_origin_policy_defaults_to_none():
    import inspect
    from harness.tools import browser_tools
    sig = inspect.signature(browser_tools)
    default = sig.parameters["origin_policy"].default
    assert default is None
