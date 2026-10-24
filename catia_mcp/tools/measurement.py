"""Measurement and analysis tools for CATIA V5.

Distance, angle, inertia, bounding box, and part property queries.
"""

from __future__ import annotations

import json
import os
import re
import struct
import tempfile
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.topo_search import is_construction, nls_of, search_topology


def _stl_extent(path: str) -> tuple[list[list[float]], int]:
    """Min/max corner and vertex count from an STL (binary or ASCII).

    Streams the file and only tracks extremes, because a dense part exports
    hundreds of thousands of triangles and keeping them all buys nothing for a
    bounding box.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
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
    return [lo, hi], seen

# CATIA V5's SPAWorkbench Measurable API returns every value in MKS units
# (meters, m2, m3, kg.m2) regardless of the document's display units.
# Feature creation APIs, by contrast, take millimeters. All measurement
# results are therefore converted to mm-based units here, at the server
# boundary, so agents always see consistent mm/mm2/mm3 values.
_M_TO_MM = 1000.0
_M2_TO_MM2 = 1e6
_M3_TO_MM3 = 1e9


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
                    },
                },
            },
            {
                "name": "catia_get_bounding_box",
                "description": (
                    "Get the bounding box of the active part in mm. 'vertices' sweeps the "
                    "final shape's vertices (exact for planar-faced solids, but a fully "
                    "curved solid like a cylinder has no vertices at all); 'mesh' exports "
                    "an STL and sweeps the triangles, which works for revolved and lofted "
                    "shapes and lands a fraction of a millimetre inside the true surface "
                    "because the mesh chords the curve. 'auto' uses vertices when they "
                    "exist and falls back to the mesh otherwise. "
                    "With an assembly active it returns the WORLD box: each "
                    "component's local box is transformed by its placement and the "
                    "results are aggregated, per component as well as overall. Pass "
                    "'component' for one instance only. Note that a component's local "
                    "box still comes from a vertex sweep, so a revolved part's "
                    "diameter is under-reported there - the mesh method cannot be "
                    "applied to a non-active document."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["auto", "vertices", "mesh"],
                            "default": "auto",
                            "description": (
                                "auto (default) = vertices if the shape has any, else "
                                "mesh; vertices = fail on a fully curved solid; mesh = "
                                "always export STL"
                            ),
                        },
                        "component": {
                            "type": "string",
                            "description": (
                                "Name of one instance in the active assembly "
                                "(see catia_list_components). Omit for all of them."
                            ),
                        },
                        "document": {
                            "type": "string",
                            "description": (
                                "Measure THIS open document instead of the active "
                                "one - the only way to measure a part while its "
                                "assembly is open (activating it does not change "
                                "ActiveDocument on this build). Alias: document_name."
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
                                              or arguments.get("document_name"))
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

        Activating a component's part inside an open product does not change
        what ``ActiveDocument`` returns on this build, so a measurement aimed at
        "the part I just activated" silently measured the assembly (reported
        2026-10-24). A name is unambiguous; matching is case-insensitive on the
        full name and on the stem, like catia_activate_document.
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
        names = ", ".join(str(docs.Item(i).Name) for i in range(1, docs.Count + 1))
        raise RuntimeError(
            f"No open document matches '{name}'. Open: {names or 'none'}. Pass the "
            "full name from catia_list_documents."
        )

    def _part_inertia(self, part: Any, density: float | None,
                      label: str) -> str:
        """Volume / area / centroid of a specific Part, by name or object."""
        doc = part.Parent
        body = part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError(f"'{label}' has no solid in its main body yet.")
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
        payload["note"] = ("measured on this document's own Part, independent of "
                           "which document CATIA reports as active; the inertia "
                           "matrix is only computed for the active document")
        return json.dumps(payload, indent=2)

    def _get_inertia(self, density: float | None = None,
                     document: str | None = None) -> str:
        self.conn.ensure_connected()
        if document:
            doc = self._resolve_open_document(document)
            return self._part_inertia(self.conn.part_of(doc), density,
                                      str(doc.Name))
        if self._document_kind() == "assembly":
            return self._assembly_inertia(density)
        spa = self._spa_workbench()
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
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
            result["mass_from_material_kg"] = round(vals[0], 6)
            inertia = vals[1:10]
            result["inertia_matrix_kg_m2"] = [
                [round(inertia[0], 4), round(inertia[1], 4), round(inertia[2], 4)],
                [round(inertia[3], 4), round(inertia[4], 4), round(inertia[5], 4)],
                [round(inertia[6], 4), round(inertia[7], 4), round(inertia[8], 4)],
            ]
        except Exception as e:
            result["inertia_matrix_kg_m2"] = f"unavailable: {e}"

        return json.dumps(result, indent=2)

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

    def _components_doc(self, doc: Any) -> list[dict[str, Any]]:
        """Every child of the assembly with placement, part and problems."""
        product = self.conn.product_of(doc)
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

    def _local_box(self, part: Any, body: Any) -> tuple[list[list[float]], int]:
        """The part's own bounding corners and the number of points used.

        Uses that document's selection (not the active one) and skips
        construction geometry, which otherwise drags sketch wires into the box.
        """
        doc = part.Parent
        sel = doc.Selection
        refs = []
        try:
            sel.Clear()
            sel.Add(body)
            search_topology(sel, "Vertex")
            for i in range(1, sel.Count + 1):
                cell = sel.Item(i)
                if is_construction(nls_of(cell)):
                    continue
                refs.append(cell.Reference)
            spa = doc.GetWorkbench("SPAWorkbench")
            raw = [self._read_measurable_array(spa.GetMeasurable(ref), "GetPoint", 3)
                   for ref in refs]
        finally:
            try:
                sel.Clear()
            except Exception:
                pass
        points, _dropped = self._bounded(raw)
        return points, len(points)

    def _assembly_box(self, doc: Any, method: str,
                      only: str | None = None) -> str:
        rows = self._components_doc(doc)
        if only:
            rows = [r for r in rows if (r["name"] or "").lower() == only.lower()
                    or (r["part_number"] or "").lower() == only.lower()]
            if not rows:
                names = ", ".join(str(r["name"]) for r in self._components_doc(doc))
                raise RuntimeError(
                    f"No component matches '{only}'. Components: {names or 'none'}."
                )

        entries, corners, skipped = [], [], []
        for row in rows:
            label = row["part_number"] or row["name"] or f"#{row['index']}"
            if row["part"] is None or row["matrix"] is None:
                skipped.append({"component": label, "error": "; ".join(row["problems"])})
                continue
            body = row["part"].MainBody
            try:
                points, used = self._local_box(row["part"], body)
            except Exception as exc:  # noqa: BLE001
                skipped.append({"component": label,
                                "error": f"local box: {str(exc).splitlines()[0][:80]}"})
                continue
            if not points:
                skipped.append({"component": label,
                                "error": "no vertices (fully curved part); the mesh "
                                         "method needs that document active"})
                continue
            world = [self._to_world(row["matrix"], row["translation"], p)
                     for p in points]
            xs = [p[0] for p in world]
            ys = [p[1] for p in world]
            zs = [p[2] for p in world]
            entry_min = [min(xs), min(ys), min(zs)]
            entry_max = [max(xs), max(ys), max(zs)]
            entries.append({
                "component": label,
                "name": row["name"],
                "min": {k: round(v, 4) for k, v in zip("xyz", entry_min)},
                "max": {k: round(v, 4) for k, v in zip("xyz", entry_max)},
                "dimensions": {f"length_{k}": round(mx - mn, 4)
                               for k, mn, mx in zip("xyz", entry_min, entry_max)},
                "vertices_used": used,
            })
            corners.extend(world)

        if not corners:
            raise RuntimeError(
                "Could not measure any component of this assembly: "
                + ("; ".join(f"{s['component']}: {s['error']}" for s in skipped)
                   or "no components")
            )
        note = ("world coordinates: each component's local vertex box transformed "
                "by its placement, then aggregated; a revolved part's diameter is "
                "under-reported because a vertex sweep has no vertex at the "
                "extremum (activate that part and use method='mesh' for the true "
                "value)")
        payload = json.loads(self._bbox_json(corners, note))
        payload["components"] = entries
        if skipped:
            payload["skipped"] = skipped
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

    def _assembly_inertia(self, density: float | None = None) -> str:
        """Volume and centre of gravity of an assembly, weighted per component.

        Each component's own volume and COG are read from its part document and
        moved into world space by its placement; the totals are the volume-
        weighted sums. That is the number a placement script has to hit, and on
        a Product document it used to be unavailable (get_inertia required a
        Part).
        """
        doc = self.conn.active_document
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
            raise RuntimeError(
                "Could not measure any component of this assembly: "
                + ("; ".join(f"{s['component']}: {s['error']}" for s in skipped)
                   or "no components")
            )
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

    def _part_bbox(self, part: Any, method: str) -> str:
        """Bounding box of a named Part document (no reliance on ActiveDocument)."""
        body = part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError("That part has no solid shape yet.")
        if method in ("auto", "vertices"):
            points, truncated, total, dropped = self._vertex_points(body)
            if points:
                return self._bbox_json(
                    points,
                    f"vertex sweep over {len(points)} of {total} vertices of "
                    f"'{part.Parent.Name}'; exact for planar-faced solids, curved "
                    "faces may extend beyond these bounds"
                    + (" (capped at 200)" if truncated else "")
                    + (f"; {dropped} construction point(s) ignored" if dropped else ""),
                )
            if method == "vertices":
                raise RuntimeError(
                    "UNSUPPORTED_CAPABILITY: that part exposes no vertices (fully "
                    "curved solid), so a vertex sweep cannot bound it. Use "
                    "method='mesh'."
                )
        corners, triangles = self._mesh_corners(part)
        return self._bbox_json(
            corners,
            f"STL mesh sweep of '{part.Parent.Name}' over {triangles} triangle "
            "vertices; the mesh chords curved faces, so the true surface lies a "
            "fraction of a millimetre outside these bounds",
        )

    def _get_bounding_box(self, method: str = "auto",
                          component: str | None = None,
                          document: str | None = None) -> str:
        # Measurable has no bounding-box method in any V5 release. Two sources
        # are available instead: the final shape's vertices (exact for
        # planar-faced solids) and an exported STL mesh (the only thing that
        # works on a fully curved solid - a cylinder is one face, zero
        # vertices - at the cost of chording the surface).
        self.conn.ensure_connected()
        if document:
            doc = self._resolve_open_document(document)
            try:
                part = self.conn.part_of(doc)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"'{document}' is not a Part document ({exc}); a Product can "
                    "only be measured without 'document' (world aggregation)."
                ) from exc
            return self._part_bbox(part, method)
        if self._document_kind() == "assembly":
            if method == "mesh":
                # Refuse instead of silently returning vertex-sweep numbers:
                # a wrong number that looks plausible is worse than an error.
                raise RuntimeError(
                    "method='mesh' is not available in Product mode: an STL "
                    "export only works for the active document, and a component's "
                    "part cannot be activated while its product is open. Call "
                    "get_bounding_box(document='<the part>.CATPart', method='mesh') "
                    "for a true per-part box, or omit method for the world "
                    "aggregation (vertex-based, so a revolved part's diameter is "
                    "under-reported - that is stated in the reply)."
                )
            return self._assembly_box(self.conn.active_document, method, component)
        part = self.conn.get_active_part()
        body = self.conn.get_active_part_body()
        if body.Shapes.Count == 0:
            raise RuntimeError("The active body has no solid shape yet.")

        if method in ("auto", "vertices"):
            points, truncated, total, dropped = self._vertex_points(body)
            if points:
                return self._bbox_json(
                    points,
                    f"vertex sweep over {len(points)} of {total} vertices of the "
                    "final shape; exact for planar-faced solids, curved faces may "
                    "extend beyond these bounds"
                    + (" (capped at 200)" if truncated else "")
                    + (f"; {dropped} construction point(s) ignored (sketch wires "
                       f"and wireframe ends at 1e7 mm are not the solid)" if dropped else ""),
                )
            if method == "vertices":
                raise RuntimeError(
                    "UNSUPPORTED_CAPABILITY: the final shape exposes no vertices "
                    "(fully curved solid), so a vertex sweep cannot bound it. "
                    "Use method='mesh'."
                )

        corners, triangles = self._mesh_corners(part)
        return self._bbox_json(
            corners,
            f"STL mesh sweep over {triangles} triangle vertices; the mesh chords "
            "curved faces, so the true surface lies a fraction of a millimetre "
            "outside these bounds",
        )

    # A wireframe element's "infinite" end point is stored as 1e7 mm. Sweeping
    # the body's vertices therefore picks up coordinates that belong to an axis
    # or a datum line, not to the solid, and the bounding box comes back as
    # +-10 000 000 mm (reported 2026-10-23). Anything with a coordinate at or
    # beyond this limit is construction geometry, not a vertex of the shape.
    _UNBOUNDED = 1.0e6

    def _vertex_points(self, body: Any) -> tuple[list[list[float]], bool, int, int]:
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
        raw = [self._read_measurable_array(spa.GetMeasurable(ref), "GetPoint", 3)
               for ref in refs]
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

    def _mesh_corners(self, part: Any) -> tuple[list[list[float]], int]:
        """Bounding corners from an STL export of the active document."""
        import shutil

        doc = part.Parent
        # ExportData raises an overwrite prompt when the target exists, and that
        # dialog blocks the worker thread forever. mkstemp would create the file
        # up front, so make a directory and hand over a name that does not exist.
        folder = tempfile.mkdtemp(prefix="catia_bbox_")
        path = os.path.join(folder, "part.stl")
        try:
            # ExportData rejects forward-slash paths (E_FAIL).
            doc.ExportData(os.path.normpath(path), "stl")
            return _stl_extent(path)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    @staticmethod
    def _bbox_json(points: list[list[float]], method_note: str) -> str:
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
            "method": method_note,
        }
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
        part.Update()
        self.conn.refresh_display()
        return "Part updated successfully"
