"""Topology selection search with localized-type-name fallback.

CATIA's ``Selection.Search`` query language uses *localized* type names on
non-English installations. On a Simplified Chinese build (verified on
V5-6R2020 B30) the English type names raise E_FAIL, while the localized
ones resolve:

    English              Simplified Chinese
    --------             -----------------
    Topology.Edge        拓扑.边线
    Topology.Face        拓扑.面
    Topology.Vertex      拓扑.顶点

Hardcoded English names therefore break edge/face enumeration (and every
tool built on it: fillet, chamfer, measure, drawing views) on such
machines. ``search_topology`` tries the English name first (works on
English builds) and falls back to the localized name, so the same code
runs on both.
"""

from __future__ import annotations

from typing import Any

# Verified localized type names (Simplified Chinese CATIA V5).
_LOCALIZED_TYPE_NAMES: dict[str, str] = {
    "Edge": "拓扑.边线",
    "Face": "拓扑.面",
    "Vertex": "拓扑.顶点",
    "Cell": "拓扑.单元",
    "Line": "拓扑.线",
    "Plane": "拓扑.平面",
    "Point": "拓扑.点",
    "Curve": "拓扑.曲线",
    "Surface": "拓扑.曲面",
}


def search_topology(sel: Any, kind: str) -> None:
    """Run ``sel.Search("<Type>,sel")`` for the given topology kind.

    Tries the English type name first; on failure falls back to the
    localized name. Raises the English attempt's error when no localized
    name is known.
    """
    try:
        sel.Search(f"Topology.{kind},sel")
        return
    except Exception:
        pass
    localized = _LOCALIZED_TYPE_NAMES.get(kind)
    if localized is None:
        # Re-raise the original (English) error for a clear message.
        sel.Search(f"Topology.{kind},sel")
        return
    sel.Search(f"{localized},sel")
