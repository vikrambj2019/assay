"""Render a Snapshot to text — a pure function ``tree → text``.

Always full: every element and all text, nothing hidden. The rendered page is
written to a file each turn and the agent reads it with the built-in Read tool,
which paginates large pages itself — so there is no budget or compression here.
  - Elements render as ``[index] <role> name (states) = value — hint``.
"""

from __future__ import annotations

from core.snapshot.models import Snapshot, SnapNode


def render(snapshot: Snapshot) -> str:
    """The full page as text — url/title header, the element tree, then evidence."""
    lines: list[str] = [f"url: {snapshot.url}", f"title: {snapshot.title or '(no title)'}", ""]
    for node in snapshot.nodes:
        _render_node(node, 0, lines)
    lines.append("")
    lines.extend(_render_evidence(snapshot))
    return "\n".join(lines).rstrip() + "\n"


def _indent(depth: int) -> str:
    return "  " * depth


def _render_node(node: SnapNode, depth: int, lines: list[str]) -> None:
    if node.type == "text":
        if node.text:
            lines.append(f"{_indent(depth)}{node.text}")
        return

    displays = _displays(node)
    if displays:
        lines.append(f"{_indent(depth)}{_line_for(node)}")

    # Drop child subtrees that merely repeat the parent's accessible name
    # (e.g. `[9] <button> Submit` should not be followed by a bare `Submit`,
    # whether the text sits directly under the button or inside nested spans).
    # Only when the parent printed its name: a flattened wrapper (e.g. a <p> whose
    # aria-label/title equals its text) never did, so its text is not a repeat —
    # dropping it would erase the text entirely.
    name_norm = (node.name or "").strip().lower() if displays else ""
    child_depth = depth + 1 if displays else depth
    for c in node.children:
        if name_norm and _repeats_name(c, name_norm):
            continue
        _render_node(c, child_depth, lines)


def _repeats_name(node: SnapNode, name_norm: str) -> bool:
    """True if this subtree would render only text already contained in the
    parent's accessible name. Names of buttons/links/headings come from
    textContent, so their descendants' text restates the name piece by piece
    (`<button> Active2` over spans `Active` and `2`). A subtree that would
    render any line of its own (interactive, landmark, states…) is never dropped.
    """
    pieces: list[str] = []
    if not _collect_text_only(node, pieces):
        return False
    return bool(pieces) and all(p in name_norm for p in pieces)


def _collect_text_only(node: SnapNode, out: list[str]) -> bool:
    """Gather the subtree's text into ``out``; False if any node would render a line."""
    if node.type == "text":
        t = (node.text or "").strip().lower()
        if t:
            out.append(t)
        return True
    if _displays(node):
        return False
    return all(_collect_text_only(c, out) for c in node.children)


_DISPLAY_ROLES = frozenset({
    "heading", "img", "alert", "table", "row", "cell", "columnheader",
    "list", "listitem", "article", "dialog", "region", "form", "iframe",
    # a <select> renders as a non-actionable combobox/listbox line that groups
    # its option children; disabled options carry no index but still render
    "combobox", "listbox", "option",
})


def _displays(node: SnapNode) -> bool:
    """Does this element get a line of its own, or is it a wrapper to flatten?"""
    return (
        node.interactive
        or node.landmark is not None
        or node.scrollable
        or node.role in _DISPLAY_ROLES
    )


def _line_for(node: SnapNode) -> str:
    """Format the line for a node that ``_displays``."""
    role = node.role or node.tag or "node"
    parts: list[str] = []
    if node.index is not None:          # [N] = actionable index
        parts.append(f"[{node.index}]")
    parts.append(f"<{role}>")           # <role> = the element's tag/role
    if node.name:                        # everything after = actual text/content
        parts.append(node.name)
    if node.states:
        parts.append(f"({', '.join(node.states)})")
    if node.value:
        parts.append(f"= {node.value}")
    if node.hint:                        # help / error / format text tied to the field
        parts.append(f"— {node.hint}")
    if node.scrollable:
        parts.append("[scroll]")
    if node.note:
        parts.append(f"<!-- {node.note} -->")
    return " ".join(parts)


def _render_evidence(snapshot: Snapshot) -> list[str]:
    lines: list[str] = []

    if not snapshot.console:
        lines.append("console: no errors or warnings")
    else:
        errs = sum(1 for c in snapshot.console if c.type == "error")
        warns = sum(1 for c in snapshot.console if c.type == "warning")
        lines.append(f"console: {errs} error(s), {warns} warning(s)")
        for c in snapshot.console[:5]:
            lines.append(f"  - {c.type}: {c.text[:200]}")

    if not snapshot.network_failures:
        lines.append("network: no failed requests")
    else:
        lines.append(f"network: {len(snapshot.network_failures)} failed request(s)")
        for f in snapshot.network_failures[:5]:
            status = f.status if f.status is not None else f.failure or "failed"
            lines.append(f"  - {f.method} {f.url[:120]} → {status}")

    return lines
