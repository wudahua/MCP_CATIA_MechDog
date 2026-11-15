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

from catia_mcp import blueprint, fidelity, sketch_frames
from catia_mcp.results import ArgumentRejected, PreconditionRejected
from catia_mcp.tools.sketcher import SketcherTools

#: Sketch elements one import may create. A pad on a 3 000-element profile takes
#: minutes in CATIA and is unusable afterwards; refusing with the count is
#: cheaper than the wait, and `max_elements` overrides it.
MAX_ELEMENTS = 2000


class BlueprintTools:
    """Tools for reading DXF/SVG/PDF drawings into sketches."""

    #: catia_read_drawing parses a file and nothing else, so the server must not
    #: open a session for it - reading a drawing has to work while CATIA is busy
    #: with something else, and on a machine where it is not installed at all.
    #: catia_import_drawing is NOT here: it creates a sketch.
    NO_SESSION = frozenset({"catia_read_drawing"})

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
                    "[xmin, ymin, xmax, ymax] in MILLIMETRES, in the DRAWING's "
                    "own coordinates - the frame `drawing_bounds_mm` reports, NOT "
                    "the shifted one `bounds_mm` reports. A contour is kept when "
                    "its bounding-box CENTRE is inside - not when it is fully "
                    "inside - so a view that crosses the window edge is not "
                    "dropped silently. The two frames differ whenever "
                    "origin='bbox' moves the result, which is why both are in the "
                    "reply: they were one frame in the reply and two in this "
                    "filter until 0.3.130 (2026-11-14 report, N8)."
                ),
            },
            "page": {
                "type": "integer",
                "description": (
                    "Which page of a multi-page PDF to read, counted from 1 in "
                    "the order the pages are written. Omit for all pages - and "
                    "for a multi-page drawing you almost certainly want one: the "
                    "pages sit in the same coordinate system, so two views land "
                    "on top of each other and the bounds describe neither. "
                    "`pages` in the reply says how many there are and how big "
                    "each one is."
                ),
            },
            "curve_tolerance": {
                "type": "number",
                "description": (
                    "PDF only: how far, in mm, a sampled curve may wander from "
                    "the real one. Default 0.05. Curves that ARE circular arcs - "
                    "the four Beziers drawing software uses for a circle - are "
                    "fitted back to real arcs regardless, so a hole stays round; "
                    "this decides the density only for the ones that are not."
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
                    "leaders are open by nature. With closed_only, "
                    "catia_import_drawing refuses BEFORE drawing anything if a "
                    "kept contour still has a free end - rather than letting "
                    "catia_pad say so three steps later."
                ),
            },
            "max_aspect": {
                "type": "number",
                "description": (
                    "Drop contours whose bounding box is longer than this many "
                    "times its shorter side. A drawing frame's edge line is 346 "
                    "mm long and 0.17 mm thick: closed, far above any min_size, "
                    "and never a part. 50 keeps a 20:1 slot and removes the "
                    "frame; 20 starts removing long thin real features."
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
                    "new sketch at 1:1, one call. In DXF and SVG lines stay lines, "
                    "arcs stay arcs (bulges included) and circles stay circles, so "
                    "the pad built on the import is not faceted. A PDF stores only "
                    "Beziers: the four that draw a circle are recognised and drawn "
                    "as a real arc, and the rest are sampled to within "
                    "curve_tolerance - so a PDF hole is round too, and the reply "
                    "says how many of each ('Curves: ...'). The sketch is "
                    "created on the plane you name and closed at the end, and the "
                    "reply reports what CATIA itself then sees - element count "
                    "and outline length - plus the bounds in mm, which is the "
                    "number to check against a dimension you know. Filters "
                    "(layers / region / min_size / closed_only / max_aspect / page) exist "
                    "because a "
                    "drawing is several views plus a frame; run "
                    "catia_read_drawing on the same file first to choose them "
                    "from the counts instead of guessing. REFUSES rather than "
                    "truncates when the profile would exceed max_elements. "
                    "MULTI-VIEW: pass 'views' instead of page/region/plane and "
                    "each view becomes its OWN sketch, all placed on ONE sheet "
                    "coordinate system shifted so a single anchor sits at the "
                    "sketch origin - which is what makes two orthographic views "
                    "line up. The SHARED AXIS IS THE VIEW'S 'u' DIRECTION: two "
                    "views line up when their u vectors point the same way, and "
                    "a frame object is the only form that says so exactly. The "
                    "two that matter can be pasted: a FRONT view (looking along "
                    "-Y) is {'type':'frame','origin':[0,0,0],'u':[1,0,0],"
                    "'v':[0,0,1]}, a TOP view (looking along +Z) is the same "
                    "with 'v':[0,1,0]. Measured on B30 2026-11-15: a 60 x 40 "
                    "view on the front frame and a 60 x 25 view on the top frame "
                    "padded to an X range of 0..60 in BOTH solids. 'xy'/'yz'/'zx' "
                    "also work as planes, but their u vectors differ (zx's u is "
                    "+Z), so a front view on 'xy' and a top view on 'zx' share "
                    "nothing - the reply says so instead of pretending. It "
                    "reports each view's box in world XYZ and which axes the "
                    "views agree on, because 'aligned' is the claim that decides "
                    "whether the model is a part or two unrelated blocks."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        **filter_props,
                        "plane": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "object"},
                            ],
                            "default": "xy",
                            "description": (
                                "Which plane to sketch on: 'xy', 'yz', 'zx', the "
                                "name of a datum plane / planar face, or a "
                                "{'type': 'frame', 'origin': [x,y,z], 'u': [...], "
                                "'v': [...]} object - EXACTLY the values "
                                "catia_create_sketch takes, which until 0.3.135 "
                                "this description claimed and this schema refused "
                                "('received object; expected string'). The frame "
                                "form is what places two orthographic views on a "
                                "shared datum, so it is the one that matters for a "
                                "multi-view sheet."
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
                            "description": (
                                "'bbox' moves the kept geometry so its lower-left "
                                "corner sits at the sketch origin - convenient, "
                                "and it DESTROYS the sheet's own coordinates, so "
                                "two views imported from one sheet no longer "
                                "line up. 'as_drawn' keeps them, which is what a "
                                "multi-view reconstruction needs (the entity then "
                                "lands 250 mm away from the origin, which is "
                                "correct and surprising). Same two values and the "
                                "same meaning as catia_read_drawing's origin."
                            ),
                        },
                        "profile_mode": {
                            "type": "string",
                            "enum": ["segments", "spline"],
                            "default": "segments",
                            "description": (
                                "What each contour becomes in the sketch. "
                                "'segments' (default) draws the primitive each "
                                "piece deserves - lines stay lines, arcs stay "
                                "arcs - which a pad/pocket accepts and a "
                                "loft/sweep section does NOT, because those need "
                                "ONE curve per section. 'spline' draws each "
                                "multi-element contour as ONE closed spline "
                                "through its points, so the same sketch can be a "
                                "loft or sweep section; single-primitive contours "
                                "(a circle, an ellipse) are left alone, since "
                                "they are already one curve. THE SPLINE IS "
                                "INTERPOLATING: it passes through every point and "
                                "ROUNDS every corner (measured 2026-11-03: a "
                                "20x12 rectangle as a closed spline lofted to "
                                "24.04 x 21.46 mm), and arcs are sampled at 24 "
                                "points each. The reply reports the point count, "
                                "the sharpest turn and how many corners are "
                                "sharper than 20 degrees, so a profile with real "
                                "corners can go back to 'segments'."
                            ),
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
                        "views": {
                            "type": "array",
                            "description": (
                                "Import SEVERAL views of one sheet in one call, "
                                "one sketch each, all sharing one anchor so they "
                                "line up. Each entry takes the same filters as the "
                                "top level, PLUS 'plane', 'offset' and 'name' for "
                                "that view alone. 'plane' decides where that "
                                "view's sheet axes land in 3D, and the SHARED AXIS "
                                "is the one both views' 'u' point along - see the "
                                "front/top frame pair in this tool's own "
                                "description. Mutually exclusive with the "
                                "top-level page/region/layers/min_size/"
                                "max_aspect/closed_only/plane/offset/name - those "
                                "would be ambiguous, and this refuses rather than "
                                "picks. `origin` is ignored here on purpose: "
                                "'bbox' would put each view's own corner at (0,0) "
                                "and destroy the shared coordinates, so a "
                                "multi-view import always keeps the sheet's own."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "page": {"type": "integer"},
                                    "region": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 4,
                                        "maxItems": 4,
                                        "description": "[x0, y0, x1, y1] in "
                                                       "drawing mm.",
                                    },
                                    "layers": {
                                        "anyOf": [
                                            {"type": "string"},
                                            {"type": "array",
                                             "items": {"type": "string"}},
                                        ]
                                    },
                                    "min_size": {"type": "number"},
                                    "max_aspect": {"type": "number"},
                                    "closed_only": {"type": "boolean"},
                                    "plane": {
                                        "anyOf": [{"type": "string"},
                                                  {"type": "object"}],
                                        "default": "xy",
                                    },
                                    "offset": {"type": "number"},
                                    "name": {
                                        "type": "string",
                                        "description": (
                                            "Sketch name for this view. A name "
                                            "already in use is REFUSED, not "
                                            "suffixed - a plan that reads the "
                                            "name back needs it to be the one "
                                            "it asked for."
                                        ),
                                    },
                                    "placement": {
                                        "type": "object",
                                        "description": (
                                            "Move THIS view so a chosen sheet "
                                            "point becomes its sketch origin: "
                                            "{'origin_2d': [x, y]} in drawing mm. "
                                            "The shared anchor moves the whole "
                                            "sheet and cannot close the gap "
                                            "between views a real drawing lays "
                                            "out side by side; this is the "
                                            "explicit fix. Mutually exclusive "
                                            "with 'align'."
                                        ),
                                        "properties": {
                                            "origin_2d": {
                                                "type": "array",
                                                "items": {"type": "number"},
                                                "minItems": 2,
                                                "maxItems": 2,
                                            },
                                        },
                                    },
                                    "align": {
                                        "type": "object",
                                        "description": (
                                            "Line THIS view up with an EARLIER "
                                            "one, by name: {'view': 1, 'axis': "
                                            "'x', 'anchor': 'center', "
                                            "'offset': 0}. The view is moved "
                                            "along the drawing axis by the "
                                            "difference between the two anchor "
                                            "points, so 'its centre line lands "
                                            "on view 1's centre line' is one "
                                            "call instead of arithmetic. Only "
                                            "earlier views: a later one may "
                                            "itself be moved afterwards."
                                        ),
                                        "properties": {
                                            "view": {"type": "integer"},
                                            "axis": {
                                                "type": "string",
                                                "enum": ["x", "y"],
                                                "default": "x",
                                            },
                                            "anchor": {
                                                "type": "string",
                                                "enum": ["min", "center", "max"],
                                                "default": "center",
                                            },
                                            "offset": {"type": "number"},
                                        },
                                        "required": ["view"],
                                    },
                                },
                            },
                        },
                        "anchor": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                            "description": (
                                "The DRAWING point, in mm, that becomes the "
                                "sketch origin for every view. Default: the "
                                "lower-left corner of the union of all the views' "
                                "boxes, which puts the whole sheet in positive "
                                "coordinates and keeps the views' relative "
                                "positions. Give it explicitly only to put a "
                                "known feature - a centre line, a datum hole - at "
                                "the origin."
                            ),
                        },
                        "deviation": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "With profile_mode='spline', measure how far the "
                                "spline strayed from the outline it replaced and "
                                "report it as deviation_mm. The probes are the "
                                "outline's own side midpoints - where an "
                                "interpolating curve bulges - plus three corners, "
                                "which must read 0.0000 mm; when they do not, the "
                                "reply says the reading is NOT calibrated instead "
                                "of quoting it. Measured on B30 2026-11-15 on a "
                                "100 x 40 rectangle: a corner reads 0.0000, a side "
                                "midpoint 33.1387 mm. Needs close=true (the "
                                "measurement happens after the sketch closes), and "
                                "costs one point element per probe."
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
            # The frame `region` is compared in: the drawing's own coordinates,
            # before origin='bbox' moved anything. Both are reported because they
            # differ whenever origin='bbox' and only one of them was in the reply
            # while the other was in the filter (N8).
            "drawing_bounds_mm": ([round(v, 4) for v in drawing["drawing_bounds_mm"]]
                                  if drawing.get("drawing_bounds_mm") else None),
            "page_bounds_mm": ([round(v, 4) for v in drawing["page_bounds_mm"]]
                               if drawing.get("page_bounds_mm") else None),
            "pages": drawing.get("pages"),
            "origin_shift_mm": [round(v, 4) for v in drawing["origin_shift_mm"]],
            # Where the size cliff is. 4196 contours and a `min_size` guessed by
            # bisection is what this replaces (N7).
            "size_histogram_mm": blueprint.size_histogram(diagonals),
            "layers": _layer_table(contours),
            "stitching": drawing.get("stitch"),
            # PAGE-WIDE, not about this one contour: 775 arcs fitted and 110
            # lines in the chosen profile are both true of the same file. The
            # key says which frame of reference it is in, because "775 arcs but
            # Profile: 110 line(s)" read like a bug in two reports.
            "page_level_stats": drawing["stats"],
            "stats": drawing["stats"],
            # Two names for one dict, kept because 0.3.135's callers read 'stats'.
            # Saying so beats removing it silently, and beats letting a reader
            # assume the two describe different scopes - which is exactly what
            # the 2026-11-15 report (N6) assumed when it saw them identical.
            "deprecated_fields": {
                "stats": "identical to page_level_stats; read that instead. This "
                         "alias is removed in the next release."},
            "notes": drawing["notes"],
            "contours": [
                {
                    "index": index,
                    "source": contour.get("source"),
                    "layer": contour.get("layer"),
                    "closed": contour["closed"],
                    # The pad's own count, per contour: `closed` can be true while
                    # a joint in the middle is open, and this is the number that
                    # decides whether catia_pad will accept the profile.
                    "free_ends": len(blueprint.contour_free_ends(contour)),
                    "elements": blueprint.segment_count(contour),
                    "bbox_mm": [round(v, 4) for v in blueprint.contour_bbox(contour)],
                    # Long side / short side. A frame's edge line is closed, above
                    # any min_size, and 2000:1 - the number that says so is here so
                    # `max_aspect` can be chosen from data (N9).
                    "aspect": _aspect(blueprint.contour_bbox(contour)),
                    # How many SEPARATE curves this contour becomes, and what
                    # that allows. `closed` and `free_ends` answer "can it be
                    # padded"; they do not answer "can it be a loft section",
                    # and a 110-line closed outline cannot (2026-11-15 reports
                    # R1/R5: closed=1, free_ends=0, elements=110 read as
                    # loft-ready and the loft then refused it).
                    "curves": blueprint.curve_count(contour),
                    "loft_ready": blueprint.curve_count(contour) == 1,
                }
                for index, contour in enumerate(contours[:limit], start=1)
            ],
            "described": min(limit, len(contours)),
        }
        pages = drawing.get("pages") or []
        if len(pages) > 1:
            report["notes"] = list(report["notes"]) + [
                f"This file has {len(pages)} page(s) and NONE was selected, so "
                "every page was read into one coordinate system: two views land on "
                "top of each other and `bounds_mm` describes neither. Pass "
                "page=N to read one."]
        return json.dumps(report, ensure_ascii=False, indent=2)

    # ── import ────────────────────────────────────────────────────────────
    def _profile_mode(self, arguments: dict[str, Any]) -> str:
        """'segments' or 'spline', decided before anything is read or drawn.

        BEFORE the file is read and long before a session: a typo here decides
        what the sketch IS, and discovering it after 110 elements are drawn
        means deleting them one by one.
        """
        raw_mode = arguments.get("profile_mode")
        # An empty string is a typo, not a request for the default: `or` would
        # swallow it, and a caller who passed "" deserves to hear about it.
        mode = "segments" if raw_mode is None else str(raw_mode).strip().lower()
        if mode not in ("segments", "spline"):
            raise ArgumentRejected(
                f"profile_mode={arguments.get('profile_mode')!r} is not one of "
                "'segments' or 'spline'. 'segments' draws each piece with its own "
                "primitive (a pad accepts that outline; a loft section does not, "
                "because it needs ONE curve); 'spline' draws each multi-element "
                "contour as one closed spline. Nothing was read and nothing was "
                "drawn.", code="BAD_ARGUMENT", stage="validate")
        return mode

    def _import_drawing(self, arguments: dict[str, Any]) -> str:
        views = arguments.get("views")
        if views is not None:
            return self._import_views(arguments, views)
        mode = self._profile_mode(arguments)
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

        # THE VERDICT IS TAKEN BEFORE ANYTHING IS DRAWN, with the rule catia_pad
        # itself applies (an endpoint no other segment meets). The 2026-11-14
        # report built 1 273 sketch elements and then had pad answer "six free
        # endpoints" - two verdicts on one sketch, the second one three steps
        # later. If `closed_only` was asked for, an open contour in the result
        # means the filter did not do what its name says, and saying so here
        # costs nothing because nothing has been created yet.
        open_ends = sum(len(blueprint.contour_free_ends(c)) for c in contours)
        if open_ends and arguments.get("closed_only"):
            listed = ", ".join(
                f"({x:.4f}, {y:.4f})"
                for contour in contours
                for x, y in blueprint.contour_free_ends(contour)[:2])[:300]
            raise PreconditionRejected(
                f"'closed_only' kept {len(contours)} contour(s), but {open_ends} "
                f"endpoint(s) among them are met by nothing - {listed} - so a pad "
                "on this sketch would be refused. Nothing was drawn. A gap of a "
                "few microns still counts: the joint was accepted by the stitcher "
                "and closed here, and what is left is geometry that really does "
                "not meet. Widen `tolerance` (it only decides what counts as "
                "joined, it does not move anything), drop `closed_only` to import "
                "the open curves too, or narrow with `region` / `min_size`.",
                code="PROFILE_NOT_CLOSED", stage="validate",
                data={"contours": len(contours), "free_ends": open_ends,
                      "counts": drawing["counts"]})

        sketcher = self._sketcher_tools()
        plane = arguments.get("plane") or "xy"
        offset = arguments.get("offset")
        created = sketcher._create_sketch(plane, offset)
        notes: list[str] = []
        closed = 0
        drawn = 0
        # What the sketch actually holds, contour by contour - the number the
        # caller needs before choosing pad vs loft, and the one this tool used to
        # leave them to infer from an unrelated field.
        profile: dict[str, Any] = {"mode": mode, "curves": 0, "splined": 0,
                                   "points": 0, "kept_primitives": 0,
                                   "sharp_corners": 0, "sharpest": None}
        for index, contour in enumerate(contours, start=1):
            try:
                reply = self._draw_contour(sketcher, contour, arguments, mode,
                                           profile)
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

        sketch_name = str(getattr(sketcher._active_sketch, "Name", "") or "")
        try:
            drawn_elements = int(sketcher._active_sketch.GeometricElements.Count)
        except Exception:  # noqa: BLE001 - the parsed count is still reported
            drawn_elements = elements
        close_note = ""
        if arguments.get("close", True):
            close_note = " " + " ".join(str(sketcher._close_sketch()).split())
        else:
            notes.append("The sketch is still OPEN, so no element count from "
                         "CATIA is quoted here - call catia_close_sketch when the "
                         "editing is done.")

        # AFTER the close, because the measurement needs the sketch out of edit
        # mode: GetMinimumDistance is asked of a real element, and an element
        # inside an open sketch answers "方法 … 失败".
        deviation = None
        if mode == "spline" and profile.get("outlines"):
            if not arguments.get("close", True):
                notes.append(
                    "deviation_mm was NOT measured: the probes need the sketch "
                    "closed (close=true). The conversion is still interpolating - "
                    "catia_gsd_spline on the same outline measures it.")
            elif arguments.get("deviation", True):
                deviation = self._measure_deviation(sketcher, profile)

        return self._import_reply(drawing, drawn, closed, elements, created,
                                  close_note, notes, open_ends,
                                  profile=profile, sketch_name=sketch_name,
                                  drawn_elements=drawn_elements,
                                  deviation=deviation)

    def _measure_deviation(self, sketcher: SketcherTools,
                           profile: dict[str, Any]) -> dict[str, Any]:
        """How far each converted spline strayed from the outline it replaced.

        Probes are COORDINATE points (a point-on-curve cannot be put on a
        Spline2D, measured 2026-11-15) and the target is the Spline2D itself
        rather than the whole sketch: the sketch also holds one point element per
        control point, and a minimum distance over the sketch could be answered
        by a control point instead of by the curve.
        """
        placement = getattr(sketcher, "_last_placement", None)
        if sketch_frames.frame_of(placement) is None:
            return {"error": "the sketch's frame is unknown here, so its 2D "
                             "points cannot be turned into world XYZ",
                    "samples": 0}
        rows = []
        for outline in profile.get("outlines") or []:
            probes = [sketch_frames.point_world(placement, p[0], p[1])
                      for p in outline.get("probes") or []]
            calibration = [sketch_frames.point_world(placement, p[0], p[1])
                           for p in outline.get("calibration") or []]
            probes = [p for p in probes if p]
            calibration = [p for p in calibration if p]
            if not probes:
                continue
            target = outline.get("curve")
            if target is None:
                return {"error": "the spline object of this contour was not "
                                 "recorded, so there is nothing to measure "
                                 "against", "samples": 0}
            try:
                rows.append(fidelity.probe_points(self.conn, target, probes,
                                                  calibration))
            except Exception as error:  # noqa: BLE001 - reported, not raised
                return {"error": str(error).splitlines()[0][:140], "samples": 0}
        if not rows:
            return {"error": "no probe could be built", "samples": 0}
        # Worst contour wins: the reply is about the sketch, and a clean contour
        # must not average away the one that bulged.
        worst = max((row for row in rows if row.get("max_mm") is not None),
                    key=lambda row: float(row["max_mm"]), default=None)
        summary = dict(worst or rows[0])
        summary["contours"] = len(rows)
        summary["left_behind"] = sum(int(row.get("left_behind") or 0)
                                     for row in rows)
        if len(rows) > 1:
            summary["all_max_mm"] = [row.get("max_mm") for row in rows]
        return summary

    # ── multi-view import ─────────────────────────────────────────────────
    #: Top-level keys that belong to ONE view once `views` is used. Passing them
    #: beside `views` is ambiguous - "which view did that region mean" - so the
    #: call is refused instead of guessed.
    _VIEW_CONFLICTS = ("page", "region", "layers", "min_size", "max_aspect",
                       "closed_only", "plane", "offset", "name")

    def _check_view_moves(self, views: Any) -> None:
        """Validate every view's ``placement`` / ``align`` before the file is read.

        A move that is malformed is a typo, and a typo must not cost a PDF parse:
        the same ordering rule ``catia_open_document`` learned in 0.3.127 when it
        connected to CATIA before refusing a non-ASCII name.
        """
        total = len(views)
        for index, view in enumerate(views, start=1):
            placement_arg = view.get("placement")
            align_arg = view.get("align")
            if placement_arg is not None and align_arg is not None:
                raise ArgumentRejected(
                    f"views[{index}] has both 'placement' and 'align'. Both move "
                    "the view, and applying two moves makes the result depend on "
                    "their order. Give one. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate")
            if placement_arg is not None:
                point = (placement_arg.get("origin_2d")
                         if isinstance(placement_arg, dict) else None)
                try:
                    values = [float(v) for v in (point or [])]
                except (TypeError, ValueError):
                    values = []
                if len(values) != 2:
                    raise ArgumentRejected(
                        f"views[{index}].placement takes {{'origin_2d': [x, y]}} "
                        "in DRAWING mm - the sheet point that should become this "
                        f"view's sketch origin; got {placement_arg!r}. Nothing "
                        "was read.", code="BAD_ARGUMENT", stage="validate")
                continue
            if align_arg is None:
                continue
            if not isinstance(align_arg, dict):
                raise ArgumentRejected(
                    f"views[{index}].align takes an object like {{'view': 1, "
                    f"'axis': 'x', 'anchor': 'center'}}; got {align_arg!r}. "
                    "Nothing was read.", code="BAD_ARGUMENT", stage="validate")
            target = align_arg.get("view")
            if isinstance(target, bool) or not isinstance(target, int):
                raise ArgumentRejected(
                    f"views[{index}].align needs an integer 'view' (the view this "
                    f"one should line up with); got {align_arg.get('view')!r}. "
                    "Nothing was read.", code="BAD_ARGUMENT", stage="validate")
            if not 1 <= target <= total or target == index:
                raise ArgumentRejected(
                    f"views[{index}].align names view {target}; it has to be "
                    f"another view of this call (1..{total}, not this one). "
                    "Nothing was read.", code="BAD_ARGUMENT", stage="validate")
            if target > index:
                raise ArgumentRejected(
                    f"views[{index}] aligns to view {target}, which comes LATER in "
                    "the list. Align to an EARLIER view: a later one may itself be "
                    "moved afterwards, and the result would depend on the order of "
                    "the list. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate")
            axis_text = str(align_arg.get("axis") or "x").strip().lower()
            if axis_text not in ("x", "y", "u", "v"):
                raise ArgumentRejected(
                    f"views[{index}].align axis is 'x' or 'y' (the drawing's own "
                    f"axes); got {align_arg.get('axis')!r}. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate")
            which = str(align_arg.get("anchor") or "center").strip().lower()
            if which not in ("min", "center", "max"):
                raise ArgumentRejected(
                    f"views[{index}].align anchor is 'min', 'center' or 'max'; got "
                    f"{align_arg.get('anchor')!r}. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate")
            try:
                float(align_arg.get("offset") or 0.0)
            except (TypeError, ValueError):
                raise ArgumentRejected(
                    f"views[{index}].align offset is a distance in mm; got "
                    f"{align_arg.get('offset')!r}. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate") from None

    def _import_views(self, arguments: dict[str, Any], views: Any) -> str:
        """Several views of one sheet, one sketch each, sharing one anchor.

        The 2026-11-15 report's C2 was "multi-view has no datum": each view could
        be imported, but every import put its own lower-left corner at (0,0), so
        two orthographic views ended up in unrelated places. The fix is not a
        coordinate system per view but ONE for the sheet: every view is moved by
        the same shift, and each still says which plane it lies in.
        """
        mode = self._profile_mode(arguments)
        if isinstance(views, dict) or not isinstance(views, (list, tuple)):
            raise ArgumentRejected(
                f"'views' takes a list of views; got {type(views).__name__}. "
                "One entry per view: {'page': 1, 'region': [...], 'plane': "
                "'xy'}. Nothing was read.", code="BAD_ARGUMENT", stage="validate")
        if not views:
            raise ArgumentRejected(
                "'views' was empty. Pass one entry per view, or leave it out and "
                "use page/region/plane for a single view. Nothing was read.",
                code="BAD_ARGUMENT", stage="validate")
        clashing = [key for key in self._VIEW_CONFLICTS
                    if arguments.get(key) not in (None, False, "", [])]
        if clashing:
            raise ArgumentRejected(
                "These belong to one view each and cannot sit beside 'views': "
                + ", ".join(clashing)
                + ". Move them into the view that needs them - every entry takes "
                "page/region/layers/min_size/max_aspect/closed_only/plane/"
                "offset/name of its own. Nothing was read.",
                code="BAD_ARGUMENT", stage="validate")
        raw_origin = arguments.get("origin")
        if raw_origin is not None and str(raw_origin).strip().lower() != "as_drawn":
            raise ArgumentRejected(
                f"origin={raw_origin!r} cannot be used with 'views': 'bbox' moves "
                "each view's OWN lower-left corner to (0,0), which is exactly what "
                "destroys the alignment between views. A multi-view import always "
                "keeps the sheet's own coordinates and moves them all by one "
                "anchor (see 'anchor'). Nothing was read.",
                code="BAD_ARGUMENT", stage="validate")

        anchor_arg = arguments.get("anchor")
        anchor: tuple[float, float] | None = None
        if anchor_arg is not None:
            try:
                values = [float(v) for v in anchor_arg]
            except (TypeError, ValueError):
                values = []
            if len(values) != 2:
                raise ArgumentRejected(
                    f"anchor takes two numbers, [x, y] in drawing mm; got "
                    f"{anchor_arg!r}. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate")
            anchor = (values[0], values[1])

        path = str(arguments.get("file_path") or "")
        # EVERY entry is checked before ANY file is read: a bad entry at the end
        # of the list must not be discovered after three views have been parsed
        # (and, without this loop, never - the first read fails first).
        for index, view in enumerate(views, start=1):
            if not isinstance(view, dict):
                raise ArgumentRejected(
                    f"views[{index}] is a {type(view).__name__}; each entry is an "
                    "object like {'page': 1, 'region': [0, 0, 60, 40], "
                    "'plane': 'xy'}. Nothing was read.",
                    code="BAD_ARGUMENT", stage="validate")
        # Before any file is read: a malformed move is a typo the caller can fix
        # without the drawing being there (the ordering rule 0.3.127 learned).
        self._check_view_moves(views)
        scale = float(arguments.get("scale") or 1.0)
        tolerance = float(arguments.get("tolerance") or 1e-6)
        curve_tolerance = arguments.get("curve_tolerance")
        parsed: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for index, view in enumerate(views, start=1):
            layers = view.get("layers")
            if isinstance(layers, str):
                layers = [layers]
            page = view.get("page")
            if page in (None, ""):
                page = None
            drawing = blueprint.read_drawing(
                path, layers=layers, region=view.get("region"),
                min_size=view.get("min_size"), max_aspect=view.get("max_aspect"),
                closed_only=bool(view.get("closed_only", False)),
                tolerance=tolerance, page=page, scale=scale,
                curve_tolerance=curve_tolerance, origin="as_drawn")
            if not drawing["contours"]:
                raise PreconditionRejected(
                    f"View {index} of {len(views)} kept no contour: "
                    f"{drawing['counts']['dropped']}. Nothing was drawn. Loosen "
                    "that view's region / min_size / closed_only - "
                    "catia_read_drawing on the same arguments reports what each "
                    "filter would keep.",
                    code="NOTHING_TO_IMPORT", stage="validate",
                    data={"view": index, "dropped": drawing["counts"]["dropped"]})
            parsed.append((index, view, drawing))

        # ── the anchor: one sheet point that becomes (0,0) in every view ────
        if anchor is None:
            boxes = [blueprint.contour_bbox(contour)
                     for _, _, drawing in parsed for contour in drawing["contours"]]
            anchor = (min(box[0] for box in boxes), min(box[1] for box in boxes))
        for _, _, drawing in parsed:
            blueprint.move_to_anchor(drawing, anchor)

        # ── per-view placement, because a real sheet puts views SIDE BY SIDE ──
        # The anchor moves the whole sheet, so two orthographic views stay 70 mm
        # apart on it - which is correct for the sheet and useless for a model.
        # A drawing shares DIMENSIONS, not page coordinates, so each view can be
        # moved onto a common datum by name (2026-11-15 report N3). Every argument
        # was checked before the file was read; what is left here is arithmetic.
        shift_notes: dict[int, str] = {}
        for index, view, drawing in parsed:
            placement_arg = view.get("placement")
            align_arg = view.get("align")
            if placement_arg is not None:
                values = [float(v) for v in placement_arg["origin_2d"]]
                blueprint.shift_drawing(drawing, -(values[0] - anchor[0]),
                                        -(values[1] - anchor[1]))
                shift_notes[index] = (
                    f" moved so the sheet point ({values[0]:.4f}, "
                    f"{values[1]:.4f}) is the sketch origin")
            elif align_arg is not None:
                target = int(align_arg["view"])
                axis_text = str(align_arg.get("axis") or "x").strip().lower()
                axis = 0 if axis_text in ("x", "u") else 1
                which = str(align_arg.get("anchor") or "center").strip().lower()
                offset = float(align_arg.get("offset") or 0.0)
                mine = drawing["bounds_mm"]
                theirs = parsed[target - 1][2]["bounds_mm"]
                if not mine or not theirs:
                    raise PreconditionRejected(
                        f"views[{index}] asks to align to view {target}, but one "
                        "of the two kept no geometry to line up. Nothing was "
                        "drawn.", code="NOTHING_TO_IMPORT", stage="validate")
                delta = (_anchor_value(theirs, axis, which) + offset
                         - _anchor_value(mine, axis, which))
                blueprint.shift_drawing(drawing, delta if axis == 0 else 0.0,
                                        delta if axis == 1 else 0.0)
                shift_notes[index] = (
                    f" moved {delta:+.4f} mm along {axis_text.upper()} so its "
                    f"{which} lands on view {target}'s {which}")


        total_elements = sum(drawing["counts"]["elements"]
                             for _, _, drawing in parsed)
        limit = arguments.get("max_elements")
        try:
            limit = MAX_ELEMENTS if limit is None else int(limit)
        except (TypeError, ValueError):
            limit = MAX_ELEMENTS
        if total_elements > limit:
            raise ArgumentRejected(
                f"These {len(parsed)} view(s) would create {total_elements} sketch "
                f"elements in total, above the limit of {limit}. Nothing was "
                "drawn. Narrow the views' region / min_size, or import fewer of "
                "them.", code="TOO_MANY_ELEMENTS", stage="validate",
                data={"elements": total_elements, "limit": limit,
                      "views": [drawing["counts"]["elements"]
                                for _, _, drawing in parsed]})

        sketcher = self._sketcher_tools()
        deviation_on = bool(arguments.get("deviation", True))
        close = bool(arguments.get("close", True))
        rows: list[dict[str, Any]] = []
        for index, view, drawing in parsed:
            if bool(view.get("closed_only", False)):
                open_ends = sum(len(blueprint.contour_free_ends(c))
                                for c in drawing["contours"])
                if open_ends:
                    raise PreconditionRejected(
                        f"View {index} asked for closed contours only, but "
                        f"{open_ends} endpoint(s) among its "
                        f"{len(drawing['contours'])} contour(s) are met by "
                        "nothing, so a pad on that sketch would be refused. "
                        "Nothing was drawn. Widen that view's tolerance, or drop "
                        "closed_only to import the open curves too.",
                        code="PROFILE_NOT_CLOSED", stage="validate",
                        data={"view": index, "free_ends": open_ends})
            # The per-view arguments are the ones that place geometry: which
            # plane, how far off it, and what the sketch is called. Everything
            # else (mode, tolerance, construction) is one decision for the whole
            # call, and it lives at the top level.
            view_args = dict(arguments)
            view_args.update({key: view[key] for key in ("plane", "offset")
                              if key in view})
            created = sketcher._create_sketch(view_args.get("plane") or "xy",
                                              view_args.get("offset"),
                                              name=view.get("name"))
            profile: dict[str, Any] = {"mode": mode, "curves": 0, "splined": 0,
                                       "points": 0, "kept_primitives": 0,
                                       "sharp_corners": 0, "sharpest": None}
            for contour in drawing["contours"]:
                try:
                    self._draw_contour(sketcher, contour, view_args, mode, profile)
                except Exception as error:  # noqa: BLE001 - reported with its view
                    raise PreconditionRejected(
                        f"View {index} ('{view.get('name') or 'unnamed'}') could "
                        "not be drawn "
                        f"({str(error).splitlines()[0][:140]}). The views before "
                        "it ARE in the part, and this one is half drawn - nothing "
                        "was rolled back: a half-imported view is easier to "
                        "delete than to rebuild.",
                        code="CONTOUR_FAILED", stage="execute", modified=True
                    ) from error
            sketch_name = str(getattr(sketcher._active_sketch, "Name", "") or "")
            try:
                drawn_elements = int(sketcher._active_sketch.GeometricElements.Count)
            except Exception:  # noqa: BLE001
                drawn_elements = drawing["counts"]["elements"]
            placement = getattr(sketcher, "_last_placement", None)
            close_note = ""
            if close:
                close_note = " " + " ".join(str(sketcher._close_sketch()).split())
            else:
                close_note = (" This sketch is still OPEN: no element count from "
                              "CATIA is quoted for it, and its deviation cannot be "
                              "measured until it is closed.")
            deviation = None
            if mode == "spline" and profile.get("outlines") and close and deviation_on:
                deviation = self._measure_deviation(sketcher, profile)
            bounds = drawing["bounds_mm"]
            rows.append({
                "index": index,
                "name": sketch_name,
                "asked_name": view.get("name"),
                "plane": view.get("plane") or "xy",
                "mode": mode,
                "offset": view.get("offset"),
                "elements": drawing["counts"]["elements"],
                "closed": drawing["counts"]["closed"],
                "curves": int(profile.get("curves") or 0),
                "drawn_elements": drawn_elements,
                "bounds_mm": [round(v, 4) for v in bounds] if bounds else None,
                "box_mm": _view_box(placement, bounds) if bounds else None,
                "frame": sketch_frames.frame_of(placement),
                "shift_note": shift_notes.get(index, ""),
                "deviation": deviation,
                "notes": [line for line in (close_note,) if line],
            })
        return self._views_reply(arguments, parsed, rows, anchor)

    def _views_reply(self, arguments: dict[str, Any],
                     parsed: list[tuple[int, dict[str, Any], dict[str, Any]]],
                     rows: list[dict[str, Any]],
                     anchor: tuple[float, float]) -> str:
        drawing = parsed[0][2]
        unit = drawing["units"] or "units"
        total = sum(row["elements"] for row in rows)
        parts = [
            f"Imported {len(rows)} view(s) - {total} sketch element(s) in total - "
            f"from '{_basename(drawing['file'])}' ({drawing['format'].upper()}, "
            f"{unit} x {drawing['scale']:g} = {drawing['mm_per_file_unit']:g} mm "
            "per file unit) onto ONE sheet frame.",
            (f" The drawing point ({anchor[0]:.4f}, {anchor[1]:.4f}) sits at the "
             "sketch origin in EVERY view, so the views keep the alignment they "
             "have on the sheet. That is the whole point of importing them "
             "together: `origin='bbox'` one at a time would put each view's own "
             "corner at (0,0) and lose it."),
        ]
        for row in rows:
            name = row["name"] or "(unnamed)"
            where = row["plane"]
            if isinstance(where, dict):
                origin = where.get("origin")
                where = (f"a frame at {origin}" if origin
                         else f"a frame ({where.get('type')})")
            parts.append(f"\n  [{row['index']}] sketch '{name}' on {where}")
            if row.get("asked_name") and row["asked_name"] != row["name"]:
                parts.append(f" (ASKED FOR '{row['asked_name']}' and CATIA called "
                             f"it '{row['name']}' - a by-name tool written "
                             "against the requested name will miss it)")
            if row.get("offset") is not None:
                parts.append(f" offset {float(row['offset']):g} mm")
            parts.append(f": {row['elements']} element(s), {row['curves']} "
                         f"curve(s), {row['closed']} contour(s) closed")
            if row.get("bounds_mm"):
                b = row["bounds_mm"]
                parts.append(f"; sheet box {b[2] - b[0]:.3f} x {b[3] - b[1]:.3f} mm")
            if row.get("box_mm"):
                parts.append("; world box " + _box_text(row["box_mm"]))
            parts.append(".")
            extra = (row["drawn_elements"] or 0) - row["elements"]
            if extra:
                # WHY the two counts differ, measured on B30 2026-11-15: a sketch
                # holding four lines reports FIVE elements, because its own
                # absolute axis (绝对轴) is one of them - and profile_mode='spline'
                # adds a point element per control point on top of that. Saying
                # "the control points" for a segments import was wrong the first
                # time this reply was written.
                why = ["the sketch's own absolute axis is an element"]
                if row.get("mode") == "spline":
                    why.append("and a 2D spline is defined through point "
                               "elements, one per control point")
                parts.append(f" CATIA counts {row['drawn_elements']} elements in "
                             "this sketch, not "
                             f"{row['elements']}: " + "; ".join(why) + ". "
                             "They are not profile geometry - pad and loft read "
                             "the curves.")
            for note in row.get("notes") or []:
                parts.append(" " + " ".join(str(note).split()))
            if row.get("shift_note"):
                parts.append(row["shift_note"] + ".")
            parts.append(fidelity.deviation_sentence(row.get("deviation")))

        # ── the claim the anchor exists to make, and where it can fail ─────
        readable = [row for row in rows if row.get("box_mm")]
        if len(readable) < 2:
            parts.append("\n Alignment check: at least one view has no readable "
                         "world box (its frame is not one this server can turn "
                         "into XYZ), so no shared axis could be compared.")
        else:
            verdicts = view_alignment(readable)
            agreed: list[str] = []
            offset: list[str] = []
            for pair in verdicts:
                left, right = pair["views"]
                if pair["aligned"]:
                    for item in pair["shared"]:
                        agreed.append(
                            f"view {left} and view {right} agree on "
                            f"{AXIS_NAMES[item['axis']]} "
                            f"({item['left_range'][0]:.3f}.."
                            f"{item['left_range'][1]:.3f} mm, overlap "
                            f"{item['ratio'] * 100:.0f} %"
                            + ("" if item["same_direction"] else
                               ", mirrored") + ")")
                elif pair["shared"]:
                    detail = "; ".join(
                        f"{AXIS_NAMES[item['axis']]} "
                        f"{item['left_range'][0]:.3f}..{item['left_range'][1]:.3f} "
                        f"vs {item['right_range'][0]:.3f}.."
                        f"{item['right_range'][1]:.3f} mm" for item in pair["shared"])
                    offset.append(f"view {left} and view {right} share a sheet "
                                  f"direction but do not line up: {detail}")
                else:
                    offset.append(f"view {left} and view {right} share NO sheet "
                                  "direction")
            if agreed and not offset:
                parts.append("\n Alignment check: " + "; ".join(agreed)
                             + ". Every shared sheet axis overlaps, so these "
                               "sketches sit in one coordinate system - a model "
                               "built on them is one part.")
            elif agreed:
                parts.append("\n Alignment check: " + "; ".join(agreed)
                             + ". BUT " + "; ".join(offset) + ".")
            else:
                parts.append("\n Alignment check: NOT aligned - "
                             + "; ".join(offset) + ". Nothing is wrong with the "
                               "geometry, but these views do not share a frame: "
                               "give each view the frame it looks along (a FRONT "
                               "view is {'type':'frame','origin':[0,0,0],"
                               "'u':[1,0,0],'v':[0,0,1]}, a TOP view the same "
                               "with 'v':[0,1,0]'), and where the sheet puts the "
                               "views side by side, give one of them 'align' or "
                               "'placement' to move it onto the shared datum.")
            # The shapes have to be comparable before their ranges can be: a
            # corner of a sketch is its own (x, y) and an upside-down range in
            # this sentence would be quoted back as evidence (report N2).
            for row in readable:
                box = row["box_mm"]
                if any(box[index] > box[3 + index] + 1e-9 for index in range(3)):
                    parts.append(f" WARNING: view {row['index']}'s world box has "
                                 "min > max, so its frame is not one this server "
                                 "read correctly - treat the ranges above as "
                                 "unreliable.")
        parts.append("\n Nothing has been built on these sketches: pad or loft "
                     "each one on its own plane, in the order the drawing's "
                     "dimensions want.")
        notes = drawing.get("notes") or []
        if notes:
            parts.append(" " + " ".join(notes[:2]))
        return "".join(parts)

    def _parse(self, arguments: dict[str, Any]) -> dict[str, Any]:
        layer_values = arguments.get("layers")
        if isinstance(layer_values, str):
            layer_values = [layer_values]
        page = arguments.get("page")
        if page in (None, ""):
            page = None
        return blueprint.read_drawing(
            str(arguments.get("file_path") or ""),
            scale=float(arguments.get("scale") or 1.0),
            layers=layer_values,
            region=arguments.get("region"),
            min_size=arguments.get("min_size"),
            max_aspect=arguments.get("max_aspect"),
            closed_only=bool(arguments.get("closed_only", False)),
            origin=str(arguments.get("origin") or "bbox"),
            tolerance=float(arguments.get("tolerance") or 1e-6),
            page=page,
            curve_tolerance=arguments.get("curve_tolerance"),
        )

    def _draw_contour(self, sketcher: SketcherTools, contour: dict,
                      arguments: dict[str, Any], mode: str = "segments",
                      profile: dict[str, Any] | None = None) -> str:
        """One contour, drawn with the primitive each piece deserves.

        ``mode="spline"`` makes each contour ONE curve, which is the difference
        between a sketch a pad accepts and a sketch a loft accepts. The 2026-11-15
        report found that `catia_gsd_spline(element=<imported sketch>)` could do
        this after the fact; doing it while the points are already in hand costs
        one call and no round trip through the session record.
        """
        pieces = contour["pieces"]
        construction = bool(arguments.get("construction", False))
        tolerance = float(arguments.get("tolerance") or 0.05)
        stats = profile if profile is not None else {}

        # A circle or an ellipse IS one curve already. Converting it to a spline
        # would replace an exact conic with an interpolation of 24 sampled points
        # - all cost, no benefit.
        if len(pieces) == 1 and pieces[0]["t"] == "circle":
            stats["kept_primitives"] = int(stats.get("kept_primitives", 0)) + 1
            stats["curves"] = int(stats.get("curves", 0)) + 1
            centre, radius = pieces[0]["c"], pieces[0]["r"]
            return sketcher._draw_circle(centre[0], centre[1], radius)
        if len(pieces) == 1 and pieces[0]["t"] == "ellipse":
            stats["kept_primitives"] = int(stats.get("kept_primitives", 0)) + 1
            stats["curves"] = int(stats.get("curves", 0)) + 1
            piece = pieces[0]
            return sketcher._draw_ellipse(piece["c"][0], piece["c"][1],
                                          piece["rx"], piece["ry"],
                                          piece.get("angle") or 0.0,
                                          construction)
        if mode == "spline":
            points = blueprint.spline_points(contour)
            if len(points) >= 3:
                corners = blueprint.corner_report(points)
                stats["splined"] = int(stats.get("splined", 0)) + 1
                stats["points"] = int(stats.get("points", 0)) + len(points)
                stats["curves"] = int(stats.get("curves", 0)) + 1
                stats["sharp_corners"] = (int(stats.get("sharp_corners", 0))
                                          + int(corners["corner_count"]))
                if corners["sharpest_turn_deg"] is not None:
                    stats["sharpest"] = max(float(stats.get("sharpest") or 0.0),
                                            float(corners["sharpest_turn_deg"]))
                flat = [[float(p[0]), float(p[1])] for p in points]
                reply = sketcher._draw_spline([list(p) for p in points],
                                              closed=True)
                # What the deviation measurement will probe: the side midpoints
                # (where an interpolating curve bulges) and three corners (which
                # must read 0.0000, or the reading is not calibrated). Collected
                # HERE because this is the only place the outline's own points
                # exist - a CATIA sketch cannot give them back.
                probes, calibration = fidelity.outline_probes(flat)
                stats.setdefault("outlines", []).append({
                    "probes": probes,
                    "calibration": calibration,
                    "curve": getattr(sketcher, "_last_drawn_spline", None),
                    "corners": len(flat),
                })
                return reply
            # Two points cannot be a closed spline. Falling back to segments is
            # the only honest option, and the reply says how many did.
            stats["spline_fallback"] = int(stats.get("spline_fallback", 0)) + 1
        segments = _segments(contour)
        if not segments:
            return ""
        stats["curves"] = int(stats.get("curves", 0)) + blueprint.curve_count(contour)
        return sketcher._draw_profile(segments, tolerance)

    def _import_reply(self, drawing: dict, drawn: int, closed: int,
                      elements: int, created: str, close_note: str,
                      notes: list[str], open_ends: int = 0,
                      profile: dict[str, Any] | None = None,
                      sketch_name: str = "",
                      drawn_elements: int | None = None,
                      deviation: dict[str, Any] | None = None) -> str:
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
        ]
        profile = profile or {}
        curves = int(profile.get("curves") or 0)
        mode = str(profile.get("mode") or "segments")
        if curves:
            # The field both 2026-11-15 reports had to infer: `closed` says a pad
            # will take the outline, and NOTHING said a loft will not.
            parts.append(
                f" This sketch holds {curves} curve(s). pad / pocket accept that; "
                "a loft, a sweep section and a multi-section surface need ONE "
                "curve per section" + (" - this one qualifies."
                                      if curves == 1 else
                                      ", so this outline cannot be one as it is."))
        if mode == "segments" and curves > 1:
            hint = (f"catia_gsd_spline(element='{sketch_name}', closed=true)"
                    if sketch_name else "catia_gsd_spline(element='<sketch>', "
                                        "closed=true)")
            parts.append(
                " Two ways to make it one curve: re-run this import with "
                "profile_mode='spline' (each multi-element contour becomes one "
                f"closed spline), or convert this sketch afterwards with {hint}. "
                "Both are interpolating - they pass through the points and round "
                "the corners - so a profile with real corners is better cut with "
                "one sketch per corner run.")
        elif mode == "spline":
            splined = int(profile.get("splined") or 0)
            kept = int(profile.get("kept_primitives") or 0)
            points = int(profile.get("points") or 0)
            detail = []
            if splined:
                detail.append(f"{splined} contour(s) were drawn as ONE closed "
                              f"spline through {points} point(s)")
            if kept:
                detail.append(f"{kept} contour(s) were already a single curve "
                              "(a circle or an ellipse) and were left exact")
            fallback = int(profile.get("spline_fallback") or 0)
            if fallback:
                detail.append(f"{fallback} contour(s) had fewer than 3 points "
                              "and were drawn as segments")
            if detail:
                parts.append(" profile_mode='spline': " + "; ".join(detail) + ".")
            if drawn_elements is not None and drawn_elements != elements:
                parts.append(
                    f" CATIA's own element count for this sketch is "
                    f"{drawn_elements}, not {elements}: every control point of a "
                    "2D spline is a sketch element, and the sketch's own absolute "
                    "axis is one too (measured 2026-11-15: a four-LINE rectangle "
                    "reports 5 elements for 4, and a 4-point closed spline leaves "
                    "5 point elements beside the spline; the collection has no "
                    "Remove). They are not profile geometry - the sketch holds "
                    f"{curves} curve(s) for pad and loft either way.")
            sharpest = profile.get("sharpest")
            sharp = int(profile.get("sharp_corners") or 0)
            if sharpest:
                parts.append(
                    f" Sharpest turn in the point chain: {float(sharpest):.1f} deg, "
                    f"{sharp} corner(s) sharper than 20 deg - an interpolating "
                    "spline ROUNDS those (measured 2026-11-03: a 20x12 rectangle "
                    "lofted to 24.04 x 21.46 mm). If this profile really has "
                    "corners, import it with profile_mode='segments' and build the "
                    "loft from a section that is one curve for another reason.")
            parts.append(fidelity.deviation_sentence(deviation))
        if open_ends:
            parts.append(
                f" {open_ends} endpoint(s) are met by nothing, so a pad on this "
                "sketch is refused - that is the same count catia_pad reports, "
                "taken here before you build on it. Pass closed_only=true to keep "
                "the closed contours only, or widen `tolerance` if the gap is "
                "rounding.")
        parts.append(" " + " ".join(str(created).split()))
        parts.append(close_note)
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
        stats = drawing.get("stats") or {}
        fitted = int(stats.get("arcs_fitted") or 0)
        flattened = int(stats.get("curves_flattened") or 0)
        if fitted or flattened:
            # N6: the description promised "arcs stay arcs" while a PDF's Beziers
            # were silently sampled into 110 chords. Saying which is which, per
            # import, is the only way the promise can be checked.
            detail = []
            if fitted:
                detail.append(f"{fitted} curve(s) were recognised as circular and "
                              "drawn as real arcs, so those holes are round")
            if flattened:
                detail.append(f"{flattened} curve(s) were not arcs and are drawn as "
                              "chords within the requested curve_tolerance")
            parts.append(" Page-level curves (the whole sheet, not this "
                         "profile): " + "; ".join(detail) + ".")
        pages = drawing.get("pages") or []
        if len(pages) > 1:
            parts.append(
                f" This file has {len(pages)} page(s) and all of them were read, "
                "into ONE coordinate system - pass page=N to import a single view.")
        if notes:
            parts.append(" " + " ".join(notes))
        if drawing["notes"]:
            parts.append(" " + " ".join(drawing["notes"][:3]))
        return "".join(parts)


