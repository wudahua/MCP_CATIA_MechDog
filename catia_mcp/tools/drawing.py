"""Drafting (Drawing) tools for CATIA V5.

Ported from upstream in 0.3.64, where its eleven tools were exercised on B30
(2026-11-05) one by one rather than trusted from their docstrings. What that
found, and what changed:

  * face-driven projection could not work here at all. ``Selection.Search``
    takes a LOCALIZED query on a Chinese install (``拓扑.面,sel`` answers, the
    English ``Topology.Face,sel`` raises ``方法 Search 失败``), and upstream's
    indices were the last feature's rather than the body's. It now resolves
    ``Face.N`` through the same ``topology_cells`` path as catia_list_faces, so
    the index means the same thing to both.
  * ``catia_drawing_add_dimension`` is refused when index 1 is asked for: item 1
    of a view's GeometricElements is the ABSOLUTE AXIS, exactly as in a sketch,
    and constraining it gave CATIA's bare ``方法 Add 失败``.
  * a distance between a horizontal and a vertical line is refused BY CATIA, not
    by this tool, and the reply now says which pairings work instead of
    forwarding the COM error.
  * ``catia_drawing_add_view`` no longer demands ``part_name`` when several parts
    are open and one of them is the part this session last built.

Everything else was measured working: drawing creation, generative front view
(0.3 s for a plate), projection, section, detail, text, table with filled cells,
and GenerateDimensions producing an associative 3d_driven radius.

Dimensioning route, unchanged from upstream and confirmed: CATIA never exposes
the projected curves of generative views to Automation, so generative views are
dimensioned from the part's 3D sketch constraints through
DrawingSheet.GenerateDimensions. The manual Add path only works on 2D geometry
drawn in the view.
"""

from __future__ import annotations

import re
from typing import Any

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.measurement import read_measurable_array

# DefineFrontView takes the two sheet-plane axis vectors expressed in the
# 3D part's coordinate system.
# CatProjViewType (R20 automation enum, verified against the V5 reference
# binding): the projection of a view relative to its parent.
_PROJECTION_TYPES = {"right": 0, "left": 1, "top": 2, "bottom": 3, "rear": 4}

# A view's Name is printed under it on the sheet, so the defaults are Chinese
# like the rest of the sheet furniture. Pass 'name' and nothing is translated.
_PROJECTION_NAMES = {
    "right": "右视图", "left": "左视图", "top": "俯视图",
    "bottom": "仰视图", "rear": "后视图",
}

_PLANE_VECTORS = {
    "xy": (1, 0, 0, 0, 1, 0),
    "yz": (0, 1, 0, 0, 0, 1),
    "zx": (0, 0, 1, 1, 0, 0),
}

# CatPaperSize, read out of the DrawingSheet library on B30 2026-11-05:
#   catPaperA0 = 2 ... catPaperA4 = 6, catPaperUser = 13
# The enum also names Letter(0), Legal(1) and A..F(7-12); writing any of those on
# this install raises 方法 PaperSize 失败, so offering them would be offering a
# call that cannot work.
_PAPER_CODES = {"A0": 2, "A1": 3, "A2": 4, "A3": 5, "A4": 6}
_PAPER_NAMES = {code: name for name, code in _PAPER_CODES.items()}

# CatPaperOrientation: portrait = 0, landscape = 1, bestfit = 2.
# 2 is accepted and reads back 1, i.e. it is silently ignored - not offered.
_ORIENTATION_CODES = {"landscape": 1, "portrait": 0}

# Title-block field labels. Chinese is the default because this installation is
# a Chinese CATIA and its drafting font (SICH) does carry Chinese glyphs - a
# table cell written as '材料' renders as 材料 in the exported PDF (probe
# scripts/b30_v0366_zh_font.py, B30 2026-11-05). The English set stays
# available because ISO 5457 sheets are routinely exchanged that way.
_LABELS = {
    "zh": {
        "material": "材料", "scale": "比例", "drawn": "制图",
        "date": "日期", "checked": "审核", "sheet": "张次",
    },
    "en": {
        "material": "MATERIAL", "scale": "SCALE", "drawn": "DRAWN",
        "date": "DATE", "checked": "CHECKED", "sheet": "SHEET",
    },
}
_LANGUAGE_ALIASES = {
    "zh": "zh", "zh-cn": "zh", "cn": "zh", "chinese": "zh", "中文": "zh",
    "en": "en", "en-us": "en", "english": "en", "英文": "en",
}


def _normalise_language(value: Any) -> str:
    key = str(value).strip().lower()
    language = _LANGUAGE_ALIASES.get(key)
    if language is None:
        raise ValueError(
            f"Unknown language '{value}'. Use 'zh' (中文, default) or 'en'. "
            "To change a single label instead, pass labels={'material': ...}."
        )
    return language


