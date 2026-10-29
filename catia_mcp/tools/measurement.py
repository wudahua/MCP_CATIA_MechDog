"""Measurement and analysis tools for CATIA V5.

Distance, angle, inertia, bounding box, and part property queries.
"""

from __future__ import annotations

import json
import os
import re
import struct
import tempfile
import time
import uuid
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.regime import (
    BOOK_MASS,
    BOOK_TOPOLOGY,
    MASS_PROPERTIES,
    MESH_EXPORT,
    TOPOLOGY,
    SessionBooks,
)
from catia_mcp.results import CodedError
from catia_mcp.topo_search import is_construction, nls_of, search_topology


def _stl_scan(path: str, limit: int | None = None
              ) -> tuple[list[float], list[float], list[float] | None, int]:
    """Extent - and optionally the cloud - of an STL (binary or ASCII).

    Streams the file and tracks extremes, because a dense part exports hundreds
    of thousands of triangles. ``limit`` also keeps up to that many vertices as a
    flat ``[x, y, z, x, y, z, …]`` list: the assembly route needs the points, not
    the extent, because a box taken from two opposite corners and then rotated is
    NOT the box of the rotated part (see ``_mesh_cloud``).
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    cloud: list[float] | None = [] if limit is not None else None
    seen = 0

    def add(x: float, y: float, z: float) -> None:
        nonlocal seen
        if x < lo[0]:
            lo[0] = x
        if y < lo[1]:
            lo[1] = y
        if z < lo[2]:
            lo[2] = z
        if x > hi[0]:
            hi[0] = x
        if y > hi[1]:
            hi[1] = y
        if z > hi[2]:
            hi[2] = z
        seen += 1
        if cloud is not None and len(cloud) < 3 * limit:
            cloud.extend((x, y, z))

    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(84)
        if len(head) < 84:
            raise RuntimeError(f"STL export produced only {size} bytes")
        triangles = struct.unpack("<I", head[80:84])[0]
        if triangles and size == 84 + 50 * triangles:
            for _ in range(triangles):
                record = handle.read(50)
                if len(record) < 50:
                    break
                values = struct.unpack("<12fH", record)
                for k in (3, 6, 9):
                    add(values[k], values[k + 1], values[k + 2])
        else:
            # ASCII STL: "vertex x y z" lines.
            handle.seek(0)
            for raw in handle:
                text = raw.strip()
                if text[:6].lower() == b"vertex":
                    parts = text.split()
                    if len(parts) >= 4:
                        add(float(parts[1]), float(parts[2]), float(parts[3]))
    if not seen:
        raise RuntimeError("STL export contained no triangles")
    return lo, hi, cloud, seen


def _stl_extent(path: str) -> tuple[list[list[float]], int]:
    """Min/max corner and vertex count from an STL (binary or ASCII)."""
    lo, hi, _cloud, seen = _stl_scan(path)
    return [lo, hi], seen

# CATIA V5's SPAWorkbench Measurable API returns every value in MKS units
# (meters, m2, m3, kg.m2) regardless of the document's display units.
# Feature creation APIs, by contrast, take millimeters. All measurement
# results are therefore converted to mm-based units here, at the server
# boundary, so agents always see consistent mm/mm2/mm3 values.
_M_TO_MM = 1000.0
_M2_TO_MM2 = 1e6
_M3_TO_MM3 = 1e9

# Said once, in both part routes, whenever a mass was computed from a density the
# caller passed: the number is density x volume, and it is NOT the tensor's mass
# when the part carries a material. Measured 2026-10-24 on an 8000 mm3 block.
_MASS_NOTE = (
    "mass_kg = the density you passed x volume; mass_from_material_kg = the mass "
    "CATIA gets from the part's own material, and that is also the density the "
    "inertia tensor is computed with - so when the two masses differ, the tensor "
    "and mass_kg differ by the same ratio (measured 2026-10-24: a 8000 mm3 block "
    "reported 0.0628 kg for density 7850, 0.008 kg from the material, and Ixx "
    "3.33e-7 = 0.008/12*(0.02^2+0.01^2))."
)

# The two buckets a PART measurement publishes, in the same words on both part
# routes (the active document and `document=`). The assembly route has had them
# since 0.3.34; on a part they answer the one question `elapsed_ms` cannot: two
# parts of the same tool differed by 12x (174.3 ms against 2 117.3 ms) and the
# reply said nothing about where it went (reported 2026-10-28, §6.2).
_PART_COST_NOTE = (
    "the same two buckets the assembly route publishes, for one part: 'parts_ms' "
    "is this part's own measurement and 'overhead_ms' is everything else, which on "
    "this route is finding the document you named and building the reply - "
    "parts_ms + overhead_ms = elapsed_ms. Inside parts_ms there are three terms "
    "that add up exactly: 'measure_ms' reading volume / area / centroid, "
    "'tensor_ms' borrowing the activation for the inertia tensor (including the "
    "work of saying WHY it is missing, which walks the open assemblies), and "
    "'parts_other_ms', the remainder - fetching the main body, its shape count, "
    "the density arithmetic. 'tensor_ms' is the field that explains a slow part: a "
    "tensor that cannot be read still pays for the activation attempt")

# How many STL vertices a part's cloud may hold while an assembly is being
# measured. The cloud is kept per PART and transformed once per INSTANCE, so the
# memory is bounded by this and the time by the instance count. Typical parts in
# the acceptance assemblies are 500-5 400 vertices; 200 000 is far above that and
# still only 4.8 MB per part as a flat list of floats.
_MESH_CLOUD_LIMIT = 200_000

# Mesh boxes measured from the SAVED file, keyed by that file's identity. The copy
# route (the only one used for an in-context part) reads the file, so an unchanged
# file gives the same box - and the route costs 12-17 s per part on B30 (measured
# 2026-10-26). Only this route is cached: an in-place export may include unsaved
# edits, which no file identity can describe.
_MESH_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_MESH_CACHE_LIMIT = 48
# A cloud bigger than this is not kept (it would be 1.2 MB+ each); the extent still
# is, so an extent-only caller keeps hitting the cache.
_MESH_CACHE_CLOUD_LIMIT = 50_000

# What this PROCESS has read, per JOB. Session state - CATIA's own caches are
# session state too, so two tool objects talking to one CATIA share it (a second
# book would report "first time" about a part this server had already read).
#
# It used to be `_MEASURED_PARTS` (a bare set) plus five attributes on
# `MeasurementTools`, and the cold/warm fields built from them were written out
# THREE times and missing from four branches. There is one book now
# (`catia_mcp.regime.SessionBooks`) and one function that turns it into fields.
_BOOKS = SessionBooks()


def reset_session_books() -> None:
    """Forget every first-reading and every warm time. Tests call this."""
    _BOOKS.reset()


def _file_identity(path: str) -> tuple[str, int, int] | None:
    """``(path, size, mtime_ns)`` - what makes a saved file's box reproducible."""
    try:
        stat = os.stat(path)
    except OSError:  # deleted, moved, or unreadable: do not cache
        return None
    return (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns)


def _mesh_cache_get(identity: tuple[str, int, int], want_cloud: bool
                    ) -> tuple[list[float], list[float], list[float] | None,
                               int] | None:
    entry = _MESH_CACHE.get(identity)
    if entry is None:
        return None
    if not want_cloud:
        return list(entry["lo"]), list(entry["hi"]), None, entry["seen"]
    cloud = entry.get("cloud")
    if cloud is None:
        # The stored entry has no cloud (it was too big to keep). Do NOT fake one
        # from the two corners: that is the diagonal bug of 0.3.23.
        return None
    return list(entry["lo"]), list(entry["hi"]), cloud, entry["seen"]


def _mesh_cache_put(identity: tuple[str, int, int],
                    result: tuple[list[float], list[float], list[float] | None, int],
                    want_cloud: bool) -> None:
    lo, hi, cloud, seen = result
    entry = _MESH_CACHE.get(identity)
    if entry is None:
        if len(_MESH_CACHE) >= _MESH_CACHE_LIMIT:
            _MESH_CACHE.pop(next(iter(_MESH_CACHE)))
        entry = _MESH_CACHE[identity] = {"lo": list(lo), "hi": list(hi),
                                         "seen": seen, "cloud": None}
    else:
        entry["lo"], entry["hi"], entry["seen"] = list(lo), list(hi), seen
    if want_cloud and cloud is not None and len(cloud) // 3 == seen \
            and seen <= _MESH_CACHE_CLOUD_LIMIT:
        entry["cloud"] = cloud


def _sig(value: float, digits: int = 6) -> float:
    """Round to significant digits, not decimal places.

    Quantities that span many orders of magnitude - inertia in kg.m2 goes from
    1e-9 (a 2 g screw) to 1e2 (a jig) - cannot be rounded to a fixed number of
    decimals without either losing the small ones or padding the large ones.
    """
    try:
        return float(f"{float(value):.{digits}g}")
    except (TypeError, ValueError):
        return 0.0


def read_measurable_array(app: Any, measurable: Any, method: str, count: int) -> list[float]:
    """Run `measurable.<method> a` inside CATIA via SystemService.Evaluate and
    return the filled array (field-proven on V5R20 for GetCOG/GetPlane).

    Coordinates returned this way (GetCOG, GetPoint, GetPlane) are observed in
    the document's length unit (mm), unlike Area/Volume which are MKS.
    """
    code = (
        "Public Function catia_mcp_out(measurable)\n"
        f"    Dim a({count - 1})\n"
        f"    measurable.{method} a\n"
        "    catia_mcp_out = a\n"
        "End Function\n"
    )
    result = app.SystemService.Evaluate(code, 0, "catia_mcp_out", [measurable])
    values = [float(v) for v in result]
    if len(values) != count:
        raise RuntimeError(
            f"{method} returned {len(values)} values, expected {count}"
        )
    return values


_MIN_DISTANCE_POINTS = (
    # GetMinimumDistance returns a distance and nothing else, and
    # GetMinimumDistancePoints needs an OUT array, which pywin32's late-bound
    # call cannot supply (it answers "type mismatch" / drops the result). The
    # CATScript engine can, so the pair of closest points is read there.
    "Public Function catia_mcp_minpoints(measurable, other)\n"
    "    Dim a(5)\n"
    "    measurable.GetMinimumDistancePoints other, a\n"
    "    catia_mcp_minpoints = a\n"
    "End Function\n"
)


def read_min_distance_points(app: Any, measurable: Any,
                             other_ref: Any) -> tuple[list[float], list[float]]:
    """Closest point on the measured object, and the matching point on ``other``.

    ``other_ref`` is a REFERENCE (a point object), not a Measurable: passing a
    Measurable raises "type mismatch" - measured on B30 2026-10-25.
    """
    result = app.SystemService.Evaluate(_MIN_DISTANCE_POINTS, 0,
                                        "catia_mcp_minpoints",
                                        [measurable, other_ref])
    values = [float(v) for v in result]
    if len(values) != 6:
        raise RuntimeError(
            f"GetMinimumDistancePoints returned {len(values)} values, expected 6"
        )
    return values[:3], values[3:]


_BATCH_POINTS = (
    # Reading one point per SystemService.Evaluate call costs about 75 ms per
    # vertex, because each call compiles a fresh CATScript. Measured on B30
    # 2026-10-26: 8 vertices took 605 ms one at a time and 98 ms through this
    # function (6.2x), with identical coordinates - and the assembly bounding box
    # (2 049 vertices over 26 parts) spent 149 s of its 304 s in that loop.
    "Public Function catia_mcp_points(spa, refs)\n"
    "    Dim i, n, a(2)\n"
    "    n = UBound(refs) + 1\n"
    "    Dim out()\n"
    "    ReDim out(3 * n - 1)\n"
    "    For i = 0 To n - 1\n"
    "        spa.GetMeasurable(refs(i)).GetPoint a\n"
    "        out(3 * i) = a(0)\n"
    "        out(3 * i + 1) = a(1)\n"
    "        out(3 * i + 2) = a(2)\n"
    "    Next\n"
    "    catia_mcp_points = out\n"
    "End Function\n"
)


_BATCH_SWEEP = (
    # The whole sweep in ONE script call: enumerate the selection, drop the
    # construction cells by their NLS name, and read each survivor's coordinates.
    #
    # Why it exists: the per-cell route costs about 9.4 ms per topology cell warm
    # (26 parts, 3 349 cells, 31.5 s of a 43 s call) and 16-90 ms cold, because
    # every `sel.Item(i)`, `.Value.Name` and `.Reference` is a COM round trip from
    # Python. The same statements inside CATIA cost a fraction of that, so a
    # 70-instance assembly pays Python's overhead 3 349 times for no reason
    # (reported 2026-10-27: "cold 4.65x only annotated, not fixed").
    #
    # out(0) = cells returned by the search, out(1) = cells kept, out(2) =
    # characters of NLS name read (the size indicator), then 3 floats per kept
    # cell. The filter mirrors topo_search.is_construction: a leading type token
    # containing Wire/Border is construction, and so is a datum-plane border.
    "Public Function catia_mcp_sweep(sel, spa, limit)\n"
    "    Dim i, n, a(2), out(), kept, chars, name, head, skip\n"
    "    n = sel.Count\n"
    "    ReDim out(4 + 3 * n)\n"
    "    kept = 0\n"
    "    chars = 0\n"
    "    On Error Resume Next\n"
    "    For i = 1 To n\n"
    "        name = sel.Item(i).Value.Name\n"
    "        chars = chars + Len(name)\n"
    "        head = name\n"
    "        If InStr(name, \":\") > 0 Then head = Left(name, InStr(name, \":\") - 1)\n"
    "        skip = False\n"
    "        If InStr(head, \"Wire\") > 0 Then skip = True\n"
    "        If InStr(head, \"Border\") > 0 Then skip = True\n"
    "        If InStr(name, \"Brp:(GSMPlane.\") > 0 Then skip = True\n"
    "        If limit > 0 And kept >= limit Then skip = True\n"
    "        If Not skip Then\n"
    "            spa.GetMeasurable(sel.Item(i).Reference).GetPoint a\n"
    "            out(4 + 3 * kept) = a(0)\n"
    "            out(4 + 3 * kept + 1) = a(1)\n"
    "            out(4 + 3 * kept + 2) = a(2)\n"
    "            kept = kept + 1\n"
    "        End If\n"
    "    Next\n"
    "    out(0) = n\n"
    "    out(1) = kept\n"
    "    out(2) = chars\n"
    "    out(3) = 0\n"
    "    catia_mcp_sweep = out\n"
    "End Function\n"
)


def batch_vertex_sweep(app: Any, sel: Any, spa: Any,
                       stats: dict[str, Any] | None = None,
                       limit: int = 0
                       ) -> tuple[list[list[float]], int, int] | None:
    """``(points, cells, nls_chars)`` for a selection, in one script call.

    Returns None when the script route is refused, so the caller keeps its own
    per-cell reader: the numbers a caller acts on must not depend on whether this
    build accepts CATScript. ``stats["route"]`` says which route ran.

    ``limit`` caps how many points are READ (0 = all). It is applied inside the
    script, not afterwards: the single-part route wants at most 200 points, and
    reading 5 000 of them to throw 4 800 away would cost more than the route it
    replaced.
    """
    try:
        sel_count = int(sel.Count)
    except Exception:  # noqa: BLE001
        return None
    if sel_count <= 0:
        return [], 0, 0
    flat: list[float] = []
    for attempt in (1, 2):
        try:
            raw = app.SystemService.Evaluate(_BATCH_SWEEP, 0, "catia_mcp_sweep",
                                             [sel, spa, int(limit)])
            # Unset slots come back as Empty/None: a VBScript array is typed by
            # its first assignment, and `out(3)` is never written by the script.
            flat = [float(v) if v is not None else 0.0 for v in raw]
            break
        except Exception:  # noqa: BLE001 - the per-cell route still works
            if attempt == 2:
                if stats is not None:
                    stats["sweep_route"] = "per_cell"
                return None
            continue
    if len(flat) < 4:
        if stats is not None:
            stats["sweep_route"] = "per_cell"
        return None
    cells, kept, chars = int(flat[0]), int(flat[1]), int(flat[2])
    values = flat[4:4 + 3 * kept]
    if len(values) != 3 * kept:
        if stats is not None:
            stats["sweep_route"] = "per_cell"
        return None
    if stats is not None:
        stats["sweep_route"] = "one_call"
    return ([values[i:i + 3] for i in range(0, len(values), 3)], cells, chars)


def _route_name(route: Any) -> str:
    """Name the reader that ran, in the reply - including when nothing went wrong.

    ``points_route`` used to appear only on the degraded path, so a caller could
    not tell "the batch worked" from "this build has no such field" (reported
    2026-10-26). ``_local_box`` reports CATIA's half of it; this maps the internal
    value onto the published one.
    """
    if route == "per_point":
        return "per_point"
    if route == "batch_retried":
        return "batched_retried"
    return "batched"


def _pose_note(route: str) -> str:
    """What the pose reader did, in the words of the route that actually ran.

    Three routes, three sentences - including the one that read a single pose
    because that is all a narrowed call needs. A caller that branches on
    ``poses_route`` should never have to infer "the key is missing, so maybe it
    was the cheap path".
    """
    if route == "single":
        return ("this call was narrowed to one component, so exactly one "
                "placement was read. 'components_doc_ms' is NOT that one read: it "
                "also covers matching the name you passed against every component "
                "in the assembly (two COM string reads each), which is why a "
                "narrowed call on 70 instances still paid 303.3 ms for one pose in "
                "one B30 run on 2026-10-28 - three times what a single unfiltered "
                "read costs. That number is B30's, not a figure from anyone's "
                "acceptance run: the 2026-10-28 acceptance machine reads this same "
                "line at 166.5 ms and the 2026-10-29 one at 378.8 ms, so read it "
                "as 'the lookup can dominate the pose', not as a constant. "
                "'component_lookup_ms' - published beside "
                "'overhead_breakdown', not inside it, because it is a part of that "
                "block's first term - is the matching half; the rest is the pose. "
                "Drop 'component' to measure the whole assembly")
    if route == "per_component":
        return ("one COM round trip per instance: this build refused the script "
                "that reads them all in one call (~86 ms per instance on B30, so "
                "~6 s on 70 instances - the cost 0.3.34 removed). The numbers are "
                "the same; only the price differs")
    return ("every instance's placement (12 numbers) was read inside ONE CATIA "
            "script call. Before 0.3.34 each instance cost a round trip of its "
            "own: 5 036 ms of a 5 563 ms overhead block on 70 instances")


def _document_key(doc: Any) -> str:
    """The document's name, or ``""`` when it cannot be read.

    The regime book keys warm times by document, and a document whose name
    cannot be read simply gets no delta rather than a KeyError: the numbers are
    the answer, the label is a courtesy.
    """
    try:
        return str(doc.Name)
    except Exception:  # noqa: BLE001 - see docstring
        return ""


def _cost_first(payload: dict[str, Any]) -> dict[str, Any]:
    """Answer first, cost model next, per-component detail last.

    The 197.8-second call in the 2026-10-27 acceptance run came back CUT OFF in
    the caller's wrapper, and the two fields that say which regime produced that
    number - ``cold_parts`` and ``elapsed_note`` - sat at the back, behind 28
    per-part objects. So the most useful fields were exactly the ones a truncated
    reply loses. Nothing is dropped; the order is.
    """
    order = ("dimensions", "min", "max", "complete", "empty_assembly",
             "elapsed_ms", "regime", "session_first_parts", "parts_total",
             "cold_reasons", "part_boxes_total", "part_boxes", "parts_ms",
             "overhead_ms",
             "overhead_note", "overhead_breakdown", "component_lookup_ms",
             "poses_route", "poses_route_note", "after_cold", "elapsed_note",
             "document", "assembly_depth_max", "instances",
             "mesh_fallback_instances", "scope", "note_scope")
    head = {key: payload[key] for key in order if key in payload}
    for key in head:
        payload.pop(key)
    return {**head, **payload}


#: What a per-part cold/warm ratio means, in the same words on every entry.
#:
#: No numbers. This note has now carried wrong numbers twice: 0.3.36 quoted
#: 1.30x / 15.10x as "measured in one session", and 0.3.37 replaced them with a
#: range from the 0.3.36 run - which the very next session falsified, because all
#: seven 12-cell parts read 1.93x-3.70x, ABOVE the range's upper end, and P01 read
#: 18.84x against a 17.25x ceiling (reported 2026-10-29, §6.2). Adding a version
#: tag to a number that moves with the session does not make it stable; the fix is
#: to stop publishing multipliers here and let the two fields in this entry - which
#: ARE this call's reading - carry the value.
#:
#: The rule the report proposed, adopted as the standard: a range is published only
#: if it holds in three independent sessions. Otherwise: mechanism + the field.
_COLD_RATIO_NOTE = (
    "this part's FIRST measurement in the session against its latest warm one. The "
    "ratio is per PART - it is how much internal structure that part's first "
    "topology read has to build - and it moves between sessions on one unchanged "
    "model, so no multiplier is quoted here on purpose: 'cold_ms' and "
    "'cold_warm_ratio' in this entry ARE the reading. Use those two, and do not "
    "carry this part's ratio into a comparison with another part, assembly, "
    "session or version. The assembly-level 'cold costs N times warm' figure is a "
    "weighted sum over these, not a property of CATIA or of this code")

