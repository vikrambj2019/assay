"""Evidence reports — terminal print + self-contained HTML, one file per run.

Screenshots are inlined as base64 so the report is a single portable file, no
server. Pure function of (test, logs, out_dir); the agent loop already wrote
the per-step screenshots to out_dir/step-<index>.png.
"""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from core.schema import Verdict, overall
from core.redact import make_redactor

if TYPE_CHECKING:
    from core.schema import StepLog, TestFile

_COLOR = {
    Verdict.PASS: "#1a7f37",
    Verdict.FAIL: "#cf222e",
    Verdict.ERROR: "#bf8700",
    Verdict.BLOCKED: "#6e7781",
    Verdict.SKIPPED: "#6e7781",
    Verdict.UNVERIFIED: "#9a6700",
}

_CSS = """
body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}
header{padding:20px 28px;background:#fff;border-bottom:1px solid #d0d7de}
h1{margin:0 0 8px;font-size:20px}
.source{color:#6e7781;font-size:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;color:#fff;font-weight:600;font-size:12px}
main{padding:20px 28px;max-width:900px}
.step{background:#fff;border:1px solid #d0d7de;border-radius:8px;margin:0 0 16px;overflow:hidden}
.head{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid #eaeef2}
.head .id{font-weight:600}
.head .meta{margin-left:auto;color:#6e7781;font-size:12px}
.body{padding:12px 16px}
.reason{margin:0 0 10px}
.actions{margin:0 0 10px;padding-left:18px;color:#57606a;font-size:13px;font-family:ui-monospace,monospace}
.evidence{margin:0 0 10px;padding-left:18px;color:#57606a;font-size:13px}
figure{margin:0 0 14px}
figcaption{color:#1f2328;font-size:13px;margin:0 0 5px}
figcaption .n{color:#6e7781;font-weight:600;margin-right:6px;font-family:ui-monospace,monospace}
img{max-width:100%;border:1px solid #d0d7de;border-radius:6px;display:block}
"""


def write_report(test: "TestFile", logs: list["StepLog"], out_dir: Path) -> Path:
    headline = overall(logs)
    counts = " ".join(
        _badge(v, sum(1 for l in logs if l.verdict is v))
        for v in Verdict
        if any(l.verdict is v for l in logs)
    )
    cards = "\n".join(_card(l) for l in logs)
    body = (
        f"<header><h1>{escape(test.name)}</h1>"
        f"{_badge(headline, None)} {counts}"
        f"<div class='source'>{escape(str(test.path))}</div></header>"
        f"<main>{cards}</main>"
    )
    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{escape(test.name)} — report</title><style>{_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
    path = out_dir / "report.html"
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(make_redactor().scrub(doc), encoding="utf-8")
    return path


def _img(path: Path, caption: str, file: str = "") -> str:
    if not path.exists():
        return ""
    b64 = base64.b64encode(path.read_bytes()).decode()
    # The screenshot's own step id (e.g. "step-01") leads the caption; no separate
    # serial number since the id already carries the step's position.
    label = f"<span class='n'>{escape(file)}:</span>" if file else ""
    return (f"<figure><figcaption>{label}{escape(caption)}</figcaption>"
            f"<img src='data:image/png;base64,{b64}' alt='{escape(caption)}'></figure>")


def _figures(l: "StepLog") -> str:
    """One entry per ActionRecord: a figure when the screenshot exists, otherwise
    a plain-text action line.

    Each ActionRecord carries its own detail and screenshot, so captions are
    tied to the specific action that produced them — not to parallel-list
    position. A None screenshot (failed capture) renders as a text entry and
    never shifts the captions of later records.
    """
    out: list[str] = []
    for rec in l.action_records:
        if rec.screenshot is not None:
            out.append(_img(rec.screenshot, rec.detail, file=rec.screenshot.stem))
        else:
            out.append(f"<p class='actions'>{escape(rec.detail)}</p>")
    return "".join(out)


def _badge(verdict: Verdict, count: int | None) -> str:
    label = verdict.value if count is None else f"{verdict.value} {count}"
    return f"<span class='badge' style='background:{_COLOR[verdict]}'>{label}</span>"


def _card(l: "StepLog") -> str:
    meta = f"{l.duration_ms}ms" if l.duration_ms is not None else ""
    evidence_html = (
        "<ul class='evidence'>" + "".join(f"<li>{escape(e)}</li>" for e in l.evidence) + "</ul>"
        if l.evidence else ""
    )
    return (
        f"<section class='step'>"
        f"<div class='head'>{_badge(l.verdict, None)}"
        f"<span class='id'>Step {l.index}: {escape(l.text)}</span>"
        f"<span class='meta'>{escape(meta)}</span></div>"
        f"<div class='body'><p class='reason'>{escape(l.reason or '')}</p>"
        f"{evidence_html}{_figures(l)}</div></section>"
    )
