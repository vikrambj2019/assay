"""Tests for context collection (Task 09).

Uses temporary git repositories via real subprocess calls.
Tests marked @git_required are skipped when git is not available.
No real application, no LLM, no browser, no real credentials.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.context import (
    CheckContext,
    ExcludedFile,
    TruncatedFile,
    _is_secret_file,
    _is_binary_file,
    collect_context,
)

git_required = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not installed",
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_cfg(**kwargs):
    """Minimal fake Config object."""
    return SimpleNamespace(auth_state=None, **kwargs)


def _init_repo(path: Path) -> None:
    """Initialise a git repo with a single 'init' commit."""
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# My App\n")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True,
                   capture_output=True)


def _commit(repo: Path, message: str = "change") -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True,
                   capture_output=True)


# ── secret / binary detection (pure unit) ─────────────────────────────────────

@pytest.mark.parametrize("name", [
    ".env", ".env.local", ".env.production", ".env_staging",
    "credentials.json", "secrets.json", "id_rsa", "id_ed25519",
    "server.pem", "private.key", "store.pfx", "keystore.jks",
])
def test_is_secret_file_detects_known_patterns(name):
    assert _is_secret_file(name)


@pytest.mark.parametrize("name", [
    "README.md", "app.py", "config.json", "settings.yaml",
    ".envrc",        # direnv file — not a secret env file
    "deployment.key.md",  # .md suffix, not .key
])
def test_is_secret_file_does_not_reject_safe_files(name):
    assert not _is_secret_file(name)


def test_is_binary_file_detects_null_bytes(tmp_path):
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\x00\x01\x02")
    assert _is_binary_file(f)


def test_is_binary_file_returns_false_for_text(tmp_path):
    f = tmp_path / "text.txt"
    f.write_text("hello world\n")
    assert not _is_binary_file(f)


# ── notes loading ─────────────────────────────────────────────────────────────

def test_notes_loaded_correctly(tmp_path):
    notes = tmp_path / "changes.md"
    notes.write_text("## Changes\n- Fixed bug\n")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, None, cfg, cwd=tmp_path)
    assert ctx.notes_text == "## Changes\n- Fixed bug\n"
    assert ctx.notes_path == notes


def test_context_scrubs_configured_credentials(tmp_path, monkeypatch):
    secret = "fixture-context-secret"
    monkeypatch.setenv("BTA_TEST_PASSWORD", secret)
    notes = tmp_path / "changes.md"
    notes.write_text(f"login password={secret}\n")
    readme = tmp_path / "README.md"
    readme.write_text(f"token={secret}\n")
    ctx = collect_context(notes, readme, None, _make_cfg(), cwd=tmp_path)
    assert secret not in ctx.notes_text
    assert secret not in (ctx.readme_text or "")
    assert "[REDACTED]" in ctx.notes_text


def test_missing_notes_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_context(tmp_path / "no.md", None, None, _make_cfg(), cwd=tmp_path)


def test_notes_truncated_to_limit(tmp_path):
    from core.context import _MAX_FILE_CHARS
    notes = tmp_path / "big.md"
    notes.write_text("x" * (_MAX_FILE_CHARS + 100))
    cfg = _make_cfg()
    ctx = collect_context(notes, None, None, cfg, cwd=tmp_path)
    assert len(ctx.notes_text) == _MAX_FILE_CHARS
    assert len(ctx.truncated) == 1
    assert ctx.truncated[0].kept_chars == _MAX_FILE_CHARS


# ── README loading ────────────────────────────────────────────────────────────

def test_readme_defaults_to_README_md(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    (tmp_path / "README.md").write_text("# Project\n")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, None, cfg, cwd=tmp_path)
    assert ctx.readme_text == "# Project\n"


def test_readme_not_found_generates_warning(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, None, cfg, cwd=tmp_path)
    assert ctx.readme_text is None
    assert any("README.md" in w for w in ctx.warnings)


def test_explicit_readme_not_found_warns(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    cfg = _make_cfg()
    ctx = collect_context(notes, tmp_path / "MISSING.md", None, cfg, cwd=tmp_path)
    assert ctx.readme_text is None
    assert any("MISSING.md" in w for w in ctx.warnings)


def test_readme_explicit_path_loaded(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    readme = tmp_path / "CUSTOM_README.md"
    readme.write_text("# Custom\n")
    cfg = _make_cfg()
    ctx = collect_context(notes, readme, None, cfg, cwd=tmp_path)
    assert ctx.readme_text == "# Custom\n"


def test_binary_readme_excluded(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    readme = tmp_path / "README.md"
    readme.write_bytes(b"\x00\x01PDF binary content")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, None, cfg, cwd=tmp_path)
    assert ctx.readme_text is None
    assert any(e.reason == "binary file" for e in ctx.excluded)


# ── diff: no request ──────────────────────────────────────────────────────────

def test_no_diff_when_not_requested(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, None, cfg, cwd=tmp_path)
    assert ctx.diff_ref is None
    assert ctx.diff_text is None
    assert ctx.untracked_files == []


# ── diff: git operations ───────────────────────────────────────────────────────

@git_required
def test_diff_committed_branch_changes(tmp_path):
    """Committed changes since merge-base appear in diff_text."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # branch off main → add a feature file → commit
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=str(repo), check=True,
                   capture_output=True)
    (repo / "feature.py").write_text("def hello(): pass\n")
    _commit(repo, "add feature")

    notes = tmp_path / "notes.md"
    notes.write_text("Added hello function")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "main", cfg, cwd=repo)
    assert ctx.diff_ref == "main"
    assert ctx.diff_text is not None
    assert "feature.py" in ctx.diff_text
    assert "hello" in ctx.diff_text


