"""Context collection for `assay check`.

Loads the notes file, README, and optional git diff into a CheckContext.
All files are scrubbed for secrets and subject to size limits; provenance
(excluded files, truncations, warnings) is always recorded so the planner
can communicate to the user exactly what context the model had access to.

Git operations use subprocess with argument arrays — never shell=True.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core.redact import make_redactor

if TYPE_CHECKING:
    from core.config import Config

# ── Size limits ───────────────────────────────────────────────────────────────

_MAX_FILE_CHARS = 100_000   # per individual text file (notes, readme)
_MAX_DIFF_CHARS = 400_000   # total across committed + staged + unstaged sections

# ── Secret file patterns ──────────────────────────────────────────────────────

_EXACT_SECRET_NAMES: frozenset[str] = frozenset({
    "credentials.json", "secrets.json", "token.json", "service-account.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "id_rsa.pub", "id_ed25519.pub", "id_ecdsa.pub",
    ".netrc", ".npmrc", ".pypirc",
})

_SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem", ".key", ".pfx", ".p12", ".p8", ".keystore", ".jks",
)


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ExcludedFile:
    """A file that was not included in the context, with the reason."""
    path: str
    reason: str   # e.g. "secret file", "binary file", "auth-state file"


@dataclass
class TruncatedFile:
    """A file whose content was cut to fit within size limits."""
    path: str
    original_chars: int
    kept_chars: int


@dataclass
class CheckContext:
    """Collected context for one `assay check` run.

    Provenance (excluded, truncated, warnings) is always populated so the
    planner can include it in plan.json and the user understands exactly
    what context the model received.
    """

    notes_path: Path
    notes_text: str
    readme_path: Path | None      # resolved path that was attempted
    readme_text: str | None       # None = not found or excluded
    diff_ref: str | None
    diff_text: str | None         # committed + staged + unstaged; None = not requested
    untracked_files: list[str]    # filenames reported but not ingested
    excluded: list[ExcludedFile]
    truncated: list[TruncatedFile]
    warnings: list[str]


# ── Public entry point ────────────────────────────────────────────────────────

def collect_context(
    notes: Path,
    readme: Path | None,
    diff_ref: str | None,
    cfg: "Config",
    *,
    cwd: Path | None = None,
) -> CheckContext:
    """Load and validate all context for a `assay check` run.

    Args:
        notes:    Required notes/changes file (must exist).
        readme:   README path; defaults to README.md in *cwd*. Omission is warned.
        diff_ref: Git reference for ``--diff``; None = skip diff collection.
        cfg:      Resolved Config (used for auth-state exclusion).
        cwd:      Working directory for git operations; defaults to Path.cwd().

    Raises:
        ValueError: for non-git directories or invalid git references.
        FileNotFoundError: when *notes* does not exist.
    """
    work_dir = (cwd or Path.cwd()).resolve()
    excluded: list[ExcludedFile] = []
    truncated: list[TruncatedFile] = []
    warnings: list[str] = []
    redactor = make_redactor()

    # ── Notes (required) ─────────────────────────────────────────────────────
    if _is_binary_file(notes):
        raise ValueError(f"--notes {notes}: binary files cannot be used as context")
    notes_text, notes_trunc = _read_text_file(notes, _MAX_FILE_CHARS)
    notes_text = redactor.scrub(notes_text)
    if notes_trunc:
        truncated.append(TruncatedFile(str(notes), *notes_trunc))

    # ── README (optional) ────────────────────────────────────────────────────
    effective_readme = readme if readme is not None else work_dir / "README.md"
    readme_text: str | None = None
    if effective_readme.is_file():
        if _is_secret_file(effective_readme.name):
            excluded.append(ExcludedFile(str(effective_readme), "secret file"))
        elif _is_binary_file(effective_readme):
            excluded.append(ExcludedFile(str(effective_readme), "binary file"))
        else:
            readme_text, readme_trunc = _read_text_file(effective_readme, _MAX_FILE_CHARS)
            readme_text = redactor.scrub(readme_text)
            if readme_trunc:
                truncated.append(TruncatedFile(str(effective_readme), *readme_trunc))
    else:
        if readme is not None:
            warnings.append(
                f"--readme {effective_readme}: file not found; README context omitted"
            )
        else:
            warnings.append("README.md not found in working directory; README context omitted")

    # ── Diff (optional) ──────────────────────────────────────────────────────
    diff_text: str | None = None
    untracked_files: list[str] = []
    if diff_ref is not None:
        diff_text, untracked_files, diff_excluded, diff_warnings = _collect_diff(
            diff_ref, work_dir, cfg
        )
        excluded.extend(diff_excluded)
        warnings.extend(diff_warnings)
        if diff_text and len(diff_text) > _MAX_DIFF_CHARS:
            orig = len(diff_text)
            diff_text = diff_text[:_MAX_DIFF_CHARS]
            truncated.append(TruncatedFile(f"git diff {diff_ref}", orig, _MAX_DIFF_CHARS))
            warnings.append(
                f"git diff output truncated from {orig:,} to {_MAX_DIFF_CHARS:,} characters"
            )
        if diff_text:
            diff_text = redactor.scrub(diff_text)

    return CheckContext(
        notes_path=notes,
        notes_text=notes_text,
        readme_path=effective_readme,
        readme_text=readme_text,
        diff_ref=diff_ref,
        diff_text=diff_text,
        untracked_files=untracked_files,
        excluded=excluded,
        truncated=truncated,
        warnings=warnings,
    )


# ── Git operations ────────────────────────────────────────────────────────────

def _git_run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git subcommand using argument arrays (never shell=True)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, result.stdout, result.stderr


def _collect_diff(
    ref: str,
    cwd: Path,
    cfg: "Config",
) -> tuple[str | None, list[str], list[ExcludedFile], list[str]]:
    """Collect committed-since-merge-base + staged + unstaged diff for *ref*.

    Returns (diff_text, untracked_files, excluded_files, warnings).
    Raises ValueError with an actionable message on bad references or
    non-git directories.
    """
    excluded: list[ExcludedFile] = []
    warnings: list[str] = []

    # Verify this is a git repository.
    rc, _, err = _git_run(["rev-parse", "--git-dir"], cwd)
    if rc != 0:
        raise ValueError(
            f"--diff requires a git repository; {cwd} does not appear to be one"
        )

    # Validate the reference (the ^{commit} suffix dereferences tags).
    rc, _, err = _git_run(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd)
    if rc != 0:
        raise ValueError(
            f"--diff {ref!r}: invalid git reference — "
            f"{err.strip() or 'not found'}"
        )

    # Find merge-base (common ancestor of HEAD and REF).
    rc, merge_base_out, err = _git_run(["merge-base", "HEAD", ref], cwd)
    if rc != 0:
        raise ValueError(
            f"--diff: cannot find merge-base between HEAD and {ref!r}: "
            f"{err.strip() or 'unrelated histories'}"
        )
    merge_base = merge_base_out.strip()

    # Basename of the auth-state file to exclude (when configured).
    auth_state_basename = cfg.auth_state.name if cfg.auth_state else ""

    def _filter_names(raw: str) -> tuple[list[str], list[ExcludedFile]]:
        """Split a newline-separated file list; return (allowed, excluded)."""
        allowed: list[str] = []
        excl: list[ExcludedFile] = []
        for name in raw.splitlines():
            name = name.strip()
            if not name:
                continue
            if _is_secret_file(name):
                excl.append(ExcludedFile(name, "secret file"))
            elif auth_state_basename and Path(name).name == auth_state_basename:
                excl.append(ExcludedFile(name, "auth-state file"))
            else:
                allowed.append(name)
        return allowed, excl

    def _exclude_binary(names: list[str], diff_args: list[str]) -> list[str]:
        """Remove binary paths using Git's numstat marker (``-\t-``)."""
        if not names:
            return names
        _, raw, _ = _git_run(["diff", *diff_args, "--numstat", "--"] + names, cwd)
        binary: set[str] = set()
        for line in raw.splitlines():
            fields = line.split("\t", 2)
            if len(fields) == 3 and fields[0] == "-" and fields[1] == "-":
                binary.add(fields[2])
        for name in sorted(binary):
            excluded.append(ExcludedFile(name, "binary file"))
        return [name for name in names if name not in binary]

    sections: list[str] = []

    # Committed changes since merge-base.
    _, names_raw, _ = _git_run(
        ["diff", "--name-only", f"{merge_base}..HEAD"], cwd
    )
    allowed, excl = _filter_names(names_raw)
    excluded.extend(excl)
    allowed = _exclude_binary(allowed, [f"{merge_base}..HEAD"])
    if allowed:
        _, diff_out, _ = _git_run(
            ["diff", f"{merge_base}..HEAD", "--"] + allowed, cwd
        )
        if diff_out.strip():
            sections.append(
                f"# committed changes since merge-base ({merge_base[:8]})\n{diff_out}"
            )
    elif names_raw.strip():
        warnings.append("All committed changed files were excluded from diff context")

    # Staged changes.
    _, staged_names_raw, _ = _git_run(["diff", "--cached", "--name-only"], cwd)
    allowed, excl = _filter_names(staged_names_raw)
    excluded.extend(excl)
    allowed = _exclude_binary(allowed, ["--cached"])
    if allowed:
        _, staged_out, _ = _git_run(
            ["diff", "--cached", "--"] + allowed, cwd
        )
        if staged_out.strip():
            sections.append(f"# staged changes\n{staged_out}")

    # Unstaged tracked changes.
    _, unstaged_names_raw, _ = _git_run(["diff", "--name-only"], cwd)
    allowed, excl = _filter_names(unstaged_names_raw)
    excluded.extend(excl)
    allowed = _exclude_binary(allowed, [])
    if allowed:
        _, unstaged_out, _ = _git_run(
            ["diff", "--"] + allowed, cwd
        )
        if unstaged_out.strip():
            sections.append(f"# unstaged changes (tracked files)\n{unstaged_out}")

    # Untracked filenames (reported, never ingested).
    _, untracked_raw, _ = _git_run(
        ["ls-files", "--others", "--exclude-standard"], cwd
    )
    untracked_all = [n.strip() for n in untracked_raw.splitlines() if n.strip()]
    untracked_reported = [n for n in untracked_all if not _is_secret_file(n)]
    for name in untracked_all:
        if _is_secret_file(name):
            excluded.append(ExcludedFile(name, "secret file (untracked, filename only)"))
    if untracked_reported:
        sample = ", ".join(untracked_reported[:20])
        suffix = "…" if len(untracked_reported) > 20 else ""
        warnings.append(
            f"Untracked files not ingested (filenames only): {sample}{suffix}"
        )

    diff_text: str | None = "\n".join(sections) if sections else None
    if diff_text is None:
        warnings.append(f"No diff content found relative to {ref!r}")

    return diff_text, untracked_reported, excluded, warnings


# ── File utilities ────────────────────────────────────────────────────────────

def _read_text_file(
    path: Path,
    max_chars: int,
) -> tuple[str, tuple[int, int] | None]:
    """Read a UTF-8 text file (replacing undecodable bytes).

    Returns (text, None) when within limits, or (truncated_text, (orig, kept))
    when the file was truncated.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    if len(raw) <= max_chars:
        return raw, None
    return raw[:max_chars], (len(raw), max_chars)


def _is_secret_file(path: str) -> bool:
    """True when the file (by basename) should be excluded for security."""
    bn = Path(path).name
    if bn in _EXACT_SECRET_NAMES:
        return True
    # .env and all variants: .env.local, .env.production, .env_staging, etc.
    if bn == ".env" or bn.startswith(".env.") or bn.startswith(".env_"):
        return True
    return bn.endswith(_SECRET_SUFFIXES)


def _is_binary_file(path: Path) -> bool:
    """True when the file appears binary (null bytes in the first 8 KB)."""
    try:
        return b"\x00" in path.read_bytes()[:8192]
    except OSError:
        return False