def _anchor_value(bounds: Any, axis: int, which: str) -> float:
    """One number off a 2D bounds list: its low end, its middle, or its high end."""
    low, high = float(bounds[axis]), float(bounds[axis + 2])
    if which == "min":
        return low
    if which == "max":
        return high
    return (low + high) / 2.0


def _box_text(box: Any) -> str:
    """``X a..b Y c..d Z e..f`` from the six numbers ``_view_box`` builds.

    The first version of this call passed the whole list to ``format(*box)``,
    which put min-x against min-y: the reply said ``X 0.000..-0.000 Y
    0.000..15.367 Z 23.876..0.000`` for a 15 x 24 mm sketch, and two of the three
    ranges came out inverted (2026-11-15 report N2). The alignment check below
    read the same list correctly, so the bug was in the words, not the numbers -
    which is exactly the kind that gets quoted back as evidence.
    """
    return ("X {:.3f}..{:.3f} Y {:.3f}..{:.3f} Z {:.3f}..{:.3f}"
            .format(box[0], box[3], box[1], box[4], box[2], box[5]))


AXIS_NAMES = ("X", "Y", "Z")


def view_axes(frame: Any) -> dict[str, tuple[int, float]]:
    """Which world axis each of a view's own sheet axes points along.

    ``frame`` is ``(origin, u, v)`` as ``sketch_frames.frame_of`` returns it -
    already resolved, because the caller has one per view by the time a reply is
    written. Only an AXIS-ALIGNED frame can share a sheet direction with another
    view; a frame rotated by 30 degrees shares nothing a range comparison could
    judge. Returns ``{'u': (axis, sign), 'v': (axis, sign)}``, missing the axes
    that are not axis-aligned.
    """
    if not isinstance(frame, (tuple, list)) or len(frame) != 3:
        return {}
    _origin, u, v = frame
    out: dict[str, tuple[int, float]] = {}
    for role, vector in (("u", u), ("v", v)):
        axis = max(range(3), key=lambda index: abs(float(vector[index])))
        if abs(float(vector[axis])) < 0.999:
            continue
        out[role] = (axis, 1.0 if float(vector[axis]) > 0 else -1.0)
    return out


