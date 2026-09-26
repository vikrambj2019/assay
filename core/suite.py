"""The suite runner — one YAML file describing many goals, run as a graph.

A suite is the "what to do" layer sitting above the skills' "how to do": each
test is a goal string (an instruction) plus config and `needs:` dependencies.
The runner reads the YAML, checks the dependency graph, and drives each test with
`run_goal` in its own browser — running independent tests concurrently (up to
`max_parallel`) and honouring `needs:` so, e.g., login runs before the rest.

    docker compose run --rm agent suite suites/fixture-demo.yaml

Session sharing: a *root* test (no `needs`, e.g. login) writes the shared session
file so dependents inherit its logged-in cookies; each dependent reads an isolated
copy, so concurrent write-back can never clobber the login.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Callable

import yaml

from core.browser import BrowserSession
from core.config import Config
from core.report import write_report
from core.redact import make_redactor
from core.schema import StepLog, TestFile, Verdict, overall

# Config keys a test (or `defaults:`) may set. Anything else in a test block is a
# structural key (name/goal/needs/skip) handled explicitly below.
_CONFIG_KEYS = ("headless", "model", "effort", "slowmo_ms", "session", "base_url")


@dataclass
class TestSpec:
    name: str
    goal: str
    needs: list[str] = field(default_factory=list)
    skip: bool = False
    # None means "not set in YAML" — _resolve_test_config falls through to env/built-in.
    headless: bool | None = None
    model: str | None = None
    effort: str | None = None
    slowmo_ms: int | None = None
    session: str | None = None  # shared session file path (relative to cwd)

    @property
    def is_root(self) -> bool:
        return not self.needs


def _safe_name(name: str) -> None:
    """Raise ValueError if name could escape the suite output directory.

    A test name is used directly as a path component (suite_dir / name), so it
    must be a single, non-special component with no separators.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(
            f"unsafe test name {name!r}: must not be empty, '.', '..', "
            "or contain path separators")


