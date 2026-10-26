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

import re
from typing import Any, NamedTuple

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


class TopoRef(NamedTuple):
    """A resolved topology cell: the reference, and CATIA's name for it."""

    reference: Any
    name: str


# A topology search on a body returns construction geometry as well as the
# solid's own topology. Measured on V5-6R2020 B30, a part whose final shape has
# 16 real faces exposes 30 "faces" in the search: the rest are the three datum
# planes and the sketch wires. They have no length and cannot be filleted or
# measured, but they occupied the low indices - so ``Edge.1`` was a datum-plane
# border, not an edge of the model, and every length-based selection built on
# those indices was reading construction geometry.
_CONSTRUCTION_PREFIXES = (
    "Selection_BorderREdge",    # datum-plane border
    "Selection_WireREdge",      # sketch wire
    "Selection_BorderRSur",     # datum-plane face
    "Selection_WireFVertex",    # sketch wire corner
    "Selection_BorderRVertex",  # datum-plane corner
)

# The word CATIA puts in front of a cell's type name is the whole difference
# between construction geometry and the solid: a vertex of the solid is
# ``Selection_FVertex``, a corner of a sketch wire is ``Selection_WireFVertex``.
# The list above only ever covered edges and faces, so *every* vertex passed the
# test and a deleted-but-still-referenced sketch stretched a bounding box. That
# was invisible in the single-part box (the polluted points happened to sit
# inside the solid) and only showed up per component in an assembly: a
# 40 x 20 x 10 pad measured 60 x 80 x 10 (reported 2026-10-25, reproduced here
# 2026-10-25 - 20 vertices found, 4 of them ``Selection_WireFVertex``).
#
# Matching the marker anywhere in the leading type token, rather than listing
# every name CATIA can invent, also covers the variants nobody has met yet
# (``Selection_WireRVertex``, ``Selection_BorderRFVertex``...). It is checked
# against the head only - the full NLS path mentions ``Brp:(Sketch.1;...)``
# everywhere and nothing in it may be read as a marker.
_CONSTRUCTION_MARKERS = ("Wire", "Border")


def nls_of(cell: Any) -> str:
    """CATIA's NLS path for a selected topology cell ('' when unavailable).

    The path names the feature that produced the element: a real edge reads
    ``Selection_REdge:(Edge:(Face:(Brp:(Pad.5;0:...``, so it tells you *which
    feature* an edge belongs to - the one thing length and type cannot.
    """
    try:
        return str(cell.Value.Name)
    except Exception:  # noqa: BLE001 - purely diagnostic
        return ""


def construction_reason(nls: str) -> str | None:
    """Why the NLS path says this cell is not the solid, or None when it is.

    The reason, not just the verdict: a count that excludes 19 of 20 cells and one
    that excludes 8 are very different answers, and the reply used to give the
    total only (reported 2026-10-26, which had to infer the breakdown itself).
    """
    if not nls:
        # No path, no verdict: a cell CATIA will not name is still measured, and
        # guessing "construction" here would silently shrink the model.
        return None
    if nls.startswith("Selection_BorderREdge"):
        return "datum_plane_border_edge"
    if nls.startswith("Selection_WireREdge"):
        return "sketch_wire_edge"
    if nls.startswith("Selection_BorderRSur"):
        return "datum_plane_border_face"
    if nls.startswith("Selection_WireFVertex"):
        return "sketch_wire_vertex"
    if nls.startswith("Selection_BorderRVertex"):
        return "datum_plane_corner"
    head = nls.split(":", 1)[0]
    if any(marker in head for marker in _CONSTRUCTION_MARKERS):
        return "construction_" + head.replace("Selection_", "").lower()
    if "Brp:(GSMPlane." in nls:
        return "datum_plane_border"
    return None


def is_construction(nls: str) -> bool:
    """True when the NLS path describes geometry that is not the solid."""
    return construction_reason(nls) is not None