def view_alignment(rows: list[dict]) -> list[dict]:
    """Every pair of views: the sheet axes they share, and whether they line up.

    Three things had to change from the first version of this check, all three
    from the 2026-11-15 report (N1):

    * **A zero-width range is a PLANE, not a direction.** Two views both on 'xy'
      both have Z = 0, so "they agree on Z (0.000..0.000)" was true of every pair
      of co-planar views ever imported, including two that sat 70 mm apart.
    * **Only a frame's u/v can be a shared SHEET axis.** The normal is not a
      direction the drawing has.
    * **Agreement means the ranges overlap**, and every shared axis has to.
      Sharing one axis and missing the other is an offset view, not an aligned
      one.

    ``ratio`` is the overlap divided by the narrower of the two ranges, so 1.0 is
    co-located and 0.0 is "70 mm apart on the sheet".
    """
    pairs: list[dict] = []
    for left_index in range(len(rows)):
        for right_index in range(left_index + 1, len(rows)):
            left, right = rows[left_index], rows[right_index]
            left_box = left.get("box") or left.get("box_mm")
            right_box = right.get("box") or right.get("box_mm")
            if not left_box or not right_box:
                continue
            left_axes = view_axes(left.get("frame"))
            right_axes = view_axes(right.get("frame"))
            shared: list[dict] = []
            for left_role, (left_axis, left_sign) in left_axes.items():
                for right_role, (right_axis, right_sign) in right_axes.items():
                    if left_axis != right_axis:
                        continue
                    low_left, high_left = (left_box[left_axis],
                                           left_box[3 + left_axis])
                    low_right, high_right = (right_box[right_axis],
                                             right_box[3 + right_axis])
                    width_left = high_left - low_left
                    width_right = high_right - low_right
                    if width_left <= 1e-6 or width_right <= 1e-6:
                        continue
                    overlap = max(0.0, min(high_left, high_right)
                                  - max(low_left, low_right))
                    shared.append({
                        "axis": left_axis,
                        "left_role": left_role,
                        "right_role": right_role,
                        "same_direction": left_sign == right_sign,
                        "left_range": (low_left, high_left),
                        "right_range": (low_right, high_right),
                        "overlap_mm": overlap,
                        "ratio": overlap / min(width_left, width_right),
                    })
            if not shared:
                pairs.append({"views": (left.get("index"), right.get("index")),
                              "shared": [], "aligned": False,
                              "reason": "no shared sheet direction"})
                continue
            aligned = all(item["ratio"] >= 0.9 for item in shared)
            reason = ("aligned" if aligned else
                      "shared direction but the ranges do not line up"
                      if any(item["ratio"] < 0.05 for item in shared)
                      else "shared direction, partly overlapping")
            pairs.append({"views": (left.get("index"), right.get("index")),
                          "shared": shared, "aligned": aligned, "reason": reason})
    return pairs