#: What `nls_chars` means, in the same words on both routes.
#:
#: A SIZE indicator, not a cost predictor. 0.3.29 claimed it was "the filter's
#: whole per-cell cost"; an acceptance run over 26 parts falsified that - nls/cell
#: varied 1.31x while ms/cell varied 5.49x (r = 0.34), and the part with the
#: longest paths (P10, 267 chars/cell) was the fastest (16.4 ms/cell). 0.3.31 makes
#: the sentence travel with the number, because the single-part route published the
#: number without it (reported 2026-10-27, §P3-3).
_NLS_NOTE = (
    "characters of NLS path the construction filter read (one path per cell): how "
    "much construction geometry there was to filter, NOT how long the sweep took. "
    "Measured on 26 parts: nls/cell varies 1.31x while ms/cell varies 5.49x "
    "(r = 0.34), and the part with the longest paths was the fastest. Treat this "
    "as a size indicator, not a cost predictor")


def _nls_fields(stats: dict[str, Any]) -> dict[str, Any]:
    """``nls_chars`` (and how many cells it covers) when the filter counted them.

    Characters of NLS path read by the construction filter: a SIZE indicator for
    how much construction geometry a part carries - not a cost predictor. It was
    published in 0.3.29 with the claim that it was "the filter's whole per-cell
    cost"; an acceptance run on 26 parts falsified that (nls/cell varies 1.31x
    while ms/cell varies 5.49x, r = 0.34, and the part with the longest paths was
    the fastest). Free to collect - the string is already in hand - so it rides
    along; it just does not explain the time (reported 2026-10-27).
    """
    if stats.get("nls_chars") is None:
        return {}
    fields: dict[str, Any] = {"nls_chars": stats["nls_chars"],
                              "nls_chars_note": _NLS_NOTE}
    cells, walked = stats.get("cells"), stats.get("nls_chars_cells")
    if walked is not None and cells is not None and walked < cells:
        # The single-part route walks at most 200 cells, so on a big part the
        # number is a SAMPLE. Say so rather than let it be compared with the
        # assembly route's full count.
        fields["nls_chars_cells"] = walked
    return fields


def read_measurable_points(app: Any, spa: Any, refs: list[Any],
                           fallback: Any = None,
                           stats: dict[str, Any] | None = None
                           ) -> list[list[float]]:
    """Every point's coordinates in ONE call, falling back to one call each.

    ``refs`` are topology references; ``spa`` is the document's SPAWorkbench and
    is passed INTO the engine so the Python side does not pay a COM round trip
    per vertex either. ``fallback`` is the per-point reader to use if the batched
    form is refused (it takes a reference and returns three floats).

    The batch is retried once before falling back, because the failure is not a
    property of the model: measured on B30 2026-10-26, the same 335 references
    went through this call in 424 ms on a warm session - and in 27.3 s (the
    per-point route, 335 x 82 ms) right after an assembly had been opened, when
    CATIA refused the script once and the silent fallback took over. A 60x
    slowdown that reports nothing is worse than a slow path that says so, hence
    ``stats["route"]``.
    """
    if not refs:
        return []
    for attempt in (1, 2):
        try:
            flat = list(app.SystemService.Evaluate(_BATCH_POINTS, 0,
                                                   "catia_mcp_points",
                                                   [spa, list(refs)]))
            if len(flat) == 3 * len(refs):
                values = [float(v) for v in flat]
                if stats is not None:
                    stats["route"] = "batch" if attempt == 1 else "batch_retried"
                return [values[i:i + 3] for i in range(0, len(values), 3)]
        except Exception:  # noqa: BLE001 - the slow route still works
            if attempt == 1:
                continue
    if stats is not None:
        stats["route"] = "per_point"
    if fallback is None:
        return []
    return [fallback(ref) for ref in refs]


def _pick(arguments: dict[str, Any], *names: str) -> str:
    """First present alias, or a message naming every accepted spelling.

    Callers reach for element1 / elem1_name / element1_name interchangeably -
    the schemas were not consistent about it - and a strict lookup then failed
    with "is a required property", which reads like the caller passed nothing
    at all rather than the wrong spelling.
    """
    for name in names:
        if arguments.get(name) is not None:
            return arguments[name]
    raise ValueError(
        "missing element argument: pass %s (aliases: %s)."
        % (names[0], ", ".join(names[1:]))
    )


