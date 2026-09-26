"""Snapshot data model — the perception layer's output (§6).

A Snapshot carries both "what can I do" (interactive nodes with an ``index``) and
"what is true" (roles, names, states, visible text). The ``index → locator`` map
is not stored explicitly: each interactive element was tagged with
``data-assay-index`` during capture, so Actions (core/actions.py) resolve an index
to a live Playwright locator on demand. Indices appear in the rendered text;
selectors never do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SnapNode:
    type: str  # "element" | "text"
    tag: str | None = None
    role: str | None = None
    name: str | None = None
    value: str | None = None
    text: str | None = None
    interactive: bool = False
    index: int | None = None
    states: list[str] = field(default_factory=list)
    landmark: str | None = None
    scrollable: bool = False
    note: str | None = None
    hint: str | None = None  # help/error/format text tied to a field (aria-describedby, etc.)
    children: list["SnapNode"] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "SnapNode":
        return cls(
            type=d["type"],
            tag=d.get("tag"),
            role=d.get("role"),
            name=d.get("name") or None,
            value=d.get("value") or None,
            text=d.get("text"),
            interactive=bool(d.get("interactive", False)),
            index=d.get("index"),
            states=list(d.get("states") or []),
            landmark=d.get("landmark"),
            scrollable=bool(d.get("scrollable", False)),
            note=d.get("note"),
            hint=d.get("hint") or None,
            children=[cls.from_json(c) for c in d.get("children", [])],
        )

    def walk(self):
        """Yield this node and all descendants, depth-first (reading order)."""
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class ConsoleEntry:
    type: str
    text: str


@dataclass
class NetworkFailure:
    method: str
    url: str
    status: int | None
    failure: str | None = None
    third_party: bool = False


@dataclass
class Snapshot:
    url: str
    title: str
    nodes: list[SnapNode]
    interactive_count: int = 0
    console: list[ConsoleEntry] = field(default_factory=list)
    network_failures: list[NetworkFailure] = field(default_factory=list)

    def _iter(self):
        for root in self.nodes:
            yield from root.walk()