class DraftingTools:
    """Tools for CATIA V5 Drawing documents (generative drafting)."""

    def __init__(self, connection: CATIAConnection) -> None:
        self.conn = connection

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "catia_new_drawing",
                "description": (
                    "Create a new empty Drawing document (CATDrawing) for "
                    "generative drafting. The drawing becomes the active "
                    "document; the source Part stays open and is referenced "
                    "by catia_drawing_add_view."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "catia_drawing_list_view_geometry",
                "description": (
                    "List the manually drawn 2D geometry of a drawing view "
                    "as indexed elements for catia_drawing_add_dimension. "
                    "Field-proven on V5R20: generated projection curves are "
                    "NOT exposed here (GeometricElements shows only the axis "
                    "and Factory2D items). To dimension generative views use "
                    "catia_drawing_generate_dimensions. Defaults to the last "
                    "view of the active sheet."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view_name": {
                            "type": "string",
                            "description": "View to inspect (default: last view of the active sheet)",
                        },
                    },
                },
            },
            {
                "name": "catia_drawing_add_dimension",
                "description": (
                    "Add a dimension between one or two manually drawn 2D "
                    "elements (indices from catia_drawing_list_view_geometry). "
                    "Works on Factory2D geometry only: the projected curves of a "
                    "generative view are not addressable, so use "
                    "catia_drawing_generate_dimensions for those. INDEX 1 IS THE "
                    "VIEW'S ABSOLUTE AXIS and is refused - the first element you "
                    "draw is index 2. Which pairings CATIA accepts is not "
                    "negotiable and not this tool's choice: two parallel lines "
                    "give a distance, two lines that meet give an angle, an arc "
                    "gives a radius, and a horizontal line with a vertical one "
                    "gives nothing (measured on B30 2026-11-05)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["distance", "length", "angle", "radius", "diameter"],
                            "description": (
                                "Dimension type. 'length'/'radius'/'diameter' "
                                "take one element; 'distance'/'angle' take two. "
                                "For the length of one line use 'length' - a "
                                "'distance' on a single element is the same CATIA "
                                "dimension but reads as if a second element were "
                                "missing."
                            ),
                        },
                        "element_index_1": {
                            "type": "integer",
                            "description": "First element index from catia_drawing_list_view_geometry",
                        },
                        "element_index_2": {
                            "type": "integer",
                            "description": "Second element index (distance/angle)",
                        },
                        "view_name": {
                            "type": "string",
                            "description": "Target view (default: last view of the active sheet)",
                        },
                    },
                    "required": ["type", "element_index_1"],
                },
            },
            {
                "name": "catia_drawing_generate_dimensions",
                "description": (
                    "Generate associative dimensions on a generative view "
                    "from the part's 3D sketch constraints (one dimension per "
                    "distance/length/angle/radius/diameter constraint). This "
                    "is the only route that dimensions generated geometry in "
                    "CATIA V5 (field-proven on R20): add constraints with "
                    "catia_sketch_constraint, then call this. Reports the "
                    "dimensions with values; generated ones follow the 3D "
                    "model (status 3d_driven)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view_name": {
                            "type": "string",
                            "description": "Target view (default: last view of the active sheet)",
                        },
                    },
                },
            },
            {
                "name": "catia_drawing_list_dimensions",
                "description": (
                    "List the dimensions of a drawing view with their values "
                    "and status (basic_2d or 3d_driven). Regenerates the view "
                    "first so 3d_driven values reflect the current part "
                    "(set refresh=false to skip)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view_name": {
                            "type": "string",
                            "description": "View to inspect (default: last view of the active sheet)",
                        },
                        "refresh": {
                            "type": "boolean",
                            "description": "Regenerate the view before reading (default true)",
                        },
                    },
                },
            },
            {
                "name": "catia_drawing_projection_view",
                "description": (
                    "Add a projection view (right/left/top/bottom/rear) "
                    "derived from an existing generative view, placed next "
                    "to it at the same scale. DefineProjectionView with the "
                    "documented CatProjViewType enum."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["right", "left", "top", "bottom", "rear"],
                            "description": "Projection relative to the parent view",
                        },
                        "parent_view": {
                            "type": "string",
                            "description": "Parent view name (default: last view of the active sheet)",
                        },
                        "name": {"type": "string", "description": "New view name (default: 右视图/左视图/俯视图/仰视图/后视图 for the direction)"},
                        "gap": {
                            "type": "number",
                            "description": "Distance from the parent view in mm (default 100)",
                        },
                        "x": {"type": "number", "description": "Explicit X position in mm (overrides gap)"},
                        "y": {"type": "number", "description": "Explicit Y position in mm (overrides gap)"},
                    },
                    "required": ["direction"],
                },
            },
            {
                "name": "catia_drawing_section_view",
                "description": (
                    "Add a section view or section cut from a cutting "
                    "profile drawn as a polyline in the parent view's 2D "
                    "coordinates (mm). DefineSectionView per the V5 "
                    "reference: profile points, section type, profile type, "
                    "side to draw, parent view."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Flat list [x1, y1, x2, y2, ...] of the cutting polyline in parent view coordinates (mm), at least two points",
                        },
                        "parent_view": {"type": "string", "description": "Parent view name (default: last view)"},
                        "section_type": {
                            "type": "string",
                            "enum": ["SectionView", "SectionCut"],
                            "description": "SectionView shows geometry behind the plane, SectionCut only the cut (default SectionView)",
                        },
                        "profile_type": {
                            "type": "string",
                            "enum": ["Offset", "Aligned"],
                            "description": "Cutting profile type (default Offset)",
                        },
                        "side": {
                            "type": "integer",
                            "enum": [0, 1],
                            "description": "Side to draw: 0 clockwise, 1 counterclockwise (default 1)",
                        },
                        "name": {"type": "string", "description": "New view name"},
                        "x": {"type": "number", "description": "X position in mm (default: parent x + 150)"},
                        "y": {"type": "number", "description": "Y position in mm (default: parent y)"},
                    },
                    "required": ["profile"],
                },
            },
            {
                "name": "catia_drawing_detail_view",
                "description": (
                    "Add a circular detail view of a region of the parent "
                    "view (center and radius in the parent view's 2D "
                    "coordinates, mm). DefineCircularDetailView."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "center_x": {"type": "number", "description": "Circle center X in parent view coordinates (mm)"},
                        "center_y": {"type": "number", "description": "Circle center Y in parent view coordinates (mm)"},
                        "radius": {"type": "number", "description": "Circle radius (mm)"},
                        "parent_view": {"type": "string", "description": "Parent view name (default: last view)"},
                        "scale": {"type": "number", "description": "Detail scale factor (default 2)"},
                        "name": {"type": "string", "description": "New view name"},
                        "x": {"type": "number", "description": "X position in mm (default: parent x + 150)"},
                        "y": {"type": "number", "description": "Y position in mm (default: parent y + 100)"},
                    },
                    "required": ["center_x", "center_y", "radius"],
                },
            },
            {
                "name": "catia_drawing_add_text",
                "description": (
                    "Add a text annotation to a drawing view at (x, y) mm in "
                    "the view's coordinate system (DrawingTexts.Add). Use the "
                    "sheet's Background view for title block text."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text content"},
                        "x": {"type": "number", "description": "X in mm (view coordinates)"},
                        "y": {"type": "number", "description": "Y in mm (view coordinates)"},
                        "view_name": {
                            "type": "string",
                            "description": "Target view (default: last view; use 'Background View' for frames and title blocks)",
                        },
                    },
                    "required": ["text", "x", "y"],
                },
            },
            {
                "name": "catia_drawing_add_table",
                "description": (
                    "Add a table to a drawing view (DrawingTables.Add) and "
                    "optionally fill its cells. Building block for title "
                    "blocks, revision tables and BOMs: pass 'cells' as rows "
                    "of strings, optional per-column widths, and the "
                    "Background View as target for sheet-level tables."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "Table X in mm (view coordinates)"},
                        "y": {"type": "number", "description": "Table Y in mm (view coordinates)"},
                        "rows": {"type": "integer", "description": "Number of rows"},
                        "columns": {"type": "integer", "description": "Number of columns"},
                        "row_height": {"type": "number", "description": "Row height in mm (default 8)"},
                        "column_width": {"type": "number", "description": "Column width in mm (default 40)"},
                        "cells": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "string"}},
                            "description": "Optional cell contents, row-major, e.g. [[\"Part\", \"Qty\"], [\"Bracket\", \"2\"]]",
                        },
                        "column_widths": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Optional per-column widths in mm (0 = automatic)",
                        },
                        "view_name": {
                            "type": "string",
                            "description": "Target view (default: last view; use 'Background View' for title blocks)",
                        },
                    },
                    "required": ["x", "y", "rows", "columns"],
                },
            },
            {
                "name": "catia_drawing_add_view",
                "description": (
                    "Add a generative front view of an open Part to the "
                    "active Drawing's active sheet, projected on the chosen "
                    "plane or on a planar face, then update it. Create the "
                    "drawing first with catia_new_drawing; derive other "
                    "views with catia_drawing_projection_view / "
                    "section_view / detail_view. Field-validated on V5R20."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "part_name": {
                            "type": "string",
                            "description": (
                                "Open Part document to represent (e.g. "
                                "'Part1.CATPart'). Defaults to the only "
                                "open Part when unambiguous."
                            ),
                        },
                        "body_name": {
                            "type": "string",
                            "description": (
                                "Optional body to draw alone (e.g. 'PartBody' "
                                "or 'Body.2'); other bodies are excluded. "
                                "Field-validated on V5R20."
                            ),
                        },
                        "plane": {
                            "type": "string",
                            "enum": ["xy", "yz", "zx"],
                            "description": "Projection plane (default xy); ignored when 'face' is given",
                        },
                        "face": {
                            "type": "string",
                            "description": (
                                "Face-driven projection: 'Face.N' of the "
                                "body's final shape, as listed by "
                                "catia_list_faces run with the SAME body_name; "
                                "the view is projected on that planar face's "
                                "own plane, orientation-independent. THE INDEX "
                                "IS SESSION-SCOPED: the same plate gave the "
                                "hole's cylinder at Face.1 in one build and at "
                                "Face.7 in another (B30 2026-11-05), so list "
                                "faces and use the number immediately rather "
                                "than reusing one from an earlier session. A "
                                "non-planar face is refused by name; a "
                                "face-driven view took ~30 s on that plate "
                                "against 0.3 s for a plane view."
                            ),
                        },
                        "angle": {
                            "type": "number",
                            "description": "In-plane rotation of the view in degrees (default 0)",
                        },
                        "x": {
                            "type": "number",
                            "description": "View X position on the sheet in mm (default 300)",
                        },
                        "y": {
                            "type": "number",
                            "description": "View Y position on the sheet in mm (default 150)",
                        },
                        "name": {
                            "type": "string",
                            "description": "View name (default '主视图')",
                        },
                    },
                },
            },
            {
                "name": "catia_drawing_set_sheet",
                "description": (
                    "Set the active sheet's paper size, orientation and scale. "
                    "A NEW DRAWING IS NOT A3: it comes up as catPaperUser (13), "
                    "an oversized custom sheet, so a frame drawn before this runs "
                    "sits on the wrong page. Paper is one of the ISO A sizes - "
                    "CatPaperSize also names Letter/Legal/A..F, and every one of "
                    "them is REFUSED by this install (measured on B30 "
                    "2026-11-05: values 0, 1 and 7-12 all raise), so they are not "
                    "offered. The reply reports the size CATIA says it has, not "
                    "the one asked for."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paper": {
                            "type": "string",
                            "enum": ["A0", "A1", "A2", "A3", "A4"],
                            "description": "ISO paper size (default: leave unchanged)",
                        },
                        "orientation": {
                            "type": "string",
                            "enum": ["landscape", "portrait"],
                            "description": "Default: leave unchanged",
                        },
                        "scale": {
                            "type": "number",
                            "description": (
                                "Sheet scale as a single number, e.g. 2 for 2:1 "
                                "(default: leave unchanged)"
                            ),
                        },
                    },
                },
            },
            {
                "name": "catia_drawing_add_frame",
                "description": (
                    "Draw a border and a title block on the active sheet, in the "
                    "BACKGROUND view, and optionally set the paper first. The "
                    "border is a single line at the margins (20 mm filing margin "
                    "on the left by ISO 5457, 10 mm elsewhere) and the title "
                    "block is a real CATIA table - 8 columns x 5 rows, merged into "
                    "title / drawing number / material / scale / drawn / date / "
                    "checked / sheet / footer - so its cells can be edited later "
                    "in CATIA without redrawing it. Zone markings and the ISO "
                    "corner grid are NOT drawn. Positions are sheet millimetres "
                    "with the origin at the bottom-left; Table.Add anchors the "
                    "block's TOP-left corner and it grows downward (measured)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "paper": {
                            "type": "string",
                            "enum": ["A0", "A1", "A2", "A3", "A4"],
                            "description": "Set the paper before drawing (default: leave as is)",
                        },
                        "orientation": {
                            "type": "string",
                            "enum": ["landscape", "portrait"],
                            "description": "Only used together with paper",
                        },
                        "scale": {
                            "type": "number",
                            "description": "Sheet scale, e.g. 2 for 2:1 (default: leave as is)",
                        },
                        "margin": {
                            "type": "number",
                            "description": "Right/top/bottom margin in mm (default 10)",
                        },
                        "margin_left": {
                            "type": "number",
                            "description": "Left (filing) margin in mm (default 20)",
                        },
                        "border": {
                            "type": "boolean",
                            "description": "Draw the border rectangle (default true)",
                        },
                        "title_block": {
                            "type": "object",
                            "description": (
                                "Title block layout and content. Any field left "
                                "out stays empty rather than being invented."
                            ),
                            "properties": {
                                "width": {"type": "number", "description": "mm (default 180)"},
                                "height": {"type": "number", "description": "mm (default 45)"},
                                "title": {"type": "string"},
                                "drawing_number": {"type": "string"},
                                "material": {"type": "string"},
                                "scale": {"type": "string", "description": "Text, e.g. '1:1'"},
                                "drawn_by": {"type": "string"},
                                "date": {"type": "string"},
                                "checked_by": {"type": "string"},
                                "sheet": {"type": "string", "description": "Text, e.g. '1/2'"},
                                "footer": {"type": "string", "description": "Merged bottom row"},
                            },
                        },
                        "language": {
                            "type": "string",
                            "description": (
                                "Language of the field labels the tool writes: "
                                "'zh' (材料/比例/制图/日期/审核/张次) or 'en' "
                                "(MATERIAL/SCALE/DRAWN/DATE/CHECKED/SHEET). "
                                "Default 'zh' - CATIA's drafting font draws "
                                "Chinese and this install is a Chinese one. "
                                "Your own values (title, material, notes) are "
                                "never translated."
                            ),
                        },
                        "labels": {
                            "type": "object",
                            "description": (
                                "Override single labels, e.g. "
                                "{'checked': '校对'} or {'sheet': 'SHEET NO.'}. "
                                "Keys: material, scale, drawn, date, checked, "
                                "sheet. Takes precedence over language."
                            ),
                            "properties": {
                                "material": {"type": "string"},
                                "scale": {"type": "string"},
                                "drawn": {"type": "string"},
                                "date": {"type": "string"},
                                "checked": {"type": "string"},
                                "sheet": {"type": "string"},
                            },
                        },
                        "notes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lines printed just above the title block",
                        },
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        match tool_name:
            case "catia_new_drawing":
                return self._new_drawing()
            case "catia_drawing_add_view":
                return self._add_view(arguments)
            case "catia_drawing_set_sheet":
                return self._set_sheet(arguments)
            case "catia_drawing_add_frame":
                return self._add_frame(arguments)
            case "catia_drawing_projection_view":
                return self._projection_view(arguments)
            case "catia_drawing_section_view":
                return self._section_view(arguments)
            case "catia_drawing_detail_view":
                return self._detail_view(arguments)
            case "catia_drawing_add_text":
                return self._add_text(arguments)
            case "catia_drawing_add_table":
                return self._add_table(arguments)
            case "catia_drawing_generate_dimensions":
                return self._generate_dimensions(arguments)
            case "catia_drawing_list_dimensions":
                return self._list_dimensions(arguments)
            case "catia_drawing_list_view_geometry":
                return self._list_view_geometry(arguments)
            case "catia_drawing_add_dimension":
                return self._add_dimension(arguments)
            case _:
                raise ValueError(f"Unknown drafting tool: {tool_name}")

    def _new_drawing(self) -> str:
        self.conn.ensure_connected()
        drawing = self.conn.documents.Add("Drawing")
        sheet = drawing.Sheets.ActiveSheet
        return (
            f"Drawing created: '{drawing.Name}' (active sheet '{sheet.Name}', "
            f"{drawing.Sheets.Count} sheet(s)). Use catia_drawing_add_view "
            "to project a part onto it."
        )

    # ── sheet setup and the frame ────────────────────────────────────────────

    def _apply_paper(self, sheet: Any, paper: str | None,
                     orientation: str | None,
                     scale: float | None) -> dict[str, Any]:
        """Set what was asked for, then report what CATIA says it now is.

        The read-back is the point: a new drawing is catPaperUser (13), and
        Orientation could not even be READ on that sheet ("方法 Orientation
        失败") until a real paper size existed. So the reply is built from these
        numbers, never from the arguments.
        """
        if paper is not None:
            name = str(paper).upper()
            code = _PAPER_CODES.get(name)
            if code is None:
                raise ValueError(
                    f"Unknown paper '{paper}'. This server offers "
                    f"{', '.join(_PAPER_CODES)}. CatPaperSize also names "
                    "Letter/Legal/A..F, and CATIA refuses every one of them on "
                    "this installation (values 0, 1, 7-12 all raise)."
                )
            sheet.PaperSize = code
        if orientation is not None:
            key = str(orientation).lower()
            code = _ORIENTATION_CODES.get(key)
            if code is None:
                raise ValueError(
                    f"Unknown orientation '{orientation}'. Use 'landscape' or "
                    "'portrait'. 2 = best fit is accepted by CATIA and silently "
                    "ignored (it reads back as landscape), so it is not offered."
                )
            sheet.Orientation = code
        if scale is not None:
            sheet.Scale = float(scale)

        state: dict[str, Any] = {}
        try:
            state["paper_code"] = int(sheet.PaperSize)
        except Exception:  # noqa: BLE001
            state["paper_code"] = None
        state["paper"] = _PAPER_NAMES.get(state["paper_code"])
        try:
            state["paper_name"] = str(sheet.PaperName)
        except Exception:  # noqa: BLE001
            state["paper_name"] = None
        # GetPaperWidth/Height FAIL on a catPaperUser sheet - and a new drawing is
        # exactly that (PaperSize 13), so this cannot be an unguarded call. The
        # sheet only reports a width once a real ISO size is set (B30 2026-11-05).
        state["width"] = None
        state["height"] = None
        try:
            state["width"] = float(sheet.GetPaperWidth())
            state["height"] = float(sheet.GetPaperHeight())
        except Exception as error:  # noqa: BLE001
            state["size_error"] = str(error).splitlines()[0][:90]
        try:
            state["orientation"] = int(sheet.Orientation)
        except Exception:  # noqa: BLE001
            state["orientation"] = None
        try:
            state["scale"] = float(sheet.Scale)
        except Exception:  # noqa: BLE001
            state["scale"] = None
        return state

    @staticmethod
    def _sheet_summary(state: dict[str, Any]) -> str:
        paper = state.get("paper") or state.get("paper_name") or "custom size"
        orientation = {0: "portrait", 1: "landscape"}.get(
            state.get("orientation"), f"orientation {state.get('orientation')}")
        scale = state.get("scale")
        if state.get("width") is None:
            text = (f"{paper}, {orientation} - CATIA will not report a width "
                    f"for it ({state.get('size_error', 'no size')})")
        else:
            text = (f"{paper} {state['width']:g} x {state['height']:g} mm "
                    f"{orientation}")
        if scale:
            text += f", scale {scale:g}"
        return text

    def _set_sheet(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        drawing = self._find_active_drawing()
        drawing.Activate()
        sheet = drawing.Sheets.ActiveSheet
        before = self._apply_paper(sheet, None, None, None)
        state = self._apply_paper(sheet, args.get("paper"),
                                  args.get("orientation"), args.get("scale"))
        self.conn.refresh_display()
        changed = state != before
        return (
            f"Sheet '{sheet.Name}' is now {self._sheet_summary(state)} "
            f"(read back from CATIA)."
            + ("" if changed else
               " Nothing changed - no paper, orientation or scale was given.")
        )

    def _add_frame(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        drawing = self._find_active_drawing()
        drawing.Activate()
        sheet = drawing.Sheets.ActiveSheet
        state = self._apply_paper(sheet, args.get("paper"),
                                  args.get("orientation"), args.get("scale"))
        if state.get("width") is None or state.get("height") is None:
            raise RuntimeError(
                "This sheet has no ISO paper size, so CATIA cannot say how big "
                f"it is ({state.get('size_error', 'no width reported')}). A new "
                "drawing starts as catPaperUser (13), which is an oversized "
                "custom sheet. Pass paper='A3' (or A0/A1/A2/A4) to this tool, or "
                "call catia_drawing_set_sheet first - a frame drawn on an "
                "unmeasured sheet is a rectangle in the wrong place."
            )
        width, height = state["width"], state["height"]

        margin = float(args.get("margin", 10.0))
        margin_left = float(args.get("margin_left", 20.0))
        if margin <= 0 or margin_left <= 0:
            raise ValueError("margins must be positive (mm)")
        x0, y0 = margin_left, margin
        x1, y1 = width - margin, height - margin
        if x1 - x0 < 100 or y1 - y0 < 60:
            raise ValueError(
                f"The border would leave only {x1 - x0:g} x {y1 - y0:g} mm on a "
                f"{width:g} x {height:g} mm sheet - too small to hold a title "
                "block. Set the paper first (catia_drawing_set_sheet) if this "
                "sheet is still the custom default."
            )

        # Every number is resolved BEFORE anything is drawn. The first version
        # drew the border and then refused an oversized title block, which left
        # four stray lines on the sheet (measured on B30 2026-11-05: the
        # Background view went from 5 elements to 9). A refused call has to
        # leave the drawing alone.
        # Labels resolve before anything is drawn, for the same reason the
        # geometry does: a rejected call must leave the sheet alone.
        labels = dict(_LABELS[language := _normalise_language(args.get("language") or "zh")])
        for key, value in (args.get("labels") or {}).items():
            if key not in labels:
                raise ValueError(
                    f"Unknown label '{key}'. The title block has "
                    f"{', '.join(sorted(labels))}."
                )
            labels[key] = str(value)

        block = args.get("title_block") or {}
        block_w = float(block.get("width", 180.0))
        block_h = float(block.get("height", 45.0))
        if block_w <= 0 or block_h <= 0:
            raise ValueError("title_block width and height must be positive (mm)")
        if block_w > x1 - x0 or block_h > y1 - y0:
            raise ValueError(
                f"The title block is {block_w:g} x {block_h:g} mm but the border "
                f"leaves {x1 - x0:g} x {y1 - y0:g} mm. Shrink the block or the "
                "margins."
            )

        views = sheet.Views
        if views.Count < 2:
            raise RuntimeError(
                "This sheet has no Background View, and a frame belongs there. "
                "Create the drawing with catia_new_drawing first."
            )
        background = views.Item(2)
        try:
            background.Activate()
        except Exception:  # noqa: BLE001 - the draws below still work or fail loudly
            pass

        problems: list[str] = []
        lines = 0
        if args.get("border", True):
            factory = background.Factory2D
            for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1),
                               (x1, y1, x0, y1), (x0, y1, x0, y0)):
                try:
                    factory.CreateLine(float(a), float(b), float(c), float(d))
                    lines += 1
                except Exception as error:  # noqa: BLE001
                    problems.append(
                        f"border line ({a:g},{b:g})-({c:g},{d:g}) failed: "
                        f"{str(error).splitlines()[0][:60]}")
            if lines == 0:
                raise RuntimeError(
                    "The border could not be drawn: CATIA refused all four "
                    f"Factory2D.CreateLine calls ({problems[0] if problems else ''}). "
                    "The Background View has to be active for it."
                )

                # Tables.Add anchors the TOP-left corner and the rows grow DOWNWARD.
        # Passing the bottom margin put the whole block off the sheet - seen in
        # the exported PDF on the first run (B30 2026-11-05).
        bx = x1 - block_w
        by = y0 + block_h
        rows, columns = 5, 8
        table = background.Tables.Add(bx, by, rows, columns,
                                      block_h / rows, block_w / columns)
        for index in range(1, columns + 1):
            try:
                table.SetColumnSize(index, block_w / columns)
            except Exception as error:  # noqa: BLE001
                problems.append(f"column {index} size: {str(error).splitlines()[0][:50]}")
        for index, share in enumerate((0.26, 0.185, 0.185, 0.185, 0.185), start=1):
            try:
                table.SetRowSize(index, block_h * share)
            except Exception as error:  # noqa: BLE001
                problems.append(f"row {index} size: {str(error).splitlines()[0][:50]}")

        merges = (
            (1, 1, 1, 5), (1, 6, 1, 3),
            (2, 1, 1, 2), (2, 3, 1, 2), (2, 5, 1, 2), (2, 7, 1, 2),
            (3, 1, 1, 2), (3, 3, 1, 2), (3, 5, 1, 2), (3, 7, 1, 2),
            (4, 1, 1, 2), (4, 3, 1, 2), (4, 5, 1, 2), (4, 7, 1, 2),
            (5, 1, 1, 8),
        )
        for merge in merges:
            try:
                table.MergeCells(*merge)
            except Exception as error:  # noqa: BLE001
                problems.append(f"merge {merge}: {str(error).splitlines()[0][:50]}")

        cells = {
            (1, 1): str(block.get("title") or ""),
            (1, 6): str(block.get("drawing_number") or ""),
            (2, 1): labels["material"], (2, 3): str(block.get("material") or ""),
            (2, 5): labels["scale"], (2, 7): str(block.get("scale") or ""),
            (3, 1): labels["drawn"], (3, 3): str(block.get("drawn_by") or ""),
            (3, 5): labels["date"], (3, 7): str(block.get("date") or ""),
            (4, 1): labels["checked"], (4, 3): str(block.get("checked_by") or ""),
            (4, 5): labels["sheet"], (4, 7): str(block.get("sheet") or ""),
            (5, 1): str(block.get("footer") or ""),
        }
        filled = 0
        for (row, column), value in cells.items():
            try:
                table.SetCellString(row, column, value)
                if value:
                    filled += 1
            except Exception as error:  # noqa: BLE001
                problems.append(
                    f"cell {row},{column}: {str(error).splitlines()[0][:50]}")

        for row, column, align in ((1, 1, 1), (1, 6, 1), (5, 1, 0)):
            try:
                table.SetCellAlignment(row, column, align)
            except Exception as error:  # noqa: BLE001
                problems.append(
                    f"alignment {row},{column}: {str(error).splitlines()[0][:50]}")

        title = cells[(1, 1)]
        if title:
            try:
                text_object = table.GetCellObject(1, 1)
                text_object.SetFontSize(1, len(title), 5.0)
            except Exception as error:  # noqa: BLE001
                problems.append(f"title font: {str(error).splitlines()[0][:50]}")

        notes = [str(line) for line in (args.get("notes") or []) if str(line)]
        note_top = y0 + block_h + 6.0
        for offset, line in enumerate(notes):
            try:
                note = background.Texts.Add(line, x0 + 5.0, note_top - offset * 5.0)
                note.AnchorPosition = 3          # catBottomLeft
                note.SetFontSize(1, len(line), 3.5)
            except Exception as error:  # noqa: BLE001
                problems.append(f"note {offset + 1}: {str(error).splitlines()[0][:50]}")

        self.conn.refresh_display()
        try:
            elements = background.GeometricElements.Count
        except Exception:  # noqa: BLE001
            elements = -1
        note = ""
        if problems:
            note = (" NOT EVERYTHING TOOK: " + "; ".join(problems[:6])
                    + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""))
        return (
            f"Frame drawn on sheet '{sheet.Name}' ({self._sheet_summary(state)}): "
            f"{lines} border line(s) at {margin_left:g}/{margin:g} mm margins, "
            f"title block {block_w:g} x {block_h:g} mm at ({bx:g}, {by:g}) with "
            f"{rows} x {columns} cells, {filled} filled, labels in "
            f"{'中文' if language == 'zh' else 'English'}, "
            f"{len(notes)} note line(s). "
            f"Background view now holds {elements} element(s). "
            f"Drawing area is x {x0:g}..{x1:g}, y {y0:g}..{y1:g} mm - views placed "
            "outside it land on the border (this tool does not move views)."
            + note
        )

    def _find_part_document(self, part_name: str | None) -> Any:
        docs = self.conn.documents
        open_parts = []
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            try:
                doc_name = str(doc.Name)
            except Exception:
                continue
            if doc_name.lower().endswith(".catpart"):
                open_parts.append(doc)
                if part_name and doc_name == part_name:
                    return doc

        if part_name:
            names = ", ".join(str(d.Name) for d in open_parts) or "none"
            raise RuntimeError(
                f"No open Part named '{part_name}'. Open parts: {names}."
            )
        if len(open_parts) == 1:
            return open_parts[0]

        # More than one part open. The part the caller just built is the one
        # they mean far more often than not, and CATIA chose its name
        # ("Part1.CATPart"), so requiring part_name forced them to read the
        # document list back. Remembered by NAME, not by reference: a document
        # reference outlives a close and then blows up on use.
        remembered = getattr(self.conn, "last_part_name", None)
        if remembered:
            for doc in open_parts:
                try:
                    if str(doc.Name) == remembered:
                        return doc
                except Exception:  # noqa: BLE001
                    continue
        names = ", ".join(str(d.Name) for d in open_parts) or "none"
        raise RuntimeError(
            "Pass 'part_name' to pick the part to project - more than one part "
            f"is open and none of them is the one this session last built. "
            f"Open parts: {names}."
        )

    def _find_active_drawing(self) -> Any:
        app = self.conn.app
        try:
            active = app.ActiveDocument
            if str(active.Name).lower().endswith(".catdrawing"):
                return active
        except Exception:
            pass
        docs = self.conn.documents
        drawings = []
        for i in range(1, docs.Count + 1):
            doc = docs.Item(i)
            try:
                if str(doc.Name).lower().endswith(".catdrawing"):
                    drawings.append(doc)
            except Exception:
                continue
        if len(drawings) == 1:
            return drawings[0]
        raise RuntimeError(
            "No active Drawing document. Run catia_new_drawing first."
        )

    def _add_view(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        app = self.conn.app
        part_doc = self._find_part_document(args.get("part_name"))
        drawing = self._find_active_drawing()

        plane = (args.get("plane") or "xy").lower()
        if plane not in _PLANE_VECTORS:
            raise ValueError(f"Unknown plane '{plane}'. Use 'xy', 'yz', or 'zx'.")
        vx1, vy1, vz1, vx2, vy2, vz2 = _PLANE_VECTORS[plane]

        # Resolve every input before touching the sheet, so a bad argument
        # never leaves an empty unconfigured view behind.
        body_name = args.get("body_name")
        target_body = None
        if body_name:
            bodies = part_doc.Part.Bodies
            try:
                target_body = bodies.Item(body_name)
            except Exception:
                names = ", ".join(str(bodies.Item(i).Name) for i in range(1, bodies.Count + 1))
                raise RuntimeError(
                    f"No body named '{body_name}' in '{part_doc.Name}'. "
                    f"Top-level bodies: {names or 'none'}."
                )
        face_name = args.get("face")
        face_plane = None
        if face_name:
            # Face enumeration and GetPlane run with the PART active, as in
            # the proven run; the drawing is re-activated afterwards.
            try:
                part_doc.Activate()
            except Exception:
                pass
            try:
                face_plane = self._face_plane(part_doc, body_name, face_name)
            finally:
                try:
                    drawing.Activate()
                except Exception:
                    pass
        angle = args.get("angle")

        sheet = drawing.Sheets.ActiveSheet
        view = sheet.Views.Add(args.get("name") or "主视图")

        gb = view.GenerativeBehavior
        if target_body is not None:
            # gb.Document accepts a Body and then draws that body only
            # (field-validated with a control view on V5R20).
            gb.Document = target_body
            source = f"{part_doc.Name} / {body_name}"
        else:
            gb.Document = part_doc
            source = str(part_doc.Name)
        if face_plane is not None:
            # SetProjectionPlane takes the two in-plane direction vectors
            # (normal = V1 x V2), field-corrected and proven on V5R20.
            gb.SetProjectionPlane(
                face_plane[3], face_plane[4], face_plane[5],
                face_plane[6], face_plane[7], face_plane[8],
            )
            projection = f"plane of {face_name}"
        else:
            gb.DefineFrontView(vx1, vy1, vz1, vx2, vy2, vz2)
            projection = f"{plane.upper()} projection"

        view.x = args.get("x", 300)
        view.y = args.get("y", 150)
        if angle:
            import math as _math

            view.Angle = _math.radians(angle)
        gb.Update()

        self.conn.refresh_display()
        return (
            f"View '{view.Name}' of '{source}' added to sheet "
            f"'{sheet.Name}' ({projection}) and updated."
        )

    # CatDimType is 0-based in R20 (enum_CatDimType, 21 values, verified
    # live: catDimRadius = 5): Distance=0, DistanceOffset=1, Length=2,
    # LengthCurvilinear=3, Angle=4, Radius=5, RadiusTangent=6,
    # RadiusCylinder=7, RadiusEdge=8, Diameter=9, ...
    _DIM_TYPES = {
        "distance": 0,
        "length": 2,
        "angle": 4,
        "radius": 5,
        "diameter": 9,
    }

    def _find_view(self, view_name: str | None) -> Any:
        drawing = self._find_active_drawing()
        views = drawing.Sheets.ActiveSheet.Views
        if view_name:
            for i in range(1, views.Count + 1):
                v = views.Item(i)
                if str(v.Name) == view_name:
                    return v
            names = ", ".join(str(views.Item(i).Name) for i in range(1, views.Count + 1))
            raise RuntimeError(f"No view named '{view_name}'. Views: {names}.")
        if views.Count < 3:
            raise RuntimeError(
                "The active sheet has no user view yet (only Main/Background). "
                "Add one with catia_drawing_add_view."
            )
        # Items 1 and 2 are the sheet's Main and Background views; the last
        # item is the most recently added user view.
        return views.Item(views.Count)

    def _list_view_geometry(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        view = self._find_view(args.get("view_name"))
        geoms = view.GeometricElements
        elements = []
        for i in range(1, geoms.Count + 1):
            try:
                name = str(geoms.Item(i).Name)
            except Exception:
                name = "<unnamed>"
            elements.append({"index": i, "name": name})
        if not elements:
            return (
                f"View '{view.Name}' exposes no 2D geometry. For a "
                "generative view, make sure it was updated "
                "(catia_drawing_add_view updates automatically)."
            )
        import json as _json

        return _json.dumps(
            {"view": str(view.Name), "elements": elements}, indent=2
        )

    def _add_dimension(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        dim_type = args["type"]
        code = self._DIM_TYPES.get(dim_type)
        if code is None:
            raise ValueError(
                f"Unknown dimension type '{dim_type}'. Use one of: "
                + ", ".join(self._DIM_TYPES)
            )
        view = self._find_view(args.get("view_name"))
        geoms = view.GeometricElements

        idx1 = args["element_index_1"]
        idx2 = args.get("element_index_2")
        two_element_types = ("distance", "angle")
        if dim_type in two_element_types and idx2 is None:
            raise ValueError(f"'{dim_type}' needs element_index_2 as well.")

        # Item 1 is the view's ABSOLUTE AXIS, exactly as in a sketch (measured on
        # B30 2026-11-05: the listing of a fresh manual view reads
        # [1: 绝对轴, 2: 直线.1, 3: 直线.2]). Constraining it is what produced the
        # bare 方法 Add 失败 that made "my first line" look broken - the same
        # defect fixed for catia_sketch_constraint in 0.3.62, repeated here.
        for label, index in (("element_index_1", idx1),
                             ("element_index_2", idx2)):
            if index is None:
                continue
            if index < 1 or index > geoms.Count:
                raise RuntimeError(
                    f"{label} is {index}, but view '{view.Name}' has "
                    f"{geoms.Count} element(s). Use catia_drawing_list_view_geometry."
                )
            if index == 1:
                raise RuntimeError(
                    f"{label} is 1, which is the view's ABSOLUTE AXIS, not "
                    "geometry you drew. The first element you draw is index 2 - "
                    "confirm with catia_drawing_list_view_geometry."
                )

        elems = [geoms.Item(idx1)]
        if idx2 is not None:
            elems.append(geoms.Item(idx2))

        # DrawingDimensions.Add(iTypeDim, iGeomElem, iPtCoordElem, iLineRep).
        # Input safearrays marshal fine through late-bound COM (only in/out
        # arrays do not). Zeroed selection points, iLineRep = 3 (catDimAuto,
        # CATIA places the dimension line).
        try:
            view.Activate()
        except Exception:
            pass
        pt_coords = [0.0] * (2 * len(elems))
        try:
            dim = view.Dimensions.Add(code, elems, pt_coords, 3)
        except Exception as error:
            # The call itself is fine - the ELEMENTS cannot carry that dimension.
            # Measured on B30 2026-11-05: distance between two PARALLEL lines
            # works (0 -> 1, 1 -> 2), while the same call on a horizontal and a
            # vertical line raises the bare 方法 Add 失败, and so does radius on
            # a line. Upstream reported this as a success path with no guidance.
            raise RuntimeError(
                f"CATIA refused a {dim_type} dimension on the {len(elems)} "
                f"element(s) given on view '{view.Name}'. The arguments were "
                "accepted, the geometry was not: a distance needs two elements "
                "that HAVE a distance between them - two parallel lines or two "
                "points, not a horizontal line and a vertical one; radius needs "
                "an arc or a circle; angle needs two lines that meet. A generative "
                "view cannot be dimensioned this way at all - its curves are not "
                f"addressable (use catia_drawing_generate_dimensions). "
                f"CATIA said: {str(error).splitlines()[0][:110]}"
            ) from error

        self.conn.refresh_display()
        return (
            f"{dim_type.capitalize()} dimension '{dim.Name}' added on view "
            f"'{view.Name}' (associative value computed by CATIA)."
        )

    def _face_plane(self, part_doc: Any, body_name: str | None, face_name: str) -> list[float]:
        """Return the 9 GetPlane components (origin, dir1, dir2) of Face.N.

        ``Face.N`` means what ``catia_list_faces`` means - the Nth surviving
        solid face of the BODY's final shape - because that is the numbering every
        other tool here resolves. Upstream searched the last FEATURE with the
        query ``"Topology.Face,sel"``, and two things are wrong with that on this
        install, both measured on B30 2026-11-05:

          * the query is English. A Chinese CATIA raises a bare
            ``方法 Search 失败`` for it and answers ``拓扑.面,sel`` with the 6
            faces of the same plate, so face-driven projection could not work
            at all here;
          * its indices are the last feature's, not the body's, so ``Face.3``
            meant different geometry to this tool and to catia_list_faces.

        ``read_measurable_array`` is required for GetPlane: the in/out SAFEARRAY
        cannot be returned through late binding (a direct GetPlane() call raises
        方法 GetPlane 失败).
        """
        from catia_mcp.topo_search import topology_cells

        m = re.match(r"^Face\.(\d+)$", face_name or "")
        if not m:
            raise ValueError(f"'face' must look like 'Face.3' (from catia_list_faces), got '{face_name}'.")
        idx = int(m.group(1))
        part = part_doc.Part
        body = part.Bodies.Item(body_name) if body_name else part.MainBody
        if body.Shapes.Count == 0:
            raise RuntimeError("The body has no solid shape to take a face from.")
        cells = topology_cells(self.conn, body, "Face")
        if idx < 1 or idx > len(cells):
            raise RuntimeError(
                f"{face_name} is out of range: body '{body.Name}' has "
                f"{len(cells)} face(s). Use catia_list_faces to see them."
            )
        spa = part_doc.GetWorkbench("SPAWorkbench")
        measurable = spa.GetMeasurable(cells[idx - 1].Reference)
        try:
            plane = read_measurable_array(self.conn.app, measurable, "GetPlane", 9)
        except Exception as e:
            # GetPlane does not answer "this is not planar" - it raises a
            # CATScript stack. On the 60x40x10 plate with a through hole
            # (B30 2026-11-05) Face.1 is the HOLE'S CYLINDER, area 314.2 mm2
            # against 2321.5 for the flat faces, and asking for its plane fails
            # exactly like this. So read the area: a readable area with no plane
            # is a non-planar face, which is a normal answer, not a defect.
            area = ""
            try:
                area = f" (its area reads {float(measurable.Area) * 1e6:.1f} mm2, so the face itself is fine)"
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"{face_name} has no plane, so it cannot drive a projection. A "
                f"cylindrical or free-form face has no plane{area} - pick a flat "
                "one: catia_list_faces reports each face's area and the feature "
                f"that made it. Underlying call: {str(e).splitlines()[0][:90]}"
            ) from e
        if not any(abs(v) > 0 for v in plane):
            raise RuntimeError(
                f"{face_name} returned no usable plane (not planar?). Pick a "
                "planar face."
            )
        return plane

    def _dimension_rows(self, view: Any) -> list[dict[str, Any]]:
        rows = []
        dims = view.Dimensions
        for j in range(1, dims.Count + 1):
            d = dims.Item(j)
            row: dict[str, Any] = {"name": str(d.Name)}
            try:
                row["value"] = round(float(d.GetValue().Value), 4)
            except Exception:
                row["value"] = None
            try:
                row["dim_type"] = int(d.DimType)
            except Exception:
                pass
            try:
                # 6 = catBasic (manual 2D parents), 7 = cat3DDrivableDim
                # (generated from a 3D constraint, follows the part).
                status = int(d.DimStatus)
                row["status"] = {6: "basic_2d", 7: "3d_driven"}.get(status, status)
            except Exception:
                pass
            rows.append(row)
        return rows

    def _generate_dimensions(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        drawing = self._find_active_drawing()
        view = self._find_view(args.get("view_name"))
        before = view.Dimensions.Count

        # Field-proven on V5R20: DrawingSheet.GenerateDimensions() is a
        # silent no-op unless the DRAWING is the active document and the
        # view is active. One dimension per supported 3D constraint
        # (distance/length/angle/radius/diameter), idempotent on re-run.
        app = self.conn.app
        try:
            drawing.Activate()
        except Exception:
            pass
        try:
            view.Activate()
        except Exception:
            pass
        sheet = drawing.Sheets.ActiveSheet
        active_doc = str(app.ActiveDocument.Name)
        if active_doc != str(drawing.Name):
            raise RuntimeError(
                f"Could not activate drawing '{drawing.Name}' (active "
                f"document is '{active_doc}'); GenerateDimensions would "
                "silently do nothing. Bring the drawing window to front and "
                "retry."
            )
        try:
            active_view = str(sheet.Views.ActiveView.Name)
        except Exception:
            active_view = None
        if active_view is not None and active_view != str(view.Name):
            raise RuntimeError(
                f"Could not activate view '{view.Name}' (active view is "
                f"'{active_view}'); retry or pass view_name explicitly."
            )
        sheet.GenerateDimensions()
        self.conn.refresh_display()

        after = view.Dimensions.Count
        rows = self._dimension_rows(view)
        created = max(0, after - before)
        import json as _json

        if created == 0:
            return _json.dumps({
                "view": str(view.Name),
                "generated": 0,
                "dimensions": rows,
                "note": (
                    "No dimension was generated. Causes: the part's sketches "
                    "carry no supported constraints, or they were already "
                    "generated (idempotent). catia_sketch_constraint has no "
                    "'length' type: a length IS 'distance' with only "
                    "geometry_index_1. Measured on B30 2026-11-05 - one radius "
                    "constraint on a circle that a pad extrudes produced exactly "
                    "one dimension, DimStatus 7 (3d_driven), and a plate whose "
                    "profile carried no constraint produced none."
                ),
            }, indent=2)
        return _json.dumps({
            "view": str(view.Name),
            "generated": created,
            "dimensions": rows,
        }, indent=2)

    def _list_dimensions(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        view = self._find_view(args.get("view_name"))
        # 3d_driven dimensions only follow the part after the generative
        # view is regenerated (field-proven: part.Update alone leaves the
        # old value). Refresh before reading.
        if args.get("refresh", True):
            try:
                view.GenerativeBehavior.Update()
            except Exception:
                pass
        import json as _json

        return _json.dumps(
            {"view": str(view.Name), "dimensions": self._dimension_rows(view)},
            indent=2,
        )

    def _find_view_any(self, view_name: str | None) -> Any:
        """Resolve a view for annotations: by name (Main/Background views
        included), else the last user view, else the Background View so
        title blocks can be filled on an otherwise empty sheet."""
        if view_name:
            return self._find_view(view_name)
        views = self._find_active_drawing().Sheets.ActiveSheet.Views
        if views.Count >= 3:
            return views.Item(views.Count)
        return views.Item(2) if views.Count >= 2 else views.Item(1)

    def _parent_generative_view(self, parent_name: str | None) -> Any:
        parent = self._find_view(parent_name)
        try:
            generative = bool(parent.IsGenerative())
        except Exception:
            generative = True  # cannot tell; let CATIA decide
        if not generative:
            raise ValueError(
                f"'{parent.Name}' is not a generative view; derive from a "
                "view created by catia_drawing_add_view."
            )
        return parent

    def _create_derived_view(self, sheet: Any, parent: Any, name: str, define: Any) -> Any:
        """Add a view, link it to the parent's 3D source, run the Define*
        call; remove the view again if anything fails so no empty orphan
        is left on the sheet (and becomes the next default parent)."""
        view = sheet.Views.Add(name)
        try:
            linked = False
            try:
                view.GenerativeBehavior.Document = parent.GenerativeBehavior.Document
                linked = True
            except Exception:
                try:
                    parent.GenerativeLinks.CopyLinksTo(view.GenerativeLinks)
                    linked = True
                except Exception:
                    pass
            if not linked:
                raise RuntimeError(
                    "Could not carry the parent's 3D link to the new view; "
                    "it would generate no geometry."
                )
            define(view)
            return view
        except Exception:
            try:
                sheet.Views.Remove(view.Name)
            except Exception:
                pass
            raise

    @staticmethod
    def _parent_placement(parent: Any) -> tuple[float, float, float]:
        try:
            return float(parent.x), float(parent.y), float(parent.Scale)
        except Exception:
            return 0.0, 0.0, 1.0

    @staticmethod
    def _place(view: Any, x: float, y: float, scale: float | None = None) -> float | None:
        applied = None
        if scale:
            try:
                view.Scale = scale
                applied = float(view.Scale)
            except Exception:
                applied = None
        view.x = x
        view.y = y
        return applied

    def _projection_view(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        direction = str(args["direction"]).lower()
        if direction not in _PROJECTION_TYPES:
            raise ValueError(f"Unknown direction '{direction}'. Use right, left, top, bottom or rear.")
        gap = float(args.get("gap", 100.0))
        drawing = self._find_active_drawing()
        sheet = drawing.Sheets.ActiveSheet
        parent = self._parent_generative_view(args.get("parent_view"))

        # Placement follows the sheet's projection method: third angle puts
        # the right view to the right of the front view, first angle (the
        # ISO default) puts it to the left. CatSheetProjectionMethod:
        # 0 = first angle, 1 = third angle.
        try:
            first_angle = int(sheet.ProjectionMethod) == 0
        except Exception:
            first_angle = False
        sign = -1.0 if first_angle else 1.0
        offsets = {
            "right": (sign * gap, 0.0), "left": (-sign * gap, 0.0),
            "top": (0.0, sign * gap), "bottom": (0.0, -sign * gap),
            "rear": (2 * sign * gap, 0.0),
        }
        dx, dy = offsets[direction]
        px, py, pscale = self._parent_placement(parent)

        def define(view: Any) -> None:
            view.GenerativeBehavior.DefineProjectionView(
                parent.GenerativeBehavior, _PROJECTION_TYPES[direction]
            )

        view = self._create_derived_view(
            sheet, parent, args.get("name") or _PROJECTION_NAMES[direction],
            define)
        self._place(view, args.get("x", px + dx), args.get("y", py + dy), pscale)
        view.GenerativeBehavior.Update()
        self.conn.refresh_display()
        method = "first angle" if first_angle else "third angle"
        return (
            f"Projection view '{view.Name}' ({direction}) of '{parent.Name}' "
            f"added to sheet '{sheet.Name}' ({method} layout) and updated."
        )

    def _section_view(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        profile = [float(v) for v in args["profile"]]
        if len(profile) < 4 or len(profile) % 2:
            raise ValueError("profile must be a flat list [x1, y1, x2, y2, ...] with at least two points")
        section_type = args.get("section_type") or "SectionView"
        profile_type = args.get("profile_type") or "Offset"
        if section_type not in ("SectionView", "SectionCut"):
            raise ValueError("section_type must be 'SectionView' or 'SectionCut'")
        if profile_type not in ("Offset", "Aligned"):
            raise ValueError("profile_type must be 'Offset' or 'Aligned'")
        side = int(args.get("side", 1))
        if side not in (0, 1):
            raise ValueError("side must be 0 (clockwise) or 1 (counterclockwise)")
        drawing = self._find_active_drawing()
        sheet = drawing.Sheets.ActiveSheet
        parent = self._parent_generative_view(args.get("parent_view"))
        px, py, pscale = self._parent_placement(parent)

        def define(view: Any) -> None:
            # DefineSectionView(iProfile, iSectionType, iProfileType,
            # iSideToDraw, iParentGB); the profile is an input safearray.
            view.GenerativeBehavior.DefineSectionView(
                profile, section_type, profile_type, side, parent.GenerativeBehavior
            )

        view = self._create_derived_view(sheet, parent, args.get("name") or "剖视图", define)
        self._place(view, args.get("x", px + 150.0), args.get("y", py), pscale)
        view.GenerativeBehavior.Update()
        self.conn.refresh_display()
        return (
            f"{section_type} '{view.Name}' from '{parent.Name}' added to sheet "
            f"'{sheet.Name}' ({profile_type}, {len(profile) // 2} profile points) and updated."
        )

    def _detail_view(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        cx, cy, radius = float(args["center_x"]), float(args["center_y"]), float(args["radius"])
        if radius <= 0:
            raise ValueError("radius must be positive")
        factor = float(args.get("scale", 2.0))
        drawing = self._find_active_drawing()
        sheet = drawing.Sheets.ActiveSheet
        parent = self._parent_generative_view(args.get("parent_view"))
        px, py, pscale = self._parent_placement(parent)

        def define(view: Any) -> None:
            view.GenerativeBehavior.DefineCircularDetailView(cx, cy, radius, parent.GenerativeBehavior)

        view = self._create_derived_view(sheet, parent, args.get("name") or "局部视图", define)
        applied = self._place(view, args.get("x", px + 150.0), args.get("y", py + 100.0), factor * pscale)
        view.GenerativeBehavior.Update()
        self.conn.refresh_display()
        scale_note = f"scale {applied:g}" if applied else "scale unchanged (CATIA kept the parent scale)"
        return (
            f"Detail view '{view.Name}' of '{parent.Name}' (r={radius} mm at "
            f"({cx}, {cy}), {scale_note}) added."
        )

    def _add_text(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        view = self._find_view_any(args.get("view_name"))
        text = view.Texts.Add(str(args["text"]), float(args["x"]), float(args["y"]))
        self.conn.refresh_display()
        return f"Text '{text.Name}' added to view '{view.Name}' at ({args['x']}, {args['y']}) mm."

    def _add_table(self, args: dict[str, Any]) -> str:
        self.conn.ensure_connected()
        view = self._find_view_any(args.get("view_name"))
        rows, cols = int(args["rows"]), int(args["columns"])
        if rows < 1 or cols < 1:
            raise ValueError("rows and columns must be at least 1")
        table = view.Tables.Add(
            float(args["x"]), float(args["y"]), rows, cols,
            float(args.get("row_height", 8.0)), float(args.get("column_width", 40.0)),
        )
        widths = args.get("column_widths") or []
        for c, w in enumerate(widths[:cols], start=1):
            table.SetColumnSize(c, float(w))
        filled = 0
        cells = args.get("cells") or []
        for r, row in enumerate(cells[:rows], start=1):
            for c, value in enumerate(list(row)[:cols], start=1):
                if value is None or value == "":
                    continue
                table.SetCellString(r, c, str(value))
                filled += 1
        self.conn.refresh_display()
        return (
            f"Table '{table.Name}' ({rows}x{cols}) added to view '{view.Name}' "
            f"at ({args['x']}, {args['y']}) mm, {filled} cell(s) filled."
        )
