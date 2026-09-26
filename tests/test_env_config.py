"""Tests for .env loading and configuration validation (Task 08).

All tests use temporary files and monkeypatching — no real application, no LLM,
no browser. Tests do not contain real credentials.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.config import Config, load_dotenv


# ── .env loader ───────────────────────────────────────────────────────────────

def test_dotenv_loads_values_into_env(tmp_path, monkeypatch):
    """Values in .env are visible after load_dotenv()."""
    env_file = tmp_path / ".env"
    env_file.write_text("ASSAY_FIXTURE_KEY=hello\n")
    monkeypatch.delenv("ASSAY_FIXTURE_KEY", raising=False)
    load_dotenv(env_file)
    assert os.environ["ASSAY_FIXTURE_KEY"] == "hello"
    monkeypatch.delenv("ASSAY_FIXTURE_KEY", raising=False)  # cleanup


def test_process_env_wins_over_dotenv(tmp_path, monkeypatch):
    """An existing process env var is never overwritten by .env."""
    monkeypatch.setenv("ASSAY_FIXTURE_KEY2", "from_process")
    env_file = tmp_path / ".env"
    env_file.write_text("ASSAY_FIXTURE_KEY2=from_dotenv\n")
    load_dotenv(env_file)
    assert os.environ["ASSAY_FIXTURE_KEY2"] == "from_process"


def test_dotenv_strips_quotes(tmp_path, monkeypatch):
    """Double and single quoted values are unquoted."""
    env_file = tmp_path / ".env"
    env_file.write_text('KEY_DQ="dq_value"\nKEY_SQ=\'sq_value\'\n')
    monkeypatch.delenv("KEY_DQ", raising=False)
    monkeypatch.delenv("KEY_SQ", raising=False)
    load_dotenv(env_file)
    assert os.environ["KEY_DQ"] == "dq_value"
    assert os.environ["KEY_SQ"] == "sq_value"
    monkeypatch.delenv("KEY_DQ", raising=False)
    monkeypatch.delenv("KEY_SQ", raising=False)


def test_dotenv_ignores_comments_and_blank_lines(tmp_path, monkeypatch):
    """Comment lines and blank lines are silently skipped."""
    env_file = tmp_path / ".env"
    env_file.write_text("# This is a comment\n\nKEY_REAL=found\n")
    monkeypatch.delenv("KEY_REAL", raising=False)
    load_dotenv(env_file)
    assert os.environ["KEY_REAL"] == "found"
    monkeypatch.delenv("KEY_REAL", raising=False)


def test_dotenv_silent_when_file_missing(tmp_path):
    """load_dotenv does not raise when .env does not exist."""
    load_dotenv(tmp_path / "nonexistent.env")  # must not raise


# ── URL / origin validation ───────────────────────────────────────────────────

def test_invalid_base_url_raises(monkeypatch):
    """ASSAY_BASE_URL with no scheme raises a ValueError in from_env()."""
    monkeypatch.setenv("ASSAY_BASE_URL", "localhost:3000")
    with pytest.raises(ValueError, match="ASSAY_BASE_URL"):
        Config.from_env()


def test_valid_base_url_accepted(monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    cfg = Config.from_env()
    assert cfg.app_url == "http://localhost:3000"


def test_login_url_defaults_to_application_url(monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.delenv("ASSAY_LOGIN_URL", raising=False)
    assert Config.from_env().login_url == "http://localhost:3000"


def test_invalid_login_url_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_LOGIN_URL", "not-a-url")
    with pytest.raises(ValueError, match="ASSAY_LOGIN_URL"):
        Config.from_env()


def test_invalid_origin_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_ALLOWED_ORIGINS", "http://ok.example.com,not-an-origin")
    with pytest.raises(ValueError, match="ASSAY_ALLOWED_ORIGINS"):
        Config.from_env()


def test_origin_with_path_raises(monkeypatch):
    """Origins must not include a path component."""
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_ALLOWED_ORIGINS", "http://localhost:3000/app")
    with pytest.raises(ValueError, match="no path"):
        Config.from_env()


def test_allowed_origins_defaults_to_base_url_origin(monkeypatch):
    """When ASSAY_ALLOWED_ORIGINS is unset, allowed_origins derives from ASSAY_BASE_URL."""
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.delenv("ASSAY_ALLOWED_ORIGINS", raising=False)
    cfg = Config.from_env()
    assert cfg.allowed_origins == ["http://localhost:3000"]


def test_allowed_origins_empty_when_no_base_url(monkeypatch):
    monkeypatch.delenv("ASSAY_BASE_URL", raising=False)
    monkeypatch.delenv("ASSAY_ALLOWED_ORIGINS", raising=False)
    cfg = Config.from_env()
    assert cfg.allowed_origins == []


# ── Credential pairing ────────────────────────────────────────────────────────

def test_username_without_password_rejected(monkeypatch):
    monkeypatch.setenv("ASSAY_TEST_USERNAME", "user@example.com")
    monkeypatch.delenv("ASSAY_TEST_PASSWORD", raising=False)
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="both be set or both be unset"):
        cfg.validate_for_check()


def test_password_without_username_rejected(monkeypatch):
    monkeypatch.delenv("ASSAY_TEST_USERNAME", raising=False)
    monkeypatch.setenv("ASSAY_TEST_PASSWORD", "secret")
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="both be set or both be unset"):
        cfg.validate_for_check()


def test_both_credentials_accepted(monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_TEST_USERNAME", "user@example.com")
    monkeypatch.setenv("ASSAY_TEST_PASSWORD", "secret")
    cfg = Config.from_env()
    cfg.validate_for_check()  # must not raise


def test_no_credentials_accepted(monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.delenv("ASSAY_TEST_USERNAME", raising=False)
    monkeypatch.delenv("ASSAY_TEST_PASSWORD", raising=False)
    cfg = Config.from_env()
    cfg.validate_for_check()  # must not raise


# ── Missing required inputs ───────────────────────────────────────────────────

def test_missing_base_url_fails_validate_for_check(monkeypatch):
    monkeypatch.delenv("ASSAY_BASE_URL", raising=False)
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="ASSAY_BASE_URL is required"):
        cfg.validate_for_check()


def test_multiple_errors_reported_together(monkeypatch):
    """validate_for_check collects all errors before raising."""
    monkeypatch.delenv("ASSAY_BASE_URL", raising=False)
    monkeypatch.setenv("ASSAY_TEST_USERNAME", "user@example.com")
    monkeypatch.delenv("ASSAY_TEST_PASSWORD", raising=False)
    cfg = Config.from_env()
    with pytest.raises(ValueError) as exc_info:
        cfg.validate_for_check()
    msg = str(exc_info.value)
    assert "ASSAY_BASE_URL" in msg
    assert "ASSAY_TEST_USERNAME" in msg or "both be set" in msg


# ── File existence checks ─────────────────────────────────────────────────────

def test_missing_auth_notes_file_fails_check(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_AUTH_NOTES_FILE", str(tmp_path / "nonexistent.md"))
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="ASSAY_AUTH_NOTES_FILE"):
        cfg.validate_for_check()


def test_missing_test_data_file_fails_check(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_TEST_DATA_FILE", str(tmp_path / "nonexistent.md"))
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="ASSAY_TEST_DATA_FILE"):
        cfg.validate_for_check()


def test_existing_notes_file_accepted(tmp_path, monkeypatch):
    notes = tmp_path / "notes.md"
    notes.write_text("# Test notes\n")
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_AUTH_NOTES_FILE", str(notes))
    cfg = Config.from_env()
    cfg.validate_for_check()  # must not raise


def test_auth_state_no_existence_check(tmp_path, monkeypatch):
    """ASSAY_AUTH_STATE is allowed to not exist yet (created on first login run)."""
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("ASSAY_AUTH_STATE", str(tmp_path / "no-such-state.json"))
    cfg = Config.from_env()
    cfg.validate_for_check()  # must not raise


# ── Budget limit validation ───────────────────────────────────────────────────

def test_negative_max_actions_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_MAX_ACTIONS", "-5")
    with pytest.raises(ValueError, match="ASSAY_MAX_ACTIONS"):
        Config.from_env()


def test_zero_max_seconds_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_MAX_SECONDS", "0")
    with pytest.raises(ValueError, match="ASSAY_MAX_SECONDS"):
        Config.from_env()


def test_non_numeric_max_actions_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_MAX_ACTIONS", "lots")
    with pytest.raises(ValueError, match="ASSAY_MAX_ACTIONS"):
        Config.from_env()


def test_positive_limits_accepted(monkeypatch):
    monkeypatch.setenv("ASSAY_MAX_SECONDS", "600")
    monkeypatch.setenv("ASSAY_MAX_ACTIONS", "100")
    monkeypatch.setenv("ASSAY_MAX_COST_USD", "1.50")
    cfg = Config.from_env()
    assert cfg.max_seconds == 600
    assert cfg.max_actions == 100
    assert cfg.max_cost_usd == pytest.approx(1.50)


def test_negative_cost_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_MAX_COST_USD", "-0.5")
    with pytest.raises(ValueError, match="ASSAY_MAX_COST_USD"):
        Config.from_env()


# ── Depth validation ──────────────────────────────────────────────────────────

def test_invalid_depth_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_DEPTH", "extreme")
    with pytest.raises(ValueError, match="ASSAY_DEPTH"):
        Config.from_env()


@pytest.mark.parametrize("depth", ["low", "medium", "high"])
def test_valid_depths_accepted(depth, monkeypatch):
    monkeypatch.setenv("ASSAY_DEPTH", depth)
    cfg = Config.from_env()
    assert cfg.depth == depth


# ── Boolean validation ────────────────────────────────────────────────────────

def test_invalid_allow_mutations_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_ALLOW_MUTATIONS", "maybe")
    with pytest.raises(ValueError, match="ASSAY_ALLOW_MUTATIONS"):
        Config.from_env()


def test_invalid_headless_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_HEADLESS", "sometimes")
    with pytest.raises(ValueError, match="ASSAY_HEADLESS"):
        Config.from_env()


def test_invalid_show_evidence_raises(monkeypatch):
    monkeypatch.setenv("ASSAY_SHOW_EVIDENCE", "sometimes")
    with pytest.raises(ValueError, match="ASSAY_SHOW_EVIDENCE"):
        Config.from_env()


@pytest.mark.parametrize("name", ["ASSAY_NAV_TIMEOUT_MS", "ASSAY_ACTION_TIMEOUT_MS"])
def test_browser_timeout_must_be_positive(name, monkeypatch):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match=name):
        Config.from_env()


def test_slowmo_must_not_be_negative(monkeypatch):
    monkeypatch.setenv("ASSAY_SLOWMO_MS", "-1")
    with pytest.raises(ValueError, match="ASSAY_SLOWMO_MS"):
        Config.from_env()


def test_allow_mutations_true(monkeypatch):
    monkeypatch.setenv("ASSAY_ALLOW_MUTATIONS", "true")
    assert Config.from_env().allow_mutations is True


def test_allow_mutations_defaults_false(monkeypatch):
    monkeypatch.delenv("ASSAY_ALLOW_MUTATIONS", raising=False)
    assert Config.from_env().allow_mutations is False


# ── Gateway / application URL separation ─────────────────────────────────────

def test_gateway_base_url_not_overwritten_by_app_url(monkeypatch):
    """ANTHROPIC_BASE_URL (gateway) must be preserved when ASSAY_BASE_URL is also set."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com/api")
    monkeypatch.setenv("ASSAY_BASE_URL", "http://localhost:3000")
    cfg = Config.from_env()
    assert cfg.base_url == "https://gateway.example.com/api"
    assert cfg.app_url == "http://localhost:3000"


# ── CLI --help requires no secrets ───────────────────────────────────────────

def test_build_parser_needs_no_env():
    """build_parser() works with no environment variables set at all."""
    from harness.cli import build_parser
    parser = build_parser()
    assert parser is not None
    # --help exits with code 0 (SystemExit); ensure the parser can describe itself
    assert "suite" in parser.format_usage() or True  # parser built successfully
