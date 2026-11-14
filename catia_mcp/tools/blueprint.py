"""Reading a 2D drawing into a sketch - the entry point the 2026-11-14 report
said was missing (P0-1).

The report's chain was: install a PDF renderer outside the MCP, render to PNG,
crop at high zoom, read the numbers with a vision model, type them into 21 plan
steps. Nothing in that chain is reproducible or checkable, and the model that
comes out has no machine-readable link to the drawing. The fix is not OCR and
not a vision model: it is reading the geometry the drawing already contains.

WHAT IS READ, AND WHAT IS NOT
    DXF  (ASCII)  - LINE, LWPOLYLINE (with bulges, which are real arcs),
                    POLYLINE/VERTEX, CIRCLE, ARC, ELLIPSE, SPLINE, INSERT with
                    its block transform. Units from $INSUNITS.
    SVG           - line, polyline, polygon, rect, circle, ellipse, path
                    (M/L/H/V/C/S/Q/T/A/Z), nested groups, transforms.
                    Units from width/height and viewBox.
    PDF (vector)  - the painted paths of each page's content stream, including
                    Form XObjects and /Rotate. Units are points by definition.

    Text is NOT converted anywhere: a glyph's outline lives in the font program.
    A drawing with its text converted to curves has no text left and imports
    whole; a drawing with live text imports the geometry and REPORTS the text it
    skipped, because "the dimensions are missing" is something the caller has to
    know before they trust the outline.

WHY THE REPLY IS LONGER THAN THE GEOMETRY
    The three ways this silently ruins a model are all invisible in the result:
    units read wrong (a 1:100 sheet at 1:1), one layer of six imported, and a
    vectorised raster mistaken for a drawing (thousands of 0.2 mm strokes, no
    closed profile). So every reply carries the unit factor, the per-layer and
    per-reason counts of what was dropped, and the bounds in millimetres - which
    is the number a caller can check against a dimension they know.
"""
from __future__ import annotations

import json
import math
from typing import Any

from catia_mcp import blueprint
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.sketcher import SketcherTools

#: Sketch elements one import may create. A pad on a 3 000-element profile takes
#: minutes in CATIA and is unusable afterwards; refusing with the count is
#: cheaper than the wait, and `max_elements` overrides it.
MAX_ELEMENTS = 2000