@git_required
def test_diff_staged_changes(tmp_path):
    """Staged (cached) changes appear in diff_text."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    (repo / "staged.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "staged.py"], cwd=str(repo), check=True,
                   capture_output=True)

    notes = tmp_path / "notes.md"
    notes.write_text("staged change")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)
    assert ctx.diff_text is not None
    assert "staged.py" in ctx.diff_text


@git_required
def test_diff_unstaged_changes(tmp_path):
    """Unstaged changes to tracked files appear in diff_text."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    # Modify the already-committed README without staging it
    (repo / "README.md").write_text("# Modified\n")

    notes = tmp_path / "notes.md"
    notes.write_text("modified readme")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)
    assert ctx.diff_text is not None
    assert "README.md" in ctx.diff_text


@git_required
def test_diff_untracked_files_names_only(tmp_path):
    """Untracked filenames are reported but their contents are not loaded."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    (repo / "untracked.py").write_text("secret content that must not appear")

    notes = tmp_path / "notes.md"
    notes.write_text("untracked test")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)

    assert "untracked.py" in ctx.untracked_files
    # Content must not appear in diff_text
    if ctx.diff_text:
        assert "secret content" not in ctx.diff_text
    # Filenames are reported in warnings
    assert any("untracked.py" in w for w in ctx.warnings)


@git_required
def test_invalid_diff_ref_raises(tmp_path):
    """A bad git reference gives a clear ValueError."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    with pytest.raises(ValueError, match="invalid git reference"):
        collect_context(notes, None, "no-such-branch-xyz", _make_cfg(), cwd=repo)


@git_required
def test_non_git_folder_raises(tmp_path):
    """A directory that is not a git repo raises a clear ValueError."""
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    with pytest.raises(ValueError, match="git repository"):
        collect_context(notes, None, "HEAD", _make_cfg(), cwd=plain_dir)


# ── secret file exclusion ─────────────────────────────────────────────────────