def nls_features(nls: str) -> list[str]:
    """Feature names mentioned in an NLS path, in order of appearance.

    ``['EdgeFillet.3', 'CloseSurface.2', 'GSMLoft.2', 'Sketch.6', ...]`` - enough
    to answer "was this edge made by the pad or by the fillet I just added".
    """
    import re

    seen: list[str] = []
    for name in re.findall(r"[A-Za-z][A-Za-z0-9_]*\.\d+", nls):
        if name not in seen:
            seen.append(name)
    return seen


# A topology cell's NLS names the faces it is built from, as
# ``Brp:(Pad.6;2)`` - feature name plus face index. Those pairs are the only
# handle that survives renumbering, because they belong to the *feature*, not to
# the enumeration order. Measured 2026-10-23 by the reporter: after one fillet
# the same edge moved from Edge.30 to Edge.34 and its length changed from
# 17.6112 to 9.8429 mm, while the pair of faces that bound it did not change.
_BRP = re.compile(r"Brp:\(([A-Za-z][A-Za-z0-9_]*\.\d+);(\d+)\)")


def face_keys(nls: str) -> list[str]:
    """``['Pad.6;2', 'Sketch.7;2']`` - the faces a cell is bounded by."""
    seen: list[str] = []
    for feature, index in _BRP.findall(nls or ""):
        key = f"{feature};{index}"
        if key not in seen:
            seen.append(key)
    return seen


def edge_fingerprint(faces: list[str], length: float | None) -> str:
    """A paste-back-able description of one edge: ``L17.6112|Pad.6;2|Sketch.7;2``.

    This is what ``catia_fillet`` accepts in place of ``Edge.N``. The index is
    an enumeration position and drifts after every feature; the bounding faces
    and the length do not.
    """
    stamp = "?" if length is None else "%.4f" % length
    return "L" + stamp + "|" + "|".join(faces)


def topology_cells(conn: Any, body: Any, kind: str,
                   stats: dict[str, Any] | None = None) -> list[Any]:
    """Every *solid* cell of ``kind`` on the body's final shape, in CATIA order.

    Construction geometry is filtered out so that ``<kind>.N`` means the same
    thing to ``catia_list_edges`` / ``catia_list_faces`` and to the tools that
    resolve ``Edge.N`` / ``Face.N`` afterwards. The search itself is left alone
    (it is the only way to enumerate), and the selection is kept alive because
    a Reference belongs to the selection that produced it.

    ``stats`` is filled with ``raw``, ``retried`` and the ``excluded`` breakdown.
    Two things make it worth carrying:

    * a transient state can make nearly every solid cell look like construction -
      reported 2026-10-26 on B32, where the same untouched part answered
      ``edges 1`` and then ``edges 12``. One enumeration decides the numbering of
      every ``Edge.N`` the caller is about to use, so a suspiciously small result
      is enumerated a second time rather than published;
    * ``Selection.Item`` occasionally fails while CATIA has a modal window up
      (also 2026-10-26). That is not "the model has no edges", so it is retried
      once and then reported as what it is.
    """
    cells, reasons, raw, retried = _enumerate_cells(conn, body, kind)
    if len(cells) <= 1 and raw >= 8:
        # 1 of 20 is not a model, it is an enumeration that went wrong.
        again_cells, again_reasons, again_raw, _ = _enumerate_cells(conn, body, kind)
        if len(again_cells) > len(cells):
            cells, reasons, raw = again_cells, again_reasons, again_raw
            retried = True
    if stats is not None:
        breakdown: dict[str, int] = {}
        for reason in reasons:
            if reason:
                breakdown[reason] = breakdown.get(reason, 0) + 1
        stats.update(raw=raw, retried=retried, excluded=breakdown,
                     kept=len(cells))
    return cells