def _view_box(placement: Any, bounds: Any) -> list[float] | None:
    """World XYZ box of a view's sheet box, through the frame it was drawn in.

    A sketch exposes no measurable at all (measured on B30 2026-11-15: Length,
    Area, Volume, GetPoint, GetCOG, GetBoundingBox and GetPlane all answer
    「方法 … 失败」 on a sketch reference), so the box is computed from its four
    corners and the frame - and it is None when the frame is one this server
    cannot turn into XYZ, because an unknown frame is an unknown place.
    """
    if not bounds:
        return None
    corners = [(bounds[0], bounds[1]), (bounds[2], bounds[1]),
               (bounds[2], bounds[3]), (bounds[0], bounds[3])]
    points = [sketch_frames.point_world(placement, u, v) for u, v in corners]
    if any(point is None for point in points):
        return None
    low = [min(point[i] for point in points) for i in range(3)]
    high = [max(point[i] for point in points) for i in range(3)]
    return ([round(v, 4) for v in low] + [round(v, 4) for v in high])


def _aspect(box: Any) -> float | None:
    """Long side / short side of a bounding box; None when it has no area."""
    long_side = max(box[2] - box[0], box[3] - box[1])
    short_side = min(box[2] - box[0], box[3] - box[1])
    if short_side <= 1e-9:
        return None
    return round(long_side / short_side, 2)


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