def load_suite(path: str | Path) -> tuple[list[TestSpec], int]:
    """Parse the YAML suite into specs + max_parallel; validate the dependency graph."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {}) or {}
    max_parallel = int(defaults.get("max_parallel", 3))
    if max_parallel < 1:
        raise ValueError(f"max_parallel must be at least 1, got {max_parallel}")
    # Suite-level base_url beats the env var; env var beats nothing.
    base_url = defaults.get("base_url") or os.environ.get("ASSAY_BASE_URL", "")

    specs: list[TestSpec] = []
    for raw in data.get("tests") or []:
        name_hint = str(raw.get("name", "?"))
        merged = {k: raw.get(k, defaults.get(k)) for k in _CONFIG_KEYS}

        # Type validation — reject values that would silently produce wrong behavior.
        raw_goal = raw.get("goal")
        if raw_goal is None:
            raise ValueError(f"test {name_hint!r}: 'goal' must not be null")

        raw_skip = raw.get("skip", False)
        if not isinstance(raw_skip, bool):
            raise ValueError(
                f"test {name_hint!r}: 'skip' must be a boolean (true/false), "
                f"got {type(raw_skip).__name__!r}")

        raw_headless = merged["headless"]
        if raw_headless is not None and not isinstance(raw_headless, bool):
            raise ValueError(
                f"test {name_hint!r}: 'headless' must be a boolean (true/false), "
                f"got {type(raw_headless).__name__!r}")

        # Per-test base_url takes precedence over suite-level base_url for interpolation.
        effective_base_url = (str(merged["base_url"])
                              if merged["base_url"] is not None
                              else (base_url or ""))
        goal = str(raw_goal).replace("{base_url}", effective_base_url)
        specs.append(TestSpec(
            name=str(raw["name"]),
            goal=goal,
            needs=list(raw.get("needs") or raw.get("depends_on") or []),
            skip=raw_skip,
            headless=raw_headless,
            model=(str(merged["model"]) if merged["model"] is not None else None),
            effort=(str(merged["effort"]) if merged["effort"] is not None else None),
            slowmo_ms=(int(merged["slowmo_ms"]) if merged["slowmo_ms"] is not None else None),
            session=merged["session"],
        ))

    if not specs:
        raise ValueError("suite has no tests")
    _validate(specs)
    return specs, max_parallel


def _validate(specs: list[TestSpec]) -> None:
    # Name safety must come first — names are used as path components.
    for s in specs:
        _safe_name(s.name)

    names = [s.name for s in specs]
    if len(names) != len(set(names)):
        raise ValueError("duplicate test names in suite")
    known = set(names)
    for s in specs:
        for dep in s.needs:
            if dep not in known:
                raise ValueError(f"test {s.name!r} needs unknown test {dep!r}")
    # Cycle check via DFS.
    graph = {s.name: s.needs for s in specs}
    state: dict[str, int] = {}  # 0=visiting, 1=done

    def visit(n: str, trail: list[str]) -> None:
        if state.get(n) == 1:
            return
        if state.get(n) == 0:
            raise ValueError(f"dependency cycle: {' -> '.join(trail + [n])}")
        state[n] = 0
        for dep in graph[n]:
            visit(dep, trail + [n])
        state[n] = 1

    for s in specs:
        visit(s.name, [])

    # Two non-skipped root tests writing the same session file would race — reject.
    # A root is any test with no needs:. If the conflict exists, the author can
    # add a needs: dependency so only one root writes the shared session.
    # Use Path objects as keys so "shared.json" and "./shared.json" collapse to the
    # same key (Path normalizes away the redundant "./").
    root_sessions: dict[Path, str] = {}  # resolved session path → first root test name
    for s in specs:
        if s.is_root and not s.skip and s.session:
            norm = Path(s.session).resolve()
            if norm in root_sessions:
                raise ValueError(
                    f"tests {root_sessions[norm]!r} and {s.name!r} are both "
                    f"root tests writing session file {s.session!r}; add a "
                    f"needs: dependency so only one root produces this session"
                )
            root_sessions[norm] = s.name


def _resolve_test_config(spec: TestSpec, cfg_base: Config) -> Config:
    """Build a Config for one test: env/built-in defaults, overridden by explicit YAML values.

    Fields that were not set in the YAML (stored as None on TestSpec) fall through
    to whatever Config.from_env() read from the environment, so env vars are never
    silently shadowed by suite-runner hard-coded defaults.
    """
    cfg = Config.from_env()
    if spec.headless is not None:
        cfg.headless = spec.headless
    if spec.model is not None:
        cfg.model = spec.model
    if spec.effort is not None:
        cfg.effort = spec.effort
    if spec.slowmo_ms is not None:
        cfg.slow_mo_ms = spec.slowmo_ms
    cfg.results_root = cfg_base.results_root
    return cfg


async def _run_test(spec: TestSpec, cfg_base: Config, suite_dir: Path,
                    emit: Callable[[str], None], usage: dict) -> list[StepLog]:
    """Drive one test with its own browser + per-test config, into suite_dir/<name>/.

    The final SDK ResultMessage (token + cost tally) is stashed in `usage[spec.name]`
    so run_suite can sum cost across the whole suite."""
    from harness.agent import run_goal  # deferred: pulls in the Agent SDK

    out_dir = suite_dir / spec.name
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = _resolve_test_config(spec, cfg_base)
    redactor = make_redactor()
    safe_emit = lambda message: emit(redactor.scrub(message))
    session_copy: Path | None = None

    if spec.session:
        shared = Path(spec.session)
        if spec.is_root:
            cfg.storage_state = shared  # root produces the shared login session
        else:
            # Keep the isolated runtime copy outside the results tree. Auth state
            # contains cookies/tokens and must never be a shareable artifact.
            if shared.exists():
                tmp = tempfile.NamedTemporaryFile(prefix="assay-session-", suffix=".json", delete=False)
                tmp.close()
                session_copy = Path(tmp.name)
                shutil.copy(shared, session_copy)  # isolated read-copy; never clobbers shared
            cfg.storage_state = session_copy

    # Use explicit start/close instead of `async with` so that a teardown error
    # (raised in close()) does not discard logs already returned by run_goal.
    session = await BrowserSession(cfg).start()
    logs: list[StepLog] = []
    teardown_error: Exception | None = None
    try:
        logs = await run_goal(session, spec.goal, cfg, out_dir, on_event=safe_emit,
                              on_result=lambda r: usage.__setitem__(spec.name, r))
    finally:
        try:
            await session.close()
        except Exception as td_exc:  # noqa: BLE001
            teardown_error = td_exc
            safe_emit(f"[{spec.name}] teardown error (result preserved): {td_exc}")
        if session_copy is not None:
            try:
                session_copy.unlink(missing_ok=True)
            except OSError:
                safe_emit(f"[{spec.name}] could not remove temporary auth state")
    if teardown_error is not None:
        logs.append(StepLog(
            len(logs) + 1,
            f"Cleanup for {spec.name}",
            Verdict.ERROR,
            redactor.scrub(
                f"teardown failed: {type(teardown_error).__name__}: {teardown_error}"
            ),
        ))
    return logs


async def run_suite(path: str | Path, on_event: Callable[[str], None] | None = None) -> int:
    """Run the whole suite; return 0 if every test passed, else 1."""
    emit = on_event or (lambda _s: None)
    specs, max_parallel = load_suite(path)
    cfg_base = Config.from_env()
    suite_dir = cfg_base.new_run_dir()

    sem = asyncio.Semaphore(max_parallel)
    done = {s.name: asyncio.Event() for s in specs}
    verdicts: dict[str, Verdict] = {}
    logs_by_name: dict[str, list[StepLog]] = {}
    usage: dict = {}  # test name → final ResultMessage (token + cost tally)

    async def worker(spec: TestSpec) -> None:
        for dep in spec.needs:
            await done[dep].wait()
        try:
            if spec.skip:
                verdicts[spec.name] = Verdict.SKIPPED
                emit(f"» SKIP {spec.name}")
                return
            blockers = [d for d in spec.needs if verdicts.get(d) is not Verdict.PASS]
            if blockers:
                verdicts[spec.name] = Verdict.BLOCKED
                emit(f"– BLOCKED {spec.name} (upstream failed: {', '.join(blockers)})")
                return
            async with sem:
                emit(f"\n▶ START {spec.name} (headless={spec.headless})")
                try:
                    logs = await _run_test(spec, cfg_base, suite_dir,
                                           lambda s, n=spec.name: emit(f"[{n}] {s}"), usage)
                except Exception as e:  # noqa: BLE001
                    emit(f"[{spec.name}] ERROR: {type(e).__name__}: {e}")
                    logs = [StepLog(1, f"Goal: {spec.goal}", Verdict.ERROR,
                                   f"{type(e).__name__}: {e}")]
            logs_by_name[spec.name] = logs
            verdicts[spec.name] = overall(logs)
            emit(f"■ DONE {spec.name}: {verdicts[spec.name].value}")
        finally:
            done[spec.name].set()

    await asyncio.gather(*(worker(s) for s in specs))

    index = _write_index(specs, verdicts, logs_by_name, usage, suite_dir)
    _print_summary(specs, verdicts, emit)
    _print_total_cost(usage, emit)
    emit(f"\n  suite report: {index}")
    return 0 if all(verdicts.get(s.name) is Verdict.PASS for s in specs
                    if not s.skip) else 1


def _print_summary(specs: list[TestSpec], verdicts: dict[str, Verdict],
                   emit: Callable[[str], None]) -> None:
    icon = {Verdict.PASS: "✓", Verdict.FAIL: "✗", Verdict.BLOCKED: "–",
            Verdict.ERROR: "!", Verdict.SKIPPED: "»", Verdict.UNVERIFIED: "?"}
    emit("\n=== suite summary ===")
    for s in specs:
        v = verdicts.get(s.name, Verdict.ERROR)
        dep = f"  (needs {', '.join(s.needs)})" if s.needs else ""
        emit(f"  {icon.get(v, '?')} {v.value:8} {s.name}{dep}")


def _print_total_cost(usage: dict, emit: Callable[[str], None]) -> None:
    """Sum the per-test SDK tallies into one suite total (cost is the SDK's own
    total_cost_usd per test, so the sum tracks real pricing)."""
    if not usage:
        return
    cost = sum((r.total_cost_usd or 0) for r in usage.values())
    turns = sum((r.num_turns or 0) for r in usage.values())
    inp = sum((r.usage or {}).get("input_tokens", 0) for r in usage.values())
    out = sum((r.usage or {}).get("output_tokens", 0) for r in usage.values())
    emit("\n=== suite cost (all tests) ===")
    emit(f"  Tokens  {inp:,} in  ·  {out:,} out")
    emit(f"  Turns   {turns}   ·   Tests billed {len(usage)}   ·   Total cost  ${cost:.4f}")


def _fmt_time(ms: int | None) -> str:
    """Human-friendly duration: '850ms' under a second, else whole seconds."""
    if not ms:
        return "—"
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def _write_index(specs: list[TestSpec], verdicts: dict[str, Verdict],
                 logs_by_name: dict[str, list[StepLog]], usage: dict,
                 suite_dir: Path) -> Path:
    """Write per-test reports + a suite index linking them."""
    color = {Verdict.PASS: "#1a7f37", Verdict.FAIL: "#cf222e", Verdict.ERROR: "#bf8700",
             Verdict.BLOCKED: "#6e7781", Verdict.SKIPPED: "#6e7781",
             Verdict.UNVERIFIED: "#9a6700"}
    rows = []
    for s in specs:
        v = verdicts.get(s.name, Verdict.ERROR)
        link = "—"
        if s.name in logs_by_name:
            report = write_report(TestFile(path=Path(s.name), name=s.name,
                                           raw_text=s.goal), logs_by_name[s.name],
                                  suite_dir / s.name)
            link = f"<a href='{escape(s.name)}/{report.name}'>report</a>"
        needs = ", ".join(s.needs) or "—"
        r = usage.get(s.name)
        u = (r.usage or {}) if r is not None else {}
        time = _fmt_time(r.duration_ms if r is not None else None)
        inp = u.get("input_tokens", 0)
        out = u.get("output_tokens", 0)
        cache = u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        rows.append(
            f"<tr><td><span class='b' style='background:{color[v]}'>{v.value}</span></td>"
            f"<td>{escape(s.name)}</td><td>{escape(needs)}</td>"
            f"<td>{link}</td><td class='n'>{time}</td>"
            f"<td class='n'>{inp:,}</td><td class='n'>{out:,}</td>"
            f"<td class='n'>{cache:,}</td></tr>"
        )
    css = ("body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}"
           "h1{margin:0;font-size:20px}header{padding:20px 28px;background:#fff;"
           "border-bottom:1px solid #d0d7de}main{padding:20px 28px}"
           "table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #d0d7de;"
           "border-radius:8px;overflow:hidden}td,th{padding:10px 14px;text-align:left;"
           "border-bottom:1px solid #eaeef2}th{font-size:12px;color:#6e7781;text-transform:uppercase;"
           "letter-spacing:.03em}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}"
           ".b{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;font-weight:600;"
           "font-size:12px}")
    doc = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<title>suite — report</title><style>{css}</style></head><body>"
           f"<header><h1>Suite report</h1></header><main><table>"
           f"<tr><th>Status</th><th>Test</th><th>Depends on</th><th>Link</th>"
           f"<th class='n'>Time</th><th class='n'>Input</th><th class='n'>Output</th>"
           f"<th class='n'>Cache</th></tr>"
           f"{''.join(rows)}</table></main></body></html>")
    path = suite_dir / "index.html"
    path.write_text(doc, encoding="utf-8")
    return path