class MeasurementTools:
    """Tools for measurement and analysis in CATIA V5."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection
        # Set when a measurement had to open a component's part to reach it, so
        # the reply can say the session gained a document.
        self.opened_for_measurement: str | None = None
        # "hit" / "miss" for the last mesh export, so the reply can state whether
        # the box was read from the file this time or reused from an earlier call.
        self._mesh_last_cache: str | None = None
        # Which way the last _components_doc read the per-instance placements.
        # Kept on the instance rather than added to the signature: every test and
        # every caller stubs _components_doc(doc, only), and a third argument
        # would have been a breaking change for a diagnostic field. The server
        # runs tools on ONE worker thread, so the attribute cannot race with
        # itself inside one call.
        self._poses_route: str | None = None
        # Milliseconds a narrowed call spent matching the requested name against
        # every component, inside components_doc_ms. Published as a sub-bucket so
        # "components_doc_ms is that one read" stops being a false label
        # (reported 2026-10-28, §5.3).
        self._lookup_ms: float | None = None
        # Cold/warm bookkeeping lives in `_BOOKS` (module level, one book per
        # JOB), not on this object: what a part's first reading cost is a fact
        # about the session, and a second MeasurementTools sharing one CATIA must
        # answer it the same way. The four attributes that used to sit here grew
        # a second set when the inertia route needed them, and the two sets
        # disagreed about what "cold" meant - which is what `catia_mcp.regime`
        # exists to stop.

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_measure_distance",
                "description": (
                    "Measure the minimum distance between two geometry elements. "
                    "Returns distance in mm. Elements resolve by tree name "
                    "(features, sketches, e.g. 'Pad.1', 'Sketch.2') or by "
                    "indexed topology ('Face.N' / 'Edge.N' as enumerated by "
                    "catia_list_faces / catia_list_edges on the final solid)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "element1": {
                            "type": "string",
                            "description": (
                                "Name of first element (feature, face, edge, point). "
                                "Aliases accepted: elem1_name, element1_name, elem1."
                            ),
                        },
                        "element2": {
                            "type": "string",
                            "description": (
                                "Name of second element. Aliases: elem2_name, "
                                "element2_name, elem2."
                            ),
                        },
                        "elem1_name": {"type": "string", "description": "Alias of element1."},
                        "element1_name": {"type": "string", "description": "Alias of element1."},
                        "elem2_name": {"type": "string", "description": "Alias of element2."},
                        "element2_name": {"type": "string", "description": "Alias of element2."},
                    },
                    # Deliberately not required: the aliases above are equally
                    # valid, and jsonschema would reject them before the
                    # resolver could accept the alternative spelling.
                },
            },
            {
                "name": "catia_get_inertia",
                "description": (
                    "Get inertia properties: volume (mm3 and cm3), surface area (mm2 "
                    "and cm2), center of gravity (mm), mass in kg/g (if a density is "
                    "passed), and - for the active part only - the inertia matrix "
                    "(kg.m2, computed from the material density defined in CATIA). "
                    "All values are converted server-side from CATIA's internal MKS "
                    "measurement units. "
                    "On an assembly it aggregates per component: each component's own "
                    "volume / area / centre of gravity moved into world space by its "
                    "placement, summed volume-weighted, plus a 'components' list. "
                    "Sub-assemblies are expanded, not skipped: their leaves appear "
                    "with a path ('ASM/PART.1') and their placement composed, so "
                    "'components' adds up to the totals. The totals carry 'complete', "
                    "'instances' {total, measured, skipped} and, when anything was "
                    "left out, 'incomplete_because' - read that before using "
                    "volume_mm3 as the whole assembly's mass: it is then a PARTIAL "
                    "sum. Surface area is summed too (it is additive for disjoint "
                    "solids). The inertia matrix is not aggregated."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "density": {
                            "type": "number",
                            "description": "Material density in kg/m3 (optional, for mass calculation)",
                        },
                        "document": {
                            "type": "string",
                            "description": (
                                "Measure THIS document's own Part instead of the "
                                "active one (alias: document_name). This is the "
                                "reliable route on an assembly: whether activating a "
                                "component's part really changes ActiveDocument "
                                "depends on whether that part has a window of its "
                                "own (reported both ways on B32), while naming the "
                                "document is unambiguous. A part that exists only as "
                                "a component is opened by path here. A "
                                "'<assembly>.CATProduct' is accepted too and measured "
                                "through the same per-component aggregation as an "
                                "active assembly. Every part route returns the same "
                                "keys (volume_mm3/cm3, area_mm2/cm2, "
                                "center_of_gravity_mm), whatever the call shape; a "
                                "quantity that could not be read is null with a "
                                "'<name>_error' beside it. Note that the inertia "
                                "TENSOR is only computed for the active document, so "
                                "this route returns volume / area / centroid without "
                                "a matrix."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_get_bounding_box",
                "description": (
                    "Get the bounding box of the active part in mm. 'auto' (the "
                    "default) ALWAYS takes the STL mesh: exact for a solid whose "
                    "faces are all planar, a LOWER bound of about 0.5-0.7 % for one "
                    "with curved faces, because the mesh chords the curve. "
                    "'vertices' is the cheap sweep of the final shape's vertices - "
                    "exact only when every face is planar, and wrong by however much "
                    "the extremum sticks out when it does not (a cylinder has no "
                    "vertex at its diameter at all). 'mesh' is the same route as "
                    "'auto', stated explicitly. "
                    "With an assembly active it returns the WORLD box: each "
                    "component's local box is transformed by its placement and the "
                    "results are aggregated, per component as well as overall. Pass "
                    "'component' for one instance only - it matches TOP-LEVEL "
                    "components, and when the same part number is ALSO installed "
                    "inside a sub-assembly the reply reports complete: false, lists "
                    "those instances in 'also_nested' and 'skipped', and says the box "
                    "covers the top-level one only. Those per-component boxes "
                    "are still vertex sweeps, so ANY part whose extremum lies on a "
                    "curved face is under-reported there - revolved or not. The true "
                    "per-part box comes from get_bounding_box(document='<part>"
                    ".CATPart', method='mesh'), which opens the part by path when it "
                    "exists only as a component. "
                    "document='<assembly>.CATProduct' works too: the world box never "
                    "needed the assembly to be ACTIVE (it is arithmetic on each "
                    "component's placement), and naming it is the only unambiguous "
                    "route when a second client can change ActiveDocument between "
                    "two calls. Each PART is bounded once and only the placement is "
                    "repeated, so the cost is per part, not per instance. A "
                    "sub-assembly is EXPANDED, not skipped: its parts are bounded as "
                    "if they had been placed directly here (the two placements "
                    "composed) and every entry says which level it came from with "
                    "'path' and 'depth'. "
                    "Exactness has a price, and there are TWO regimes. Warm, a part "
                    "costs about 200 ms + 3-5 ms per topology cell (B32 SP6, "
                    "2026-10-28: 3.2-4.9 ms/cell across the 28 acceptance parts, "
                    "after the sweep moved inside CATIA; it was 9.5 ms before). "
                    "COLD - the first time a part is touched in a session - the same "
                    "work costs 16-90 ms per cell, because CATIA builds that part's "
                    "internal representation on the first topology read: on the "
                    "28-part acceptance assembly the first call cost 173 s (0.3.31) "
                    "and 183 s (0.3.33) against 23 s and 25-26 s for the same call "
                    "right after (7.2-7.6x), with cells_scanned, vertices_used and "
                    "nls_chars IDENTICAL. Read these as RANGES, not constants: the "
                    "same model moved 4.65x -> 7.6x -> 7.24x in three versions with "
                    "the same geometry, and quoting one number as the model of the "
                    "machine is how a later run gets read as a regression. "
                    "'first_measure' per part "
                    "in 'parts', 'cold_parts' and 'elapsed_note' say which regime a "
                    "number came from; compare elapsed_ms across sessions only with "
                    "that in view. A 70-instance assembly is MINUTES cold, ~25 s "
                    "warm, and the server runs tools one at a time. warm_up=true runs "
                    "the same measurement and returns the COST instead of the box, so "
                    "that first read can be paid when the caller has idle time rather "
                    "than inside a call that needs the answer. 'cells_scanned', "
                    "'elapsed_ms' (total, and per part in 'parts'), 'parts_ms' and "
                    "'overhead_ms' report what it actually cost: overhead_ms is the "
                    "part no entry in 'parts' owns, so a share-of-total table built "
                    "from 'parts' alone is short by that much - 22-24 % of a warm "
                    "70-instance call across versions. Measure the instances you need "
                    "with "
                    "'component' "
                    "rather than the whole assembly when you can. A curved part's "
                    "mesh box is read from its SAVED file and reused while that "
                    "file's size and modified time are unchanged (its source_note "
                    "says 'reused') - that saves one export per curved part (~5-17 s "
                    "each). The cache belongs to the COPY route, and the copy route "
                    "is what runs when the part has no window of its own - the test "
                    "is whether activating it changes ActiveDocument. A part that "
                    "DOES change it is exported in place and never cached, because "
                    "its loaded state may hold edits that were not saved and no file "
                    "identity can describe them. catia_open_document cannot give an "
                    "already-loaded component a window (it activates it instead), so "
                    "a component always takes the cached route; the in-place route "
                    "is for a part opened from disk on its own."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["auto", "vertices", "mesh"],
                            "default": "auto",
                            "description": (
                                "auto (default) = always the STL mesh, exact for "
                                "planar-faced solids and a lower bound for curved "
                                "ones; vertices = cheap vertex sweep, exact only when "
                                "every face is planar (fails on a fully curved solid); "
                                "mesh = the same as auto, named explicitly"
                            ),
                        },
                        "component": {
                            "type": "string",
                            "description": (
                                "Name of one instance in the active assembly "
                                "(see catia_list_components). Omit for all of them."
                            ),
                        },
                        "include_curved": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Assembly mode only. A fully curved part has no "
                                "vertex at its extremum, so a vertex sweep cannot "
                                "bound it; with this on (the default) that part "
                                "falls back to its own STL mesh box and the "
                                "instance is marked source='mesh_fallback', which is "
                                "what makes 'complete' mean what it says. Turn it "
                                "off to keep every instance on the vertex route."
                            ),
                        },
                        "warm_up": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Pay the COLD cost on purpose: run the same "
                                "measurement, discard the geometry, and return what "
                                "it cost (parts_measured, cold_parts, elapsed_ms, "
                                "per-part times). The first topology read of a part "
                                "costs 4-7x the later ones and lands inside whichever "
                                "call comes first - on the 28-part acceptance assembly "
                                "173-183 s against 23-26 s for the identical call right "
                                "after. "
                                "Warm up after opening an assembly, then measure. "
                                "There is no cheaper warm-up: CATIA's first-read "
                                "setup IS the work, and a per-part vertex box cannot "
                                "be cached from a file identity the way a mesh box "
                                "is (the live part may hold unsaved edits)."
                            ),
                        },
                        "document": {
                            "type": "string",
                            "description": (
                                "Measure THIS document instead of the active one - "
                                "the unambiguous route, and the only way to get a "
                                "true mesh box for a part that lives inside an open "
                                "assembly. A part that exists only as a component is "
                                "not in the Documents collection, so it is opened by "
                                "path here (it stays open; the reply says so). "
                                "A Product document is accepted as well - its world "
                                "box is aggregated from the named document, so no "
                                "activation is involved (method='mesh' is refused "
                                "for one, because a mesh box is per part). "
                                "Alias: document_name."
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_get_parameters",
                "description": (
                    "List all user-defined and computed parameters of the active part. "
                    "Includes dimensions, formulas, and design tables."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "Optional name filter (partial match)",
                        },
                    },
                },
            },
            {
                "name": "catia_set_parameter",
                "description": (
                    "Set the value of a named parameter in the active part. "
                    "Useful for parametric design modifications."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Full parameter name (e.g., 'Part1\\\\Pad.1\\\\FirstLimit\\\\Length')",
                        },
                        "value": {
                            "type": "number",
                            "description": "New value for the parameter",
                        },
                    },
                    "required": ["name", "value"],
                },
            },
            {
                "name": "catia_update_part",
                "description": "Force update/rebuild of the active part. Recalculates all features.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]



    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_measure_distance":
                return self._measure_distance(
                    _pick(arguments, "element1", "elem1_name", "element1_name", "elem1"),
                    _pick(arguments, "element2", "elem2_name", "element2_name", "elem2"))
            case "catia_get_inertia":
                return self._get_inertia(arguments.get("density"),
                                         arguments.get("document")
                                         or arguments.get("document_name"))
            case "catia_get_bounding_box":
                reply = self._get_bounding_box(
                    arguments.get("method", "auto"),
                    arguments.get("component"),
                    arguments.get("document") or arguments.get("document_name"),
                    bool(arguments.get("include_curved", True)))
                # warm_up runs the same measurement and throws the geometry away:
                # the point is to make CATIA pay its first-read cost now rather
                # than inside the call that needs the answer (reported 2026-10-27).
                if arguments.get("warm_up"):
                    return self._warm_up_report(reply)
                return reply
            case "catia_get_parameters":
                return self._get_parameters(arguments.get("filter"))
            case "catia_set_parameter":
                return self._set_parameter(arguments["name"], arguments["value"])
            case "catia_update_part":
                return self._update_part()
            case _:
                raise ValueError(f"Unknown measurement tool: {tool_name}")

    def _evaluate_floats(self, script: str, func: str, params: list) -> list[float]:
        """Run a VBScript function inside CATIA and return its array as floats.

        In/out CATSafeArrayVariant parameters (GetCOG, GetPoint, GetPlane,
        GetInertiaMatrix) do not marshal through late-bound IDispatch: the
        Python list is passed by value and silently left untouched. The
        R20-documented route (also used by pycatia, field-proven on V5R20)
        is SystemService.Evaluate running a VBScript function that declares
        the array locally and returns it.
        """
        raw = self.conn.app.SystemService.Evaluate(script, 0, func, params)
        return [float(v) for v in raw]

    def _read_measurable_array(self, measurable: Any, method: str, count: int) -> list[float]:
        """Call an array-filling Measurable method inside CATIA and return it."""
        return read_measurable_array(self.conn.app, measurable, method, count)

    _VBS_INERTIA = (
        "Public Function catia_mcp_inertia(oBody)\n"
        "    Dim oSPA, oInertia, i\n"
        "    Dim m(8)\n"
        "    Dim a(9)\n"
        "    Set oSPA = CATIA.ActiveDocument.GetWorkbench(\"SPAWorkbench\")\n"
        "    Set oInertia = oSPA.Inertias.Add(oBody)\n"
        "    oInertia.GetInertiaMatrix m\n"
        "    a(0) = oInertia.Mass\n"
        "    For i = 0 To 8\n"
        "        a(i + 1) = m(i)\n"
        "    Next\n"
        "    catia_mcp_inertia = a\n"
        "End Function\n"
    )

    def _spa_workbench(self) -> Any:
        """Get the SPAWorkbench measurement workbench.

        GetWorkbench is documented on Document, not Application, so try the
        active document first and keep the application call as a fallback
        for environments where it happens to resolve there.
        """
        doc = self.conn.active_document
        try:
            return doc.GetWorkbench("SPAWorkbench")
        except AttributeError:
            return self.conn.app.GetWorkbench("SPAWorkbench")

    def _resolve_reference(self, part: Any, name: str) -> Any:
        """Resolve an element name to a CATIA Reference.

        Tree-named objects (Pad.1, Sketch.2) resolve through a Name= search.
        Topology uses the indexed form Face.N / Edge.N over the body's final
        shape, matching the indices listed by catia_list_faces and
        catia_list_edges. Topology references come from the selection's
        Reference property: CreateReferenceFromObject rejects HSO-resolved
        topology cells (field-verified E_INVALIDARG on V5R20).
        """
        sel = self.conn.hso
        topo = re.match(r"^(Face|Edge)\.(\d+)$", name)
        try:
            if topo:
                kind, idx = topo.group(1), int(topo.group(2))
                body = self.conn.get_active_part_body()
                if body.Shapes.Count == 0:
                    raise RuntimeError(
                        f"Cannot resolve '{name}': the active body has no "
                        "solid shape to enumerate topology from."
                    )
                # Search the body itself: only it resolves to the final
                # solid's topology (Shapes.Item(last) is the last feature's
                # own region).
                sel.Clear()
                sel.Add(body)
                search_topology(sel, kind)
                count = sel.Count
                if idx < 1 or idx > count:
                    raise RuntimeError(
                        f"'{name}' is out of range: the final shape exposes "
                        f"{count} {kind.lower()}(s). Use catia_list_faces / "
                        "catia_list_edges to enumerate valid indices."
                    )
                return sel.Item(idx).Reference

            sel.Clear()
            sel.Search(f"Name={name},all")
            if sel.Count == 0:
                raise RuntimeError(
                    f"Element '{name}' not found by tree name. Use feature "
                    "or sketch names (e.g. 'Pad.1'), or the indexed topology "
                    "form Face.N / Edge.N from catia_list_faces / "
                    "catia_list_edges."
                )
            obj = sel.Item(1).Value
            return part.CreateReferenceFromObject(obj)
        finally:
            try:
                sel.Clear()
            except Exception:
                pass

    def _measure_distance(self, elem1_name: str, elem2_name: str) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        spa = self._spa_workbench()

        ref1 = self._resolve_reference(part, elem1_name)
        ref2 = self._resolve_reference(part, elem2_name)

        # Unit split in the SPA Measurable API (field-verified 2026-10-13 on
        # V5-6R2020 B30, zh): Volume/Area are MKS, GetCOG and
        # GetMinimumDistance come back in the document's length unit (mm).
        # GetMinimumDistance therefore needs NO conversion - multiplying by
        # 1000 here used to inflate every distance 1000x (a phi80 disc cannot
        # have two edges 2828 mm apart).
        measurable = spa.GetMeasurable(ref1)
        distance = measurable.GetMinimumDistance(ref2)

        if abs(distance) < 1e-9:
            # 0 is a legitimate answer for touching faces, and 0 is also what you
            # get when both names resolve to the same element - which happens as
            # soon as an Edge.N / Face.N index has been renumbered by a feature.
            # Reported 2026-10-23 as "parallel faces always read 0.0000": the two
            # parallel faces of a plain block measure 10.0000 mm here, so the
            # number was not the bug, the stale index was.
            return (
                f"'{elem1_name}' and '{elem2_name}' are 0.0000 mm apart: they touch "
                "or overlap, or both names resolved to the same element. If a gap "
                "was expected, the names are stale - Face.N / Edge.N are "
                "enumeration positions, renumbered by every feature that changes "
                "the topology. Re-list with catia_list_faces / catia_list_edges and "
                "retry. (Parallel non-adjacent faces do measure: 10.0000 mm on a "
                "10 mm block.)"
            )
        return f"Minimum distance between '{elem1_name}' and '{elem2_name}': {distance:.4f} mm"

    def _resolve_open_document(self, name: str) -> Any:
        """An open document by name - the assembly cannot steal this one.

        Activating a component's part inside an open product does not reliably
        change what ``ActiveDocument`` returns on every build, so a measurement
        aimed at "the part I just activated" silently measured the assembly
        (reported 2026-10-24). Matching is case-insensitive on the full name and
        on the stem, like catia_activate_document - but NO LONGER "first hit
        wins". This docstring used to open with "A name is unambiguous", and this
        server is what falsified it: creating a part under a name that is already
        open leaves two documents with the same ``Name`` (see
        ``CATIAConnection.resolve_document``), so the first hit answered with the
        other model's mass properties and echoed the name back - no error, no
        hint, a plausible wrong number (reported 2026-10-28, P1).

        A part that exists only as a component of an open product is NOT in
        ``Documents`` at all (measured on B30 2026-10-24: opening a product that
        references two parts leaves Documents holding just the product), which is
        why ``document='<part>.CATPart'`` used to answer "No open document
        matches" in exactly the case the argument was added for. The component's
        own file is located by path and opened, so the call works without the
        caller knowing this rule.
        """
        doc, matches = self.conn.resolve_document(name)
        if doc is not None:
            return doc
        if matches:
            # Two documents answer to this name. Choosing one is the defect, so
            # name both and let the caller pass a path - the saved copy carries
            # its full path as its Name, which resolves unambiguously.
            rows = self.conn.describe_matches(matches)
            # One formatter, in the connection, so this sentence and the plan
            # route's cannot drift apart (reported 2026-10-29, §5.2).
            listing = self.conn.format_matches(rows)
            raise CodedError(
                f"'{name}' matches {len(rows)} open documents: {listing}. This call "
                "did NOT pick one: the two models differ, and the name you passed "
                "reads the same either way. Pass the full path of the one you mean "
                "(that is what catia_list_documents prints), or close/rename the "
                "other copy first.",
                code="DOCUMENT_NAME_AMBIGUOUS",
                modified=False,
                next_actions=[
                    "Pass the full path from catia_list_documents instead of the "
                    "bare name - a saved document's Name IS its path, so the path "
                    "matches exactly one document and nothing was measured or "
                    "changed by this call",
                    "If both copies are unsaved (neither shows a path), give them "
                    "different part numbers with catia_new_part or close one: no "
                    "argument can tell them apart",
                ],
                data={"candidates": rows},
            )
        opened = self._open_component_part(name)
        if opened is not None:
            return opened
        docs = self.conn.documents
        names = ", ".join(str(docs.Item(i).Name) for i in range(1, docs.Count + 1))
        raise CodedError(
            f"No open document matches '{name}', and no component of an open "
            f"assembly references a part by that name either. Open: "
            f"{names or 'none'}. Pass the full name from catia_list_documents, or "
            "open the part first with catia_open_document.",
            # A lookup in the Documents collection. COM_ERROR made "I typed the
            # name wrong" indistinguishable from "CATIA failed" (reported
            # 2026-10-28, §5.3).
            code="DOCUMENT_NOT_FOUND",
            modified=False,
            next_actions=[
                "Pass a name from catia_list_documents, or open the file first - "
                "no document was opened or changed by this call"
            ],
        )

    def _open_component_part(self, name: str) -> Any:
        """Open a part that is only reachable through an open assembly.

        Returns the opened document, or None when nothing matches. Matching is on
        the component name (``P05_CylinderCap.1``), its part number, and the file
        name of the part it references, so every handle a caller may hold works.
        """
        key = name.strip().casefold()
        for i in range(1, self.conn.documents.Count + 1):
            try:
                document = self.conn.documents.Item(i)
                product = self.conn.product_of(document)
            except Exception:  # noqa: BLE001 - not a Product
                continue
            try:
                children = product.Products
            except Exception:  # noqa: BLE001
                continue
            for index in range(1, children.Count + 1):
                try:
                    child = children.Item(index)
                    candidates = {str(child.Name).casefold(),
                                  str(child.PartNumber).casefold()}
                    part_doc = child.ReferenceProduct.Parent
                    candidates.add(str(part_doc.Name).casefold())
                    candidates.add(str(part_doc.Name).split(".")[0].casefold())
                except Exception:  # noqa: BLE001 - a dead link, not a match
                    continue
                if key not in candidates:
                    continue
                try:
                    path = str(part_doc.FullName)
                except Exception:  # noqa: BLE001
                    path = ""
                if not path or not os.path.isabs(path):
                    raise RuntimeError(
                        f"'{name}' is a component of '{document.Name}' but its part "
                        "has never been saved, so it cannot be opened as its own "
                        "document; save the part first (catia_save_document)."
                    )
                opened = self.conn.documents.Open(os.path.normpath(path))
                self.opened_for_measurement = str(opened.Name)
                return opened
        return None

    def _part_inertia(self, part: Any, density: float | None,
                      label: str) -> str:
        """Volume / area / centroid of a specific Part, by name or object."""
        started = time.perf_counter()
        doc = part.Parent
        body = part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError(self._no_solid_reason(part, body, label))
        # Where the milliseconds of this route go, measured rather than inferred.
        # A 24000 mm3 box measured in 2 117.3 ms next to a 12-times smaller
        # 174.3 ms for another part is not explainable from `elapsed_ms` alone,
        # and the answer (a failed activation attempt) was only reachable from a
        # KNOWN bug described in another section of the same report (reported
        # 2026-10-28, §6.2).
        t_read = time.perf_counter()
        spa = doc.GetWorkbench("SPAWorkbench")
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
        payload: dict[str, Any] = {"document": label}
        payload.update(self._measured_quantities(measurable))
        measure_ms = round((time.perf_counter() - t_read) * 1000, 1)
        volume_mm3 = payload.get("volume_mm3")
        if density and volume_mm3 is not None:
            payload["mass_kg"] = round(density * volume_mm3 / 1e9, 6)
            payload["mass_g"] = round(density * volume_mm3 / 1e6, 3)
            payload["density_kg_m3"] = density
        # The tensor is only computed for the ACTIVE document, so this route used
        # to return volume/area/centroid without one - and getting nine parts'
        # tensors took 18 calls (open + activate each). CATIA's activation is
        # temporary here: activate, read, restore (reported 2026-10-24).
        t_tensor = time.perf_counter()
        tensor = self._tensor_for(part)
        if tensor:
            payload.update(tensor)
            if "mass_kg" in payload:
                payload["mass_note"] = _MASS_NOTE
            payload["note"] = (
                "measured on this document's own Part. The inertia tensor came from "
                "activating it for the measurement and restoring the previous active "
                "document afterwards.")
        else:
            # A field, not a paragraph: this used to be five lines of prose in
            # EVERY reply of a nine-part sweep, and a caller had to read it to
            # find out whether the tensor was missing or merely unreadable
            # (reported 2026-10-25). The prose stays, but only as the reason -
            # and beside a one-word CODE, because nine parts in a row produced
            # nine identical sentences and the only thing a reader needs is which
            # case it is (reported 2026-10-26).
            payload["tensor"] = None
            code, reason = self._tensor_refusal(doc, label)
            payload["tensor_reason_code"] = code
            payload["tensor_reason"] = reason
            # The partial route is a FACT, not prose: it says which fields a plan
            # can still assert on and which it cannot. Nine identical sentences in
            # a nine-part sweep were the second-longest block in the reply
            # (reported 2026-10-26); the same information in one object is
            # readable by a caller and costs 4 lines instead of 7.
            payload["tensor_partial_route"] = {
                "code": "plan_expect_document",
                "tool": "catia_run_plan",
                "shape": {"expect": [{"document": "<name>"}]},
                "borrows_activation": True,
                "answers": ["volume_mm3", "volume_cm3", "area_mm2", "area_cm2",
                            "center_of_gravity_mm", "mass_kg/mass_g (with density)"],
                "does_not_answer": ["inertia_tensor"],
            }
            payload["note"] = (
                "measured on this document's own Part, independent of which document "
                "CATIA reports as active. The inertia tensor is missing - see "
                "tensor_reason.")
        # The window closes HERE, not right after `_tensor_for`: working out WHY
        # the tensor is missing is part of the tensor step (the refusal asks the
        # connection which assemblies hold the part, and that walks every open
        # product). Closing it earlier made the diagnosis look like unattributed
        # overhead - 18-19 % of parts_ms on three samples, with no field to
        # account for it (reported 2026-10-29, §6.1).
        tensor_ms = round((time.perf_counter() - t_tensor) * 1000, 1)
        # What this route cost, in the vocabulary the assembly route already uses,
        # so "174.3 ms here, 2 117.3 ms there, same tool" has an answer inside the
        # reply instead of in a bug report (reported 2026-10-28, §6.2).
        parts_ms = round((time.perf_counter() - started) * 1000, 1)
        # The residual, named. `parts_ms` is not "measure + tensor" - it also holds
        # fetching the main body, its shape count, the density arithmetic and
        # building this payload - and calling it a sum of two gave a caller who
        # added them a number 18-19 % short of the field beside it (reported
        # 2026-10-29, §6.1). Same failure as the four-key breakdown this release
        # fixed, one field over, so it gets the same treatment: a name for the
        # remainder, and a closure that holds to 0.1 ms.
        parts_other_ms = round(max(parts_ms - measure_ms - tensor_ms, 0.0), 1)
        payload["parts_ms"] = parts_ms
        payload["measure_ms"] = measure_ms
        payload["tensor_ms"] = tensor_ms
        payload["parts_other_ms"] = parts_other_ms
        payload["overhead_note"] = _PART_COST_NOTE
        payload.update(self._mass_property_regime(
            part, route="inertia.part_document", payload=payload,
            elapsed_ms=parts_ms))
        return json.dumps(payload, indent=2)

    def _tensor_refusal(self, doc: Any, label: str) -> tuple[str, str]:
        """Why the tensor is missing, as a code AND a sentence that agree.

        One code could not say two different things. ``no_own_window`` asserted
        "this part is a component of an open assembly" about every part that
        could not be activated - including a part reached through
        ``document=`` that no assembly references at all, where the same reply's
        ``catia_list_documents`` output showed no ``component_of`` entry. The
        advice was wrong in the same way: it sent the caller to look for an
        assembly that does not hold the part (reported 2026-10-28, §6.3).

        So: ask the connection which assemblies reference this part (the reading
        the listing prints), and answer ``nested_in_assembly`` only when one does.
        Anything else is ``not_activated`` - CATIA kept another document in front,
        which ``catia_activate_document`` fixes.
        """
        why = getattr(self, "_tensor_why", None)
        try:
            doc_name = str(doc.Name)
        except Exception:  # noqa: BLE001 - the label is what the caller passed
            doc_name = str(label)
        users: list[str] = []
        try:
            users = list(self.conn.component_usage().get(doc_name) or [])
        except Exception:  # noqa: BLE001 - a refusal is not worth an exception
            users = []
        if users:
            holders = ", ".join(f"'{name}'" for name in users)
            return ("nested_in_assembly",
                    f"nested in an open assembly: CATIA computes the tensor only "
                    f"for the ACTIVE document, and {holders} uses this part as a "
                    "component, so Activate() leaves the assembly in front. Fix: "
                    f"catia_get_inertia(document='{label}') already reads its own "
                    "Part without needing the window - for the tensor itself, open "
                    "it as its own document (catia_open_document), activate it, then "
                    "call get_inertia without 'document'")
        if why == "unreadable":
            return ("unreadable",
                    "CATIA raised on the inertia query for this part's main body "
                    "(the message is in the plan's diagnostics): the part is in no "
                    "open assembly and CATIA did put it in front, so this is the "
                    "measurement itself. Check that the body holds a solid "
                    "(catia_measure_topology).")
        return ("not_activated",
                f"not activated: CATIA computes the tensor only for the ACTIVE "
                f"document, and activating '{doc_name}' did not leave it in front - "
                "it is in no open assembly, so this is a focus refusal, not a "
                "nested component. Fix: catia_activate_document('"
                f"{doc_name}') and retry, or double the route with "
                "catia_open_document if it was never opened as its own document.")

    def _measured_quantities(self, measurable: Any) -> dict[str, Any]:
        """`volume` / `area` / `center_of_gravity`, in one key table.

        Both part routes (`document=<part>` and "the active part") read the SAME
        measurable, and until 0.3.28 they published different subsets of it:
        `volume_cm3` / `area_cm2` / `mass_g` existed only on the active-part
        route, so `r["area_cm2"]` worked in one call shape and raised KeyError in
        the other - for the same document and the same numbers (reported
        2026-10-27). One reader, one set of keys, for both.

        A quantity that cannot be read is ``None`` with ``<name>_error`` beside
        it: never absent (a KeyError is not a diagnosis) and never a sentence
        where a number belongs (``"unavailable: …"`` in a float field broke the
        caller that tried to compare it).
        """
        fields: dict[str, Any] = {}
        volume_mm3 = None
        try:
            volume_mm3 = measurable.Volume * _M3_TO_MM3
        except Exception as exc:  # noqa: BLE001 - reported as a field
            fields["volume_error"] = str(exc).splitlines()[0][:90]
        fields["volume_mm3"] = None if volume_mm3 is None else round(volume_mm3, 4)
        fields["volume_cm3"] = (None if volume_mm3 is None
                                else round(volume_mm3 / 1000.0, 4))
        area_mm2 = None
        try:
            area_mm2 = measurable.Area * _M2_TO_MM2
        except Exception as exc:  # noqa: BLE001
            fields["area_error"] = str(exc).splitlines()[0][:90]
        fields["area_mm2"] = None if area_mm2 is None else round(area_mm2, 4)
        fields["area_cm2"] = None if area_mm2 is None else round(area_mm2 / 100.0, 4)
        try:
            cog = self._read_measurable_array(measurable, "GetCOG", 3)
            fields["center_of_gravity_mm"] = {k: round(v, 4)
                                              for k, v in zip("xyz", cog)}
        except Exception as exc:  # noqa: BLE001
            fields["center_of_gravity_mm"] = None
            fields["center_of_gravity_error"] = str(exc).splitlines()[0][:90]
        return fields

    def _tensor_for(self, part: Any) -> dict[str, Any] | None:
        """Inertia tensor of a specific part, by borrowing the activation.

        Returns None when the part cannot be made active (a component with no
        window of its own), which is the one case where the caller still has to
        open the document first. ``self._tensor_why`` records WHICH case it was -
        ``activation_refused`` or ``unreadable`` - because the caller has to say
        something more useful than "no tensor" and the two need different advice
        (see ``_tensor_refusal``).

        The restore keeps the previous document as an OBJECT, not as a name. It
        used to search ``Documents`` for the old name and activate the first hit,
        so with two documents sharing a name - which this server itself creates,
        see ``CATIAConnection.resolve_document`` - measuring the SAVED copy left
        the UNSAVED copy in front, or the other way round (measured on B30
        2026-10-28: active went from the unsaved ``v36_dup.CATPart`` to the saved
        one after a named ``catia_get_inertia``). A measurement that silently
        changes which document is in front also changes what the NEXT no-argument
        call measures - which is how a wrong number appears two calls later.
        """
        doc = part.Parent
        self._tensor_why = None
        previous = None
        try:
            previous = self.conn.active_document
        except Exception:  # noqa: BLE001 - nothing was in front
            previous = None
        try:
            doc.Activate()
            # Identity, not name: two documents can share a name, and then a name
            # comparison says "activated" about whichever one CATIA left in front.
            active = self.conn.app.ActiveDocument
            same = None
            try:
                same = bool(active == doc)
            except Exception:  # noqa: BLE001 - not comparable on this build
                same = None
            if same is None:
                same = str(active.Name) == str(doc.Name)
            if not same:
                self._tensor_why = "activation_refused"
                return None
            body = part.MainBody
            if body.Shapes.Count == 0:
                self._tensor_why = "unreadable"
                return None
            vals = self._evaluate_floats(self._VBS_INERTIA, "catia_mcp_inertia", [body])
            return self._matrix_fields(vals)
        except Exception:  # noqa: BLE001 - the caller reports what is missing
            self._tensor_why = "unreadable"
            return None
        finally:
            if previous is not None:
                try:
                    previous.Activate()
                except Exception:  # noqa: BLE001 - best effort, as before
                    pass

    @staticmethod
    def _no_solid_reason(part: Any, body: Any, label: str) -> str:
        """Say why a part has no solid, without calling an empty part 'surface-only'.

        A brand-new part and a part made of GSD surfaces both have an empty main
        body, and they need different advice: the first one has nothing to measure
        at all, the second one is one close_surface away from a solid. Reported
        2026-10-24: a completely empty part was told it was "surface-only".
        """
        try:
            surfaces = sum(
                part.HybridBodies.Item(i).HybridShapes.Count
                for i in range(1, part.HybridBodies.Count + 1))
        except Exception:  # noqa: BLE001
            surfaces = -1
        if surfaces == 0:
            return (
                f"'{label}' is empty: '{getattr(body, 'Name', 'the main body')}' holds "
                "no solid and there is no surface geometry either, so volume / area / "
                "centroid are undefined. Build something first (catia_pad / "
                "catia_gsd_*)."
            )
        counted = ("no surface geometry either" if surfaces < 0
                   else f"{surfaces} surface element(s) and no solid")
        return (
            f"'{label}' has no solid in '{getattr(body, 'Name', 'the main body')}' "
            f"({counted}), so volume / area / centroid are undefined. Close the "
            "surfaces into a solid first (catia_gsd_close_surface) or measure the "
            "surfaces another way."
        )

    def _get_inertia(self, density: float | None = None,
                     document: str | None = None) -> str:
        self.conn.ensure_connected()
        # The assembly route has published elapsed_ms since 0.3.26; the two PART
        # routes did not, so the field's presence depended on which document
        # happened to be active - an asymmetry a caller budgeting a timeout runs
        # straight into (reported 2026-10-27).
        started = time.perf_counter()
        if document:
            self.opened_for_measurement = None
            doc = self._resolve_open_document(document)
            if self._is_product_document(doc):
                # A Product has no Part, but its mass properties are exactly what
                # the no-argument route returns when the assembly is active - and
                # naming it is the only unambiguous way to ask (a second client can
                # change ActiveDocument between two calls). Reaching for `.Part`
                # here produced "Active document 'X' exposes no 'Part'" about a
                # document the caller had just NAMED (reported 2026-10-27).
                return self._assembly_inertia(density, doc)
            try:
                part = self.conn.part_of(doc)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"'{doc.Name}' is neither a Part nor a Product document ({exc})."
                ) from exc
            reply = self._stamp_elapsed(
                self._part_inertia(part, density, str(doc.Name)), started)
            if self.opened_for_measurement:
                payload = json.loads(reply)
                payload["opened"] = (
                    f"'{self.opened_for_measurement}' existed only as a component of "
                    "an open assembly, so it was opened as its own document to be "
                    "measured; it stays open (close it with catia_close_document)."
                )
                return json.dumps(payload, indent=2)
            return reply
        if self._document_kind() == "assembly":
            return self._assembly_inertia(density)
        spa = self._spa_workbench()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        # A part made only of surfaces has volume/area/centroid = undefined, and
        # SPA answers with placeholder strings that a caller then tries to use.
        # Say it here: run_plan records this as inertia_error and carries on.
        try:
            if body.Shapes.Count == 0:
                raise RuntimeError(self._no_solid_reason(part, body, part.Name))
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001 - Shapes is readable on every part
            pass
        ref = part.CreateReferenceFromObject(body)

        measurable = spa.GetMeasurable(ref)

        result: dict[str, Any] = {}
        # Same reader as the `document=` route: one key table for the same
        # reading (reported 2026-10-27 - `area_cm2` existed on this route only).
        t_read = time.perf_counter()
        result.update(self._measured_quantities(measurable))
        measure_ms = round((time.perf_counter() - t_read) * 1000, 1)
        volume_mm3 = result.get("volume_mm3")

        if density and volume_mm3 is not None:
            result["mass_kg"] = round(density * volume_mm3 / 1e9, 6)
            result["mass_g"] = round(density * volume_mm3 / 1e6, 3)
            result["density_kg_m3"] = density

        t_tensor = time.perf_counter()
        try:
            # Inertia data lives on the SPAWorkbench Inertia object, not on
            # Measurable (Measurable.GetInertia does not exist in V5).
            vals = self._evaluate_floats(self._VBS_INERTIA, "catia_mcp_inertia", [body])
            result.update(self._matrix_fields(vals))
            if "mass_kg" in result:
                result["mass_note"] = _MASS_NOTE
        except Exception as e:
            result["inertia_matrix_kg_m2"] = f"unavailable: {e}"
        tensor_ms = round((time.perf_counter() - t_tensor) * 1000, 1)

        # The same buckets the `document=` route publishes, measured from this
        # call's own start so `parts_ms + overhead_ms = elapsed_ms` holds here too
        # (reported 2026-10-28, §6.2: the ACTIVE part route was the one with
        # elapsed_ms and nothing else), including the residual that makes
        # `measure_ms + tensor_ms + parts_other_ms = parts_ms` close (reported
        # 2026-10-29, §6.1).
        parts_ms = round((time.perf_counter() - started) * 1000, 1)
        result["parts_ms"] = parts_ms
        result["measure_ms"] = measure_ms
        result["tensor_ms"] = tensor_ms
        result["parts_other_ms"] = round(
            max(parts_ms - measure_ms - tensor_ms, 0.0), 1)
        result["overhead_note"] = _PART_COST_NOTE
        result.update(self._mass_property_regime(
            part, route="inertia.part_active", payload=result,
            elapsed_ms=parts_ms))
        return self._stamp_elapsed(json.dumps(result, indent=2), started)

    @staticmethod
    def _matrix_fields(vals: list[float]) -> dict[str, Any]:
        """The tensor fields, from the 10 numbers the VBS macro returns."""
        inertia = vals[1:10]
        # Inertia spans 1e-9 .. 1e2 kg.m2, so a fixed number of DECIMALS is wrong
        # by construction: rounding to 4 places turned a 72 cm3 plate's three
        # principal moments (Ixx 2.1e-5, Izz 5.6e-5) into 0.0 / 0.0001 and a
        # 73 cm3 loft's into all zeros - a number a caller then compares against
        # CAD and reads as "0 % error" (reported 2026-10-24). Six significant
        # digits survive the whole range.
        matrix = [
            [_sig(inertia[0]), _sig(inertia[1]), _sig(inertia[2])],
            [_sig(inertia[3]), _sig(inertia[4]), _sig(inertia[5])],
            [_sig(inertia[6]), _sig(inertia[7]), _sig(inertia[8])],
        ]
        # Products of inertia come back as floating-point dust on a symmetric part
        # (-4.28e-50 was reported). Six significant digits keeps it, and a caller
        # then has to decide whether 1e-50 is an error. Anything below 1e-12 of
        # the largest principal moment is zero for every purpose this is used for.
        scale = max((abs(matrix[i][i]) for i in range(3)), default=0.0)
        if scale > 0:
            floor = 1e-12 * scale
            matrix = [[0.0 if abs(value) < floor else value for value in row]
                      for row in matrix]
        return {
            "mass_from_material_kg": round(vals[0], 6),
            "inertia_matrix_kg_m2": matrix,
            "inertia_matrix_unit": "kg*m^2",
            "inertia_matrix_about": (
                "component axes through the centre of gravity, in the document's "
                "own axis system (CATIA Inertia.GetInertiaMatrix); the diagonal is "
                "Ixx/Iyy/Izz, the off-diagonals are the products of inertia"
            ),
        }

    # ── assembly-side measurement ───────────────────────────────────────
    #
    # A Product document has no Part, so get_inertia / get_bounding_box used to
    # fail outright on an assembly - and an assembly's acceptance is exactly the
    # thing a placement script needs to check (reported 2026-10-24). The world
    # box of a component is its local box transformed by Position.GetComponents;
    # CATIA stores the three axis images first, then the translation:
    #   [ix,iy,iz, jx,jy,jz, kx,ky,kz, Tx,Ty,Tz]  ->  world = A*p + T
    # with A's COLUMNS being i, j, k.

    def _placement(self, child: Any) -> tuple[list[list[float]], list[float]]:
        # GetComponents needs the Evaluate route - the direct call fails with
        # "方法 GetComponents 失败" (12 by-ref slots).
        from catia_mcp.tools.assembly import position_components

        vals = position_components(self.conn.app, child.Position)
        if len(vals) < 12:
            raise RuntimeError(f"Position.GetComponents returned {len(vals)} values")
        matrix = [[vals[0], vals[3], vals[6]],
                  [vals[1], vals[4], vals[7]],
                  [vals[2], vals[5], vals[8]]]
        return matrix, [vals[9], vals[10], vals[11]]

    @staticmethod
    def _to_world(matrix: list[list[float]], translation: list[float],
                  point: list[float]) -> list[float]:
        return [sum(matrix[i][j] * point[j] for j in range(3)) + translation[i]
                for i in range(3)]

    def _top_level_sub_assemblies(self, doc: Any) -> list[tuple[str, Any]]:
        """``(name, nested document)`` for the top-level children that are products.

        Only the narrowed route needs this - it answers "does the same part also
        live inside a sub-assembly?" - and until 0.3.38 it answered by re-reading
        the WHOLE component list, poses included. On a narrowed call that is a
        second full pose read for a question about structure, which is why a
        narrowed call could cost MORE than the full scan it replaced (reported
        2026-10-29, §3.1: 6 309.9 ms against 4 815.1 ms on one part). Asking each
        child whether it holds products costs one call per child and reads no
        placement at all.
        """
        try:
            product = self.conn.product_of(doc)
            children = product.Products
            count = children.Count
        except Exception:  # noqa: BLE001 - no product tree, fall back below
            return self._sub_assemblies_via_rows(doc)
        found: list[tuple[str, Any]] = []
        for i in range(1, count + 1):
            try:
                child = children.Item(i)
                if child.Products.Count <= 0:
                    continue
            except Exception:  # noqa: BLE001 - a leaf, or a broken link
                continue
            try:
                nested = child.ReferenceProduct.Parent
            except Exception:  # noqa: BLE001 - no document behind it
                nested = None
            try:
                name = str(child.Name)
            except Exception:  # noqa: BLE001
                name = ""
            found.append((name, nested))
        return found

    def _sub_assemblies_via_rows(self, doc: Any) -> list[tuple[str, Any]]:
        """The expensive fallback: read every row to find the sub-assemblies.

        Kept because the product tree is not readable on every build (and a test
        connection has no `product_of` at all), and because a wrong answer here
        would silently drop the "the same part is also nested" warning.
        """
        return [(str(row.get("name") or ""), row.get("nested_doc"))
                for row in self._components_doc(doc)
                if row.get("kind") == "sub_assembly"
                and row.get("nested_doc") is not None]

    def _components_doc(self, doc: Any,
                        only: str | None = None) -> list[dict[str, Any]]:
        """Every child of the assembly with placement, part and problems.

        ``only`` filters by component name or part number BEFORE the placement is
        read. A pose costs ~70 ms on V5-6R2020 and the filter used to run after
        all of them had been read, so ``component='P24.1'`` on a 70-instance
        assembly paid ~5 s to answer a question about one instance - which is
        exactly the "5.8 s per part entry fee" an acceptance report measured on a
        16-vertex block (reported 2026-10-26). Same rule as `list_components`
        since 0.3.22: read a pose only for the components that will be reported.

        Without a filter every pose is needed, and one Evaluate per component was
        90.5 % of a 70-instance reply's overhead - 5 036 ms of 5 563 ms (reported
        2026-10-28, §5). So the unfiltered case reads them all in ONE script call
        (``batch_positions``), and falls back to the per-component reader when
        that is refused. ``self._poses_route`` says which ran, and the reply
        publishes it.
        """
        product = self.conn.product_of(doc)
        wanted = (only or "").strip().casefold() or None
        rows: list[dict[str, Any]] = []
        products = product.Products
        count = products.Count
        poses = None
        lookup_seconds = 0.0
        if wanted is None:
            try:
                from catia_mcp.tools.assembly import batch_positions

                poses = batch_positions(self.conn.app, products, count)
            except Exception:  # noqa: BLE001 - no app handle, no script route
                poses = None
            self._poses_route = "one_call" if poses is not None else "per_component"
            self._lookup_ms = None
        else:
            # The third honest value, for the third thing that happens. A
            # narrowed call reads exactly ONE pose on purpose (0.3.26 removed the
            # 5.8 s entry fee by not reading the rest), so neither "one_call" nor
            # "per_component" describes it - and leaving the key out entirely is
            # what made a caller reading reply["poses_route"] get a KeyError on
            # the narrowed path while "one_call" elsewhere meant "all of them
            # were batched" (reported 2026-10-28, P1). Same rule 0.3.32 set for
            # `sweep_route: mesh`.
            self._poses_route = "single"
        for i in range(1, count + 1):
            child = products.Item(i)
            row: dict[str, Any] = {"index": i, "name": None, "part_number": None,
                                   "matrix": None, "translation": None,
                                   "part": None, "nested_doc": None,
                                   "kind": "unknown", "problems": []}
            scan_started = time.perf_counter() if wanted is not None else 0.0
            for key, getter in (("name", lambda c: str(c.Name)),
                                ("part_number", lambda c: str(c.PartNumber))):
                try:
                    row[key] = getter(child)
                except Exception as exc:  # noqa: BLE001
                    row["problems"].append(f"{key}: {str(exc).splitlines()[0][:70]}")
            if wanted is not None:
                # Timed separately, because it is NOT free: comparing the requested
                # name against 70 components costs two COM string reads each, and
                # on a 70-instance assembly that was 303.3 ms for ONE pose read -
                # 33.5 % of the full sweep's placement cost, and 3x the 95-101 ms
                # a single unfiltered read costs. Labelling that "the one read"
                # sent the cost-hunt to the wrong place (reported 2026-10-28,
                # §5.3).
                lookup_seconds += time.perf_counter() - scan_started
                # Two cheap string reads decide; the expensive reads below only
                # happen for a component that will actually be reported.
                if (row["name"] or "").casefold() != wanted and \
                        (row["part_number"] or "").casefold() != wanted:
                    continue
            try:
                if poses is not None and poses[i - 1] is not None:
                    vals = poses[i - 1]
                    row["matrix"] = [[vals[0], vals[3], vals[6]],
                                     [vals[1], vals[4], vals[7]],
                                     [vals[2], vals[5], vals[8]]]
                    row["translation"] = [vals[9], vals[10], vals[11]]
                else:
                    row["matrix"], row["translation"] = self._placement(child)
            except Exception as exc:  # noqa: BLE001
                row["problems"].append(f"placement: {str(exc).splitlines()[0][:70]}")
            try:
                row["part"] = self.conn.part_of(child.ReferenceProduct.Parent)
                row["kind"] = "part"
            except Exception as exc:  # noqa: BLE001 - the classic dead link
                # A SUB-ASSEMBLY reached this way says "part not loaded: Active
                # document 'X' exposes no 'Part'", which reads as "the file is
                # not open" and sends the caller to catia_open_document - where
                # opening it changes nothing, because the component really is an
                # assembly. Fifth time this project mistook a Product for a Part
                # (0.3.15 add_component, 0.3.20 run_plan, 0.3.25 list_components,
                # 0.3.27 get_inertia top level); this one is one level DOWN
                # (reported 2026-10-27).
                parent = None
                try:
                    parent = child.ReferenceProduct.Parent
                except Exception:  # noqa: BLE001
                    parent = None
                if parent is not None and self._is_product_document(parent):
                    row["kind"] = "sub_assembly"
                    row["nested_doc"] = parent
                    row["problems"].append(
                        "sub-assembly, not a part: '%s' is an assembly of its own, "
                        "so it has no single main body to sweep or measure. Its "
                        "components are aggregated only where the tool says it "
                        "recurses (catia_get_inertia does); for its own box, measure "
                        "it by name: get_bounding_box(document='%s')"
                        % (getattr(parent, "Name", "<sub-assembly>"),
                           getattr(parent, "Name", "<sub-assembly>.CATProduct")))
                else:
                    row["problems"].append(
                        f"part not loaded: {str(exc).splitlines()[0][:70]}")
            rows.append(row)
        if wanted is not None:
            # How much of components_doc_ms was spent RESOLVING the name rather
            # than reading the one pose the narrowed call needs.
            self._lookup_ms = round(lookup_seconds * 1000, 1)
        return rows

    def _local_box(self, part: Any, body: Any,
                   stats: dict[str, Any] | None = None
                   ) -> tuple[list[list[float]], int]:
        """The part's own bounding corners and the number of points used.

        Uses that document's selection (not the active one) and skips
        construction geometry, which otherwise drags sketch wires into the box.

        The coordinates are read in ONE script call (measured on B30, 2026-10-26:
        335 vertices took 424 ms this way and 27.3 s one at a time - the batched
        form is 60x, not the 6x that 8 vertices suggested). ``stats`` receives
        ``cells`` and ``points_route`` so a caller can see which reader ran and
        how much work the construction filter had to do.
        """
        doc = part.Parent
        sel = doc.Selection
        refs = []
        read_stats: dict[str, Any] = {}
        nls_chars = 0
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, "Vertex")
            spa = doc.GetWorkbench("SPAWorkbench")
            swept = batch_vertex_sweep(self.conn.app, sel, spa)
            if swept is not None:
                points, cells, nls_chars = swept
                if stats is not None:
                    stats["cells"] = cells
                    stats["nls_chars"] = nls_chars
                    stats["points_route"] = "batch"
                    stats["sweep_route"] = "one_call"
                kept, _dropped = self._bounded(points)
                return kept, len(kept)
            for i in range(1, sel.Count + 1):
                cell = sel.Item(i)
                nls = nls_of(cell)
                nls_chars += len(nls)
                if is_construction(nls):
                    continue
                refs.append(cell.Reference)
            if stats is not None:
                stats["cells"] = int(sel.Count)
                # Characters of NLS path the construction filter read, summed.
                # Free (the string is already in hand) and the only per-part
                # number that measures the FILTER rather than the geometry: two
                # parts with 239 and 246 cells cost 66.4 and 30.5 ms per cell,
                # and path length is the candidate that "cell count" cannot
                # explain (reported 2026-10-27, §3.4).
                stats["nls_chars"] = nls_chars
            raw = read_measurable_points(
                self.conn.app, spa, refs,
                fallback=lambda ref: self._read_measurable_array(
                    spa.GetMeasurable(ref), "GetPoint", 3),
                stats=read_stats)
        finally:
            try:
                sel.Clear()
            except Exception:
                pass
        if stats is not None:
            stats["points_route"] = read_stats.get("route", "batch")
        points, _dropped = self._bounded(raw)
        return points, len(points)

    @staticmethod
    def _part_identity(part: Any) -> Any:
        """A cache key that is the same for every instance of one part.

        ``Document.FullName`` when the part has been saved (all instances of the
        same reference share one PartDocument), otherwise the part number plus the
        body name. Two different parts never share a key, because a wrong cache
        hit would move a component's box to another component's geometry.
        """
        doc = None
        try:
            doc = part.Parent
        except Exception:  # noqa: BLE001
            pass
        try:
            return ("file", str(doc.FullName).casefold())
        except Exception:  # noqa: BLE001 - never saved
            pass
        try:
            return ("part", str(part.Name), str(part.MainBody.Name))
        except Exception:  # noqa: BLE001
            return ("object", id(part))

    @staticmethod
    def _is_product_document(doc: Any) -> bool:
        """Whether ``doc`` is an assembly, decided by its type - not by ``.Product``.

        ``PartDocument`` answers ``.Product`` too (a stub for its single
        component), so probing the property proves nothing - the same trap that
        made ``add_component`` fail on a Part document.
        """
        try:
            kind = str(doc.Type)
        except Exception:  # noqa: BLE001
            kind = ""
        if kind:
            return "PRODUCT" in kind.upper()
        try:
            return str(doc.FullName).upper().endswith(".CATPRODUCT")
        except Exception:  # noqa: BLE001
            return False

    def _part_box_entry(self, part: Any, body: Any,
                        include_curved: bool) -> dict[str, Any]:
        """One part's local box: vertices when possible, mesh when not.

        The mesh fallback is what makes ``complete`` mean something. A fully
        curved part has no vertex at its extremum, so a vertex sweep cannot bound
        it - but its mesh can, and that box is per PART, so it costs one extra
        call for the two parts that need it, not one per instance (reported
        2026-10-26: the tool could already do this by name and made the caller do
        it instead).
        """
        stats: dict[str, Any] = {}
        try:
            points, used = self._local_box(part, body, stats)
        except Exception as exc:  # noqa: BLE001 - reported per component
            return {"error": f"local box: {str(exc).splitlines()[0][:80]}"}
        if points:
            return {"points": points, "used": used, "source": "vertices",
                    "mesh_vertices": None, "copy_note": "",
                    "cells": stats.get("cells"),
                    "nls_chars": stats.get("nls_chars"),
                    "points_route": stats.get("points_route"),
                    "sweep_route": stats.get("sweep_route")}
        if not include_curved:
            return {"error": "no vertices (fully curved part), so a vertex sweep "
                             "cannot bound it; include_curved=true (the default) "
                             "lets this tool use the part's mesh box instead"}
        try:
            cloud, mesh_vertices, copy_note = self._mesh_cloud(part)
        except Exception as exc:  # noqa: BLE001
            return {"error": "mesh fallback failed: "
                             + str(exc).splitlines()[0][:90]}
        if not cloud:
            return {"error": "no vertices and no mesh: neither route could bound "
                             "this part"}
        return {"points": cloud, "used": len(cloud) // 3,
                "source": "mesh_fallback", "mesh_vertices": mesh_vertices,
                "truncated": mesh_vertices > len(cloud) // 3,
                "copy_note": copy_note or ""}

    @staticmethod
    def _local_dimensions(points: Any, source: str) -> dict[str, float]:
        """The part's own box size, so a part detail needs no second call."""
        if source == "mesh_fallback":
            xs = points[0::3]
            ys = points[1::3]
            zs = points[2::3]
        else:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            zs = [p[2] for p in points]
        return {f"length_{k}": round(max(v) - min(v), 4)
                for k, v in zip("xyz", (xs, ys, zs))}

    def _assembly_box(self, doc: Any, method: str,
                      only: str | None = None,
                      include_curved: bool = True) -> str:
        started = time.perf_counter()
        self._poses_route = None
        self._lookup_ms = None
        rows = self._components_doc(doc, only)
        # Captured now, not read at the end: the narrowed branch re-reads the
        # component list below (to find a nested twin) and that second read would
        # overwrite the answer with "one_call".
        poses_route = self._poses_route
        if only and not rows:
            names = ", ".join(str(r["name"]) for r in self._components_doc(doc))
            raise RuntimeError(
                f"No component matches '{only}'. Components: {names or 'none'}. "
                "This matches TOP-LEVEL components only - a component that lives "
                "inside a sub-assembly has to be measured through that "
                "sub-assembly, or by naming the part's document."
            )
        # Where the time goes, in three buckets: reading the component tree,
        # walking it (which includes each part's own measurement), and building
        # this reply. `overhead_ms` below is the part of elapsed_ms that no entry
        # in `parts` accounts for; without the buckets that number is a residual
        # with no explanation, and a caller cannot tell "assembly traversal" from
        # "serialising 70 entries" (reported 2026-10-27: 5 394.6 ms, 23.6 % of the
        # warm call, unchanged for four versions).
        t_rows = time.perf_counter()

        # The walk below is recursive: a sub-assembly gets its own level with the
        # placement composed on the way down. Everything it accumulates lives in
        # this context so one part is still bounded once per identity, however
        # many levels deep it appears.
        ctx: dict[str, Any] = {
            "entries": [], "corners": [], "skipped": [], "warnings": [],
            "parts_detail": {}, "warned": set(), "local_boxes": {},
            "include_curved": include_curved,
        }
        self._box_walk(rows, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                       [0.0, 0.0, 0.0], 1, "", ctx)
        t_walk = time.perf_counter()
        entries = ctx["entries"]
        corners = ctx["corners"]
        skipped = ctx["skipped"]
        parts_detail = ctx["parts_detail"]
        local_boxes = ctx["local_boxes"]
        fallback_warnings = ctx["warnings"]

        if not corners:
            # Every component failed: return the partial result instead of an
            # exception. "Which parts could not be measured, and why" is itself
            # the answer a caller needs, and it was previously thrown away
            # (reported 2026-10-24: an assembly holding only a fully curved part
            # failed the whole call).
            #
            # An EMPTY product is not "every component failed" - there was no
            # component - but this branch said "each one's reason is in 'skipped'"
            # and handed back `skipped: []`, sending the caller to read an empty
            # array for a reason that was never recorded (reported 2026-10-28,
            # §P2). 0.3.34 answered by inventing one placeholder entry - named
            # after the ACTIVE WINDOW, so an empty assembly measured while a part
            # was in front reported `skipped[0].component = 'F35S.CATPart'`, a
            # document the call never mentioned (reported 2026-10-28, §P2), and
            # `instances.total: 1` for a product holding nothing, while
            # catia_get_inertia answered 0 for the same document.
            #
            # Both routes now say the same thing: zero instances, zero skipped,
            # and the reason in a field that is ABOUT the reason. An entry in
            # `skipped` has to mean "here is a component, and here is why its box
            # is missing" - there is no such component here.
            empty_product = not entries and not skipped
            try:
                measured_name = str(doc.Name)
            except Exception:  # noqa: BLE001 - the numbers are the point
                measured_name = "<assembly>"
            # The same cost vocabulary as a non-empty answer, even when there is
            # nothing to measure: catia_get_inertia published parts_ms /
            # overhead_ms / breakdown for an empty product while this route
            # published none, and the inertia note says "the same three buckets
            # catia_get_bounding_box publishes" (reported 2026-10-29, §6.3). The
            # numbers are tiny and irrelevant - the CONSISTENCY is not, because a
            # caller that reads the block has to branch on which tool answered.
            empty_elapsed = round((time.perf_counter() - started) * 1000, 1)
            empty_components = round((t_rows - started) * 1000, 1)
            empty_walk = round(max((t_walk - t_rows) * 1000, 0.0), 1)
            empty_report = round(max(empty_elapsed - empty_components
                                     - empty_walk, 0.0), 1)
            return json.dumps({
                "dimensions": None,
                "min": None,
                "max": None,
                "components": [],
                "complete": False,
                "empty_assembly": empty_product,
                "elapsed_ms": empty_elapsed,
                "parts_ms": 0.0,
                "overhead_ms": empty_elapsed,
                "overhead_breakdown": {
                    "components_doc_ms": empty_components,
                    "walk_other_ms": empty_walk,
                    "report_ms": empty_report,
                },
                "overhead_note": (
                    "an empty product still publishes the three buckets, so this "
                    "reply has the same keys as a measured one: parts_ms is 0 "
                    "because no part was read, and everything spent is reading the "
                    "(empty) component list and building this reply"),
                # The regime fields too, and this is the branch that proved the
                # point: 0.3.38 added them to the EMPTY INERTIA branch and left
                # this one without, so the two aggregators answered the same
                # question differently for the same document (reported
                # 2026-10-29, report A §6). One call, one key set, every route.
                **_BOOKS.describe(
                    book=BOOK_TOPOLOGY,
                    route="box.empty",
                    measured=False,
                    parts_total=0,
                    session_first=0,
                    doc_key=_document_key(doc),
                    empty_reason=(
                        "'parts_total' is 0 for the same reason 'instances.total' "
                        "is: this product holds no component, so no part's regime "
                        "is being reported"),
                ),
                **({"poses_route": poses_route,
                    "poses_route_note": _pose_note(poses_route)}
                   if poses_route else {}),
                # instances.skipped == len(skipped), on every route: this counted
                # the placeholder entry as an "instance" of an empty product,
                # while catia_get_inertia answered 0 for the same document
                # (reported 2026-10-28, §3.3).
                "instances": {"total": 0 if empty_product else len(skipped),
                              "measured": 0,
                              "skipped": 0 if empty_product else len(skipped)},
                "assembly_depth_max": max((entry.get("depth", 1)
                                           for entry in skipped), default=1),
                "skipped": [] if empty_product else skipped,
                "incomplete_because": (
                    f"'{measured_name}' holds no component, so there is nothing to "
                    "bound - 'complete: false' here means 'nothing was asked for', "
                    "not 'the measurement failed', and 'skipped' is empty because "
                    "no component was skipped"
                    if empty_product else
                    "no component could be measured from its vertices; each one's "
                    "reason is in 'skipped'"),
                "method": ("the measured product holds no component, so there is "
                           "nothing to measure - 'complete: false' here means "
                           "'nothing was asked for', not 'the measurement failed'"
                           if empty_product else
                           "no component could be measured from its vertices; each "
                           "one's reason is in 'skipped'. A fully curved part has no "
                           "vertex at any extremum, so measure it by name - "
                           "catia_get_bounding_box(document='<part>.CATPart', "
                           "method='mesh')"),
            }, indent=2)
        note = ("world coordinates: each component's local box transformed by its "
                "placement, then aggregated. A vertex sweep is exact only when "
                "every face is planar: any part whose extremum lies on a curved "
                "face is under-reported by it, and that is not limited to revolved parts"
                " - an arched or cylindrical face loses the same way "
                "(measured: a camera bracket's arch, 33.27 mm reported against "
                "41.40 mm true, -19.6 %). A part with no vertex at an extremum is "
                "bounded from its mesh instead, and its entry says "
                "source='mesh_fallback'. To bound one part on its own, open it "
                "(catia_open_document) and call "
                "catia_get_bounding_box(document='<part>.CATPart', method='mesh'). "
                "catia_get_component_transform READS an instance's placement; it "
                "does not place a box, so to move one into world coordinates read "
                "the placement (catia_list_components or "
                "catia_get_component_transform) and apply rotation/translation "
                "yourself. A sub-assembly is expanded rather than skipped: its "
                "parts are bounded as if they had been placed directly in this "
                "assembly (the two placements composed), and each entry says which "
                "level it came from with 'path' and 'depth'.")
        from_mesh = [entry["component"] for entry in entries
                     if entry.get("source") == "mesh_fallback"]
        bounds = ("lower bound: each component's box came from its vertices, so any "
                  "part whose extremum lies on a curved face is under-reported")
        if from_mesh:
            bounds = (f"mixed: {len(from_mesh)} instance(s) are bounded from their "
                      "mesh (a lower bound on curved faces, measured 0.47-0.73 % "
                      "small), the rest from vertices")
        payload = json.loads(self._bbox_json(corners, note, bounds=bounds))
        payload["components"] = entries
        # What this call cost, measured. The box is exact and single-threaded, so a
        # caller that budgets "seconds" for a 70-instance assembly waits three
        # minutes with the whole server behind it; the number makes that visible
        # instead of surprising (reported 2026-10-26).
        payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        # Which regime this call ran in, from the one implementation every route
        # shares. Two calls of the same assembly in one session differ by 4.7x
        # with every work counter identical, so the number alone cannot be
        # compared across calls - let alone across sessions (reported 2026-10-27:
        # 194.7 s, then 41.4 s, then 41.5 s). Before 0.3.39 this was 100 lines
        # written out here, again for the inertia route, again in `_part_bbox`, and
        # nowhere at all in the two empty branches and the two part routes.
        session_first = sum(1 for cached in local_boxes.values()
                            if cached.get("first_measure"))
        cold_reasons: dict[str, int] = {}
        for cached in local_boxes.values():
            if not cached.get("first_measure"):
                continue
            reason = (MESH_EXPORT if cached.get("source") == "mesh_fallback"
                      else TOPOLOGY)
            cold_reasons[reason] = cold_reasons.get(reason, 0) + 1
        payload["part_boxes_total"] = len(local_boxes)
        parts_ms = round(sum(float(entry.get("elapsed_ms") or 0.0)
                             for entry in parts_detail.values()), 1)
        payload["parts_ms"] = parts_ms
        payload["overhead_ms"] = round(max(payload["elapsed_ms"] - parts_ms, 0.0), 1)
        payload.update(_BOOKS.describe(
            book=BOOK_TOPOLOGY,
            route="box.assembly",
            measured=True,
            parts_total=len(local_boxes),
            session_first=session_first,
            cold_reasons=cold_reasons,
            doc_key=_document_key(doc),
            elapsed_ms=payload["elapsed_ms"],
            # What this call measured, so a delta is only quoted against a call
            # of the SAME shape: `component='P05.1'` (1 instance) against the
            # whole 70-instance assembly read as -67 % and meant nothing
            # (reported 2026-10-29, §5.3).
            scope=(only or "", len(entries)),
            # The one sentence only this route can write: `warm_up` is a bounding
            # box argument, so the advice belongs to this reply and not to the
            # shared note.
            tail=("warm_up=true pays this cost in a call that does not need "
                  "the box."),
        ))
        try:
            payload["document"] = str(doc.Name)
        except Exception:  # noqa: BLE001 - the numbers are the point
            pass
        payload["part_boxes"] = len(local_boxes)
        # The route actually taken, per PART, as numbers a caller can assert on.
        # 0.3.23 only said it in the `bounds` sentence, so "how many curved parts
        # should this assembly have" could not be checked without parsing prose
        # (reported 2026-10-26).
        by_route = {"vertices": 0, "mesh_fallback": 0}
        for cached in local_boxes.values():
            source = cached.get("source")
            if source in by_route:
                by_route[source] += 1
        payload["part_boxes_by_vertices"] = by_route["vertices"]
        payload["part_boxes_by_mesh"] = by_route["mesh_fallback"]
        # Per PART, once: the counts and caveats are properties of the part, and
        # repeating them on every instance entry meant 70 entries carried 28
        # distinct values (reported 2026-10-26). The instance entries keep the
        # geometry; this list keeps the cost model and the caveats.
        payload["parts"] = list(parts_detail.values())
        # The cost no part owns. A caller adding up `parts[].elapsed_ms` to find
        # "which part eats the call" was working with 76.4 % of the number: on the
        # 70-instance assembly the difference was 5 394.6 ms of 22 875.1, and it
        # would have been the largest single item in that table - except it was not
        # in the table (reported 2026-10-27). Published, with the phases, so the
        # attribution is measured rather than guessed.
        # The three buckets must ADD UP to overhead_ms, not merely look like it:
        # rounding each of them to 0.1 ms made the sum 0.1-0.2 ms larger than the
        # total on every sample (reported 2026-10-28, §P3), in the one reply that
        # promises "reconciling to the millisecond". So the last bucket absorbs
        # the remainder instead of being rounded on its own, and if rounding ever
        # pushed the sum past the total, the excess comes off the largest bucket.
        components_doc = round((t_rows - started) * 1000, 1)
        walk_other = round((t_walk - t_rows) * 1000 - parts_ms, 1)
        report_ms = round(payload["overhead_ms"] - components_doc - walk_other, 1)
        if report_ms < 0:
            walk_other = round(walk_other + report_ms, 1)
            report_ms = 0.0
        payload["overhead_breakdown"] = {
            "components_doc_ms": components_doc,
            "walk_other_ms": walk_other,
            "report_ms": report_ms,
        }
        if poses_route == "single" and self._lookup_ms is not None:
            # Published BESIDE the breakdown, not inside it. 0.3.36 put it in the
            # dict and said in the same release that "the three terms still add up
            # to overhead_ms exactly" - true of those three, false of the dict,
            # which now had four keys: a caller doing the documented
            # `sum(overhead_breakdown.values())` silently over-counted by the
            # lookup time (reported 2026-10-28, P3). A sub-bucket inside a block
            # whose whole contract is "these add up" cannot be made safe by
            # renaming it, because sum() does not read names.
            payload["component_lookup_ms"] = self._lookup_ms
        if poses_route:
            # Which way the 12 numbers per instance were read: one script call
            # for the whole assembly, one COM round trip each, or exactly one
            # because the call was narrowed. Reading `self._poses_route` here
            # would be wrong - a narrowed reply reads it again (unfiltered) to
            # find the nested twin below.
            payload["poses_route"] = poses_route
            payload["poses_route_note"] = _pose_note(poses_route)
        payload["overhead_note"] = (
            "elapsed_ms minus the sum of parts[].elapsed_ms: reading the component "
            "tree, transforming every instance's points into world coordinates, "
            "counting instances per part, and building this document. It is not "
            "attributable to any entry in 'parts', so a share-of-total table built "
            "from 'parts' alone is short by this much. 'overhead_breakdown' splits "
            "it into the three phases it was measured in; 'report_ms' is what is "
            "left after the other two, so the three add up to overhead_ms exactly - "
            "summing this dict is safe, and it has exactly three keys. "
            "'component_lookup_ms' (when 'poses_route' is 'single') is a SIBLING of "
            "this block, not a fourth term: it is the part of 'components_doc_ms' "
            "spent matching the name you passed, so subtract it from that bucket - "
            "adding it to the three double-counts. "
            "Which phase dominates is a property of the CALL, not of the model: "
            "'components_doc_ms' is reading each instance's placement, so the "
            "instance count drives it, and 'poses_route' says whether those reads "
            "were one script call, one round trip per instance, or a single read "
            "because 'component' narrowed the call. In 0.3.33 and earlier the "
            "per-instance round trips made it 90.5 % of this block (5 036 ms on a "
            "70-instance assembly); read the two phases THIS call reports rather "
            "than the number in that sentence")
        # `component=` narrows to TOP-LEVEL components. When the same part is ALSO
        # installed inside a sub-assembly, the reply used to say complete: true
        # with instances {total: 1} while its own note_scope claimed sub-assemblies
        # are expanded - a narrowed answer wearing the whole assembly's
        # completeness, and three statements contradicting each other in one reply
        # (reported 2026-10-27). Find what the narrowing left out and say it.
        also_nested: list[dict[str, Any]] = []
        if only:
            for name, nested in self._top_level_sub_assemblies(doc):
                if nested is not None:
                    also_nested.extend(self._nested_matches(
                        nested, only, 2, name))
            for match in also_nested:
                skipped.append({
                    "component": only.strip(),
                    "name": match["path"].split("/")[-1],
                    "path": match["path"],
                    "depth": match["depth"],
                    "kind": "part",
                    "error": (f"nested instance of '{only}', NOT included because "
                              "component= narrows to TOP-LEVEL components - the "
                              "box is that of the top-level instance only"),
                })
        if only:
            payload["scope"] = "top_level_only"
            payload["note_scope"] = (
                f"narrowed to the TOP-LEVEL instance(s) of '{only}': "
                f"{len(entries)} measured. Instances of the same part inside "
                "sub-assemblies are NOT part of this box"
                + (f" - {len(also_nested)} of them exist and are listed in "
                   "'also_nested' / 'skipped', so this answer is NOT the whole "
                   "part's envelope" if also_nested else
                   " (none exist at the time of this call)")
                + ".")
            if also_nested:
                payload["also_nested"] = also_nested
        else:
            payload["note_scope"] = (
                f"{len(entries)} instance(s) of {len(local_boxes)} part(s): each "
                "part's local box is computed once and only the placement is "
                "repeated, so the cost is per part, not per instance. Per-part "
                "counts are in 'parts', not on every instance entry. A "
                "sub-assembly is EXPANDED, so its parts appear with their 'path' "
                "('ASM/PART.1') and 'depth', and both counts include them.")
        # Say whether the box covers the whole assembly. 'skipped' sat beside
        # min/max as a sibling key, so a caller reading only the numbers never
        # learned that 6 of 70 instances were left out and the box is a lower
        # bound in the directions they could have driven (reported 2026-10-25).
        payload["complete"] = not skipped
        payload["include_curved"] = include_curved
        if from_mesh:
            # Parts, not instances: `warnings` is per part, and two fields in one
            # reply counting different things made 6-vs-2 look like a defect
            # (reported 2026-10-26). The per-instance answer is on each entry
            # (`source`) and in the count beside it.
            payload["mesh_fallback"] = sorted(set(from_mesh))
            payload["mesh_fallback_instances"] = len(from_mesh)
        if fallback_warnings:
            payload["warnings"] = fallback_warnings
        payload["instances"] = {"total": len(entries) + len(skipped),
                                "measured": len(entries),
                                "skipped": len(skipped)}
        if skipped:
            payload["skipped"] = skipped
            if only and also_nested:
                payload["incomplete_because"] = (
                    f"component= narrows to TOP-LEVEL components, and {len(also_nested)} "
                    f"further instance(s) of '{only}' live inside sub-assemblies "
                    "(listed in 'also_nested'). Those are NOT in this box, so it is "
                    "the envelope of the top-level instance(s) only - not of the "
                    "part everywhere in this assembly. To get them: measure each "
                    "sub-assembly by name (get_bounding_box(document='<sub>.CATProduct')) "
                    "or measure the whole assembly without 'component'.")
            else:
                payload["incomplete_because"] = (
                    f"{len(skipped)} of {len(entries) + len(skipped)} instance(s) could "
                    "not be bounded at all - neither their vertices nor (with "
                    "include_curved=true) their mesh gave a box, so this box is a LOWER "
                    "BOUND in any direction they extend. What to try: read each "
                    "instance's placement with catia_list_components or "
                    "catia_get_component_transform, measure the part on its own with "
                    "get_bounding_box(document='<part>.CATPart', method='mesh'), and "
                    "apply the rotation/translation yourself."
                )
        depths = [entry["depth"] for entry in entries] + [entry["depth"] for entry in skipped]
        payload["assembly_depth_max"] = max(depths, default=1)
        # A sub-assembly is ONE entry in 'instances' until it is expanded, and two
        # of them can hide twenty parts - so say which counting rule produced the
        # numbers, instead of letting 2 mean "two parts" (reported 2026-10-27).
        payload["instances_note"] = (
            "counts COMPONENTS at any level: a nested part's instance is one entry "
            "(it carries 'path' and 'depth'), and a sub-assembly that could NOT be "
            "expanded counts as one, not as the instances it would have added")
        return json.dumps(_cost_first(payload), indent=2)

    _BOX_MAX_DEPTH = 8

    def _nested_matches(self, doc: Any, only: str, depth: int = 2,
                        prefix: str = "") -> list[dict[str, Any]]:
        """Instances of ``only`` that live BELOW the top level, with their paths.

        ``component=`` narrows to TOP-LEVEL components, and when the same part
        number is ALSO installed inside a sub-assembly the narrowed reply used to
        say `complete: true` with `instances: {total: 1}` - a partial answer
        wearing the whole assembly's completeness, which is the same sin as the
        0.3.28 partial sum (reported 2026-10-27). This finds the ones the
        narrowing left out so the reply can name them.
        """
        wanted = only.strip().casefold()
        found: list[dict[str, Any]] = []
        try:
            rows = self._components_doc(doc)
        except Exception:  # noqa: BLE001 - a reply is still worth sending
            return found
        for row in rows:
            name = str(row["name"] or "")
            label = str(row["part_number"] or name)
            path = f"{prefix}/{name}" if prefix else name
            if name.casefold() == wanted or label.casefold() == wanted:
                found.append({"path": path, "depth": depth})
            nested = row.get("nested_doc")
            if (row.get("kind") == "sub_assembly" and nested is not None
                    and depth < self._BOX_MAX_DEPTH):
                found.extend(self._nested_matches(nested, only, depth + 1, path))
        return found

    def _box_walk(self, rows: list[dict[str, Any]], matrix: list[list[float]],
                  translation: list[float], depth: int, prefix: str,
                  ctx: dict[str, Any]) -> None:
        """One level of the world box, recursing into sub-assemblies.

        ``matrix``/``translation`` are the placement accumulated on the way down,
        so a part two levels below is placed by the composition of both - the same
        rule the inertia route follows. Until 0.3.29 a sub-assembly was dropped
        here with "part not loaded" and the reply said `complete: false`: honest,
        but it left a nested assembly unbounded while `get_inertia` on the same
        document had started expanding it (reported 2026-10-27). Both aggregators
        now understand the same structure.

        A part is still bounded ONCE per identity, however many levels it appears
        at: the local box does not depend on the placement, so the cache is shared
        across the whole walk.
        """
        entries = ctx["entries"]
        corners = ctx["corners"]
        skipped = ctx["skipped"]
        parts_detail = ctx["parts_detail"]
        local_boxes = ctx["local_boxes"]
        warned = ctx["warned"]
        fallback_warnings = ctx["warnings"]
        include_curved = ctx["include_curved"]
        for row in rows:
            label = row["part_number"] or row["name"] or f"#{row['index']}"
            name = row["name"] or label
            path = f"{prefix}/{name}" if prefix else name
            if row["matrix"] is None or row["translation"] is None:
                skipped.append({"component": label, "name": name, "path": path,
                                "depth": depth,
                                "kind": row.get("kind", "unknown"),
                                "error": "; ".join(row["problems"])
                                         or "its placement could not be read"})
                continue
            # A sub-assembly's own placement goes ON TOP of the accumulated one.
            world_matrix = self._mat_mul(matrix, row["matrix"])
            world_t = [sum(matrix[i][k] * row["translation"][k] for k in range(3))
                       + translation[i] for i in range(3)]
            if row.get("kind") == "sub_assembly":
                nested = row.get("nested_doc")
                if nested is None:
                    skipped.append({
                        "component": label, "name": name, "path": path,
                        "depth": depth, "kind": "sub_assembly",
                        "error": "sub-assembly, but its document could not be read, "
                                 "so its components are not in this box"})
                    continue
                if depth >= self._BOX_MAX_DEPTH:
                    skipped.append({
                        "component": label, "name": name, "path": path,
                        "depth": depth, "kind": "sub_assembly",
                        "error": f"nested deeper than {self._BOX_MAX_DEPTH} levels, "
                                 "so this branch was not expanded"})
                    continue
                sub_rows = self._components_doc(nested)
                if not sub_rows:
                    skipped.append({
                        "component": label, "name": name, "path": path,
                        "depth": depth, "kind": "sub_assembly",
                        "error": "the sub-assembly holds no components, so it "
                                 "contributes nothing to this box"})
                    continue
                self._box_walk(sub_rows, world_matrix, world_t, depth + 1, path, ctx)
                continue
            if row["part"] is None:
                skipped.append({"component": label, "name": name, "path": path,
                                "depth": depth,
                                "kind": row.get("kind", "unknown"),
                                "error": "; ".join(row["problems"])})
                continue
            body = row["part"].MainBody
            key = self._part_identity(row["part"])
            if key not in local_boxes:
                part_started = time.perf_counter()
                first_measure = _BOOKS.is_new(BOOK_TOPOLOGY, key)
                local_boxes[key] = self._part_box_entry(row["part"], body,
                                                        include_curved)
                local_boxes[key]["first_measure"] = first_measure
                if "error" not in local_boxes[key]:
                    # The reason is only known once the route is: a part bounded
                    # from its mesh pays for an STL EXPORT, not for a topology
                    # build, and that is the whole difference between the parts
                    # that read 21x on their second call and the ones that read
                    # 0.97x (reported 2026-10-29, §5.2). Recording it at the
                    # moment of the reading, not later from a table of sizes.
                    _BOOKS.note(
                        BOOK_TOPOLOGY, key,
                        MESH_EXPORT if local_boxes[key].get("source") == "mesh_fallback"
                        else TOPOLOGY)
                # What this part actually cost, once. `cells_scanned` is the work
                # estimate; this is the measured price of it, which is the number
                # a caller budgets with - 1 207 cells and 24 cells differ by 40x
                # and the estimate alone does not say which part to fix
                # (reported 2026-10-26).
                elapsed_ms = round((time.perf_counter() - part_started) * 1000, 1)
                local_boxes[key]["elapsed_ms"] = elapsed_ms
                # Keep the two regimes apart per PART, not just per call: the
                # assembly-level cold/warm ratio is a weighted sum, so quoting it
                # as "CATIA's cold factor" hid that a 1207-cell part pays 15x and a
                # 12-cell one 1.3x (reported 2026-10-28, §2.4).
                _BOOKS.record_time(BOOK_TOPOLOGY, key, elapsed_ms, first_measure)
            cached = local_boxes[key]
            if "error" in cached:
                skipped.append({"component": label, "name": name, "path": path,
                                "depth": depth, "kind": "part",
                                "error": cached["error"]})
                continue
            if cached["source"] == "mesh_fallback":
                # Transform EVERY mesh vertex, not the local box's two corners.
                # The corners are a diagonal; rotate a diagonal and it becomes a
                # line with almost no extent across it, so the aggregated box came
                # out too narrow in one axis and too wide in another while still
                # claiming complete:true (reported 2026-10-26: a disc whose true
                # world box is ±72.5/±72.5 read as ±100.29/±21.32).
                entry_min, entry_max = self._world_extent(
                    cached["points"], world_matrix, world_t)
                world = [[entry_min[0], entry_min[1], entry_min[2]],
                         [entry_max[0], entry_max[1], entry_max[2]]]
            else:
                world = [self._to_world(world_matrix, world_t, p)
                         for p in cached["points"]]
                xs = [p[0] for p in world]
                ys = [p[1] for p in world]
                zs = [p[2] for p in world]
                entry_min = [min(xs), min(ys), min(zs)]
                entry_max = [max(xs), max(ys), max(zs)]
            entry = {
                "component": label,
                "name": name,
                "path": path,
                "depth": depth,
                "min": {k: round(v, 4) for k, v in zip("xyz", entry_min)},
                "max": {k: round(v, 4) for k, v in zip("xyz", entry_max)},
                "dimensions": {f"length_{k}": round(mx - mn, 4)
                               for k, mn, mx in zip("xyz", entry_min, entry_max)},
                "source": cached["source"],
            }
            if key not in parts_detail:
                # Per PART, from the part's own local box: the vertex count, the
                # cell count the construction filter walked, the mesh caveat.
                # These are properties of the PART, and on a 70-instance assembly
                # the instance entries repeated 28 distinct values 70 times
                # (reported 2026-10-26: "20 multi-instance parts answer the same
                # thing on every entry").
                detail: dict[str, Any] = {
                    "part": label,
                    "source": cached["source"],
                    "instances": 0,
                    "local_dimensions": self._local_dimensions(cached["points"],
                                                               cached["source"]),
                }
                if cached.get("elapsed_ms") is not None:
                    detail["elapsed_ms"] = cached["elapsed_ms"]
                # This part's own two regimes, when both have been seen. The
                # aggregate ratio is a weighted sum; these are the terms
                # (reported 2026-10-28, §2.4: 1.30x to 15.10x inside one session).
                detail.update(_BOOKS.time_of(BOOK_TOPOLOGY, key))
                if "cold_warm_ratio" in detail:
                    detail["cold_warm_note"] = _COLD_RATIO_NOTE
                # WHY this part's first box cost what it did. The ratio alone
                # cannot say it: 21x is an STL export and 1.0x is a vertex sweep
                # (reported 2026-10-29, §5.2 - two mesh-bounded parts among 28
                # made the aggregate count mean something it did not).
                reason = _BOOKS.reason_of(BOOK_TOPOLOGY, key)
                if reason:
                    detail["cold_reason"] = reason
                # Which of the two regimes this number came from. Without it a
                # caller compares 80 s with 11 s for the same part and the same
                # work, and concludes the tool got 7x faster (reported 2026-10-27).
                if cached.get("first_measure") is not None:
                    detail["first_measure"] = cached["first_measure"]
                if cached["source"] == "mesh_fallback":
                    detail["mesh_vertices"] = cached["mesh_vertices"]
                    detail["points_route"] = "mesh"
                    # No vertex sweep ran for this part, and saying nothing would
                    # make the key set differ per part again. "mesh" is the third
                    # honest value of the same field.
                    detail["sweep_route"] = "mesh"
                    detail["source_note"] = (
                        "no vertex lies at this part's extremum, so its box came "
                        "from the mesh - a lower bound on curved faces, measured "
                        "0.47-0.73 % small"
                        + ("; the cloud was capped at %d of %d vertices"
                           % (cached["used"], cached["mesh_vertices"])
                           if cached.get("truncated") else "") + ".")
                else:
                    detail["vertices_used"] = cached["used"]
                    if cached.get("cells") is not None:
                        detail["cells_scanned"] = cached["cells"]
                        detail["cells_note"] = (
                            "topology cells the vertex search returned for this "
                            "part's body, BEFORE construction geometry was filtered "
                            "out (sketch-wire vertices, datum-plane corners) - a "
                            "count of CATIA cells, not of mesh elements")
                    if cached.get("nls_chars") is not None:
                        detail["nls_chars"] = cached["nls_chars"]
                        detail["nls_chars_note"] = _NLS_NOTE
                    detail["points_route"] = _route_name(cached.get("points_route"))
                    # Which sweep reader ran for this part: one script call inside
                    # CATIA, or one COM round trip per topology cell. It was
                    # published on the single-part route only, so "read
                    # sweep_route to see which route ran" was false exactly where
                    # it mattered - the 70-instance assembly (reported 2026-10-27).
                    detail["sweep_route"] = cached.get("sweep_route") or "per_cell"
                parts_detail[key] = detail
            parts_detail[key]["instances"] += 1
            if cached["copy_note"] and key not in warned:
                # Once per PART: four instances of one faceplate used to repeat
                # the same 180-character sentence four times. The note is written
                # to be appended to a sentence, so its leading "; " is dropped
                # here - "drone fin capp: ; measured from a copy" read as a typo.
                warned.add(key)
                fallback_warnings.append(
                    f"{label}: " + cached["copy_note"].lstrip("; ").strip())
                base = parts_detail[key].get("source_note") or ""
                parts_detail[key]["source_note"] = (
                    (base + " " if base else "")
                    + "It also came from the SAVED file rather than the loaded "
                    "state: " + cached["copy_note"].lstrip("; ").strip())
            entries.append(entry)
            corners.extend(world)

    def _document_kind(self) -> str:
        """'part', 'assembly' or 'other' for the active document."""
        doc = self.conn.active_document
        try:
            self.conn.part_of(doc)
            return "part"
        except Exception:  # noqa: BLE001 - not a PartDocument
            pass
        try:
            self.conn.product_of(doc)
            return "assembly"
        except Exception:  # noqa: BLE001
            return "other"

    def _assembly_inertia(self, density: float | None = None,
                          doc: Any | None = None) -> str:
        """Volume, area and centre of gravity of an assembly, per component.

        Each component's own volume / area / COG are read from its part
        document and moved into world space by its placement; the totals are the
        volume-weighted sums. That is the number a placement script has to hit,
        and on a Product document it used to be unavailable (get_inertia
        required a Part).

        A SUB-ASSEMBLY is not a Part, and until 0.3.28 a component that was one
        was dropped with "part not loaded" while the totals kept the assembly's
        name - a two-level assembly holding 60 000 + 60 000 mm3 reported 60 000
        and a COG at the wrong end, with nothing in the reply saying so
        (reported 2026-10-27). It is aggregated now: the recursion composes the
        two placements, then every leaf is added up as if it had been placed
        directly. The totals also carry `complete` / `instances` /
        `incomplete_because`, the same vocabulary the bounding box uses, so a
        partial sum cannot be read as a total.

        ``doc`` names the assembly to measure; without it the active document is
        used (the older behaviour, and the only one before 0.3.27).
        """
        started = time.perf_counter()
        doc = doc if doc is not None else self.conn.active_document
        leaves: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        part_cache: dict[Any, Any] = {}
        # Same three buckets the bounding box publishes, accumulated during the
        # walk: reading the component tree (poses included), reading each part's
        # mass properties, and everything else. The box route has had a full cost
        # account since 0.3.34, so the two aggregators on ONE assembly could not be
        # compared - "4.2 s" with no breakdown, next to a box call that says where
        # every millisecond went (reported 2026-10-28, §5.6).
        cost = {"components_doc": 0.0, "part_props": 0.0}
        # Per PART (not per instance): what its mass properties cost and which
        # regime that reading came from. `leaves` answers "where is this instance";
        # this answers "which part is expensive", which is the question 28 parts
        # repeated 70 times cannot answer.
        parts_detail: dict[Any, dict[str, Any]] = {}
        self._poses_route = None
        self._inertia_collect(doc, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                                    [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0],
                              1, "", leaves, skipped, part_cache, cost,
                              parts_detail)
        poses_route = self._poses_route
        t_walk = time.perf_counter()
        total = sum(entry["volume_mm3"] for entry in leaves)
        total_area = sum(entry["area_mm2"] for entry in leaves)
        acc = [0.0, 0.0, 0.0]
        for entry in leaves:
            cog = entry["center_of_gravity_mm"]
            for i, axis in enumerate("xyz"):
                acc[i] += entry["volume_mm3"] * cog[axis]
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        parts_ms = round(cost["part_props"] * 1000, 1)
        components_doc = round(cost["components_doc"] * 1000, 1)
        computed = round((t_walk - started) * 1000, 1)
        walk_other = round(max(computed - components_doc - parts_ms, 0.0), 1)
        overhead_ms = round(max(elapsed - parts_ms, 0.0), 1)
        report_ms = round(max(overhead_ms - components_doc - walk_other, 0.0), 1)
        cost_fields: dict[str, Any] = {
            "parts_ms": parts_ms,
            "overhead_ms": overhead_ms,
            "overhead_breakdown": {
                "components_doc_ms": components_doc,
                "walk_other_ms": walk_other,
                "report_ms": report_ms,
            },
            "overhead_note": (
                "the same three buckets catia_get_bounding_box publishes, so the "
                "two aggregators can be compared on one assembly: "
                "components_doc_ms is reading the component tree (placements "
                "included), walk_other_ms is transforming each leaf and aggregating, "
                "report_ms is what is left. parts_ms is the parts' own mass "
                "properties, cached per part, so multiple instances of one part "
                "count once. walk_other_ms is small HERE by nature, not by "
                "optimisation: this route moves one centroid per instance, while "
                "the same assembly's bounding box moves every part's whole vertex "
                "cloud into world coordinates - 9.4 ms against 463-486 ms for one "
                "70-instance assembly on B30 on 2026-10-28. Read the "
                "two blocks' walk_other_ms as different jobs, not as one job done "
                "faster"),
        }
        if poses_route:
            cost_fields["poses_route"] = poses_route
            cost_fields["poses_route_note"] = _pose_note(poses_route)
        # Which regime this call ran in, and which part is expensive: the box
        # route has answered both since 0.3.34 / 0.3.36 and this one answered
        # neither, so "is this inertia number cold or warm" had no answer on the
        # route that reads every part's mass properties - the exact question
        # after_cold exists for (reported 2026-10-29, §5.1).
        inertia_parts = list(parts_detail.values())
        session_first = sum(1 for entry in inertia_parts
                            if entry.get("first_measure"))
        cold_reasons: dict[str, int] = {}
        for entry in inertia_parts:
            if entry.get("first_measure"):
                reason = entry.get("cold_reason") or MASS_PROPERTIES
                cold_reasons[reason] = cold_reasons.get(reason, 0) + 1
        cost_fields["parts_total"] = len(inertia_parts)
        cost_fields["parts"] = inertia_parts
        try:
            doc_name = str(doc.Name)
        except Exception:  # noqa: BLE001 - the numbers are the point
            doc_name = None
        depth_max = max((entry["depth"] for entry in leaves), default=1)
        instances = {"total": len(leaves) + len(skipped),
                     "measured": len(leaves), "skipped": len(skipped)}

        if total <= 0:
            # Same shape as the bounding-box path, because a script that measures
            # volume first used to be stopped dead here while the box call happily
            # returned "which component, and why" (reported 2026-10-24: the tool
            # that needed the diagnosis most was the one that threw it away).
            #
            # "No component at all" is not "every component failed", and the two
            # must not be told apart by counting entries: the box route said
            # `instances.total: 1` with a placeholder in `skipped`, this one said
            # 0, for the same document in the same second (reported 2026-10-28,
            # §3.3). Both now say 0 instances, 0 skipped, and the reason in
            # `empty_assembly` + `incomplete_because`.
            empty_product = not leaves and not skipped
            return json.dumps({
                "volume_mm3": None,
                "volume_cm3": None,
                "area_mm2": None,
                "area_cm2": None,
                "center_of_gravity_mm": None,
                "components": [],
                "complete": False,
                "empty_assembly": empty_product,
                "instances": ({"total": 0, "measured": 0, "skipped": 0}
                              if empty_product else instances),
                "assembly_depth_max": depth_max,
                "elapsed_ms": elapsed,
                **cost_fields,
                **_BOOKS.describe(
                    book=BOOK_MASS,
                    route="inertia.empty",
                    measured=False,
                    parts_total=0,
                    session_first=0,
                    doc_key=doc_name or "",
                    empty_reason=(
                        "'parts_total' is 0 because this product holds no "
                        "component, so no part's regime is being reported"),
                ),
                **({"document": doc_name} if doc_name else {}),
                "skipped": [] if empty_product else skipped,
                "incomplete_because": (
                    f"'{doc_name or 'the active document'}' holds no component, so "
                    "there is no total - 'complete: false' here means 'nothing was "
                    "asked for', not 'the measurement failed', and 'skipped' is "
                    "empty because no component was skipped"
                    if empty_product else
                    "no component could be measured, so there is no total - not "
                    "even a partial one. A component without a solid "
                    "(surface-only, or an empty part) has no volume to add; each "
                    "reason is in 'skipped'."),
                "note": ("nothing was measured, so every total is null rather than "
                         "0 - a 0 here would read as 'this assembly weighs "
                         "nothing'."),
            }, indent=2)

        payload: dict[str, Any] = {
            "volume_mm3": round(total, 4),
            "volume_cm3": round(total / 1000.0, 4),
            "area_mm2": round(total_area, 4),
            "area_cm2": round(total_area / 100.0, 4),
            "center_of_gravity_mm": {k: round(acc[i] / total, 4)
                                     for i, k in enumerate("xyz")},
            "complete": not skipped,
            "instances": instances,
            "assembly_depth_max": depth_max,
            "elapsed_ms": elapsed,
            # Cost model before the 70-entry component list, for the reason
            # `_cost_first` exists: a truncated reply used to lose exactly the
            # fields that say which regime produced the number (reported
            # 2026-10-27). Same order here, since this route now carries them.
            **cost_fields,
            **_BOOKS.describe(
                book=BOOK_MASS,
                route="inertia.assembly",
                measured=True,
                parts_total=len(inertia_parts),
                session_first=session_first,
                cold_reasons=cold_reasons,
                doc_key=doc_name or "",
                elapsed_ms=elapsed,
                # What this call measured: the whole assembly, always - this route
                # has no `component=` narrowing - so the scope only has to record
                # how many instances it walked, which is what makes two calls of
                # different assemblies (or of the same one before and after an
                # insert) non-comparable rather than a delta.
                scope=("", instances["total"]),
            ),
            "components": leaves,
            "note": ("volume-weighted aggregation of the components' own volumes "
                     "and centres of gravity, moved into world space by each "
                     "component's placement. Surface area is summed (it is "
                     "additive for disjoint solids). A sub-assembly is expanded: "
                     "its leaves are reported with their path ('ASM/PART.1') and "
                     "its placement composed with theirs, so 'components' sums to "
                     "the totals. The inertia matrix is not aggregated (it would "
                     "need each part's tensor rotated by its placement)"),
        }
        if doc_name:
            payload["document"] = doc_name
        if density:
            payload["mass_kg"] = round(density * total / 1e9, 6)
            payload["mass_g"] = round(density * total / 1e6, 3)
            payload["density_kg_m3"] = density
        if skipped:
            payload["skipped"] = skipped
            payload["incomplete_because"] = (
                f"{len(skipped)} of {instances['total']} component(s) could not be "
                "measured, so volume_mm3, area_mm2 and center_of_gravity_mm are the "
                "sum of the ones that WERE measured - a PARTIAL result, not this "
                "assembly's total. What to try: read each reason in 'skipped' (a "
                "sub-assembly that could not be expanded says so), measure that "
                "component on its own with catia_get_inertia(document=...), and add "
                "it in yourself.")
        return json.dumps(payload, indent=2)

    _INERTIA_MAX_DEPTH = 8

    def _inertia_collect(self, doc: Any, matrix: list[list[float]],
                         translation: list[float], depth: int, prefix: str,
                         leaves: list[dict[str, Any]],
                         skipped: list[dict[str, Any]],
                         part_cache: dict[Any, Any],
                         cost: dict[str, float] | None = None,
                         parts_detail: dict[Any, dict[str, Any]] | None = None
                         ) -> None:
        """Flatten an assembly into world-space leaves, recursing into sub-assemblies.

        ``matrix``/``translation`` are the accumulated placement of the assembly
        being walked, so a leaf two levels down is placed by the composition of
        both, which is what makes a nested assembly's COG land where its parent
        put it.

        ``cost`` accumulates where the time went, so this route can publish the
        same three buckets as the box route.
        """
        read_started = time.perf_counter()
        rows = self._components_doc(doc)
        if cost is not None:
            cost["components_doc"] += time.perf_counter() - read_started
        if not rows and depth > 1:
            skipped.append({"component": f"<sub-assembly {prefix}>", "kind":
                            "sub_assembly", "path": prefix, "depth": depth,
                            "error": "the sub-assembly holds no components, so it "
                                     "contributes nothing to the totals"})
            return
        for row in rows:
            label = row["part_number"] or row["name"] or f"#{row['index']}"
            name = row["name"] or label
            path = f"{prefix}/{name}" if prefix else name
            if row["matrix"] is None or row["translation"] is None:
                skipped.append({"component": label, "path": path, "depth": depth,
                                "kind": row.get("kind", "unknown"),
                                "error": "; ".join(row["problems"])
                                         or "its placement could not be read"})
                continue
            world_matrix = self._mat_mul(matrix, row["matrix"])
            world_t = [sum(matrix[i][k] * row["translation"][k] for k in range(3))
                       + translation[i] for i in range(3)]
            if row.get("kind") == "sub_assembly":
                nested = row.get("nested_doc")
                if nested is None:
                    skipped.append({"component": label, "path": path,
                                    "depth": depth, "kind": "sub_assembly",
                                    "error": "sub-assembly, but its document could "
                                             "not be read, so its components are not "
                                             "in these totals"})
                    continue
                if depth >= self._INERTIA_MAX_DEPTH:
                    skipped.append({"component": label, "path": path,
                                    "depth": depth, "kind": "sub_assembly",
                                    "error": f"nested deeper than "
                                             f"{self._INERTIA_MAX_DEPTH} levels, so "
                                             "this branch was not expanded"})
                    continue
                self._inertia_collect(nested, world_matrix, world_t, depth + 1,
                                      path, leaves, skipped, part_cache, cost,
                                      parts_detail)
                continue
            if row["part"] is None:
                skipped.append({"component": label, "path": path, "depth": depth,
                                "kind": row.get("kind", "unknown"),
                                "error": "; ".join(row["problems"])})
                continue
            props_started = time.perf_counter()
            props = self._part_mass_properties(row["part"], part_cache)
            props_ms = round((time.perf_counter() - props_started) * 1000, 1)
            if cost is not None:
                cost["part_props"] += time.perf_counter() - props_started
            if "error" in props:
                skipped.append({"component": label, "path": path, "depth": depth,
                                "kind": "part", "error": props["error"]})
                continue
            if parts_detail is not None:
                self._note_inertia_part(row["part"], label, props, props_ms,
                                        parts_detail)
            world = self._to_world(world_matrix, world_t, props["cog"])
            leaves.append({
                "component": label,
                "name": name,
                "path": path,
                "depth": depth,
                "volume_mm3": round(props["volume"], 4),
                "area_mm2": round(props["area"], 4),
                "center_of_gravity_mm": {k: round(v, 4)
                                         for k, v in zip("xyz", world)},
            })

    def _mass_property_regime(self, part: Any, *, route: str,
                              payload: dict[str, Any],
                              elapsed_ms: float) -> dict[str, Any]:
        """The regime fields for a ONE-part mass-properties call.

        The two part routes (`document='X'` and the active part) published
        `elapsed_ms`, `parts_ms`, `measure_ms` and `tensor_ms` and NOT ONE word
        about which regime produced them, so a caller holding 174 ms from one
        session and 2 117 ms from another had nothing to tell them apart - while
        the same tool's assembly route had answered that question since 0.3.38.
        Same route through the same book now, so the answer cannot depend on
        which argument shape reached the part.

        The two routes share `BOOK_MASS` with the assembly route's per-part
        reader: measuring this part's volume inside an assembly and outside one
        is the same job, and a second book would report "first time" twice.
        """
        identity = self._part_identity(part)
        first = _BOOKS.is_new(BOOK_MASS, identity)
        if first:
            _BOOKS.note(BOOK_MASS, identity, MASS_PROPERTIES)
        _BOOKS.record_time(BOOK_MASS, identity, elapsed_ms, first)
        payload["first_measure"] = first
        payload["cold_reason"] = (
            _BOOKS.reason_of(BOOK_MASS, identity) or MASS_PROPERTIES)
        payload.update(_BOOKS.time_of(BOOK_MASS, identity))
        return _BOOKS.describe(
            book=BOOK_MASS,
            route=route,
            measured=True,
            parts_total=1,
            session_first=1 if first else 0,
            cold_reasons={MASS_PROPERTIES: 1} if first else {},
            doc_key=_document_key(getattr(part, "Parent", None)),
            elapsed_ms=elapsed_ms,
            scope=("", 1),
        )

    def _note_inertia_part(self, part: Any, label: str, props: dict[str, Any],
                           props_ms: float,
                           parts_detail: dict[Any, dict[str, Any]]) -> None:
        """Record one part's mass-properties cost, and its two regimes.

        A book of its own (``BOOK_MASS``), not the box route's: the first reading
        in the session is the first mass-properties reading even if that part's
        BOX was measured long ago, because these are different jobs and they do
        not warm up together. Mixing them would report a cold/warm ratio across
        two different jobs, which is how the 21x-26x mesh-export ratios ended up
        looking like a property of CATIA (reported 2026-10-29, §5.2).
        """
        try:
            key = self._part_identity(part)
        except Exception:  # noqa: BLE001 - an unidentifiable part is still measured
            return
        entry = parts_detail.get(key)
        if entry is None:
            first = _BOOKS.is_new(BOOK_MASS, key)
            if first:
                _BOOKS.note(BOOK_MASS, key, MASS_PROPERTIES)
            _BOOKS.record_time(BOOK_MASS, key, props_ms, first)
            entry = {
                "part": label,
                "instances": 0,
                "volume_mm3": round(props["volume"], 4),
                "area_mm2": round(props["area"], 4),
                "elapsed_ms": props_ms,
                "first_measure": first,
                # Which job this part's first reading paid for. Same field name
                # as the box route's, because the question a caller asks is the
                # same one ("why was this part slow once?") and the answer has to
                # be readable without knowing which tool answered.
                "cold_reason": (_BOOKS.reason_of(BOOK_MASS, key)
                                or MASS_PROPERTIES),
            }
            entry.update(_BOOKS.time_of(BOOK_MASS, key))
            if "cold_warm_ratio" in entry:
                entry["cold_warm_note"] = _COLD_RATIO_NOTE
            parts_detail[key] = entry
        else:
            # A cached part costs nothing here, and saying 0.0 ms would read as
            # "this part got free"; the number stays the one from its first read
            # in THIS call and the instance count says why it is not paid again.
            pass
        entry["instances"] = entry.get("instances", 0) + 1

    def _part_mass_properties(self, part: Any,
                              cache: dict[Any, Any] | None = None
                              ) -> dict[str, Any]:
        """Volume, area and local COG of one part, read once per part.

        Once per PART, not per instance: 70 instances of 28 parts used to pay
        70 volume + 70 COG (and would pay 70 areas) for 28 distinct answers.
        The values are in the part's own coordinates, so every instance gets the
        same numbers and only the placement differs.
        """
        key = None
        if cache is not None:
            try:
                key = self._part_identity(part)
            except Exception:  # noqa: BLE001 - just read it again
                key = None
            if key is not None and key in cache:
                return cache[key]
        try:
            body = part.MainBody
            if body.Shapes.Count == 0:
                raise RuntimeError("no solid in the main body")
            spa = part.Parent.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
            result = {
                "volume": measurable.Volume * _M3_TO_MM3,
                "area": measurable.Area * _M2_TO_MM2,
                "cog": self._read_measurable_array(measurable, "GetCOG", 3),
            }
        except Exception as exc:  # noqa: BLE001 - reported per component
            result = {"error": str(exc).splitlines()[0][:90]}
        if key is not None:
            cache[key] = result
        return result

    @staticmethod
    def _mat_mul(a: list[list[float]],
                 b: list[list[float]]) -> list[list[float]]:
        """Row-major 3x3 product: the placement of a placement."""
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]

    def _auto_method(self, method: str, body: Any) -> tuple[str, str]:
        """Resolve 'auto' to the mesh.

        The vertex sweep is exact only on all-planar solids and can be wrong by
        tens of percent on a curved one (a revolved face has no vertex at its
        extremum), so 'auto' takes the mesh and says so. `method='vertices'`
        stays available for the cheap, exact-on-planar route.
        """
        if method != "auto":
            return method, ""
        return "mesh", (" (auto uses the STL mesh: exact for planar-faced solids and "
                        "a lower bound within ~0.5 % for curved ones; pass "
                        "method='vertices' for the cheap vertex sweep)")

    @staticmethod
    def _has_curved_faces(conn: Any, body: Any) -> bool:
        """True when any face of the final shape is not planar.

        This decides the measurement route: the vertex sweep is exact on an
        all-planar solid, but a revolved or lofted face has no vertex at its
        extremum, so a vertex box misses the diameter (reported 2026-10-24: a
        mixed body's auto box came out 37.5 % small). Curved face -> mesh.
        """
        try:
            cells = topology_cells(conn, body, "Face")
        except Exception:  # noqa: BLE001 - be conservative
            return True
        for cell in cells:
            try:
                if "Planar" not in str(cell.Type):
                    return True
            except Exception:  # noqa: BLE001
                return True
        return False

    def _part_bbox(self, part: Any, method: str) -> str:
        """Bounding box of a named Part document (no reliance on ActiveDocument)."""
        started = time.perf_counter()
        body = part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError("That part has no solid shape yet.")
        # Which regime this is. Peek now, RECORD at the return, because the reason
        # depends on the route this part ends up taking: a vertex sweep paid for a
        # topology read, an STL export paid for the export, and calling both "the
        # first topology read" is what made 21x look like a build time
        # (reported 2026-10-29, §5.2).
        try:
            identity = self._part_identity(part)
        except Exception:  # noqa: BLE001 - the box matters, the label is a courtesy
            identity = None
        first = _BOOKS.is_new(BOOK_TOPOLOGY, identity) if identity is not None else False

        def regime_fields(reason: str) -> dict[str, Any]:
            """The regime fields for a ONE-part call, when the route is known."""
            if identity is None:
                return {}
            if first:
                _BOOKS.note(BOOK_TOPOLOGY, identity, reason)
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            _BOOKS.record_time(BOOK_TOPOLOGY, identity, elapsed, first)
            fields: dict[str, Any] = {
                "first_measure": first,
                "cold_reason": _BOOKS.reason_of(BOOK_TOPOLOGY, identity) or reason,
                # One part, measured from this call's own start, so `parts_ms` is
                # the box and `_stamp_elapsed` closes it against `elapsed_ms`
                # (whose start is the outer tool call, so the difference is
                # resolving the document and building the reply - which is what
                # overhead_ms is for).
                "parts_ms": elapsed,
            }
            fields.update(_BOOKS.time_of(BOOK_TOPOLOGY, identity))
            fields.update(_BOOKS.describe(
                book=BOOK_TOPOLOGY,
                route="box.part",
                measured=True,
                parts_total=1,
                session_first=1 if first else 0,
                cold_reasons={reason: 1} if first else {},
                doc_key=_document_key(part.Parent),
                elapsed_ms=elapsed,
                scope=("", 1),
            ))
            return fields

        method, auto_note = self._auto_method(method, body)
        if method in ("auto", "vertices"):
            stats: dict[str, Any] = {}
            points, truncated, total, dropped = self._vertex_points(body, stats)
            if points:
                return self._bbox_json(
                    points,
                    f"vertex sweep over {len(points)} of {total} vertices of "
                    f"'{part.Parent.Name}'; exact for planar-faced solids, curved "
                    "faces may extend beyond these bounds"
                    + (" (capped at 200)" if truncated else "")
                    + (f"; {dropped} construction point(s) ignored" if dropped else ""),
                    bounds="exact for a planar-faced solid; a curved face has no "
                           "vertex at its extremum, so read 'bounds' as a lower "
                           "bound there - use method='mesh' for those",
                    extra={
                        # Which reader ran, and what the filter walked - the same
                        # two facts the assembly reply publishes per part, so a
                        # single-part call cannot look like a build without them
                        # (reported 2026-10-26).
                        "points_route": _route_name(stats.get("route")),
                        "vertices_used": len(points),
                        "cells_scanned": stats.get("cells"),
                        # Which reader ran inside the sweep: one script call for
                        # the whole part, or one COM round trip per topology cell.
                        "sweep_route": stats.get("sweep_route"),
                        **_nls_fields(stats),
                        **regime_fields(TOPOLOGY),
                    },
                )
            if method == "vertices":
                raise RuntimeError(
                    "UNSUPPORTED_CAPABILITY: that part exposes no vertices (fully "
                    "curved solid), so a vertex sweep cannot bound it. Use "
                    "method='mesh'."
                )
        corners, triangles, copy_note = self._mesh_corners(part)
        reply = self._bbox_json(
            corners,
            f"STL mesh sweep of '{part.Parent.Name}' over {triangles} triangle "
            "vertices; the mesh chords curved faces, so these bounds are a LOWER "
            "bound on the true envelope (measured 0.47-0.73 % small)"
            + copy_note + auto_note,
            bounds=("lower bound: mesh chording, measured 0.47-0.73 % small on "
                    "curved faces. Exact for a solid whose diameter runs through "
                    "a planar edge or a vertex"),
            extra={"points_route": "mesh", "mesh_vertices": triangles,
                   **regime_fields(MESH_EXPORT)},
        )
        if copy_note:
            # The caveat used to live only inside the prose of `method`, where a
            # caller doing `payload["dimensions"]` never sees it. "modeled but not
            # saved yet" is exactly the loop this measure-then-fix workflow runs,
            # so it gets a structured field (reported 2026-10-24).
            payload = json.loads(reply)
            payload["warnings"] = [
                "the box was measured from a COPY of the part's saved file, because "
                "the part is loaded inside an open assembly and CATIA refuses to "
                "export it in context - edits made since the last save are NOT in "
                "these numbers (catia_save_document, then measure again)"
            ]
            reply = json.dumps(payload, indent=2)
        return reply

    @staticmethod
    def _stamp_elapsed(reply: str, started: float) -> str:
        """Add ``elapsed_ms`` to a reply that was already serialised.

        The part branches build their JSON in several places (and `_part_bbox`
        again), so this stamps the one field at the exit instead of threading a
        timer through all of them. A caller needs it exactly here: the single-part
        route is the one measured at 5.2 s for 535 cells, and without the field
        "0.05 s" and "5.2 s" look identical in a reply (reported 2026-10-27).
        """
        try:
            payload = json.loads(reply)
        except Exception:  # noqa: BLE001 - a non-JSON reply is not ours to touch
            return reply
        elapsed = payload.setdefault("elapsed_ms",
                                     round((time.perf_counter() - started) * 1000, 1))
        if "parts_ms" in payload and "overhead_ms" not in payload:
            # The remainder, computed the same way the assembly route computes it:
            # elapsed minus the part's own measurement, so the two add up to
            # elapsed_ms instead of merely being adjacent (reported 2026-10-28,
            # §6.2 asked for an account; an account that does not close is the
            # defect this project keeps fixing).
            try:
                payload["overhead_ms"] = round(
                    max(float(elapsed) - float(payload["parts_ms"]), 0.0), 1)
            except Exception:  # noqa: BLE001 - a broken number is not a crash
                pass
        return json.dumps(payload, indent=2)

    def _assembly_box_checked(self, doc: Any, method: str, component: str | None,
                              include_curved: bool) -> str:
        """`_assembly_box` with the one refusal that belongs to every caller."""
        if method == "mesh":
            # Refuse instead of silently returning vertex-sweep numbers:
            # a wrong number that looks plausible is worse than an error.
            raise RuntimeError(
                f"method='mesh' is not available for an assembly "
                f"('{getattr(doc, 'Name', '<assembly>')}'): a per-component world "
                "box would need every component's own mesh, and the aggregated "
                "path only reads the vertices the assembly exposes. Two working "
                "routes: (a) get_bounding_box(document='<part>.CATPart', "
                "method='mesh') per component - the part does not have to be open "
                "first, it is opened by path if it exists only as a component - "
                "then apply the placement from catia_get_component_transform; or "
                "(b) omit method for the vertex-based aggregation, whose "
                "under-reporting on any part with a curved extremum is stated in "
                "the reply."
            )
        return self._assembly_box(doc, method, component, include_curved)

    def _assemblies_with_component(self, component: str) -> list[Any]:
        """Open assemblies that hold a component with that name (or part number)."""
        found: list[Any] = []
        try:
            docs = self.conn.documents
        except Exception:  # noqa: BLE001 - nothing open
            return found
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            if not self._is_product_document(doc):
                continue
            try:
                if self._components_doc(doc, component):
                    found.append(doc)
            except Exception:  # noqa: BLE001 - a broken assembly is not this one
                continue
        return found

    def _active_name(self) -> str:
        try:
            return str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            return "<none>"

    def _warm_up_report(self, reply: str) -> str:
        """Say what a warm-up call bought, instead of handing back a box it ignores.

        The first topology read of a part costs 4-7x the later ones, and it lands
        inside whichever call happens to come first - which is why a "just look at
        the envelope" script pays 173-183 s on the 28-part acceptance assembly and
        the *same* call right after costs 23-26 s (reported 2026-10-27; the range
        spans 0.3.31 and 0.3.33, because a single number here stops being true one
        release later). ``warm_up`` lets the caller pay it on its own time:
        this runs the same measurement (there is no cheaper way to make CATIA build
        its internal representation) and returns the cost, not the geometry.

        A per-part vertex box cannot be cached the way the mesh box is. The mesh box
        describes a FILE - path, size, mtime - and is reused only for a part that is
        not open, i.e. for a state that file identity fully describes. The vertex box
        describes the LIVE part, whose state includes edits that were never saved;
        a file-keyed cache would answer for the part as it was on disk. That is why
        the similar-looking shortcut does not exist on this route, and why the fix
        for the cold pass is to schedule it, not to remember it.
        """
        try:
            payload = json.loads(reply)
        except Exception:  # noqa: BLE001 - a non-JSON reply is already an error
            return reply
        if "error" in payload or payload.get("ok") is False:
            return reply
        parts = payload.get("parts")
        report: dict[str, Any] = {"warm_up": True}
        if isinstance(parts, list):
            report["document"] = payload.get("document")
            report["parts_measured"] = (payload.get("part_boxes_total")
                                        or payload.get("part_boxes"))
            report["instances"] = payload.get("instances")
            # The same names as the reply it summarises: a warm-up report that
            # renamed the regime fields would be the fourth spelling of them.
            for key in ("regime", "session_first_parts", "parts_total",
                        "cold_reasons", "after_cold"):
                if key in payload:
                    report[key] = payload[key]
            report["elapsed_ms"] = payload.get("elapsed_ms")
            report["parts"] = [
                {key: entry[key] for key in
                 ("part", "elapsed_ms", "first_measure", "cells_scanned",
                  "sweep_route") if key in entry}
                for entry in parts]
        else:
            report["part"] = payload.get("part") or payload.get("document")
            report["first_measure"] = payload.get("first_measure")
            report["elapsed_ms"] = payload.get("elapsed_ms")
            report["cells_scanned"] = payload.get("cells_scanned")
            report["sweep_route"] = payload.get("sweep_route")
        report["warm_up_note"] = (
            "this call measured the same geometry and then threw it away: the point "
            "was to make CATIA read every part's topology once, outside a call that "
            "needs the answer. The next catia_get_bounding_box over this document "
            "should report 'session_first_parts: 0' and run at the warm cost. "
            "There is no "
            "cheaper warm-up: CATIA's first-read setup is the work, and a per-part "
            "vertex box cannot be cached from a file identity the way a mesh box "
            "is - the live part may hold edits that were never saved, so a "
            "file-keyed cache would answer for the part as it was on disk")
        return json.dumps(report, indent=2)

    def _get_bounding_box(self, method: str = "auto",
                          component: str | None = None,
                          document: str | None = None,
                          include_curved: bool = True) -> str:
        # Measurable has no bounding-box method in any V5 release. Two sources
        # are available instead: the final shape's vertices (exact for
        # planar-faced solids) and an exported STL mesh (the only thing that
        # works on a fully curved solid - a cylinder is one face, zero
        # vertices - at the cost of chording the surface).
        started = time.perf_counter()
        self.conn.ensure_connected()
        if document:
            self.opened_for_measurement = None
            doc = self._resolve_open_document(document)
            if self._is_product_document(doc):
                # A Product has no Part, but the world box never needed one: it is
                # each component's own box moved by that component's placement, so
                # it can be computed from the NAMED document alone. That matters
                # because "activate it first" is the one thing a second client on
                # the same CATIA can invalidate between two calls (reported
                # 2026-10-26, twice).
                if method == "mesh":
                    raise RuntimeError(
                        f"method='mesh' is not available for an assembly ('{document}'): "
                        "a mesh box is per part, and the aggregation reads each "
                        "component's vertices. Two working routes: (a) "
                        "get_bounding_box(document='<part>.CATPart', method='mesh') "
                        "per component, then apply its placement; or (b) drop "
                        "method for the vertex-based aggregation."
                    )
                return self._assembly_box(doc, method, component,
                                          include_curved)
            try:
                part = self.conn.part_of(doc)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"'{document}' is neither a Part nor a Product document ({exc})."
                ) from exc
            reply = self._part_bbox(part, method)
            if self.opened_for_measurement:
                payload = json.loads(reply)
                payload["opened"] = (
                    f"'{self.opened_for_measurement}' existed only as a component of "
                    "an open assembly, so it was opened as its own document to be "
                    "measured; it stays open (close it with catia_close_document)."
                )
                reply = json.dumps(payload, indent=2)
            return self._stamp_elapsed(reply, started)
        if self._document_kind() == "assembly":
            return self._assembly_box_checked(self.conn.active_document, method,
                                              component, include_curved)
        if component:
            # `component` used to be accepted and IGNORED here: the call returned
            # the active part's box, with no error, no warning and no mention of
            # the name, so a loop over instances produced N identical plausible
            # wrong numbers (reported 2026-10-27). A component lives in a Product,
            # so look for it in the open assemblies - and if that is ambiguous,
            # say which ones rather than pick.
            candidates = self._assemblies_with_component(str(component))
            if len(candidates) == 1:
                reply = self._assembly_box_checked(candidates[0], method,
                                                   component, include_curved)
                payload = json.loads(reply)
                payload["measured_in"] = (
                    f"'{candidates[0].Name}' - the active document is not an "
                    f"assembly, so '{component}' was looked up in the open assembly "
                    "that contains it, and its world box was measured there")
                return json.dumps(payload, indent=2)
            if len(candidates) > 1:
                names = ", ".join(f"'{str(doc.Name)}'" for doc in candidates)
                raise RuntimeError(
                    f"component='{component}' exists in more than one open assembly "
                    f"({names}), and the active document is not an assembly. Pass "
                    "document='<assembly>.CATProduct' to say which one - guessing "
                    "would give N plausible numbers from the wrong assembly."
                )
            raise RuntimeError(
                f"component='{component}' was NOT measured: the active document is "
                "'" + self._active_name() + "', which is not an assembly, and no "
                "open assembly has a component by that name. Two routes: pass "
                "document='<assembly>.CATProduct' (the world box does not need the "
                "assembly to be active), or activate the assembly "
                "(catia_activate_document) and repeat."
            )
        # From here on this measures the ACTIVE document's part. With two clients on
        # one CATIA that is not a given: a bare "MainBody failed" reads like a
        # broken model while the real story is "I measured the wrong document"
        # (reported 2026-10-26). Name it before and after.
        try:
            active_name = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            active_name = "?"
        try:
            part = self.conn.get_active_part()
            body = self.conn.get_active_part_body()
            if body.Shapes.Count == 0:
                raise RuntimeError("The active body has no solid shape yet.")
        except Exception as exc:  # noqa: BLE001 - name the document, then re-raise
            raise RuntimeError(
                f"this measured '{active_name}', which is not a part with a solid "
                "body (" + str(exc).splitlines()[0][:120] + "). If you meant the "
                "assembly, pass document='<assembly>.CATProduct' - the world box no "
                "longer needs it to be active."
            ) from exc
        # ONE single-part implementation. This block used to be a second, older
        # copy of _part_bbox: it published points_route / vertices_used /
        # cells_scanned and nothing else, so the SAME call answered with
        # first_measure + elapsed_note + nls_chars when it named a document and
        # without them when it measured the active one - two field sets for one
        # question, and the cost regime was invisible on the path most callers take
        # (reported 2026-10-27, §3.2 and §P3-3).
        return self._stamp_elapsed(self._part_bbox(part, method), started)

    # A wireframe element's "infinite" end point is stored as 1e7 mm. Sweeping
    # the body's vertices therefore picks up coordinates that belong to an axis
    # or a datum line, not to the solid, and the bounding box comes back as
    # +-10 000 000 mm (reported 2026-10-23). Anything with a coordinate at or
    # beyond this limit is construction geometry, not a vertex of the shape.
    _UNBOUNDED = 1.0e6

    def _vertex_points(self, body: Any,
                       stats: dict[str, Any] | None = None
                       ) -> tuple[list[list[float]], bool, int, int]:
        """Reference vertex coordinates, capped at 200 points.

        Returns ``(points, capped, total, dropped)`` where ``dropped`` counts the
        points that were filtered out - two kinds of them: unbounded wireframe
        ends (1e7 mm) and construction geometry.

        Construction vertices matter here, not just in the listings: a body's
        vertex search also returns the vertices of every sketch wire, and a
        profile whose drawn geometry runs past the solid (a construction line, a
        helper arc) then stretches the bounding box well beyond the part.
        Reported 2026-10-23: the envelope of a 60.96 x 81.20 x 63.34 part came
        back too large and could not be used for acceptance. Filtering by
        ``is_construction`` is the same rule the edge/face listings use.
        """
        sel = self.conn.hso
        refs = []
        total = 0
        construction = 0
        nls_chars = 0
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, "Vertex")
            total = sel.Count
            swept = batch_vertex_sweep(self.conn.app, sel, self._spa_workbench(),
                                       limit=200)
            if swept is not None:
                kept, cells, chars = swept
                points, unbounded = self._bounded(kept)
                # The cap is applied INSIDE the script, so "more cells than points"
                # means the limit stopped it, not that the rest was construction.
                capped = len(kept) >= 200 and cells > len(kept)
                if stats is not None:
                    stats["route"] = "batch"
                    stats["sweep_route"] = "one_call"
                    stats["cells"] = cells
                    stats["nls_chars"] = chars
                    stats["nls_chars_cells"] = cells
                ignored = unbounded if capped else max(cells - len(kept), 0) + unbounded
                return points, capped, cells, ignored
            count = min(total, 200)
            for i in range(1, count + 1):
                cell = sel.Item(i)
                nls = nls_of(cell)
                nls_chars += len(nls)
                if is_construction(nls):
                    construction += 1
                    continue
                refs.append(cell.Reference)
        finally:
            try:
                sel.Clear()
            except Exception:
                pass
        if not refs:
            return [], False, total, construction
        spa = self._spa_workbench()
        read_stats: dict[str, Any] = {}
        raw = read_measurable_points(
            self.conn.app, spa, refs,
            fallback=lambda ref: self._read_measurable_array(
                spa.GetMeasurable(ref), "GetPoint", 3),
            stats=read_stats)
        if stats is not None:
            stats["route"] = read_stats.get("route", "batch")
            stats["cells"] = total
            # Only over the cells actually walked (capped at 200), so on a big
            # part this is a SAMPLE - the assembly route reads every cell and its
            # number is comparable across parts.
            stats["nls_chars"] = nls_chars
            stats["nls_chars_cells"] = count
        points, dropped = self._bounded(raw)
        return points, total > 200, total, dropped + construction

    @classmethod
    def _bounded(cls, points: list[list[float]]) -> tuple[list[list[float]], int]:
        """Drop points that belong to construction geometry, not to the solid."""
        kept, dropped = [], 0
        for point in points:
            if any(abs(c) >= cls._UNBOUNDED or c != c for c in point):
                dropped += 1
                continue
            kept.append(point)
        return kept, dropped

    def _mesh_export(self, part: Any, limit: int | None = None
                    ) -> tuple[list[float], list[float], list[float] | None, int, str]:
        """Bounding corners from an STL export of that part's document.

        ExportData activates the document it exports, so a measurement aimed at a
        named part used to leave the session on that part (reported 2026-10-24:
        "the tool quietly changes the active document"). The previously active
        document is restored before returning.

        Two failure modes had to be handled, both reported 2026-10-24 against
        v0.3.10, and both of them in the one scenario this method exists for - a
        part that is a component of the open assembly:

        * ``ExportData`` is REFUSED while the part is being edited in context
          (CATIA: "编辑上下文时，无法另存为 stl" / "cannot Save As stl while editing
          in context"), and the refusal arrives as a modal dialog. Depending on
          the build it then either raises or returns having written nothing, and
          the empty target surfaced as ``[WinError 2] ... part.stl`` - an error
          that says nothing about the cause.
        * Same refusal when the part has no window of its own at all.

        So the export result is checked by file size, and a part that cannot be
        exported in place is re-exported from a temporary copy opened as its own
        document. Measured on V5-6R2020 B30: a block that is a component of an
        open product exports 3244 bytes as a standalone document and 0 bytes in
        context; the copy route writes the same 3244 bytes from the same state.

        Returns ``(lo, hi, cloud, seen, copy_note)``. ``cloud`` is a flat
        ``[x, y, z, …]`` list of at most ``limit`` vertices when a limit is given,
        and ``None`` otherwise - the extent is all a standalone part needs, but the
        assembly route must transform every vertex (0.3.24).
        """
        doc = part.Parent
        previous = None
        try:
            # The OBJECT, not its name: two open documents can share a name (this
            # server creates them), and restoring by name then activates the wrong
            # twin - the same defect _tensor_for had (2026-10-28).
            previous = self.conn.active_document
        except Exception:  # noqa: BLE001 - nothing to restore
            previous = None
        # ExportData raises an overwrite prompt when the target exists, and that
        # dialog blocks the worker thread forever. mkstemp would create the file
        # up front, so make a directory and hand over a name that does not exist -
        # and a unique one, so two concurrent measurements cannot collide on it.
        folder = tempfile.mkdtemp(prefix="catia_bbox_")
        path = os.path.join(folder, "part_%s.stl" % uuid.uuid4().hex[:8])
        try:
            # Measured on V5-6R2020 B30, 2026-10-24: ExportData writes a 37-byte
            # EMPTY STL unless the part has been updated first - on a block and on
            # a cylinder alike (37 bytes vs 3244 / 28899 after Update()). That
            # failure mode is what made "mesh" look broken on curved solids.
            try:
                part.Update()
            except Exception:  # noqa: BLE001 - the export reports the real problem
                pass
            export_error = None
            in_context = self._edited_in_context(doc)
            if not in_context:
                try:
                    # ExportData rejects forward-slash paths (E_FAIL).
                    self._export_stl(doc, path)
                except Exception as exc:  # noqa: BLE001 - the file check decides
                    export_error = exc
                if (os.path.getsize(path) if os.path.exists(path) else 0) >= 128:
                    lo, hi, cloud, seen = _stl_scan(path, limit)
                    return lo, hi, cloud, seen, ""
            # In context the export is REFUSED with a modal dialog that nobody
            # can answer, and on one B32 session it still wrote a valid STL - so
            # the size check could not tell us, and every measurement left a
            # dialog plus an event report behind (reported 2026-10-24, 6 parts =
            # 6 clicks). Detect the state first and never attempt the in-place
            # export there.
            lo, hi, cloud, seen = self._mesh_from_copy(doc, path, limit)
            cached = (self._mesh_last_cache == "hit")
            return lo, hi, cloud, seen, (
                "; measured from a temporary copy of the saved file, because the "
                "part is loaded inside the assembly and CATIA refuses to export a "
                "part edited in context - unsaved edits are not included"
                + (" (reused: the same file was already measured in this session and "
                   "its size and modified time have not changed)" if cached else ""))
        except Exception as exc:  # noqa: BLE001
            # `part.Name` is not guaranteed (a Part from PartDocument has one, but
            # the error path must not raise a second error while formatting).
            label = getattr(doc, "Name", None) or getattr(part, "Name", "<part>")
            raise RuntimeError(
                f"STL export failed for '{label}', so its "
                "mesh bounding box cannot be measured. This is what CATIA does when "
                "the part is loaded inside an open assembly and has no window of its "
                "own (it refuses to save as stl while editing in context), and the "
                "raw error for that is usually the useless '[WinError 2] part.stl'. "
                f"Open the part as its own document first (catia_open_document "
                f"'{getattr(doc, 'Name', '<part>.CATPart')}') and retry. "
                f"Original error: {export_error if export_error else exc}"
            ) from exc
        finally:
            shutil = __import__("shutil")
            shutil.rmtree(folder, ignore_errors=True)
            if previous is not None:
                try:
                    previous.Activate()
                except Exception:  # noqa: BLE001 - best effort
                    pass

    def _mesh_corners(self, part: Any) -> tuple[list[list[float]], int, str]:
        """The local box of a part from its STL: two opposite corners + count."""
        lo, hi, _cloud, seen, note = self._mesh_export(part)
        return [lo, hi], seen, note

    def _mesh_cloud(self, part: Any) -> tuple[list[float], int, str]:
        """Every mesh vertex (flat, capped) - what a world box actually needs.

        Two opposite corners are enough for a local box, but NOT for a rotated
        one: transforming only the diagonal and taking its extent gives a box that
        is far too narrow in one direction and, once several instances are
        aggregated, too wide in another. Reported 2026-10-26 with the numbers: a
        disc whose true world box is ±72.5 in x and y came back as ±100.29 / ±21.32
        - the diagonal of the local box, rotated, is a line, and a line has almost
        no extent across it. Rotating the vertices is the only answer that stays
        exact for an arbitrary placement.
        """
        lo, hi, cloud, seen, note = self._mesh_export(part, _MESH_CLOUD_LIMIT)
        if cloud is None or not cloud:
            # No cloud (or an empty export) - fall back to the two corners, which
            # is still a valid LOWER bound, and say so by returning the corners.
            return [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]], seen, note
        return cloud, seen, note

    @staticmethod
    def _world_extent(cloud: list[float], matrix: list[list[float]],
                      translation: list[float]) -> tuple[list[float], list[float]]:
        """min/max of ``A·p + T`` over a flat point cloud, without building points.

        A flat cloud of a few thousand vertices is transformed in milliseconds,
        and nothing is allocated per point - which matters because this runs once
        per INSTANCE of a curved part (an assembly can hold dozens).
        """
        a = matrix
        tx, ty, tz = translation[0], translation[1], translation[2]
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for i in range(0, len(cloud) - 2, 3):
            x, y, z = cloud[i], cloud[i + 1], cloud[i + 2]
            for axis in range(3):
                value = a[axis][0] * x + a[axis][1] * y + a[axis][2] * z
                if axis == 0:
                    value += tx
                elif axis == 1:
                    value += ty
                else:
                    value += tz
                if value < lo[axis]:
                    lo[axis] = value
                if value > hi[axis]:
                    hi[axis] = value
        return lo, hi

    def _export_stl(self, doc: Any, path: str) -> None:
        """``doc.ExportData(..., "stl")`` without CATIA's file alerts.

        ExportData is a SAVE, so CATIA runs its file-alert path: on V5-6R2022 B32
        it puts up a modal "另存为 / 保存操作失败：导入的数据位于会话中…" for a
        document that has unsaved modifications, and it does that whether or not
        the STL itself gets written. Nobody answers a dialog raised on the
        automation thread, so the call sat there for **89.4 s** before returning
        (reported 2026-10-26, with the raw-COM control: ``DisplayFileAlerts =
        False`` -> 0 dialogs, 0.00 s, same 16 071-byte STL). Every bounding box
        that reached the mesh route left one on the user's desktop - and since
        0.3.23 that route runs once per curved part of an assembly, so a silent
        occasional annoyance became a guaranteed one.

        The same switch already guards ``add_component``; this is the second file
        operation in the tool that needs it. Restored in ``finally`` because
        leaving it off would make the NEXT interactive operation fail silently.
        """
        previous = None
        try:
            previous = self.conn.app.DisplayFileAlerts
            self.conn.app.DisplayFileAlerts = False
        except Exception:  # noqa: BLE001 - not every build exposes it
            previous = None
        try:
            doc.ExportData(os.path.normpath(path), "stl")
        finally:
            if previous is not None:
                try:
                    self.conn.app.DisplayFileAlerts = previous
                except Exception:  # noqa: BLE001
                    pass

    def _edited_in_context(self, doc: Any) -> bool:
        """True when the document has no window of its own.

        A part loaded inside an open assembly can be reached as a document and
        accepts ``Activate()``, but ``ActiveDocument`` does not change - that is
        the same read-back ``catia_activate_document`` uses to refuse. It is the
        only precondition available before an export that would otherwise raise a
        modal dialog, so it is checked first rather than inferred from the error.
        """
        try:
            doc.Activate()
            active = str(self.conn.app.ActiveDocument.Name)
            return active != str(doc.Name)
        except Exception:  # noqa: BLE001 - assume the risky state
            return True

    def _mesh_from_copy(self, doc: Any, path: str, limit: int | None = None
                        ) -> tuple[list[float], list[float], list[float] | None, int]:
        """Export from a temporary copy of the file, opened as its own document.

        The copy has no link to the assembly, so CATIA treats it as a standalone
        part and the in-context restriction does not apply. Its cost is stated in
        the reply: it reads the SAVED file, so a part edited since its last save
        is measured as it was on disk.

        Because it reads the saved FILE, the answer is a function of that file -
        so it is cached under the file's identity (path, size, mtime) and an
        unchanged file is not copied, opened, updated, exported and closed again.
        That lifecycle is what an in-context part costs: measured on B30
        2026-10-26, 12-17 s per curved part, and 5 of them were 74.2 s of a 94.4 s
        assembly box. Only this route is cached - an in-place export may include
        unsaved edits, which no file identity can describe.
        """
        import shutil

        try:
            full = str(doc.FullName)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"the part has no saved file to copy ({exc}), and it cannot be "
                "exported in place while it is being edited inside an assembly"
            ) from exc
        if not full or not os.path.isabs(full):
            raise RuntimeError(
                f"'{getattr(doc, 'Name', '?')}' has never been saved, so there is no "
                "file to copy and it cannot be exported while editing in context; "
                "save it first (catia_save_document)"
            )
        identity = _file_identity(full)
        if identity is not None:
            hit = _mesh_cache_get(identity, want_cloud=limit is not None)
            if hit is not None:
                self._mesh_last_cache = "hit"
                return hit
        self._mesh_last_cache = "miss"
        folder = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(full))[0]
        copy_path = os.path.join(folder, f"{stem}__bbox_copy.CATPart")
        shutil.copyfile(full, copy_path)
        opened = self.conn.documents.Open(os.path.normpath(copy_path))
        try:
            try:
                self.conn.part_of(opened).Update()
            except Exception:  # noqa: BLE001 - the export reports it
                pass
            self._export_stl(opened, path)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size < 128:
                raise RuntimeError(
                    f"the copy opened as its own document still exported {size} bytes")
            result = _stl_scan(path, limit)
        finally:
            try:
                opened.Close()
            except Exception:  # noqa: BLE001
                pass
        if identity is not None:
            _mesh_cache_put(identity, result, want_cloud=limit is not None)
        return result

    @staticmethod
    def _bbox_json(points: list[list[float]], method_note: str,
                   bounds: str | None = None,
                   extra: dict[str, Any] | None = None) -> str:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        zs = [p[2] for p in points]
        result = {
            "min": {"x": round(min(xs), 4), "y": round(min(ys), 4), "z": round(min(zs), 4)},
            "max": {"x": round(max(xs), 4), "y": round(max(ys), 4), "z": round(max(zs), 4)},
            "dimensions": {
                "length_x": round(max(xs) - min(xs), 4),
                "length_y": round(max(ys) - min(ys), 4),
                "length_z": round(max(zs) - min(zs), 4),
            },
            # Whether these numbers are the envelope or a lower bound on it. It
            # used to live only inside 'method', where a caller reading
            # 'dimensions' never sees it: a mesh box under-reports a curved face
            # by 0.47-0.73 % (reported 2026-10-25, P02 Y 219.6278 against 220).
            "bounds": bounds or "exact (no curved-face chording involved)",
            "method": method_note,
        }
        if extra:
            result.update(extra)
        return json.dumps(result, indent=2)

    def _get_parameters(self, name_filter: str | None = None) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters

        result = []
        for i in range(1, params.Count + 1):
            param = params.Item(i)
            name = param.Name

            if name_filter and name_filter.lower() not in name.lower():
                continue

            info: dict[str, Any] = {"name": name}
            try:
                info["value"] = param.Value
            except Exception:
                info["value"] = "N/A"
            try:
                info["comment"] = param.Comment
            except Exception:
                pass

            result.append(info)

        if not result:
            return "No parameters found" + (f" matching '{name_filter}'" if name_filter else "")
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _set_parameter(self, name: str, value: float) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        params = part.Parameters

        param = params.Item(name)
        old_value = param.Value
        param.Value = value
        part.Update()

        self.conn.refresh_display()
        return f"Parameter '{name}' changed: {old_value} -> {value}"

    def _update_part(self) -> str:
        self.conn.ensure_connected()
        part = self.conn.get_active_part()
        try:
            part.Update()
        except Exception as error:  # noqa: BLE001 - diagnosed below
            culprit = self._first_failing_feature(part)
            if culprit:
                raise RuntimeError(
                    f"Update failed, and '{culprit}' is the feature that fails on "
                    "its own - every later update stops there. A failed Part Design "
                    "feature that is still in the tree does exactly this (a rejected "
                    "fillet leaves an empty 倒圆角.N behind): delete it with "
                    f"catia_delete_feature(feature_name='{culprit}') and update again. "
                    f"Original error: {error}"
                ) from error
            raise RuntimeError(
                f"Update failed and no single feature reproduces it on its own, so "
                f"the problem is a relation between features (a fillet whose support "
                f"moved, a pattern whose seed changed). Original error: {error}"
            ) from error
        self.conn.refresh_display()
        return "Part updated successfully"

    def _first_failing_feature(self, part: Any) -> str | None:
        """Name of the first feature that fails to update alone, or None.

        Only called when the whole-part update already failed, so the extra COM
        calls cost nothing in the normal path. Without it the answer to "why did
        Update fail" is a bare "方法 Update 失败", which is what made a 22-feature
        part look unrecoverable (reported 2026-10-24).
        """
        try:
            bodies = part.Bodies
        except Exception:  # noqa: BLE001
            return None
        for b in range(1, bodies.Count + 1):
            try:
                shapes = bodies.Item(b).Shapes
            except Exception:  # noqa: BLE001
                continue
            for i in range(1, shapes.Count + 1):
                shape = shapes.Item(i)
                try:
                    part.UpdateObject(shape)
                except Exception:  # noqa: BLE001 - this is the culprit
                    try:
                        return str(shape.Name)
                    except Exception:  # noqa: BLE001
                        return f"#{i} in '{bodies.Item(b).Name}'"
        return None
