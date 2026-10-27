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
                    "Get inertia properties of the active part: volume (mm3 and cm3), "
                    "surface area (mm2 and cm2), center of gravity (mm), mass in kg "
                    "(if a density is passed), inertia matrix (kg.m2, from the material "
                    "density defined in CATIA). All values are converted server-side "
                    "from CATIA's internal MKS measurement units."
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
                                "a component is opened by path here. Note that the "
                                "inertia TENSOR is only computed for the active "
                                "document, so this route returns volume / area / "
                                "centroid without a matrix."
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
                    "'component' for one instance only. Those per-component boxes "
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
                    "repeated, so the cost is per part, not per instance. "
                    "Exactness has a price: the vertex route walks every topology "
                    "cell of each part, and the per-cell cost grows with the part "
                    "(measured 17-93 ms per cell across two acceptance models - a "
                    "24-cell plate is ~0.5 s, a 500-cell body can be 45 s, and one "
                    "1200-cell part dominated an assembly at 84 s). A 70-instance "
                    "assembly costs MINUTES, not seconds - and the server runs tools "
                    "one at a time, so nothing else runs meanwhile. 'cells_scanned' "
                    "and 'elapsed_ms' (total, and per part in 'parts') report what it "
                    "actually cost, which is how you find the one part that eats 40% "
                    "of the call. Measure the instances you need with 'component' "
                    "rather than the whole assembly when you can. A curved part's "
                    "mesh box is read from its SAVED file and reused while that "
                    "file's size and modified time are unchanged (its source_note "
                    "says 'reused') - that saves one export per curved part (~5-17 s "
                    "each), and only applies when the part has no window of its own; "
                    "a part opened as its own document is exported in place every "
                    "time."
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
                return self._get_bounding_box(arguments.get("method", "auto"),
                                              arguments.get("component"),
                                              arguments.get("document")
                                              or arguments.get("document_name"),
                                              bool(arguments.get("include_curved",
                                                                 True)))
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
        (reported 2026-10-24). A name is unambiguous; matching is
        case-insensitive on the full name and on the stem, like
        catia_activate_document.

        A part that exists only as a component of an open product is NOT in
        ``Documents`` at all (measured on B30 2026-10-24: opening a product that
        references two parts leaves Documents holding just the product), which is
        why ``document='<part>.CATPart'`` used to answer "No open document
        matches" in exactly the case the argument was added for. The component's
        own file is located by path and opened, so the call works without the
        caller knowing this rule.
        """
        docs = self.conn.documents
        key = name.strip().casefold()
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            try:
                full = str(doc.Name)
            except Exception:  # noqa: BLE001
                continue
            if full.casefold() == key or full.split(".")[0].casefold() == key:
                return doc
        opened = self._open_component_part(name)
        if opened is not None:
            return opened
        names = ", ".join(str(docs.Item(i).Name) for i in range(1, docs.Count + 1))
        raise RuntimeError(
            f"No open document matches '{name}', and no component of an open "
            f"assembly references a part by that name either. Open: "
            f"{names or 'none'}. Pass the full name from catia_list_documents, or "
            "open the part first with catia_open_document."
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
        doc = part.Parent
        body = part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError(self._no_solid_reason(part, body, label))
        spa = doc.GetWorkbench("SPAWorkbench")
        measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
        payload: dict[str, Any] = {"document": label}
        try:
            payload["volume_mm3"] = round(measurable.Volume * _M3_TO_MM3, 4)
        except Exception as exc:  # noqa: BLE001
            payload["volume_mm3"] = None
            payload["volume_error"] = str(exc).splitlines()[0][:90]
        try:
            payload["area_mm2"] = round(measurable.Area * _M2_TO_MM2, 4)
        except Exception:  # noqa: BLE001
            pass
        try:
            cog = self._read_measurable_array(measurable, "GetCOG", 3)
            payload["center_of_gravity_mm"] = {k: round(v, 4)
                                              for k, v in zip("xyz", cog)}
        except Exception:  # noqa: BLE001
            pass
        if density and payload.get("volume_mm3"):
            payload["mass_kg"] = round(density * payload["volume_mm3"] / 1e9, 6)
        # The tensor is only computed for the ACTIVE document, so this route used
        # to return volume/area/centroid without one - and getting nine parts'
        # tensors took 18 calls (open + activate each). CATIA's activation is
        # temporary here: activate, read, restore (reported 2026-10-24).
        tensor = self._tensor_for(part)
        if tensor:
            payload.update(tensor)
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
            payload["tensor_reason_code"] = "no_own_window"
            payload["tensor_reason"] = (
                "no own window: CATIA computes the tensor only for the active "
                "document, and this part is a component of an open assembly, so it "
                "cannot be activated where it is. Fix: catia_open_document + "
                "catia_activate_document, then get_inertia without 'document'."
            )
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
                "answers": ["volume_mm3", "area_mm2", "center_of_gravity_mm",
                            "mass_kg (with density)"],
                "does_not_answer": ["inertia_tensor"],
            }
            payload["note"] = (
                "measured on this document's own Part, independent of which document "
                "CATIA reports as active. The inertia tensor is missing - see "
                "tensor_reason.")
        return json.dumps(payload, indent=2)

    def _tensor_for(self, part: Any) -> dict[str, Any] | None:
        """Inertia tensor of a specific part, by borrowing the activation.

        Returns None when the part cannot be made active (a component with no
        window of its own), which is the one case where the caller still has to
        open the document first.
        """
        doc = part.Parent
        try:
            previous = str(self.conn.active_document.Name)
        except Exception:  # noqa: BLE001
            previous = None
        try:
            doc.Activate()
            if str(self.conn.app.ActiveDocument.Name) != str(doc.Name):
                return None
            body = part.MainBody
            if body.Shapes.Count == 0:
                return None
            vals = self._evaluate_floats(self._VBS_INERTIA, "catia_mcp_inertia", [body])
            return self._matrix_fields(vals)
        except Exception:  # noqa: BLE001 - the caller reports what is missing
            return None
        finally:
            if previous and previous != str(getattr(doc, "Name", "")):
                try:
                    for i in range(1, self.conn.documents.Count + 1):
                        candidate = self.conn.documents.Item(i)
                        if str(candidate.Name) == previous:
                            candidate.Activate()
                            break
                except Exception:  # noqa: BLE001 - best effort
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
            reply = self._part_inertia(part, density, str(doc.Name))
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
        volume_m3: float | None = None

        try:
            volume_m3 = measurable.Volume  # m3
            result["volume_mm3"] = round(volume_m3 * _M3_TO_MM3, 4)
            result["volume_cm3"] = round(volume_m3 * 1e6, 4)
        except Exception:
            pass

        try:
            area_m2 = measurable.Area  # m2
            result["area_mm2"] = round(area_m2 * _M2_TO_MM2, 4)
            result["area_cm2"] = round(area_m2 * 1e4, 4)
        except Exception:
            pass

        try:
            # GetCOG coordinates come back in the document's length unit
            # (mm), not MKS: field-observed on V5R20 for GetPlane/GetCOG.
            cog = self._read_measurable_array(measurable, "GetCOG", 3)
            result["center_of_gravity_mm"] = {
                "x": round(cog[0], 4),
                "y": round(cog[1], 4),
                "z": round(cog[2], 4),
            }
        except Exception as e:
            result["center_of_gravity_mm"] = f"unavailable: {e}"

        if density and volume_m3 is not None:
            mass_kg = density * volume_m3
            result["mass_kg"] = round(mass_kg, 6)
            result["mass_g"] = round(mass_kg * 1000, 3)
            result["density_kg_m3"] = density

        try:
            # Inertia data lives on the SPAWorkbench Inertia object, not on
            # Measurable (Measurable.GetInertia does not exist in V5).
            vals = self._evaluate_floats(self._VBS_INERTIA, "catia_mcp_inertia", [body])
            result.update(self._matrix_fields(vals))
            if "mass_kg" in result:
                result["mass_note"] = (
                    "mass_kg = the density you passed x volume; "
                    "mass_from_material_kg = the mass CATIA gets from the part's own "
                    "material, and that is also the density the inertia tensor is "
                    "computed with - so when the two masses differ, the tensor and "
                    "mass_kg differ by the same ratio (measured 2026-10-24: a 8000 "
                    "mm3 block reported 0.0628 kg for density 7850, 0.008 kg from "
                    "the material, and Ixx 3.33e-7 = 0.008/12*(0.02^2+0.01^2))."
                )
        except Exception as e:
            result["inertia_matrix_kg_m2"] = f"unavailable: {e}"

        return json.dumps(result, indent=2)

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

    def _components_doc(self, doc: Any, only: str | None = None
                        ) -> list[dict[str, Any]]:
        """Every child of the assembly with placement, part and problems.

        ``only`` filters by component name or part number BEFORE the placement is
        read. A pose costs ~70 ms on V5-6R2020 and the filter used to run after
        all of them had been read, so ``component='P24.1'`` on a 70-instance
        assembly paid ~5 s to answer a question about one instance - which is
        exactly the "5.8 s per part entry fee" an acceptance report measured on a
        16-vertex block (reported 2026-10-26). Same rule as `list_components`
        since 0.3.22: read a pose only for the components that will be reported.
        """
        product = self.conn.product_of(doc)
        wanted = (only or "").strip().casefold() or None
        rows: list[dict[str, Any]] = []
        products = product.Products
        for i in range(1, products.Count + 1):
            child = products.Item(i)
            row: dict[str, Any] = {"index": i, "name": None, "part_number": None,
                                   "matrix": None, "translation": None,
                                   "part": None, "problems": []}
            for key, getter in (("name", lambda c: str(c.Name)),
                                ("part_number", lambda c: str(c.PartNumber))):
                try:
                    row[key] = getter(child)
                except Exception as exc:  # noqa: BLE001
                    row["problems"].append(f"{key}: {str(exc).splitlines()[0][:70]}")
            if wanted is not None:
                # Two cheap string reads decide; the expensive reads below only
                # happen for a component that will actually be reported.
                if (row["name"] or "").casefold() != wanted and \
                        (row["part_number"] or "").casefold() != wanted:
                    continue
            try:
                row["matrix"], row["translation"] = self._placement(child)
            except Exception as exc:  # noqa: BLE001
                row["problems"].append(f"placement: {str(exc).splitlines()[0][:70]}")
            try:
                row["part"] = self.conn.part_of(child.ReferenceProduct.Parent)
            except Exception as exc:  # noqa: BLE001 - the classic dead link
                row["problems"].append(f"part not loaded: {str(exc).splitlines()[0][:70]}")
            rows.append(row)
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
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, "Vertex")
            for i in range(1, sel.Count + 1):
                cell = sel.Item(i)
                if is_construction(nls_of(cell)):
                    continue
                refs.append(cell.Reference)
            if stats is not None:
                stats["cells"] = int(sel.Count)
            spa = doc.GetWorkbench("SPAWorkbench")
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
                    "cells": stats.get("cells"), "points_route": stats.get("points_route")}
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
        rows = self._components_doc(doc, only)
        if only and not rows:
            names = ", ".join(str(r["name"]) for r in self._components_doc(doc))
            raise RuntimeError(
                f"No component matches '{only}'. Components: {names or 'none'}."
            )

        entries, corners, skipped = [], [], []
        fallback_warnings: list[str] = []
        parts_detail: dict[Any, dict[str, Any]] = {}
        warned: set[Any] = set()
        # One local box per PART, not per instance. Reported 2026-10-26: the box
        # cost ~6 s per component plus ~0.07 s per vertex, so 70 instances of 28
        # parts took 11 min 37 s - the same part scanned two to ten times over.
        # The local box does not depend on the placement, so it is computed once
        # and only the world transform is repeated.
        local_boxes: dict[Any, Any] = {}
        for row in rows:
            label = row["part_number"] or row["name"] or f"#{row['index']}"
            if row["part"] is None or row["matrix"] is None:
                skipped.append({"component": label, "error": "; ".join(row["problems"])})
                continue
            body = row["part"].MainBody
            key = self._part_identity(row["part"])
            if key not in local_boxes:
                part_started = time.perf_counter()
                local_boxes[key] = self._part_box_entry(row["part"], body,
                                                        include_curved)
                # What this part actually cost, once. `cells_scanned` is the work
                # estimate; this is the measured price of it, which is the number
                # a caller budgets with - 1 207 cells and 24 cells differ by 40x
                # and the estimate alone does not say which part to fix
                # (reported 2026-10-26).
                local_boxes[key]["elapsed_ms"] = round(
                    (time.perf_counter() - part_started) * 1000, 1)
            cached = local_boxes[key]
            if "error" in cached:
                skipped.append({"component": label, "error": cached["error"]})
                continue
            if cached["source"] == "mesh_fallback":
                # Transform EVERY mesh vertex, not the local box's two corners.
                # The corners are a diagonal; rotate a diagonal and it becomes a
                # line with almost no extent across it, so the aggregated box came
                # out too narrow in one axis and too wide in another while still
                # claiming complete:true (reported 2026-10-26: a disc whose true
                # world box is ±72.5/±72.5 read as ±100.29/±21.32).
                entry_min, entry_max = self._world_extent(
                    cached["points"], row["matrix"], row["translation"])
                world = [[entry_min[0], entry_min[1], entry_min[2]],
                         [entry_max[0], entry_max[1], entry_max[2]]]
            else:
                world = [self._to_world(row["matrix"], row["translation"], p)
                         for p in cached["points"]]
                xs = [p[0] for p in world]
                ys = [p[1] for p in world]
                zs = [p[2] for p in world]
                entry_min = [min(xs), min(ys), min(zs)]
                entry_max = [max(xs), max(ys), max(zs)]
            entry = {
                "component": label,
                "name": row["name"],
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
                if cached["source"] == "mesh_fallback":
                    detail["mesh_vertices"] = cached["mesh_vertices"]
                    detail["points_route"] = "mesh"
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
                    detail["points_route"] = _route_name(cached.get("points_route"))
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

        if not corners:
            # Every component failed: return the partial result instead of an
            # exception. "Which parts could not be measured, and why" is itself
            # the answer a caller needs, and it was previously thrown away
            # (reported 2026-10-24: an assembly holding only a fully curved part
            # failed the whole call).
            return json.dumps({
                "dimensions": None,
                "min": None,
                "max": None,
                "components": [],
                "complete": False,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "instances": {"total": len(skipped), "measured": 0,
                              "skipped": len(skipped)},
                "skipped": skipped,
                "method": ("no component could be measured from its vertices; each "
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
                "yourself.")
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
        payload["note_scope"] = (
            f"{len(entries)} instance(s) of {len(local_boxes)} part(s): each part's "
            "local box is computed once and only the placement is repeated, so the "
            "cost is per part, not per instance. Per-part counts are in 'parts', "
            "not on every instance entry.")
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
        return json.dumps(payload, indent=2)

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
        """Volume and centre of gravity of an assembly, weighted per component.

        Each component's own volume and COG are read from its part document and
        moved into world space by its placement; the totals are the volume-
        weighted sums. That is the number a placement script has to hit, and on
        a Product document it used to be unavailable (get_inertia required a
        Part).

        ``doc`` names the assembly to measure; without it the active document is
        used (the older behaviour, and the only one before 0.3.27).
        """
        doc = doc if doc is not None else self.conn.active_document
        rows = self._components_doc(doc)
        entries, skipped = [], []
        total = 0.0
        acc = [0.0, 0.0, 0.0]
        for row in rows:
            label = row["part_number"] or row["name"] or f"#{row['index']}"
            if row["part"] is None or row["matrix"] is None:
                skipped.append({"component": label, "error": "; ".join(row["problems"])})
                continue
            part = row["part"]
            try:
                body = part.MainBody
                if body.Shapes.Count == 0:
                    raise RuntimeError("no solid in the main body")
                doc_i = part.Parent
                spa = doc_i.GetWorkbench("SPAWorkbench")
                measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
                volume = measurable.Volume * _M3_TO_MM3
                cog = self._read_measurable_array(measurable, "GetCOG", 3)
            except Exception as exc:  # noqa: BLE001 - reported per component
                skipped.append({"component": label,
                                "error": str(exc).splitlines()[0][:90]})
                continue
            world = self._to_world(row["matrix"], row["translation"], cog)
            entries.append({
                "component": label,
                "name": row["name"],
                "volume_mm3": round(volume, 4),
                "center_of_gravity_mm": {k: round(v, 4)
                                        for k, v in zip("xyz", world)},
            })
            total += volume
            for i in range(3):
                acc[i] += volume * world[i]

        if total <= 0:
            # Same shape as the bounding-box path, because a script that measures
            # volume first used to be stopped dead here while the box call happily
            # returned "which component, and why" (reported 2026-10-24: the tool
            # that needed the diagnosis most was the one that threw it away).
            return json.dumps({
                "volume_mm3": None,
                "volume_cm3": None,
                "center_of_gravity_mm": None,
                "components": [],
                "skipped": skipped or [{"component": "<assembly>",
                                        "error": "the assembly has no components"}],
                "note": ("no component could be measured, so there is no total. A "
                         "component without a solid (surface-only, or an empty part) "
                         "has no volume to add; each reason is in 'skipped'."),
            }, indent=2)
        payload: dict[str, Any] = {
            "volume_mm3": round(total, 4),
            "volume_cm3": round(total / 1000.0, 4),
            "center_of_gravity_mm": {k: round(acc[i] / total, 4)
                                     for i, k in enumerate("xyz")},
            "components": entries,
            "note": ("volume-weighted aggregation of the components' own volumes "
                     "and centres of gravity, moved into world space by each "
                     "component's placement; the inertia matrix is not aggregated "
                     "(it would need each part's tensor rotated by its placement)"),
        }
        if density:
            payload["mass_kg"] = round(density * total / 1e9, 6)
            payload["density_kg_m3"] = density
        if skipped:
            payload["skipped"] = skipped
        return json.dumps(payload, indent=2)

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
        body = part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError("That part has no solid shape yet.")
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
            extra={"points_route": "mesh", "mesh_vertices": triangles},
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
        payload.setdefault("elapsed_ms",
                           round((time.perf_counter() - started) * 1000, 1))
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
        method, auto_note = self._auto_method(method, body)

        if method in ("auto", "vertices"):
            stats: dict[str, Any] = {}
            points, truncated, total, dropped = self._vertex_points(body, stats)
            if points:
                reply = self._bbox_json(
                    points,
                    f"vertex sweep over {len(points)} of {total} vertices of "
                    f"'{active_name}'; exact for planar-faced solids, curved faces "
                    "may extend beyond these bounds"
                    + (" (capped at 200)" if truncated else "")
                    + (f"; {dropped} construction point(s) ignored (sketch wires "
                       f"and wireframe ends at 1e7 mm are not the solid)" if dropped else ""),
                    bounds="exact for a planar-faced solid; a curved face has no "
                           "vertex at its extremum, so read 'bounds' as a lower "
                           "bound there - use method='mesh' for those",
                    extra={
                        "points_route": _route_name(stats.get("route")),
                        "vertices_used": len(points),
                        "cells_scanned": stats.get("cells"),
                    },
                )
                return self._stamp_elapsed(reply, started)
            if method == "vertices":
                raise RuntimeError(
                    "UNSUPPORTED_CAPABILITY: the final shape exposes no vertices "
                    "(fully curved solid), so a vertex sweep cannot bound it. "
                    "Use method='mesh'."
                )

        corners, triangles, copy_note = self._mesh_corners(part)
        reply = self._bbox_json(
            corners,
            f"STL mesh sweep of '{active_name}' over {triangles} triangle vertices; "
            "the mesh chords "
            "curved faces, so these bounds are a LOWER bound on the true envelope "
            "(measured 0.47-0.73 % small)" + copy_note + auto_note,
            bounds=("lower bound: mesh chording, measured 0.47-0.73 % small on "
                    "curved faces (P02 Y 219.6278 against a true 220). Exact for "
                    "a solid whose diameter lies on a planar edge or vertex"),
            extra={
                # The mesh route has no "vertices used" and no per-cell reader, but
                # the key stays present so a caller can read `points_route` without
                # asking whether this reply was built by a version that has it
                # (reported 2026-10-27).
                "points_route": "mesh",
                "mesh_vertices": triangles,
            },
        )
        return self._stamp_elapsed(reply, started)

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
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, "Vertex")
            total = sel.Count
            count = min(total, 200)
            for i in range(1, count + 1):
                cell = sel.Item(i)
                if is_construction(nls_of(cell)):
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
            previous = str(self.conn.active_document.Name)
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
            if previous and previous != str(getattr(doc, "Name", "")):
                try:
                    for i in range(1, self.conn.documents.Count + 1):
                        candidate = self.conn.documents.Item(i)
                        if str(candidate.Name) == previous:
                            candidate.Activate()
                            break
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
