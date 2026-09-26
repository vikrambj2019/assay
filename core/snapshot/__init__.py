"""Perception layer: capture a rich Snapshot and render it to text."""

from core.snapshot.capture import capture
from core.snapshot.models import Snapshot, SnapNode
from core.snapshot.render import render

__all__ = ["capture", "render", "Snapshot", "SnapNode"]