class BlueprintTools:
    """Tools for reading DXF/SVG/PDF drawings into sketches."""

    def __init__(self, connection: Any, sketcher: SketcherTools | None = None) -> None:
        self.conn = connection
        self._sketcher = sketcher
        # The sketcher holds the open-sketch state (`_active_sketch`, the
        # factory, the echo of what was drawn). A second instance would draw
        # into the same COM sketch and leave the server's copy thinking no
        # sketch is open, so the server passes its own and the fallback exists
        # only for tests that never touch COM.
        self._own_sketcher = sketcher is None

    def _sketcher_tools(self) -> SketcherTools:
        if self._sketcher is None:
            self._sketcher = SketcherTools(self.conn)
        return self._sketcher

    # ── tool definitions ──────────────────────────────────────────────────
    def get_tool_definitions(self) -> list[dict[str, Any]]:
        filter_props = {
            "scale": {
                "type": "number",
                "default": 1.0,
                "description": (
                    "Multiplies the file's OWN units, after they have been "
                    "honoured. 1 means 1:1 - a DXF saved in inches still arrives "
                    "at true size, because $INSUNITS is read first. A sheet "
                    "drawn at 1:100 needs 100 here. The reply states both the "
                    "file's units and this factor, and `bounds_mm` is the check."
                ),
            },
            "layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Keep only these layer names (DXF group code 8, SVG id/class). "
                    "A real drawing is several views plus a frame and a title "
                    "block; importing all of it makes a sketch nobody can pad. "
                    "Omit to keep every layer."
                ),
            },
            "region": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
                "description": (
                    "[xmin, ymin, xmax, ymax] in MILLIMETRES of the scaled "
                    "result. A contour is kept when its bounding-box CENTRE is "
                    "inside - not when it is fully inside - so a view that "
                    "crosses the window edge is not dropped silently."
                ),
            },
            "min_size": {
                "type": "number",
                "description": (
                    "Drop contours whose bounding-box DIAGONAL is below this, in "
                    "millimetres. This is the one filter that separates a "
                    "drawing's outline from its dimension text and arrowheads: "
                    "0.5 removes the leaders and keeps the part. Read "
                    "`counts.smallest_mm` first - the reply always reports how "
                    "small the smallest contour is, so the threshold is chosen "
                    "from data instead of guessed."
                ),
            },
            "closed_only": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Keep only contours whose ends meet. A pad needs a closed "
                    "profile, and construction lines, centrelines and dimension "
                    "leaders are open by nature."
                ),
            },
        }
        return [
            {
                "name": "catia_read_drawing",
                "description": (
                    "Read a 2D drawing file - DXF, SVG or VECTOR PDF - and report "
                    "what is in it, WITHOUT touching CATIA. Use this first on any "
                    "drawing: it answers the questions that decide whether the "
                    "import is worth doing - the units and the resulting size in "
                    "mm, how many contours and how many are closed, which layers "
                    "the geometry is on, how small the smallest contour is (text "
                    "and arrowheads are tiny), and how much text was drawn as "
                    "text and therefore cannot be read. Nothing is opened and no "
                    "document is modified, so it is safe to call on a file whose "
                    "scale you do not know yet. THEN call catia_import_drawing "
                    "with the filters this told you to use."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "DXF, SVG or PDF. The format is taken "
                                           "from the extension, then from the "
                                           "first bytes.",
                        },
                        **filter_props,
                        "origin": {
                            "type": "string",
                            "enum": ["bbox", "as_drawn"],
                            "default": "bbox",
                            "description": (
                                "'bbox' (default) moves the kept geometry so its "
                                "lower-left corner is at (0,0) - what you want "
                                "before a pad. 'as_drawn' keeps the sheet "
                                "coordinates, so two views keep their relative "
                                "position."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "default": 40,
                            "description": (
                                "How many contours to describe in the reply. The "
                                "COUNTS are always complete; this only caps the "
                                "per-contour list, so a 6 000-contour vectorised "
                                "image does not produce a 6 000-line answer."
                            ),
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "catia_import_drawing",
                "description": (
                    "Read a 2D drawing (DXF, SVG or vector PDF) and DRAW it into a "
                    "new sketch at 1:1, one call. Lines stay lines, arcs stay "
                    "arcs (DXF bulges included) and circles stay circles, so the "
                    "pad built on the import is not faceted. The sketch is "
                    "created on the plane you name and closed at the end, and the "
                    "reply reports what CATIA itself then sees - element count "
                    "and outline length - plus the bounds in mm, which is the "
                    "number to check against a dimension you know. Filters "
                    "(layers / region / min_size / closed_only) exist because a "
                    "drawing is several views plus a frame; run "
                    "catia_read_drawing on the same file first to choose them "
                    "from the counts instead of guessing. REFUSES rather than "
                    "truncates when the profile would exceed max_elements."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        **filter_props,
                        "plane": {
                            "type": "string",
                            "default": "xy",
                            "description": (
                                "Which plane to sketch on: 'xy', 'yz', 'zx', or "
                                "the name of a datum plane / planar face - the "
                                "same values catia_create_sketch takes."
                            ),
                        },
                        "offset": {
                            "type": "number",
                            "description": "Distance from that plane, in mm.",
                        },
                        "origin": {
                            "type": "string",
                            "enum": ["bbox", "as_drawn"],
                            "default": "bbox",
                            "description": "'bbox' puts the drawing's lower-left "
                                           "corner at the sketch origin.",
                        },
                        "construction": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Draw as construction geometry - for laying a "
                                "drawing under a profile you build by hand."
                            ),
                        },
                        "tolerance": {
                            "type": "number",
                            "default": 0.05,
                            "description": (
                                "Junction gap, in mm, that counts as 'joined' "
                                "when the reply says a contour is closed. It does "
                                "not move anything."
                            ),
                        },
                        "max_elements": {
                            "type": "integer",
                            "default": MAX_ELEMENTS,
                            "description": (
                                "Refuse if the import would create more sketch "
                                "elements than this. Raise it deliberately for a "
                                "vectorised image; do not raise it to make an "
                                "error go away - a 6 000-element profile is not "
                                "a part."
                            ),
                        },
                        "close": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Close the sketch after drawing, which is what "
                                "the reply's element count and outline length come "
                                "from. Set false to keep editing (the sketch stays "
                                "open, like catia_create_sketch leaves it)."
                            ),
                        },
                    },
                    "required": ["file_path"],
                },
            },
        ]

    # ── dispatch ──────────────────────────────────────────────────────────
    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_read_drawing":
                return self._read_drawing(arguments)
            case "catia_import_drawing":
                return self._import_drawing(arguments)
            case _:
                raise ArgumentRejected(f"Unknown blueprint tool: {tool_name}")

    # ── read ──────────────────────────────────────────────────────────────
    def _read_drawing(self, arguments: dict[str, Any]) -> str:
        drawing = self._parse(arguments)
        contours = drawing["contours"]
        limit = arguments.get("limit")
        try:
            limit = 40 if limit is None else max(1, int(limit))
        except (TypeError, ValueError):
            limit = 40

        boxes = [blueprint.contour_bbox(c) for c in contours] or [(0, 0, 0, 0)]
        diagonals = sorted(math.hypot(b[2] - b[0], b[3] - b[1]) for b in boxes)
        report = {
            "file": drawing["file"],
            "format": drawing["format"],
            "units": drawing["units"],
            "mm_per_file_unit": round(drawing["mm_per_file_unit"], 6),
            "scale": drawing["scale"],
            "counts": {
                **drawing["counts"],
                "smallest_mm": round(diagonals[0], 4),
                "median_mm": round(diagonals[len(diagonals) // 2], 4),
                "largest_mm": round(diagonals[-1], 4),
            },
            "bounds_mm": ([round(v, 4) for v in drawing["bounds_mm"]]
                          if drawing["bounds_mm"] else None),
            "origin_shift_mm": [round(v, 4) for v in drawing["origin_shift_mm"]],
            "layers": _layer_table(contours),
            "stitching": drawing.get("stitch"),
            "stats": drawing["stats"],
            "notes": drawing["notes"],
            "contours": [
                {
                    "index": index,
                    "source": contour.get("source"),
                    "layer": contour.get("layer"),
                    "closed": contour["closed"],
                    "elements": blueprint.segment_count(contour),
                    "bbox_mm": [round(v, 4) for v in blueprint.contour_bbox(contour)],
                }
                for index, contour in enumerate(contours[:limit], start=1)
            ],
            "described": min(limit, len(contours)),
        }
        return json.dumps(report, ensure_ascii=False, indent=2)

    # ── import ────────────────────────────────────────────────────────────
    def _import_drawing(self, arguments: dict[str, Any]) -> str:
        drawing = self._parse(arguments)
        contours = drawing["contours"]
        if not contours:
            dropped = drawing["counts"]["dropped"]
            raise PreconditionRejected(
                f"The drawing parsed, but every contour was filtered out: "
                f"{dropped}. Nothing was drawn. Loosen layers / region / "
                "min_size / closed_only - catia_read_drawing on the same file "
                "reports what each filter would keep.",
                code="NOTHING_TO_IMPORT", stage="validate",
                data={"dropped": dropped, "stats": drawing["stats"]})

        elements = drawing["counts"]["elements"]
        limit = arguments.get("max_elements")
        try:
            limit = MAX_ELEMENTS if limit is None else int(limit)
        except (TypeError, ValueError):
            limit = MAX_ELEMENTS
        if elements > limit:
            smallest = min(
                (math.hypot(box[2] - box[0], box[3] - box[1])
                 for box in (blueprint.contour_bbox(c) for c in contours)),
                default=0.0)
            raise ArgumentRejected(
                f"This import would create {elements} sketch elements, above the "
                f"limit of {limit}. Nothing was drawn: CATIA grinds for minutes "
                "over a profile that size and the result is not a part. Narrow "
                f"it - {len(contours)} contour(s) are available and the smallest "
                f"is {smallest:.3f} mm across, so `min_size` removes the small "
                "ones; `layers` and `region` cut whole views. Raise "
                "`max_elements` only if a vectorised image really is what you "
                "want in the sketch.",
                code="TOO_MANY_ELEMENTS", stage="validate",
                data={"elements": elements, "limit": limit,
                      "counts": drawing["counts"],
                      "layers": _layer_table(contours)})

        sketcher = self._sketcher_tools()
        plane = arguments.get("plane") or "xy"
        offset = arguments.get("offset")
        created = sketcher._create_sketch(plane, offset)
        notes: list[str] = []
        closed = 0
        drawn = 0
        for index, contour in enumerate(contours, start=1):
            try:
                reply = self._draw_contour(sketcher, contour, arguments)
            except Exception as error:  # noqa: BLE001 - reported with its contour
                raise PreconditionRejected(
                    f"Contour {index} of {len(contours)} could not be drawn "
                    f"({str(error).splitlines()[0][:140]}). {drawn} contour(s) "
                    f"and {sum(blueprint.segment_count(c) for c in contours[:index - 1])} "
                    "element(s) from this import ARE in the sketch - the rest are "
                    "not. Nothing was rolled back: a half-imported drawing is "
                    "easier to delete element by element than to rebuild.",
                    code="CONTOUR_FAILED", stage="execute",
                    modified=True) from error
            drawn += 1
            if reply and "NOT CLOSED" not in reply:
                closed += 1

        close_note = ""
        if arguments.get("close", True):
            close_note = " " + " ".join(str(sketcher._close_sketch()).split())
        else:
            notes.append("The sketch is still OPEN, so no element count from "
                         "CATIA is quoted here - call catia_close_sketch when the "
                         "editing is done.")

        return self._import_reply(drawing, drawn, closed, elements, created,
                                  close_note, notes)

    def _parse(self, arguments: dict[str, Any]) -> dict[str, Any]:
        layer_values = arguments.get("layers")
        if isinstance(layer_values, str):
            layer_values = [layer_values]
        return blueprint.read_drawing(
            str(arguments.get("file_path") or ""),
            scale=float(arguments.get("scale") or 1.0),
            layers=layer_values,
            region=arguments.get("region"),
            min_size=arguments.get("min_size"),
            closed_only=bool(arguments.get("closed_only", False)),
            origin=str(arguments.get("origin") or "bbox"),
            tolerance=float(arguments.get("tolerance") or 1e-6),
        )

    def _draw_contour(self, sketcher: SketcherTools, contour: dict,
                      arguments: dict[str, Any]) -> str:
        """One contour, drawn with the primitive each piece deserves."""
        pieces = contour["pieces"]
        construction = bool(arguments.get("construction", False))
        tolerance = float(arguments.get("tolerance") or 0.05)
        if len(pieces) == 1 and pieces[0]["t"] == "circle":
            centre, radius = pieces[0]["c"], pieces[0]["r"]
            return sketcher._draw_circle(centre[0], centre[1], radius)
        if len(pieces) == 1 and pieces[0]["t"] == "ellipse":
            piece = pieces[0]
            return sketcher._draw_ellipse(piece["c"][0], piece["c"][1],
                                          piece["rx"], piece["ry"],
                                          piece.get("angle") or 0.0,
                                          construction)
        segments = _segments(contour)
        if not segments:
            return ""
        return sketcher._draw_profile(segments, tolerance)

    def _import_reply(self, drawing: dict, drawn: int, closed: int,
                      elements: int, created: str, close_note: str,
                      notes: list[str]) -> str:
        counts = drawing["counts"]
        bounds = drawing["bounds_mm"]
        unit = drawing["units"] or "units"
        parts = [
            f"Imported {drawn} contour(s) - {elements} sketch element(s) - from "
            f"'{_basename(drawing['file'])}' ({drawing['format'].upper()}, "
            f"{unit} x {drawing['scale']:g} = "
            f"{drawing['mm_per_file_unit']:g} mm per file unit).",
            (f" Bounds X {bounds[0]:.3f}..{bounds[2]:.3f} and "
             f"Y {bounds[1]:.3f}..{bounds[3]:.3f} mm: check one dimension you "
             "know against this before building anything on it."
             if bounds else ""),
            f" {closed} of {drawn} contour(s) came out closed.",
            " " + " ".join(str(created).split()),
            close_note,
        ]
        stitch = drawing.get("stitch") or {}
        if stitch.get("stitched"):
            parts.append(
                f" {stitch['stitched']} of these came from joining separate "
                "edges that share an endpoint - a CAD export writes one entity "
                "per edge, not one per profile, so without that join nothing on "
                "this drawing could be padded.")
        if stitch.get("branches"):
            parts.append(
                f" {stitch['branches']} joint(s) had more than one unused edge "
                "meeting them and were left OPEN rather than guessed: a wrong "
                "guess is a profile the drawing never had. Narrow with `region` "
                "or `layers` to one view and try again.")
        dropped = counts["dropped"]
        if any(dropped.values()):
            parts.append(" Dropped: " + ", ".join(
                f"{value} by {reason}" for reason, value in dropped.items()
                if value) + ".")
        if notes:
            parts.append(" " + " ".join(notes))
        if drawing["notes"]:
            parts.append(" " + " ".join(drawing["notes"][:3]))
        return "".join(parts)


def _basename(path: Any) -> str:
    import os
    return os.path.basename(str(path))


def _segments(contour: dict) -> list[dict]:
    """Contour -> the segment list ``catia_sketch_profile`` draws.

    Arcs keep their centre/radius/angles: the angle form is exact, while the
    endpoint form makes the sketch rebuild the circle through three points (see
    ``_plan_segment``), which would move the geometry by the fit's own error on
    every arc of every imported drawing.
    """
    out: list[dict] = []
    for piece in contour["pieces"]:
        kind = piece["t"]
        if kind == "line":
            if blueprint._dist(piece["p1"], piece["p2"]) < 1e-9:
                continue
            out.append({"type": "line",
                        "p1": [piece["p1"][0], piece["p1"][1]],
                        "p2": [piece["p2"][0], piece["p2"][1]]})
        elif kind == "arc":
            out.append({"type": "arc", "center": [piece["c"][0], piece["c"][1]],
                        "radius": piece["r"],
                        "start_angle": piece["a0"], "end_angle": piece["a1"],
                        "direction": "ccw" if piece["ccw"] else "cw"})
        elif kind == "poly":
            points = piece["pts"]
            for first, second in zip(points, points[1:]):
                if blueprint._dist(first, second) < 1e-9:
                    continue
                out.append({"type": "line", "p1": [first[0], first[1]],
                            "p2": [second[0], second[1]]})
    return out


def _layer_table(contours: list[dict]) -> list[dict]:
    """What geometry is on which layer - the thing that decides `layers`."""
    table: dict[str, dict] = {}
    for contour in contours:
        name = str(contour.get("layer") or "")
        entry = table.setdefault(name, {"layer": name, "contours": 0,
                                        "closed": 0, "elements": 0})
        entry["contours"] += 1
        entry["closed"] += 1 if contour.get("closed") else 0
        entry["elements"] += blueprint.segment_count(contour)
    return sorted(table.values(), key=lambda row: -row["elements"])