def _enumerate_cells(conn: Any, body: Any, kind: str
                     ) -> tuple[list[Any], list[str | None], int, bool]:
    """One search + filter pass, retried once if ``Selection.Item`` fails."""
    for attempt in (1, 2):
        sel = conn.hso
        sel.Clear()
        sel.Add(body)
        search_topology(sel, kind)
        cells: list[Any] = []
        reasons: list[str | None] = []
        try:
            for i in range(1, sel.Count + 1):
                cell = sel.Item(i)
                reason = construction_reason(nls_of(cell))
                reasons.append(reason)
                if reason is None:
                    cells.append(cell)
        except Exception as error:  # noqa: BLE001 - reported as a busy selection
            if attempt == 1:
                try:
                    sel.Clear()
                except Exception:  # noqa: BLE001
                    pass
                continue
            raise RuntimeError(
                f"CATIA refused to hand over {kind} number "
                f"{len(reasons) + 1} of {sel.Count} ({error}). That is the "
                "SELECTION being unavailable, not the model having no "
                f"{kind.lower()}s: a modal dialog (often a '闭合' prompt raised "
                "while a sketch is still in edition) can hold it. Dismiss the "
                "dialog, or catia_activate_document the part again, and repeat "
                "the call - the geometry is untouched."
            ) from error
        return cells, reasons, sel.Count, False
    raise RuntimeError("unreachable")


def topology_reference(conn: Any, body: Any, kind: str, index: int,
                       hint: str | None = None, keep: bool = True) -> TopoRef:
    """Resolve ``<kind>.<index>`` on the body's final shape to a Reference.

    References come from the selection's ``Reference`` property because
    ``CreateReferenceFromObject`` rejects HSO-resolved topology cells
    (field-verified E_INVALIDARG). Indices match ``catia_list_faces`` /
    ``catia_list_edges``.

    The body is searched rather than ``Shapes.Item(last)``: only the body
    resolves to the final solid's topology - a shape reference yields the
    region that feature itself created.

    ``keep`` leaves the selection holding the element. That is not an
    optimisation, it is required: the Reference returned by
    ``Selection.Item(i).Reference`` belongs to the selection and is invalidated
    the moment the selection is cleared. A factory call given a cleared
    reference fails with "method failed" and no explanation - which is what
    made every fillet, chamfer, shell and draft look like a bad edge on
    V5-6R2020 B30. Callers that build a feature must therefore leave the
    selection alone until the feature exists, then clear it themselves; pass
    ``keep=False`` only when the reference is used for reading.
    """
    if body.Shapes.Count == 0:
        raise RuntimeError("The active body has no solid shape yet.")
    sel = conn.hso
    cells = topology_cells(conn, body, kind)
    count = len(cells)
    if index < 1 or index > count:
        where = hint or (
            "Use catia_list_faces / catia_list_edges to enumerate valid indices."
        )
        try:
            sel.Clear()
        except Exception:
            pass
        raise RuntimeError(
            f"{kind}.{index} is out of range: the final shape exposes "
            f"{count} {kind.lower()}(s). {where}"
        )
    cell = cells[index - 1]
    ref = TopoRef(cell.Reference, str(cell.Name))
    if not keep:
        try:
            sel.Clear()
        except Exception:
            pass
    return ref


def topology_references(conn: Any, body: Any, kind: str,
                        indices: list[int]) -> list[TopoRef]:
    """Resolve several cells of the same kind in one search.

    One search, not one per index: each ``topology_reference`` call starts with
    ``sel.Clear()``, so a second call would invalidate the first reference.
    """
    if not indices:
        return []
    if body.Shapes.Count == 0:
        raise RuntimeError("The active body has no solid shape yet.")
    cells = topology_cells(conn, body, kind)
    out = []
    for index in indices:
        if index < 1 or index > len(cells):
            raise RuntimeError(
                f"{kind}.{index} is out of range: the final shape exposes "
                f"{len(cells)} {kind.lower()}(s)."
            )
        cell = cells[index - 1]
        out.append(TopoRef(cell.Reference, str(cell.Name)))
    return out


