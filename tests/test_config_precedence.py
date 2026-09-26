"""Acceptance tests for Task 04: configuration precedence contract.

No LLM, no browser, no network. Covers every level of the precedence contract
from OPEN_SOURCE_REQUIREMENTS.md §04:

  explicit YAML test settings
      > explicit YAML suite defaults
          > environment variables
              > built-in defaults

Plus:
  - Per-test base_url is honoured in goal interpolation
  - Gateway (base_url / auth_token on Config) is NOT overridden by application base_url
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from core.config import Config
from core.suite import TestSpec, _resolve_test_config, load_suite


# ---------------------------------------------------------------------------
# _resolve_test_config — unit tests for each precedence level
# ---------------------------------------------------------------------------

def _base_cfg(**overrides) -> Config:
    """Config that stands in for cfg_base; only results_root matters here."""
    cfg = Config()
    cfg.results_root = Path("results")
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _spec(**kwargs) -> TestSpec:
    return TestSpec(name="t", goal="g", **kwargs)


# Built-in defaults (nothing set anywhere)
def test_builtin_defaults_applied(monkeypatch) -> None:
    monkeypatch.delenv("ASSAY_MODEL", raising=False)
    monkeypatch.delenv("ASSAY_EFFORT", raising=False)
    monkeypatch.delenv("ASSAY_HEADLESS", raising=False)
    monkeypatch.delenv("ASSAY_SLOWMO_MS", raising=False)

    cfg = _resolve_test_config(_spec(), _base_cfg())

    assert cfg.model == "claude-sonnet-5"
    assert cfg.effort == "high"
    assert cfg.headless is True
    assert cfg.slow_mo_ms == 0


# Env vars override built-ins
@pytest.mark.parametrize("field,env_name,env_val,attr,expected", [
    ("model",     "ASSAY_MODEL",   "claude-haiku-4",  "model",      "claude-haiku-4"),
    ("effort",    "ASSAY_EFFORT",  "low",             "effort",     "low"),
    ("headless",  "ASSAY_HEADLESS","false",           "headless",   False),
    ("slowmo_ms", "ASSAY_SLOWMO_MS","250",            "slow_mo_ms", 250),
])
def test_env_var_overrides_builtin(monkeypatch, field, env_name, env_val, attr, expected) -> None:
    monkeypatch.setenv(env_name, env_val)
    cfg = _resolve_test_config(_spec(), _base_cfg())
    assert getattr(cfg, attr) == expected


# YAML defaults override env vars (simulated by loading a suite with defaults only)
def test_yaml_defaults_override_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_MODEL", "env-model")
    monkeypatch.setenv("ASSAY_EFFORT", "low")

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"model": "defaults-model", "effort": "medium"},
        "tests": [{"name": "t", "goal": "g"}],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    cfg = _resolve_test_config(specs[0], _base_cfg())

    assert cfg.model == "defaults-model"
    assert cfg.effort == "medium"


# Explicit per-test YAML overrides suite defaults
def test_per_test_yaml_overrides_defaults(tmp_path: Path) -> None:
    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"model": "defaults-model", "effort": "medium"},
        "tests": [{"name": "t", "goal": "g", "model": "test-model", "effort": "high"}],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    cfg = _resolve_test_config(specs[0], _base_cfg())

    assert cfg.model == "test-model"
    assert cfg.effort == "high"


# Full precedence stack: builtin < env < defaults < explicit test
def test_full_precedence_stack(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_SLOWMO_MS", "100")  # env

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"slowmo_ms": 200},   # suite default overrides env
        "tests": [
            {"name": "uses_default", "goal": "g"},            # inherits default=200
            {"name": "explicit",     "goal": "g", "slowmo_ms": 300},  # explicit=300
        ],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    cfg_default  = _resolve_test_config(specs[0], _base_cfg())
    cfg_explicit = _resolve_test_config(specs[1], _base_cfg())

    assert cfg_default.slow_mo_ms == 200   # default beats env
    assert cfg_explicit.slow_mo_ms == 300  # explicit beats default


# headless precedence
def test_headless_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_HEADLESS", "false")  # env says non-headless

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"headless": True},   # suite default re-enables headless
        "tests": [
            {"name": "via_default",   "goal": "g"},
            {"name": "via_explicit",  "goal": "g", "headless": False},
        ],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    cfg_default  = _resolve_test_config(specs[0], _base_cfg())
    cfg_explicit = _resolve_test_config(specs[1], _base_cfg())

    assert cfg_default.headless is True    # suite default wins over env
    assert cfg_explicit.headless is False  # explicit wins over suite default


# ---------------------------------------------------------------------------
# base_url: application URL is interpolated in goal; gateway URL stays untouched
# ---------------------------------------------------------------------------

def test_suite_base_url_interpolated_in_goal(tmp_path: Path) -> None:
    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"base_url": "http://app.local"},
        "tests": [{"name": "t", "goal": "go to {base_url}/login"}],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    assert specs[0].goal == "go to http://app.local/login"


def test_per_test_base_url_overrides_suite_default(tmp_path: Path) -> None:
    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"base_url": "http://suite.local"},
        "tests": [
            {"name": "uses_suite",   "goal": "go to {base_url}/a"},
            {"name": "uses_own",     "goal": "go to {base_url}/b",
             "base_url": "http://test.local"},
        ],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    assert specs[0].goal == "go to http://suite.local/a"
    assert specs[1].goal == "go to http://test.local/b"


def test_gateway_base_url_not_affected_by_application_base_url(
        tmp_path: Path, monkeypatch) -> None:
    """ANTHROPIC_BASE_URL (gateway) must not be overwritten by the YAML base_url."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com/api")

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"base_url": "http://app.local"},
        "tests": [{"name": "t", "goal": "go to {base_url}/login"}],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    cfg = _resolve_test_config(specs[0], _base_cfg())

    # Application base_url lives only in the interpolated goal string
    assert "app.local" in specs[0].goal
    # Gateway URL on Config is untouched
    assert cfg.base_url == "https://gateway.example.com/api"


# ---------------------------------------------------------------------------
# Unset YAML fields fall through to env (regression: hardcoded defaults masked env)
# ---------------------------------------------------------------------------

def test_unset_yaml_model_falls_through_to_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_MODEL", "env-model-xyz")

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "tests": [{"name": "t", "goal": "g"}],  # no model anywhere
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    cfg = _resolve_test_config(specs[0], _base_cfg())

    assert cfg.model == "env-model-xyz"


def test_results_root_comes_from_cfg_base(tmp_path: Path) -> None:
    custom_root = tmp_path / "custom_results"
    base = _base_cfg(results_root=custom_root)

    cfg = _resolve_test_config(_spec(), base)

    assert cfg.results_root == custom_root


# ---------------------------------------------------------------------------
# R5: ASSAY_BASE_URL env var is used for {base_url} interpolation when no YAML url
# ---------------------------------------------------------------------------

def test_env_base_url_used_in_goal_when_no_yaml_base_url(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_BASE_URL", "http://fixture.local")

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "tests": [{"name": "t", "goal": "go to {base_url}/login"}],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    assert specs[0].goal == "go to http://fixture.local/login"


def test_env_base_url_overridden_by_yaml_suite_default(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSAY_BASE_URL", "http://env.local")

    p = tmp_path / "suite.yaml"
    p.write_text(yaml.dump({
        "defaults": {"base_url": "http://yaml.local"},
        "tests": [{"name": "t", "goal": "go to {base_url}/login"}],
    }), encoding="utf-8")

    specs, _ = load_suite(p)
    assert specs[0].goal == "go to http://yaml.local/login"