@git_required
def test_secret_file_in_diff_excluded(tmp_path):
    """.env changes in the diff are excluded and recorded."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    (repo / ".env").write_text("SECRET=hunter2\n")
    subprocess.run(["git", "add", ".env"], cwd=str(repo), check=True,
                   capture_output=True)
    (repo / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=str(repo), check=True,
                   capture_output=True)

    notes = tmp_path / "notes.md"
    notes.write_text("test")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)

    # Secret must not appear anywhere in diff_text
    if ctx.diff_text:
        assert "hunter2" not in ctx.diff_text
        assert "SECRET" not in ctx.diff_text
    # .env must appear in excluded list
    assert any(".env" in e.path for e in ctx.excluded)


@git_required
def test_binary_file_in_diff_excluded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "image.bin").write_bytes(b"\x00\x01\x02\x03")
    subprocess.run(["git", "add", "image.bin"], cwd=str(repo), check=True,
                   capture_output=True)
    notes = tmp_path / "notes.md"
    notes.write_text("test")
    ctx = collect_context(notes, None, "HEAD", _make_cfg(), cwd=repo)
    assert not ctx.diff_text or "Binary files" not in ctx.diff_text
    assert any(e.path == "image.bin" and e.reason == "binary file" for e in ctx.excluded)


@git_required
def test_untracked_secret_file_excluded_from_report(tmp_path):
    """Untracked .env files are not listed in untracked_files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    (repo / ".env.local").write_text("SECRET=password\n")

    notes = tmp_path / "notes.md"
    notes.write_text("test")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)

    assert ".env.local" not in ctx.untracked_files
    assert any(".env.local" in e.path for e in ctx.excluded)


@git_required
def test_auth_state_file_excluded_from_diff(tmp_path):
    """The configured auth-state file is excluded from diff context."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    auth_file = repo / "auth-state.json"
    auth_file.write_text('{"cookies": []}')
    subprocess.run(["git", "add", "auth-state.json"], cwd=str(repo), check=True,
                   capture_output=True)

    notes = tmp_path / "notes.md"
    notes.write_text("test")

    import types
    cfg = types.SimpleNamespace(auth_state=Path("auth-state.json"))
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)

    if ctx.diff_text:
        assert "auth-state.json" not in ctx.diff_text
    assert any("auth-state" in e.path for e in ctx.excluded)


# ── diff truncation ───────────────────────────────────────────────────────────

@git_required
def test_large_diff_truncated(tmp_path, monkeypatch):
    """A diff exceeding _MAX_DIFF_CHARS is truncated and recorded."""
    from core import context as ctx_module
    monkeypatch.setattr(ctx_module, "_MAX_DIFF_CHARS", 50)

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "big.txt").write_text("a" * 200 + "\n")
    subprocess.run(["git", "add", "big.txt"], cwd=str(repo), check=True,
                   capture_output=True)

    notes = tmp_path / "notes.md"
    notes.write_text("test")
    ctx = collect_context(notes, None, "HEAD", _make_cfg(), cwd=repo)

    assert ctx.diff_text is not None
    assert len(ctx.diff_text) == 50
    assert len(ctx.truncated) >= 1
    assert any("git diff" in t.path for t in ctx.truncated)
    assert any("truncated" in w for w in ctx.warnings)


# ── provenance completeness ───────────────────────────────────────────────────

@git_required
def test_context_provenance_is_complete(tmp_path):
    """CheckContext always carries excluded, truncated, warnings lists."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    notes = tmp_path / "notes.md"
    notes.write_text("notes")
    cfg = _make_cfg()
    ctx = collect_context(notes, None, "HEAD", cfg, cwd=repo)

    assert isinstance(ctx.excluded, list)
    assert isinstance(ctx.truncated, list)
    assert isinstance(ctx.warnings, list)
    assert isinstance(ctx.untracked_files, list)


# ── CLI --help ─────────────────────────────────────────────────────────────────

def test_check_subparser_exists():
    """build_parser() exposes a 'check' subcommand."""
    from harness.cli import build_parser
    parser = build_parser()
    # Access internal subparser actions to confirm 'check' is registered.
    for action in parser._actions:
        if hasattr(action, '_name_parser_map'):
            assert "check" in action._name_parser_map
            return
    pytest.fail("'check' subcommand not found in parser")


def test_check_help_exits_cleanly():
    """bta check --help exits with code 0 and requires no secrets."""
    from harness.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["check", "--help"])
    assert exc_info.value.code == 0


def test_missing_notes_arg_exits_nonzero():
    """bta check with no --notes exits with code 2."""
    from harness.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["check"])
    assert exc_info.value.code != 0
