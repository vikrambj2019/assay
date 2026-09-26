"""Render unit tests — pure tree → text, no browser, no LLM."""

from __future__ import annotations

from core.snapshot.models import Snapshot, SnapNode
from core.snapshot.render import render


def _snap(nodes, **kw) -> Snapshot:
    return Snapshot(url="http://x", title="T", nodes=nodes, **kw)


def el(role=None, **kw) -> SnapNode:
    return SnapNode(type="element", role=role, **kw)


def txt(text) -> SnapNode:
    return SnapNode(type="text", text=text)


def test_interactive_line_format() -> None:
    node = el(role="button", tag="button", name="Submit", interactive=True, index=9,
              states=["disabled"])
    out = render(_snap([node]))
    assert "[9] <button> Submit (disabled)" in out


def test_textbox_shows_value() -> None:
    node = el(role="textbox", tag="input", name="Email", interactive=True, index=3,
              value="a@b.com")
    out = render(_snap([node]))
    assert "[3] <textbox> Email = a@b.com" in out


def test_field_hint_renders() -> None:
    node = el(role="textbox", tag="input", name="Password", interactive=True, index=4,
              states=["invalid"], hint="Must be at least 8 characters")
    out = render(_snap([node]))
    assert "[4] <textbox> Password (invalid) — Must be at least 8 characters" in out


def test_render_is_always_full() -> None:
    # Nothing is ever collapsed or deduped — the full page always renders.
    links = [el(role="link", tag="a", name=f"L{i}", interactive=True, index=i) for i in range(30)]
    nav = el(role="navigation", tag="nav", landmark="navigation", children=links)
    out = render(_snap([nav]))
    assert "collapsed" not in out and "more rows" not in out
    assert "L0" in out and "L29" in out


def test_duplicate_text_child_is_suppressed() -> None:
    btn = el(role="button", tag="button", name="Submit", interactive=True, index=1,
             children=[txt("Submit")])
    out = render(_snap([btn]))
    assert out.count("Submit") == 1


def test_flattened_named_wrapper_keeps_text() -> None:
    # A <p> whose aria-label/title equals its text renders no line of its own;
    # its text must not be deduped against the never-printed name (that erased
    # document names/emails from real snapshots).
    p = el(role=None, tag="p", name="Seller signature missing",
           children=[txt("Seller signature missing")])
    out = render(_snap([p]))
    assert "Seller signature missing" in out


def test_name_repeated_in_nested_wrappers_is_suppressed() -> None:
    btn = el(role="button", tag="button", name="New Stack", interactive=True, index=2,
             children=[el(role=None, tag="span", children=[txt("New Stack")])])
    out = render(_snap([btn]))
    assert out.count("New Stack") == 1


def test_name_split_across_children_is_suppressed() -> None:
    # accName concatenates textContent, so a label + badge reads "Active2";
    # the per-span pieces are still just the name restated.
    btn = el(role="button", tag="button", name="Active2", interactive=True, index=3,
             children=[el(role=None, tag="span", children=[txt("Active")]),
                       el(role=None, tag="span", children=[txt("2")])])
    out = render(_snap([btn]))
    assert out.count("Active") == 1  # only in the button's own line


def test_child_text_beyond_the_name_is_kept() -> None:
    btn = el(role="button", tag="button", name="Save", interactive=True, index=0,
             children=[txt("Save"), txt("3 unsaved changes")])
    out = render(_snap([btn]))
    assert "3 unsaved changes" in out


def test_interactive_child_is_never_suppressed() -> None:
    inner = el(role="button", tag="button", name="Go", interactive=True, index=1)
    outer = el(role="link", tag="a", name="Go", interactive=True, index=0,
               children=[inner])
    out = render(_snap([outer]))
    assert "[1] <button> Go" in out


def test_structural_wrapper_is_flattened() -> None:
    wrapper = el(role=None, tag="div", children=[
        el(role="button", tag="button", name="Go", interactive=True, index=0),
    ])
    out = render(_snap([wrapper]))
    assert "<div>" not in out
    assert "[0] <button> Go" in out


def test_evidence_summary_no_issues() -> None:
    out = render(_snap([txt("hi")]))
    assert "console: no errors or warnings" in out
    assert "network: no failed requests" in out


def test_evidence_summary_reports_console_and_network() -> None:
    from core.snapshot.models import ConsoleEntry, NetworkFailure
    snap = _snap(
        [txt("hi")],
        console=[ConsoleEntry(type="error", text="Boom")],
        network_failures=[NetworkFailure(method="GET", url="http://x/api", status=500)],
    )
    out = render(snap)
    assert "console: 1 error(s), 0 warning(s)" in out
    assert "error: Boom" in out
    assert "network: 1 failed request(s)" in out
    assert "GET http://x/api → 500" in out
