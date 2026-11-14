"""Reading 2D drawings - DXF, SVG, PDF - into contours a sketch can draw.

No COM anywhere in this module. Every function is pure, because a parser that
can only be exercised by launching CATIA is a parser nobody tests, and the
failure mode of a wrong parser is not an exception: it is a plausible-looking
outline at the wrong size (a 1:100 sheet read as 1:1) or with half the geometry
missing (one layer of six). Both are silent, so the numbers in a reply have to
be checked against a file whose content is known.

WHAT A CONTOUR IS
    {"pieces": [piece, ...], "closed": bool, "layer": str | None, "source": str}
    piece := {"t": "line", "p1": (x, y), "p2": (x, y)}
           | {"t": "arc", "c": (x, y), "r": float, "a0": deg, "a1": deg,
              "ccw": bool}
           | {"t": "circle", "c": (x, y), "r": float}
           | {"t": "ellipse", "c": (x, y), "rx": float, "ry": float,
              "angle": deg}
           | {"t": "poly", "pts": [(x, y), ...]}

    Pieces keep their own kind instead of being flattened to segments: an
    imported hole has to stay a circle and a slot two arcs, or the pad built on
    the import is faceted in a way the drawing never asked for. Only curves with
    no sketch primitive behind them (Beziers, elliptical arcs) are sampled to a
    polyline, and the contour records that it was.

COORDINATES
    Every parser returns millimetres in the drawing's own frame, Y UP. DXF and
    PDF already are; SVG is Y-down and is flipped here, because a mirrored
    import is the one error a bounding box cannot reveal.
"""

from __future__ import annotations

import math
import os
import re
import zlib
from typing import Any, Iterable, Sequence

from catia_mcp.results import ArgumentOutOfRange, ArgumentRejected

#: Units as DXF's $INSUNITS enumerates them, to millimetres.
_UNIT_MM = {
    1: 25.4,          # inches
    2: 304.8,         # feet
    3: 1609344.0,     # miles
    4: 1.0,           # millimetres
    5: 10.0,          # centimetres
    6: 1000.0,        # metres
    7: 1000000.0,     # kilometres
    8: 0.0000254,     # microinches
    9: 0.0254,        # mils
    10: 914.4,        # yards
    11: 1e-7,         # angstroms
    12: 1e-6,         # nanometres
    13: 0.001,        # microns
    14: 100.0,        # decimetres
    15: 10000.0,      # decametres
    16: 100000.0,     # hectometres
}

_UNIT_NAME = {1: "inch", 2: "foot", 4: "mm", 5: "cm", 6: "m", 9: "mil",
              13: "micron", 14: "dm"}

#: One PDF point in millimetres - a definition from the format, not a
#: measurement.
_PT_MM = 25.4 / 72.0

#: One CSS pixel in millimetres, at the 96 dpi the CSS spec settled on. An SVG
#: sized in "px", or not sized at all, is read through this.
_PX_MM = 25.4 / 96.0

FORMATS = ("dxf", "svg", "pdf")

#: How close two ends have to be to count as joined when stitching. Drawing
#: exports round coordinates to 1e-6 or less, so this only ever forgives a
#: rounding difference - it is not a "close enough" tolerance for geometry.
STITCH_TOLERANCE = 1e-3


# --------------------------------------------------------------------------
# geometry helpers shared by all three parsers
# --------------------------------------------------------------------------

def _dist(p1: Sequence[float], p2: Sequence[float]) -> float:
    return math.hypot(float(p2[0]) - float(p1[0]), float(p2[1]) - float(p1[1]))


def _arc_sweep(a0: float, a1: float, ccw: bool) -> tuple[float, float]:
    """(start, end) in degrees with the sweep folded into the end angle.

    DXF writes an arc's ends as absolute angles and its direction separately, so
    "start 350, end 10, CCW" is a 20-degree arc across +X and not a 340-degree
    one. Reading it the naive way gives an arc that looks right in a corner and
    is seventeen times too long.
    """
    sweep = (a1 - a0) % 360.0 if ccw else -((a0 - a1) % 360.0)
    if abs(sweep) < 1e-12:
        sweep = 360.0 if ccw else -360.0
    return a0, a0 + sweep


def _arc_point(piece: dict, end: bool) -> tuple[float, float]:
    _, a1 = _arc_sweep(piece["a0"], piece["a1"], piece["ccw"])
    angle = math.radians(a1 if end else piece["a0"])
    return (piece["c"][0] + piece["r"] * math.cos(angle),
            piece["c"][1] + piece["r"] * math.sin(angle))


def contour_points(contour: dict, steps: int = 24) -> list[tuple[float, float]]:
    """The contour as a point list - for bounds and for closedness.

    Arcs are sampled HERE and nowhere else: the drawing keeps the real arc, and
    this is only the approximation used to answer questions about it.
    """
    out: list[tuple[float, float]] = []
    for piece in contour.get("pieces") or []:
        kind = piece.get("t")
        if kind == "line":
            out.append(tuple(piece["p1"]))
            out.append(tuple(piece["p2"]))
        elif kind == "circle":
            (cx, cy), r = piece["c"], piece["r"]
            out.extend((cx + r * math.cos(2 * math.pi * i / steps),
                        cy + r * math.sin(2 * math.pi * i / steps))
                       for i in range(steps))
        elif kind == "ellipse":
            (cx, cy), rx, ry = piece["c"], piece["rx"], piece["ry"]
            a = math.radians(piece.get("angle") or 0.0)
            ca, sa = math.cos(a), math.sin(a)
            for i in range(steps):
                t = 2 * math.pi * i / steps
                x, y = rx * math.cos(t), ry * math.sin(t)
                out.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
        elif kind == "arc":
            (cx, cy), r = piece["c"], piece["r"]
            a0, a1 = _arc_sweep(piece["a0"], piece["a1"], piece["ccw"])
            for i in range(steps + 1):
                t = math.radians(a0 + (a1 - a0) * i / steps)
                out.append((cx + r * math.cos(t), cy + r * math.sin(t)))
        elif kind == "poly":
            out.extend(tuple(p) for p in piece["pts"])
    return out


def contour_bbox(contour: dict) -> tuple[float, float, float, float]:
    pts = contour_points(contour)
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def contour_is_closed(contour: dict, tol: float = 1e-6) -> bool:
    pieces = contour.get("pieces") or []
    if not pieces:
        return False
    if len(pieces) == 1 and pieces[0].get("t") in ("circle", "ellipse"):
        return True
    first, last = _piece_start(pieces[0]), _piece_end(pieces[-1])
    return first is not None and last is not None and _dist(first, last) <= tol


def _piece_start(piece: dict) -> tuple[float, float] | None:
    kind = piece.get("t")
    if kind == "line":
        return tuple(piece["p1"])
    if kind == "poly":
        return tuple(piece["pts"][0]) if piece["pts"] else None
    if kind == "arc":
        return _arc_point(piece, end=False)
    return None


def _piece_end(piece: dict) -> tuple[float, float] | None:
    kind = piece.get("t")
    if kind == "line":
        return tuple(piece["p2"])
    if kind == "poly":
        return tuple(piece["pts"][-1]) if piece["pts"] else None
    if kind == "arc":
        return _arc_point(piece, end=True)
    return None


def segment_count(contour: dict) -> int:
    """Sketch elements the contour becomes: one per piece, one per straight run
    of a sampled polyline."""
    total = 0
    for piece in contour.get("pieces") or []:
        if piece.get("t") == "poly":
            total += max(1, len(piece["pts"]) - 1)
        else:
            total += 1
    return total


# --------------------------------------------------------------------------
# stitching: a CAD-exported drawing has one entity per EDGE, not per profile
# --------------------------------------------------------------------------

def _reverse_piece(piece: dict) -> dict:
    kind = piece["t"]
    if kind == "line":
        return {"t": "line", "p1": piece["p2"], "p2": piece["p1"]}
    if kind == "arc":
        return {"t": "arc", "c": piece["c"], "r": piece["r"],
                "a0": piece["a1"], "a1": piece["a0"],
                "ccw": not piece["ccw"]}
    if kind == "poly":
        return {"t": "poly", "pts": list(reversed(piece["pts"]))}
    return dict(piece)


def _reverse_contour(contour: dict) -> dict:
    return {**contour,
            "pieces": [_reverse_piece(p) for p in reversed(contour["pieces"])]}


def stitch_contours(contours: list[dict], tolerance: float = 1e-3) -> tuple[list[dict], dict]:
    """Join open contours whose ends meet, so a profile becomes one contour.

    WHY THIS EXISTS. A CAD-exported PDF or DXF usually holds one entity per
    EDGE, not one per profile: measured on B30 2026-11-14, the 032.pdf sample
    parsed to 672 contours of which 83 were closed - and every one of those 83
    was a 3.4-4.6 mm arrowhead. The part outline was 400-odd separate segments
    that meet end to end, so `closed_only` removed the whole model and a pad on
    the raw import was impossible. Joining them is the difference between "the
    drawing is in the sketch" and "the drawing can be extruded".

    THE RULE IS: exactly one continuation, or stop. Where two unused ends meet
    the same point, the drawing says nothing about which one belongs to which
    loop, and a guess would produce a profile the drawing never had. Those stops
    are counted and reported instead.

    Returns (contours, info) where info counts what happened:
        {"stitched": n, "loops": n, "branches": n, "open": n}
    """
    if tolerance <= 0:
        tolerance = 1e-3
    closed: list[dict] = [c for c in contours if c.get("closed")]
    open_ones: list[dict] = [c for c in contours if not c.get("closed")]
    if len(open_ones) < 2:
        return list(contours), {"stitched": 0, "loops": len(closed),
                                "branches": 0, "open": len(open_ones)}

    def cell(point: tuple[float, float]) -> tuple[int, int]:
        return (int(round(point[0] / tolerance)),
                int(round(point[1] / tolerance)))

    index: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for number, contour in enumerate(open_ones):
        first = _piece_start(contour["pieces"][0])
        last = _piece_end(contour["pieces"][-1])
        if first is not None:
            index.setdefault(cell(first), []).append((number, "start"))
        if last is not None:
            index.setdefault(cell(last), []).append((number, "end"))

    def ends_at(point: tuple[float, float]) -> list[tuple[int, str]]:
        base_x, base_y = cell(point)
        found: list[tuple[int, str]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for number, which in index.get((base_x + dx, base_y + dy), ()):
                    end = (_piece_start(open_ones[number]["pieces"][0])
                           if which == "start"
                           else _piece_end(open_ones[number]["pieces"][-1]))
                    if end is not None and _dist(end, point) <= tolerance:
                        found.append((number, which))
        return found

    used: set[int] = set()
    out: list[dict] = list(closed)
    branches = 0
    for number, contour in enumerate(open_ones):
        if number in used:
            continue
        used.add(number)
        chain = {**contour, "pieces": list(contour["pieces"])}
        origin = _piece_start(chain["pieces"][0])
        changed = False
        # extend forward, then backward, until nothing unique continues
        for direction in ("forward", "backward"):
            while True:
                end = (_piece_end(chain["pieces"][-1]) if direction == "forward"
                       else _piece_start(chain["pieces"][0]))
                if end is None:
                    break
                choices = [(n, w) for n, w in ends_at(end) if n not in used]
                if not choices:
                    break
                if len(choices) > 1:
                    branches += 1
                    break
                other, which = choices[0]
                used.add(other)
                changed = True
                piece_set = (open_ones[other] if which == "start"
                             else _reverse_contour(open_ones[other]))
                if direction == "forward":
                    chain["pieces"].extend(piece_set["pieces"])
                else:
                    chain["pieces"] = (list(piece_set["pieces"])
                                       + list(chain["pieces"]))
                if (direction == "forward" and which == "start"
                        and _dist(_piece_end(chain["pieces"][-1]), origin)
                        <= tolerance):
                    break
        chain["closed"] = contour_is_closed(chain, tolerance)
        chain["stitched"] = len(chain["pieces"]) - len(contour["pieces"]) + 1
        out.append(chain)
    info = {
        "stitched": sum(1 for c in out if c.get("stitched", 1) > 1),
        "loops": sum(1 for c in out if c.get("closed")),
        "branches": branches,
        "open": sum(1 for c in out if not c.get("closed")),
    }
    return out, info


# --------------------------------------------------------------------------
# format detection and the public entry point
# --------------------------------------------------------------------------

def detect_format(path: str, head: bytes | None = None) -> str:
    """Extension first, then the first bytes - never a guess without evidence."""
    ext = os.path.splitext(str(path))[1].lower().lstrip(".")
    if ext in FORMATS:
        return ext
    if ext == "dwg":
        raise ArgumentRejected(
            f"'{path}' is DWG, which this build does not read: it is a binary "
            "container, and CATIA itself needs a translator for it. Re-save it "
            "as DXF (Save As > DXF, 2013 or later) and pass that - the geometry "
            "is the same, only unpacked. Nothing was read.",
            code="DWG_NOT_SUPPORTED", stage="validate")
    if head:
        if head[:5] == b"%PDF-":
            return "pdf"
        probe = head[:4096].lstrip(b"\xef\xbb\xbf \r\n\t")
        if probe.startswith(b"<"):
            return "svg"
        if b"SECTION" in head[:8192]:
            return "dxf"
    raise ArgumentRejected(
        f"Cannot tell what '{path}' is: the extension is '{ext or 'none'}' and "
        "the first bytes match none of DXF, SVG or PDF. This reads DXF (ASCII), "
        "SVG and VECTOR PDF - not raster images and not DWG. Nothing was read.",
        code="UNKNOWN_FORMAT", stage="validate")


def read_drawing(path: str, *, scale: float = 1.0,
                 layers: Iterable[str] | None = None,
                 region: Sequence[float] | None = None,
                 min_size: float | None = None,
                 closed_only: bool = False,
                 origin: str = "bbox",
                 tolerance: float = 1e-6,
                 stitch: bool = True) -> dict[str, Any]:
    """Parse to contours, filter, convert to millimetres, place.

    `scale` multiplies the file's OWN units, after they have been honoured: a
    drawing saved in inches arrives at true size with scale=1, and a 1:100 sheet
    becomes a 1:1 part with scale=100. Both numbers are in the reply, because
    that decision is the one that silently ruins a model.
    """
    path = str(path)
    if not scale or float(scale) <= 0:
        raise ArgumentOutOfRange(
            f"scale has to be a positive number; got {scale!r}. It multiplies "
            "the file's own units, so 1 is 1:1 and 100 turns a 1:100 sheet into "
            "a 1:1 part. Nothing was read.",
            code="SCALE_OUT_OF_RANGE", stage="validate")
    if origin not in ("bbox", "as_drawn"):
        raise ArgumentRejected(
            f"origin is 'bbox' or 'as_drawn'; got {origin!r}. 'bbox' moves the "
            "kept geometry so its lower-left corner sits at (0,0); 'as_drawn' "
            "keeps the sheet coordinates. Nothing was read.",
            code="BAD_ARGUMENT", stage="validate")
    # AFTER the argument checks, deliberately: a bad number or a bad enum is a
    # typo the caller can fix without the file being there, and it is the same
    # ordering rule catia_open_document learned the expensive way in 0.3.127
    # (it connected to CATIA before refusing a non-ASCII name).
    if not os.path.exists(path):
        raise ArgumentRejected(
            f"No file at '{path}'. Nothing was read.",
            code="FILE_NOT_FOUND", stage="validate")

    with open(path, "rb") as handle:
        data = handle.read()
    if not data:
        raise ArgumentRejected(f"'{path}' is empty. Nothing was read.",
                              code="EMPTY_FILE", stage="validate")
    fmt = detect_format(path, data[:20000])
    if fmt == "dxf":
        parsed = parse_dxf(data)
    elif fmt == "svg":
        parsed = parse_svg(data)
    else:
        parsed = parse_pdf(data)

    contours: list[dict] = parsed["contours"]
    stitch_info = None
    if stitch:
        # BEFORE the filters: a stitched loop is what `closed_only` is meant to
        # keep, and a CAD export gives one entity per edge, so filtering first
        # would throw the whole profile away (measured: 672 contours, 83 closed,
        # all 83 of them arrowheads).
        contours, stitch_info = stitch_contours(contours, STITCH_TOLERANCE)
    factor = parsed["mm_per_unit"] * float(scale)
    kept, dropped = _filter(contours, layers=layers, region=region,
                            min_size=min_size, closed_only=closed_only,
                            factor=factor, tolerance=tolerance)
    for contour in kept:
        _scale_contour(contour, factor)
    dx = dy = 0.0
    if origin == "bbox" and kept:
        boxes = [contour_bbox(c) for c in kept]
        dx = -min(b[0] for b in boxes)
        dy = -min(b[1] for b in boxes)
        for contour in kept:
            _translate_contour(contour, dx, dy)
    bounds = None
    if kept:
        boxes = [contour_bbox(c) for c in kept]
        bounds = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                  max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return {
        "file": path,
        "format": fmt,
        "units": parsed.get("units"),
        "mm_per_unit": parsed["mm_per_unit"],
        "scale": float(scale),
        "mm_per_file_unit": factor,
        "origin": origin,
        "origin_shift_mm": [dx, dy],
        "contours": kept,
        "bounds_mm": bounds,
        "counts": {
            "contours": len(kept),
            "closed": sum(1 for c in kept if c["closed"]),
            "elements": sum(segment_count(c) for c in kept),
            "dropped": dropped,
            "stitched": (stitch_info or {}).get("stitched", 0),
            "branches": (stitch_info or {}).get("branches", 0),
        },
        "stats": parsed["stats"],
        "stitch": stitch_info,
        "notes": parsed.get("notes") or [],
    }


def _filter(contours, *, layers, region, min_size, closed_only, factor,
            tolerance) -> tuple[list[dict], dict[str, int]]:
    """The one place a contour is kept or dropped, counting every reason."""
    wanted = None
    if layers:
        wanted = {str(name).strip().upper() for name in layers} or None
    dropped = {"layer": 0, "region": 0, "size": 0, "open": 0,
               "degenerate": 0}
    kept: list[dict] = []
    for contour in contours:
        if wanted is not None:
            layer = str(contour.get("layer") or "").strip().upper()
            if layer not in wanted:
                dropped["layer"] += 1
                continue
        contour["closed"] = contour_is_closed(contour, tolerance)
        box = [v * factor for v in contour_bbox(contour)]
        # A zero-extent contour is a `m x y` with nothing after it, or a line
        # whose two ends are the same point. It draws nothing, it cannot be
        # padded, and it makes `smallest_mm` read 0.000 - which would send a
        # caller looking for tiny text that is not there. Counted, dropped.
        if abs(box[2] - box[0]) < 1e-9 and abs(box[3] - box[1]) < 1e-9:
            dropped["degenerate"] += 1
            continue
        if region is not None:
            x0, y0, x1, y1 = (float(v) for v in region)
            cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                dropped["region"] += 1
                continue
        if min_size:
            if math.hypot(box[2] - box[0], box[3] - box[1]) < float(min_size):
                dropped["size"] += 1
                continue
        if closed_only and not contour["closed"]:
            dropped["open"] += 1
            continue
        kept.append(contour)
    return kept, dropped


def _scale_contour(contour: dict, factor: float) -> None:
    if factor == 1.0:
        return
    for piece in contour["pieces"]:
        kind = piece["t"]
        if kind == "line":
            piece["p1"] = (piece["p1"][0] * factor, piece["p1"][1] * factor)
            piece["p2"] = (piece["p2"][0] * factor, piece["p2"][1] * factor)
        elif kind in ("circle", "arc"):
            piece["c"] = (piece["c"][0] * factor, piece["c"][1] * factor)
            piece["r"] = piece["r"] * factor
        elif kind == "ellipse":
            piece["c"] = (piece["c"][0] * factor, piece["c"][1] * factor)
            piece["rx"] = piece["rx"] * factor
            piece["ry"] = piece["ry"] * factor
        elif kind == "poly":
            piece["pts"] = [(x * factor, y * factor) for x, y in piece["pts"]]


def _translate_contour(contour: dict, dx: float, dy: float) -> None:
    if dx == 0.0 and dy == 0.0:
        return
    for piece in contour["pieces"]:
        kind = piece["t"]
        if kind == "line":
            piece["p1"] = (piece["p1"][0] + dx, piece["p1"][1] + dy)
            piece["p2"] = (piece["p2"][0] + dx, piece["p2"][1] + dy)
        elif kind in ("circle", "arc", "ellipse"):
            piece["c"] = (piece["c"][0] + dx, piece["c"][1] + dy)
        elif kind == "poly":
            piece["pts"] = [(x + dx, y + dy) for x, y in piece["pts"]]


# --------------------------------------------------------------------------
# DXF
# --------------------------------------------------------------------------

def parse_dxf(data: bytes) -> dict[str, Any]:
    """ASCII DXF: group-code pairs, ENTITIES, and BLOCKS for INSERTs.

    Coordinates come back in the FILE's units, with `mm_per_unit` beside them.
    `read_drawing` is what applies that factor - keeping the two steps apart is
    what lets a test check either one on its own.
    """
    text = _dxf_decode(data)
    pairs = _dxf_pairs(text)
    if not pairs:
        raise ArgumentRejected(
            f"The file has {len(data)} bytes but no DXF group-code pairs. A "
            "binary DXF starts with 'AutoCAD Binary DXF'; re-save it as ASCII "
            "DXF. Nothing was read.",
            code="DXF_UNREADABLE", stage="validate")
    stats: dict[str, Any] = {"entities": {}, "skipped": {}, "text_runs": 0,
                             "blocks": 0, "inserts": 0}
    notes: list[str] = []

    unit_code = _dxf_units(pairs)
    if not unit_code:
        mm_per_unit = 1.0
        notes.append(
            "The DXF header does not state its units ($INSUNITS is "
            f"{'missing' if unit_code is None else '0, unitless'}), so the "
            "numbers were read as MILLIMETRES at 1:1. A drawing in inches or at "
            "a paper scale needs `scale` - and `bounds_mm` is the check: a 5 mm "
            "part that reads as 127 is an inch drawing.")
    else:
        mm_per_unit = _UNIT_MM.get(unit_code, 1.0)
        if unit_code not in _UNIT_MM:
            notes.append(f"$INSUNITS={unit_code} is not a unit this build knows; "
                         "millimetres were assumed.")

    blocks = _dxf_blocks(pairs)
    stats["blocks"] = len(blocks)
    contours: list[dict] = []
    for entity in _dxf_entities(pairs):
        _dxf_entity(entity, contours, stats, blocks=blocks, matrix=None,
                    depth=0)
    return {"contours": contours, "stats": stats, "mm_per_unit": mm_per_unit,
            "unit_code": unit_code, "units": _UNIT_NAME.get(unit_code) or "mm",
            "notes": notes}


def _dxf_decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # DXF before AC1021 is code-page encoded and the header that names the code
    # page is itself inside the undecoded text - so the fallback has to be a
    # decode that cannot fail. Latin-1 keeps every group code and number intact;
    # only layer names can come out garbled, and that is reported.
    return data.decode("latin-1")


def _dxf_pairs(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    pairs: list[tuple[int, str]] = []
    index = 0
    while index + 1 < len(lines):
        try:
            code = int(lines[index].strip())
        except ValueError:
            index += 1
            continue
        pairs.append((code, lines[index + 1].rstrip()))
        index += 2
    return pairs


def _dxf_units(pairs: list[tuple[int, str]]) -> int | None:
    """$INSUNITS from HEADER only - every entity carries '70' too."""
    in_header = False
    last_var = None
    for code, value in pairs:
        if code == 2 and value.strip() == "HEADER":
            in_header = True
            continue
        if not in_header:
            continue
        if code == 0 and value.strip() == "ENDSEC":
            break
        if code == 9:
            last_var = value.strip()
        elif last_var == "$INSUNITS" and code == 70:
            try:
                return int(float(value.strip()))
            except ValueError:
                return None
    return None


def _find_section(pairs: list[tuple[int, str]], name: str) -> int | None:
    for index in range(len(pairs) - 1):
        if (pairs[index][0] == 0 and pairs[index][1].strip() == "SECTION"
                and pairs[index + 1][0] == 2
                and pairs[index + 1][1].strip() == name):
            return index + 2
    return None


def _dxf_entities(pairs: list[tuple[int, str]],
                  section: str = "ENTITIES") -> list[dict]:
    """Entities of one section, with POLYLINE's vertices folded into it."""
    start = _find_section(pairs, section)
    if start is None:
        return []
    raw: list[dict] = []
    current: dict | None = None
    index = start
    while index < len(pairs):
        code, value = pairs[index]
        if code == 0:
            kind = value.strip()
            if kind == "ENDSEC":
                break
            if current is not None:
                raw.append(current)
            current = {"type": kind, "tags": []}
        elif current is not None:
            current["tags"].append((code, value))
        index += 1
    if current is not None:
        raw.append(current)

    out: list[dict] = []
    pending: dict | None = None
    for entity in raw:
        if entity["type"] == "POLYLINE":
            pending = entity
            entity["vertices"] = []
        elif entity["type"] == "VERTEX" and pending is not None:
            pending["vertices"].append((_tag(pending, 10), _tag(pending, 20)))
            # VERTEX tags are its own: the last one seen is this vertex's
            pending["vertices"][-1] = (_tag(entity, 10), _tag(entity, 20))
        elif entity["type"] == "SEQEND" and pending is not None:
            out.append(pending)
            pending = None
        else:
            if pending is not None:
                out.append(pending)
                pending = None
            out.append(entity)
    if pending is not None:
        out.append(pending)
    return out


def _tag(entity: dict, code: int, default: float = 0.0) -> float:
    """First value of a group code, as a number."""
    for c, value in entity["tags"]:
        if c == code:
            try:
                return float(value.strip())
            except ValueError:
                return default
    return default


def _tag_text(entity: dict, code: int, default: str = "") -> str:
    for c, value in entity["tags"]:
        if c == code:
            return value.strip()
    return default


def _tag_all(entity: dict, code: int) -> list[float]:
    out: list[float] = []
    for c, value in entity["tags"]:
        if c == code:
            try:
                out.append(float(value.strip()))
            except ValueError:
                pass
    return out


def _dxf_blocks(pairs: list[tuple[int, str]]) -> dict[str, dict]:
    """BLOCKS: name -> {"base": (x, y), "entities": [...]}."""
    blocks: dict[str, dict] = {}
    current: dict | None = None
    for entity in _dxf_entities(pairs, "BLOCKS"):
        if entity["type"] == "BLOCK":
            name = _tag_text(entity, 2)
            current = {"base": (_tag(entity, 10), _tag(entity, 20)),
                       "entities": []}
            blocks[name] = current
        elif entity["type"] == "ENDBLK":
            current = None
        elif current is not None:
            current["entities"].append(entity)
    return blocks


def _dxf_entity(entity: dict, contours: list[dict], stats: dict, *,
                blocks: dict[str, dict], matrix, depth: int) -> None:
    kind = entity["type"]
    layer = _tag_text(entity, 8) or "0"
    make = _Matrix(matrix) if matrix else None
    point = make if make is not None else (lambda x, y: (x, y))
    counts = stats["entities"]
    counts[kind] = counts.get(kind, 0) + 1

    def emit(pieces: list[dict]) -> None:
        if pieces:
            contours.append({"pieces": pieces, "closed": False,
                             "layer": layer, "source": kind})

    if kind == "LINE":
        emit([{"t": "line",
               "p1": point(_tag(entity, 10), _tag(entity, 20)),
               "p2": point(_tag(entity, 11), _tag(entity, 21))}])
    elif kind == "CIRCLE":
        radius = _tag(entity, 40)
        if radius <= 0:
            _skip(stats, kind)
            return
        if make is not None:
            radius *= make.scale
        emit([{"t": "circle", "c": point(_tag(entity, 10), _tag(entity, 20)),
               "r": radius}])
    elif kind == "ARC":
        radius = _tag(entity, 40)
        if radius <= 0:
            _skip(stats, kind)
            return
        a0, a1 = _tag(entity, 50), _tag(entity, 51)
        if make is not None:
            radius *= make.scale
            a0 += make.rotation
            a1 += make.rotation
        emit([{"t": "arc", "c": point(_tag(entity, 10), _tag(entity, 20)),
               "r": radius, "a0": a0, "a1": a1, "ccw": True}])
    elif kind == "ELLIPSE":
        _dxf_ellipse(entity, emit, point, make, stats)
    elif kind == "LWPOLYLINE":
        _dxf_lwpolyline(entity, emit, point, make, stats)
    elif kind == "POLYLINE":
        vertices = entity.get("vertices") or []
        if len(vertices) < 2:
            _skip(stats, kind)
            return
        pieces = [{"t": "line", "p1": point(*p1), "p2": point(*p2)}
                  for p1, p2 in zip(vertices, vertices[1:])]
        if int(_tag(entity, 70)) & 1 and len(vertices) > 2:
            pieces.append({"t": "line", "p1": point(*vertices[-1]),
                           "p2": point(*vertices[0])})
        emit(pieces)
    elif kind == "SPLINE":
        _dxf_spline(entity, emit, point, stats)
    elif kind == "INSERT":
        _dxf_insert(entity, contours, stats, blocks=blocks, matrix=matrix,
                    depth=depth)
    elif kind in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
        stats["text_runs"] += 1
        stats["skipped"]["text"] = stats["skipped"].get("text", 0) + 1
    elif kind in ("DIMENSION", "LEADER", "MLEADER", "TOLERANCE"):
        stats["skipped"]["dimension"] = stats["skipped"].get("dimension", 0) + 1
    elif kind in ("VERTEX", "SEQEND", "BLOCK", "ENDBLK"):
        return
    else:
        stats["skipped"][kind.lower()] = stats["skipped"].get(kind.lower(), 0) + 1


def _skip(stats: dict, kind: str) -> None:
    key = kind.lower()
    stats["skipped"][key] = stats["skipped"].get(key, 0) + 1


def _dxf_lwpolyline(entity: dict, emit, point, make, stats: dict) -> None:
    """Vertices in TAG ORDER, with the per-vertex bulge that turns a straight
    run into an arc.

    A dict keyed by code would keep only the last vertex: every vertex writes
    10/20 again. That is why entities keep their ordered tag list.
    """
    vertices: list[tuple[float, float]] = []
    bulges: list[float] = []
    for code, value in entity["tags"]:
        if code == 10:
            vertices.append((_num(value), 0.0))
            bulges.append(0.0)
        elif code == 20 and vertices:
            vertices[-1] = (vertices[-1][0], _num(value))
        elif code == 42 and bulges:
            bulges[-1] = _num(value)
    if len(vertices) < 2:
        _skip(stats, "LWPOLYLINE")
        return
    closed = bool(int(_tag(entity, 70)) & 1)
    pieces: list[dict] = []
    for index in range(len(vertices) - 1):
        pieces.extend(_bulge_pieces(vertices[index], vertices[index + 1],
                                    bulges[index], point, make))
    if closed:
        pieces.extend(_bulge_pieces(vertices[-1], vertices[0], bulges[-1],
                                    point, make))
    emit(pieces)


def _bulge_pieces(p1, p2, bulge: float, point, make) -> list[dict]:
    """A polyline segment: a line, or the arc its bulge describes.

    bulge = tan(included angle / 4), negative when the arc runs clockwise from
    p1 to p2; bulge 1 is a semicircle, which is the case that makes the
    arithmetic checkable by hand.
    """
    if not bulge:
        return [{"t": "line", "p1": point(*p1), "p2": point(*p2)}]
    theta = 4.0 * math.atan(bulge)
    chord = _dist(p1, p2)
    if chord < 1e-12:
        return []
    half = chord / 2.0
    h = half / math.tan(theta / 2.0) if abs(theta) < math.pi else 0.0
    nx, ny = -(p2[1] - p1[1]) / chord, (p2[0] - p1[0]) / chord
    mx, my = (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0
    cx, cy = mx + h * nx, my + h * ny
    radius = math.hypot(p1[0] - cx, p1[1] - cy)
    a0 = math.degrees(math.atan2(p1[1] - cy, p1[0] - cx))
    a1 = math.degrees(math.atan2(p2[1] - cy, p2[0] - cx))
    if make is not None and not make.uniform:
        # A non-uniform scale turns a circle into an ellipse, and there is no
        # arc primitive for that - sample it rather than draw a wrong circle.
        pts = []
        a_start, a_end = _arc_sweep(a0, a1, theta > 0)
        for index in range(25):
            t = math.radians(a_start + (a_end - a_start) * index / 24.0)
            pts.append(point(cx + radius * math.cos(t),
                             cy + radius * math.sin(t)))
        return [{"t": "poly", "pts": pts}]
    if make is not None:
        cx, cy = point(cx, cy)
        radius *= make.scale
        a0 += make.rotation
        a1 += make.rotation
    return [{"t": "arc", "c": (cx, cy), "r": radius, "a0": a0, "a1": a1,
             "ccw": theta > 0}]


def _dxf_ellipse(entity: dict, emit, point, make, stats: dict) -> None:
    cx, cy = _tag(entity, 10), _tag(entity, 20)
    mx, my = _tag(entity, 11), _tag(entity, 21)
    ratio = _tag(entity, 40) or 1.0
    major = math.hypot(mx, my)
    if major <= 0:
        _skip(stats, "ELLIPSE")
        return
    angle = math.degrees(math.atan2(my, mx))
    t0 = _tag(entity, 41)
    t1 = _tag(entity, 42)
    if abs(t1 - t0) < 1e-9:
        t1 = t0 + 2 * math.pi
    while t1 <= t0:
        t1 += 2 * math.pi
    if abs((t1 - t0) - 2 * math.pi) < 1e-9:
        c = point(cx, cy)
        scale = make.scale if make is not None else 1.0
        emit([{"t": "ellipse", "c": c, "rx": major * scale,
               "ry": major * ratio * scale,
               "angle": angle + (make.rotation if make is not None else 0.0)}])
        return
    stats["skipped"]["elliptic_arc_sampled"] = stats["skipped"].get(
        "elliptic_arc_sampled", 0) + 1
    pts = []
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    for index in range(49):
        t = t0 + (t1 - t0) * index / 48.0
        x, y = major * math.cos(t), major * ratio * math.sin(t)
        pts.append(point(cx + x * ca - y * sa, cy + x * sa + y * ca))
    emit([{"t": "poly", "pts": pts}])


def _dxf_spline(entity: dict, emit, point, stats: dict) -> None:
    """Fit points when the file has them, otherwise control points.

    A NURBS through its CONTROL points is not the curve; through FIT points it
    is close. Which one was used is a note in the reply, because the difference
    shows on screen and would otherwise be invisible.
    """
    fit = list(zip(_tag_all(entity, 11), _tag_all(entity, 21)))
    control = list(zip(_tag_all(entity, 10), _tag_all(entity, 20)))
    use = fit if len(fit) >= 2 else control
    if len(use) < 2:
        _skip(stats, "SPLINE")
        return
    stats["splines_sampled"] = stats.get("splines_sampled", 0) + 1
    stats.setdefault("spline_source", "fit points" if fit else "control points")
    emit([{"t": "poly", "pts": [point(*p) for p in use]}])


def _dxf_insert(entity: dict, contours: list[dict], stats: dict, *,
                blocks: dict[str, dict], matrix, depth: int) -> None:
    if depth >= 4:
        stats["skipped"]["insert_too_deep"] = stats["skipped"].get(
            "insert_too_deep", 0) + 1
        return
    name = _tag_text(entity, 2)
    block = blocks.get(name)
    if not block or not block["entities"]:
        stats["skipped"]["insert_unknown_block"] = stats["skipped"].get(
            "insert_unknown_block", 0) + 1
        return
    bx, by = block["base"]
    ix, iy = _tag(entity, 10), _tag(entity, 20)
    sx = _tag(entity, 41) or 1.0
    sy = _tag(entity, 42) or 1.0
    rotation = math.radians(_tag(entity, 50))
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    a, b = sx * cos_r, sx * sin_r
    c, d = -sy * sin_r, sy * cos_r
    child = (a, b, c, d, ix - (a * bx + c * by), iy - (b * bx + d * by))
    if matrix:
        child = _mat_mul(child, matrix)
    stats["inserts"] += 1
    for member in block["entities"]:
        _dxf_entity(member, contours, stats, blocks=blocks, matrix=child,
                    depth=depth + 1)


def _num(value: str) -> float:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# affine matrices: (a, b, c, d, e, f), p' = (a x + c y + e, b x + d y + f)
# --------------------------------------------------------------------------

def _mat_mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


class _Matrix:
    """An affine map you can call, plus the two numbers arcs need from it."""

    def __init__(self, values: Sequence[float]) -> None:
        self.m = tuple(float(v) for v in values)

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.m
        return (a * x + c * y + e, b * x + d * y + f)

    @property
    def scale(self) -> float:
        a, b, c, d, _, _ = self.m
        return math.sqrt(abs(a * d - b * c)) or 1.0

    @property
    def uniform(self) -> bool:
        a, b, c, d, _, _ = self.m
        return abs(math.hypot(a, b) - math.hypot(c, d)) < 1e-9

    @property
    def rotation(self) -> float:
        a, b, _, _, _, _ = self.m
        return math.degrees(math.atan2(b, a))


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

def parse_svg(data: bytes) -> dict[str, Any]:
    """SVG: shapes and paths, through a transform stack, in USER units.

    `mm_per_unit` says what one of those is worth; `read_drawing` applies it.

    Y is flipped at the end (SVG counts down the page, CATIA counts up), for
    every piece kind - an arc flipped by negating its angles is the same arc on
    the other side, and getting that wrong mirrors the part.
    """
    import xml.etree.ElementTree as ElementTree

    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ArgumentRejected(
            f"This file is not readable as SVG: {error}. Nothing was read.",
            code="SVG_UNREADABLE", stage="validate") from error

    stats: dict[str, Any] = {"entities": {}, "skipped": {}, "text_runs": 0,
                             "elements": 0}
    notes: list[str] = []
    mm_per_unit, unit_note = _svg_scale(root)
    if unit_note:
        notes.append(unit_note)
    view_shift, view_scale = _svg_viewbox(root)
    # viewBox maps its own box onto the viewport: translate(-minx,-miny) then
    # scale, then the physical size above.
    base = (view_scale, 0.0, 0.0, view_scale,
            -view_shift[0] * view_scale, -view_shift[1] * view_scale)
    contours: list[dict] = []
    _svg_walk(root, contours, stats, base, mm_per_unit, notes, depth=0)
    for contour in contours:
        _flip_contour_y(contour)
    return {"contours": contours, "stats": stats, "mm_per_unit": mm_per_unit,
            "unit_code": None, "units": "user unit", "notes": notes}


def _svg_length(text: str | None) -> tuple[float, str] | None:
    if not text:
        return None
    match = re.match(r"\s*(-?[\d.]+(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)", text)
    if not match:
        return None
    return float(match.group(1)), (match.group(2) or "").lower()


def _svg_scale(root) -> tuple[float, str | None]:
    """Millimetres per SVG user unit, from width/height and the viewBox."""
    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    view = root.get("viewBox")
    view_w = view_h = None
    if view:
        parts = [p for p in re.split(r"[\s,]+", view.strip()) if p]
        if len(parts) == 4:
            try:
                view_w, view_h = float(parts[2]), float(parts[3])
            except ValueError:
                view_w = view_h = None

    def to_mm(pair, extent):
        value, unit = pair
        if unit == "mm":
            return value
        if unit == "cm":
            return value * 10.0
        if unit == "m":
            return value * 1000.0
        if unit == "in":
            return value * 25.4
        if unit == "pt":
            return value * _PT_MM
        if unit in ("px", ""):
            return value * _PX_MM
        return value * _PX_MM

    if width and width[1] in ("mm", "cm", "m", "in", "pt"):
        mm = to_mm(width, None)
        return (mm / view_w if view_w else mm / (width[0] or 1.0)), None
    if width and view_w:
        return to_mm(width, None) / view_w, None
    if width:
        return _PX_MM, None
    if view_w and height and height[1] in ("mm", "cm", "m", "in", "pt"):
        return to_mm(height, None) / view_h, None
    if view_w:
        return _PX_MM, None
    return _PX_MM, ("The SVG states no width/height and no viewBox, so its user "
                    "units were read as CSS pixels (1 px = 0.2646 mm). Check "
                    "bounds_mm against a dimension you know.")


def _svg_viewbox(root) -> tuple[tuple[float, float], float]:
    """(min-x, min-y) and the user-unit scale the viewBox implies.

    With a viewBox and a viewport, the content is scaled to fit; without one the
    numbers are already user units.
    """
    view = root.get("viewBox")
    if not view:
        return (0.0, 0.0), 1.0
    parts = [p for p in re.split(r"[\s,]+", view.strip()) if p]
    if len(parts) != 4:
        return (0.0, 0.0), 1.0
    try:
        min_x, min_y, vw, vh = (float(p) for p in parts)
    except ValueError:
        return (0.0, 0.0), 1.0
    width = _svg_length(root.get("width"))
    height = _svg_length(root.get("height"))
    scale = 1.0
    if vw and width:
        viewport = width[0]
        if width[1] in ("mm", "cm", "m", "in", "pt"):
            return (min_x, min_y), 1.0
        scale = viewport / vw
    elif vh and height:
        viewport = height[0]
        if height[1] in ("mm", "cm", "m", "in", "pt"):
            return (min_x, min_y), 1.0
        scale = viewport / vh
    return (min_x, min_y), scale


def _svg_transform(text: str | None) -> tuple[float, ...]:
    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not text:
        return matrix
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text):
        values = [float(v) for v in re.findall(r"-?[\d.]+(?:[eE][-+]?\d+)?", args)]
        name = name.lower()
        if name == "matrix" and len(values) >= 6:
            step = tuple(values[:6])
        elif name == "translate" and values:
            step = (1.0, 0.0, 0.0, 1.0, values[0],
                    values[1] if len(values) > 1 else 0.0)
        elif name == "scale" and values:
            sx = values[0]
            sy = values[1] if len(values) > 1 else sx
            step = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and values:
            angle = math.radians(values[0])
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            step = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                step = _mat_mul(_mat_mul((1.0, 0.0, 0.0, 1.0, cx, cy), step),
                                (1.0, 0.0, 0.0, 1.0, -cx, -cy))
        elif name == "skewx" and values:
            step = (1.0, 0.0, math.tan(math.radians(values[0])), 1.0, 0.0, 0.0)
        elif name == "skewy" and values:
            step = (1.0, math.tan(math.radians(values[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        matrix = _mat_mul(matrix, step)
    return matrix


def _svg_hidden(element) -> bool:
    style = (element.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    if (element.get("display") or "").lower() == "none":
        return True
    return (element.get("visibility") or "").lower() == "hidden"


def _svg_walk(element, contours: list[dict], stats: dict, matrix, mm_per_unit,
              notes: list[str], depth: int) -> None:
    if depth > 40:
        return
    tag = element.tag.split("}")[-1].lower()
    if tag in ("defs", "metadata", "title", "desc", "style", "script",
               "clipPath", "mask", "pattern", "marker", "filter"):
        return
    if _svg_hidden(element):
        stats["skipped"]["hidden"] = stats["skipped"].get("hidden", 0) + 1
        return
    local = _svg_transform(element.get("transform"))
    matrix = _mat_mul(matrix, local)
    layer = element.get("id") or element.get("class") or "svg"
    if tag in ("g", "svg", "a", "switch", "symbol"):
        for child in list(element):
            _svg_walk(child, contours, stats, matrix, mm_per_unit, notes,
                      depth + 1)
        return
    if tag in ("text", "tspan", "image", "use", "foreignObject".lower()):
        if tag == "text":
            stats["text_runs"] += 1
        stats["skipped"][tag] = stats["skipped"].get(tag, 0) + 1
        return
    made = _svg_shape(tag, element, matrix, stats)
    for pieces in made:
        contours.append({"pieces": pieces, "closed": False, "layer": layer,
                         "source": tag})
    stats["elements"] += 1


def _svg_shape(tag: str, element, matrix, stats: dict) -> list[list[dict]]:
    """Shape element -> list of contours, each a list of pieces."""
    apply = _Matrix(matrix)

    def num(name: str, default: float = 0.0) -> float:
        pair = _svg_length(element.get(name))
        return pair[0] if pair else default

    def xy(x_name: str, y_name: str) -> tuple[float, float]:
        # Two attributes, not one pair: cx and cy are separate, and reading
        # cx as a (value, unit) pair is how this became (20, '') the first time.
        px = _svg_length(element.get(x_name))
        py = _svg_length(element.get(y_name))
        return (px[0] if px else 0.0, py[0] if py else 0.0)

    if tag == "line":
        p1 = apply(*xy("x1", "y1"))
        p2 = apply(*xy("x2", "y2"))
        return [[{"t": "line", "p1": p1, "p2": p2}]]
    if tag in ("polyline", "polygon"):
        raw = [float(v) for v in re.findall(
            r"-?[\d.]+(?:[eE][-+]?\d+)?", element.get("points") or "")]
        pts = [apply(raw[i], raw[i + 1]) for i in range(0, len(raw) - 1, 2)]
        if len(pts) < 2:
            stats["skipped"][tag] = stats["skipped"].get(tag, 0) + 1
            return []
        pieces = [{"t": "line", "p1": a, "p2": b}
                  for a, b in zip(pts, pts[1:])]
        if tag == "polygon":
            pieces.append({"t": "line", "p1": pts[-1], "p2": pts[0]})
        return [pieces]
    if tag == "rect":
        x, y = num("x"), num("y")
        w, h = num("width"), num("height")
        if w <= 0 or h <= 0:
            stats["skipped"]["rect"] = stats["skipped"].get("rect", 0) + 1
            return []
        corners = [apply(x, y), apply(x + w, y), apply(x + w, y + h),
                   apply(x, y + h)]
        pieces = [{"t": "line", "p1": a, "p2": b}
                  for a, b in zip(corners, corners[1:] + corners[:1])]
        return [pieces]
    if tag == "circle":
        r = num("r")
        if r <= 0:
            stats["skipped"]["circle"] = stats["skipped"].get("circle", 0) + 1
            return []
        centre = apply(*xy("cx", "cy"))
        radius = r * (apply.scale if apply.uniform else 1.0)
        if not apply.uniform:
            return [[{"t": "poly", "pts": _ellipse_points(
                centre, r * apply.scale, r * apply.scale,
                apply.rotation, 48)}]]
        return [[{"t": "circle", "c": centre, "r": radius}]]
    if tag == "ellipse":
        rx, ry = num("rx"), num("ry")
        if rx <= 0 or ry <= 0:
            stats["skipped"]["ellipse"] = stats["skipped"].get("ellipse", 0) + 1
            return []
        centre = apply(*xy("cx", "cy"))
        return [[{"t": "ellipse", "c": centre,
                  "rx": rx * apply.scale, "ry": ry * apply.scale,
                  "angle": apply.rotation}]]
    if tag == "path":
        contours = _svg_path(element.get("d") or "", apply, stats)
        return contours
    stats["skipped"][tag] = stats["skipped"].get(tag, 0) + 1
    return []


def _ellipse_points(centre, rx: float, ry: float, angle_deg: float,
                    steps: int) -> list[tuple[float, float]]:
    ca = math.cos(math.radians(angle_deg))
    sa = math.sin(math.radians(angle_deg))
    out = []
    for index in range(steps + 1):
        t = 2 * math.pi * index / steps
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((centre[0] + x * ca - y * sa, centre[1] + x * sa + y * ca))
    return out


_PATH_TOKEN = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _svg_path(d: str, apply: _Matrix, stats: dict) -> list[list[dict]]:
    """The `d` attribute: line/arc pieces where a primitive exists, sampled
    polylines where none does (Beziers, elliptical arcs)."""
    tokens = _PATH_TOKEN.findall(d or "")
    contours: list[list[dict]] = []
    pieces: list[dict] = []
    start = current = (0.0, 0.0)
    last_control = None
    command = ""
    index = 0

    def flush() -> None:
        nonlocal pieces
        if pieces:
            contours.append(pieces)
        pieces = []

    def next_numbers(count: int) -> list[float]:
        nonlocal index
        out = []
        while len(out) < count and index < len(tokens):
            token = tokens[index]
            if token.isalpha():
                break
            out.append(float(token))
            index += 1
        return out

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                if pieces:
                    first = _piece_start(pieces[0])
                    if first and _dist(current, first) > 1e-9:
                        pieces.append({"t": "line",
                                       "p1": apply(*current),
                                       "p2": apply(*first)})
                    current = first or current
                    flush()
                    start = current
                last_control = None
                continue
        relative = command.islower()
        upper = command.upper()

        if upper == "M":
            values = next_numbers(2)
            if len(values) < 2:
                break
            point = (values[0], values[1])
            if relative:
                point = (current[0] + point[0], current[1] + point[1])
            flush()
            current = start = point
            last_control = None
            command = "l" if relative else "L"
        elif upper == "L":
            values = next_numbers(2)
            if len(values) < 2:
                break
            point = (values[0], values[1])
            if relative:
                point = (current[0] + point[0], current[1] + point[1])
            pieces.append({"t": "line", "p1": apply(*current),
                           "p2": apply(*point)})
            current = point
            last_control = None
        elif upper == "H":
            values = next_numbers(1)
            if not values:
                break
            point = (values[0] if not relative else current[0] + values[0],
                     current[1])
            pieces.append({"t": "line", "p1": apply(*current),
                           "p2": apply(*point)})
            current = point
            last_control = None
        elif upper == "V":
            values = next_numbers(1)
            if not values:
                break
            point = (current[0],
                     values[0] if not relative else current[1] + values[0])
            pieces.append({"t": "line", "p1": apply(*current),
                           "p2": apply(*point)})
            current = point
            last_control = None
        elif upper in ("C", "S"):
            count = 6 if upper == "C" else 4
            values = next_numbers(count)
            if len(values) < count:
                break
            if upper == "C":
                c1 = (values[0], values[1])
                c2 = (values[2], values[3])
                end = (values[4], values[5])
            else:
                c1 = ((2 * current[0] - last_control[0],
                       2 * current[1] - last_control[1])
                      if last_control else current)
                c2 = (values[0], values[1])
                end = (values[2], values[3])
            if relative:
                c1 = (c1[0] + current[0], c1[1] + current[1])
                c2 = (c2[0] + current[0], c2[1] + current[1])
                end = (end[0] + current[0], end[1] + current[1])
            pts = _cubic_points(current, c1, c2, end)
            pieces.append({"t": "poly", "pts": [apply(*p) for p in pts]})
            current, last_control = end, c2
            stats["beziers"] = stats.get("beziers", 0) + 1
        elif upper in ("Q", "T"):
            count = 4 if upper == "Q" else 2
            values = next_numbers(count)
            if len(values) < count:
                break
            if upper == "Q":
                control = (values[0], values[1])
                end = (values[2], values[3])
            else:
                control = ((2 * current[0] - last_control[0],
                            2 * current[1] - last_control[1])
                           if last_control else current)
                end = (values[0], values[1])
            if relative:
                control = (control[0] + current[0], control[1] + current[1])
                end = (end[0] + current[0], end[1] + current[1])
            pts = _quadratic_points(current, control, end)
            pieces.append({"t": "poly", "pts": [apply(*p) for p in pts]})
            current, last_control = end, control
            stats["beziers"] = stats.get("beziers", 0) + 1
        elif upper == "A":
            values = next_numbers(7)
            if len(values) < 7:
                break
            rx, ry, rotation, large, sweep = (values[0], values[1], values[2],
                                              bool(values[3]), bool(values[4]))
            end = (values[5], values[6])
            if relative:
                end = (end[0] + current[0], end[1] + current[1])
            arc_pieces = _svg_arc(current, end, rx, ry, rotation, large, sweep,
                                  apply, stats)
            pieces.extend(arc_pieces)
            current = end
            last_control = None
        else:
            index += 1
            continue
    flush()
    return contours


def _cubic_points(p0, c1, c2, p1, steps: int = 16):
    out = [tuple(p0)]
    for index in range(1, steps + 1):
        t = index / steps
        u = 1.0 - t
        out.append(tuple(u * u * u * p0[i] + 3 * u * u * t * c1[i]
                         + 3 * u * t * t * c2[i] + t * t * t * p1[i]
                         for i in range(2)))
    return out


def _quadratic_points(p0, c, p1, steps: int = 12):
    out = [tuple(p0)]
    for index in range(1, steps + 1):
        t = index / steps
        u = 1.0 - t
        out.append(tuple(u * u * p0[i] + 2 * u * t * c[i] + t * t * p1[i]
                         for i in range(2)))
    return out


def _svg_arc(start, end, rx, ry, rotation, large, sweep, apply, stats):
    """An SVG elliptical arc: a real arc when it is circular, else sampled.

    The endpoint-to-centre conversion is the one from the SVG specification
    (F.6.5) - it is the only place in this module where the arithmetic has to
    be taken on faith from a document, so the circular case is checked against
    a quarter circle whose centre is known by hand in the tests.
    """
    if rx == 0 or ry == 0 or (abs(start[0] - end[0]) < 1e-12
                              and abs(start[1] - end[1]) < 1e-12):
        return [{"t": "line", "p1": apply(*start), "p2": apply(*end)}]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx2, dy2 = (start[0] - end[0]) / 2.0, (start[1] - end[1]) / 2.0
    x1 = cos_phi * dx2 + sin_phi * dy2
    y1 = -sin_phi * dx2 + cos_phi * dy2
    # scale the radii up if they cannot span the chord
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1.0:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    sign = -1.0 if large == sweep else 1.0
    numerator = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    denominator = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = sign * math.sqrt(max(0.0, numerator / denominator)) if denominator else 0.0
    cx1 = factor * rx * y1 / ry
    cy1 = -factor * ry * x1 / rx
    cx = cos_phi * cx1 - sin_phi * cy1 + (start[0] + end[0]) / 2.0
    cy = sin_phi * cx1 + cos_phi * cy1 + (start[1] + end[1]) / 2.0
    if abs(rx - ry) < 1e-9 and abs(rotation % 360.0) < 1e-9:
        a0 = math.degrees(math.atan2(start[1] - cy, start[0] - cx))
        a1 = math.degrees(math.atan2(end[1] - cy, end[0] - cx))
        return [{"t": "arc", "c": apply(cx, cy), "r": rx * apply.scale,
                 "a0": a0 + apply.rotation, "a1": a1 + apply.rotation,
                 "ccw": sweep}]
    # sample: a rotated ellipse has no sketch primitive
    stats["elliptical_arcs_sampled"] = stats.get(
        "elliptical_arcs_sampled", 0) + 1
    theta0 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    theta1 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    delta = theta1 - theta0
    if sweep and delta < 0:
        delta += 2 * math.pi
    elif not sweep and delta > 0:
        delta -= 2 * math.pi
    pts = []
    for index in range(33):
        theta = theta0 + delta * index / 32.0
        x, y = rx * math.cos(theta), ry * math.sin(theta)
        pts.append(apply(cx + x * cos_phi - y * sin_phi,
                         cy + x * sin_phi + y * cos_phi))
    return [{"t": "poly", "pts": pts}]


def _flip_contour_y(contour: dict) -> None:
    """SVG's Y axis points down the page. Everything here is Y-up."""
    for piece in contour["pieces"]:
        kind = piece["t"]
        if kind == "line":
            piece["p1"] = (piece["p1"][0], -piece["p1"][1])
            piece["p2"] = (piece["p2"][0], -piece["p2"][1])
        elif kind in ("circle", "arc"):
            piece["c"] = (piece["c"][0], -piece["c"][1])
            if kind == "arc":
                piece["a0"] = -piece["a0"]
                piece["a1"] = -piece["a1"]
                piece["ccw"] = not piece["ccw"]
        elif kind == "ellipse":
            piece["c"] = (piece["c"][0], -piece["c"][1])
            piece["angle"] = -piece.get("angle", 0.0)
        elif kind == "poly":
            piece["pts"] = [(x, -y) for x, y in piece["pts"]]


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def parse_pdf(data: bytes) -> dict[str, Any]:
    """Vector PDF: walk each page's content stream and keep the painted paths.

    Nothing here reads glyphs. Text drawn as text stays text - its outlines live
    in the font program, and extracting those is a font parser, not a drawing
    reader (see the ceiling entry). A drawing whose text was converted to curves
    - which is what "文字已转曲" means - arrives as ordinary paths and is read
    like the rest, which is why the report's Apple sheet would work here and a
    plain Word export would not.
    """
    if data[:5] != b"%PDF-":
        raise ArgumentRejected(
            "The file does not start with '%PDF-'. Nothing was read.",
            code="PDF_UNREADABLE", stage="validate")
    objects = _pdf_objects(data)
    if not objects:
        raise ArgumentRejected(
            "No PDF objects could be found: the file is either damaged or "
            "encrypted. Nothing was read.",
            code="PDF_UNREADABLE", stage="validate")
    pages = _pdf_pages(objects)
    if not pages:
        raise ArgumentRejected(
            f"The PDF has {len(objects)} objects but no page dictionary. "
            "Nothing was read.", code="PDF_NO_PAGES", stage="validate")

    stats: dict[str, Any] = {"entities": {}, "skipped": {}, "text_runs": 0,
                             "elements": 0, "pages": len(pages),
                             "content_streams": 0, "curves": 0,
                             "form_xobjects": 0, "clip_ops": 0}
    notes: list[str] = []
    contours: list[dict] = []
    for number, page in pages:
        matrix = _pdf_page_matrix(page)
        for ref in _pdf_contents(page):
            stream = objects.get(ref)
            if stream is None:
                continue
            content = _pdf_decode(stream)
            if not content:
                continue
            stats["content_streams"] += 1
            _pdf_run(content, objects, contours, stats, matrix,
                     resources=_pdf_resources(objects, page), depth=0,
                     source=f"page {number}")
    if stats["text_runs"]:
        notes.append(
            f"{stats['text_runs']} text run(s) were drawn as TEXT, not as "
            "outlines, and were not converted: a glyph's outline lives in the "
            "font program, which this does not parse. Dimensions and notes in "
            "such a drawing are missing from the import. A drawing exported "
            "with its text converted to curves (\"convert text to outlines\") "
            "has no text runs at all and imports completely.")
    if stats["clip_ops"]:
        notes.append(
            f"{stats['clip_ops']} clipping path(s) were ignored: geometry "
            "outside the visible area is still imported. That is usually the "
            "whole model - what a clip removes is a construction box - but if "
            "the import has lines that were not on screen, this is why.")
    return {"contours": contours, "stats": stats, "mm_per_unit": _PT_MM,
            "unit_code": None, "units": "pt", "notes": notes}


# --- PDF object layer -----------------------------------------------------

_PDF_OBJ = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
_PDF_WS = b"\x00\t\n\x0c\r "
_PDF_DELIM = b"()<>[]{}/%"


def _pdf_objects(data: bytes) -> dict[int, dict]:
    """Object number -> {"head": bytes up to 'stream', "stream": bytes}.

    A tolerant scan, not an xref reader: broken xref tables are common enough in
    exported drawings that the cross-reference is not worth trusting, and the
    "N 0 obj" pattern is not ambiguous in practice.
    """
    objects: dict[int, dict] = {}
    for match in _PDF_OBJ.finditer(data):
        number = int(match.group(1))
        start = match.end()
        end = data.find(b"endobj", start)
        if end < 0:
            end = len(data)
        body = data[start:end]
        stream_at = body.find(b"stream")
        head = body if stream_at < 0 else body[:stream_at]
        stream = None
        if stream_at >= 0:
            at = stream_at + len(b"stream")
            if body[at:at + 2] == b"\r\n":
                at += 2
            elif body[at:at + 1] in (b"\n", b"\r"):
                at += 1
            stop = body.rfind(b"endstream")
            stream = body[at:stop] if stop >= 0 else body[at:]
        objects[number] = {"head": head, "stream": stream}
    objects.update(_pdf_object_streams(objects))
    return objects


def _pdf_object_streams(objects: dict[int, dict]) -> dict[int, dict]:
    """Objects packed inside /ObjStm (PDF 1.5+). Exporters use them."""
    extra: dict[int, dict] = {}
    for number, obj in list(objects.items()):
        if b"/ObjStm" not in obj["head"] or obj["stream"] is None:
            continue
        try:
            body = _pdf_decode(obj)
            info = _pdf_dict(obj["head"])
            first = int(_pdf_number(info.get("First"), 0))
            pairs = body[:first].split()
            for index in range(0, len(pairs) - 1, 2):
                child = int(pairs[index])
                offset = int(pairs[index + 1])
                stop = (int(pairs[index + 3]) if index + 3 < len(pairs)
                        else len(body) - first)
                extra[child] = {"head": body[first + offset:first + stop],
                                "stream": None}
        except Exception:  # noqa: BLE001 - a broken ObjStm is not fatal
            continue
    return extra


def _pdf_decode(obj: dict) -> bytes:
    data = obj.get("stream")
    if not data:
        return b""
    head = obj.get("head") or b""
    if b"FlateDecode" in head:
        try:
            return zlib.decompress(data)
        except zlib.error:
            try:
                # Truncated streams still carry most of the drawing; the
                # decompressor's partial output beats nothing.
                return zlib.decompressobj().decompress(data)
            except zlib.error:
                return b""
    return data


class _PdfLexer:
    """Tokens from a PDF buffer: ("obj", value) or ("op", bytes)."""

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.index = 0

    def _skip(self) -> None:
        buf, size = self.buf, len(self.buf)
        while self.index < size:
            char = buf[self.index:self.index + 1]
            if char in _PDF_WS:
                self.index += 1
            elif char == b"%":
                while self.index < size and buf[self.index:self.index + 1] not in b"\r\n":
                    self.index += 1
            else:
                return

    def tokens(self):
        buf, size = self.buf, len(self.buf)
        while True:
            self._skip()
            start = self.index
            if start >= size:
                return
            char = buf[start:start + 1]
            if char == b"/":
                stop = start + 1
                while (stop < size and buf[stop:stop + 1] not in _PDF_WS
                       and buf[stop:stop + 1] not in _PDF_DELIM):
                    stop += 1
                self.index = stop
                yield ("obj", ("name", buf[start + 1:stop]))
            elif char == b"(":
                depth, stop = 1, start + 1
                while stop < size:
                    piece = buf[stop:stop + 1]
                    if piece == b"\\":
                        stop += 2
                        continue
                    if piece == b"(":
                        depth += 1
                    elif piece == b")":
                        depth -= 1
                        if depth == 0:
                            stop += 1
                            break
                    stop += 1
                self.index = stop
                yield ("obj", ("string", buf[start + 1:stop - 1]))
            elif char == b"<" and buf[start:start + 2] == b"<<":
                depth, stop = 1, start + 2
                while stop < size:
                    if buf[stop:stop + 2] == b"<<":
                        depth += 1
                        stop += 2
                    elif buf[stop:stop + 2] == b">>":
                        depth -= 1
                        stop += 2
                        if depth == 0:
                            break
                    elif buf[stop:stop + 1] == b"(":
                        inner, stop = 1, stop + 1
                        while stop < size and inner:
                            if buf[stop:stop + 1] == b"\\":
                                stop += 2
                                continue
                            if buf[stop:stop + 1] == b"(":
                                inner += 1
                            elif buf[stop:stop + 1] == b")":
                                inner -= 1
                            stop += 1
                        continue
                    else:
                        stop += 1
                inner = buf[start + 2:stop - 2] if depth == 0 else buf[start + 2:stop]
                self.index = stop
                yield ("obj", ("dict", inner))
            elif char == b"<":
                stop = start + 1
                while stop < size and buf[stop:stop + 1] != b">":
                    stop += 1
                self.index = stop + 1
                yield ("obj", ("string", buf[start + 1:stop]))
            elif char == b"[":
                self.index = start + 1
                yield ("obj", ("array_open", None))
            elif char == b"]":
                self.index = start + 1
                yield ("obj", ("array_close", None))
            else:
                stop = start
                while (stop < size and buf[stop:stop + 1] not in _PDF_WS
                       and buf[stop:stop + 1] not in _PDF_DELIM):
                    stop += 1
                if stop == start:
                    self.index = start + 1
                    continue
                word = buf[start:stop]
                self.index = stop
                try:
                    yield ("obj", ("num", float(word)))
                    continue
                except ValueError:
                    pass
                yield ("op", word)


def _pdf_dict(head: bytes) -> dict:
    at = head.find(b"<<")
    if at < 0:
        return {}
    body = head[at + 2:]
    stop = body.rfind(b">>")
    if stop > 0:
        body = body[:stop]
    return _pdf_dict_body(body)


def _pdf_dict_body(body: bytes) -> dict:
    """Dict bytes -> {name: value}, folding 'num num R' into a ref."""
    out: dict[str, Any] = {}
    tokens = list(_PdfLexer(body).tokens())
    index = 0
    while index < len(tokens):
        kind, value = tokens[index]
        if kind != "obj" or value[0] != "name":
            index += 1
            continue
        key = value[1].decode("latin-1")
        if index + 1 >= len(tokens):
            break
        next_kind, next_value = tokens[index + 1]
        if next_kind == "obj" and next_value[0] == "array_open":
            depth, items, cursor = 1, [], index + 2
            while cursor < len(tokens) and depth:
                token_kind, token_value = tokens[cursor]
                if token_kind == "obj" and token_value[0] == "array_open":
                    depth += 1
                elif token_kind == "obj" and token_value[0] == "array_close":
                    depth -= 1
                    if depth == 0:
                        break
                else:
                    items.append(("op", token_value) if token_kind == "op"
                                 else token_value)
                cursor += 1
            folded, cursor2 = [], 0
            while cursor2 < len(items):
                if (cursor2 + 2 < len(items) and items[cursor2][0] == "num"
                        and items[cursor2 + 1][0] == "num"
                        and items[cursor2 + 2][0] == "op"
                        and items[cursor2 + 2][1] == b"R"):
                    folded.append(("ref", (int(items[cursor2][1]),
                                           int(items[cursor2 + 1][1]))))
                    cursor2 += 3
                else:
                    folded.append(items[cursor2])
                    cursor2 += 1
            out[key] = ("array", folded)
            index = cursor + 1
            continue
        if next_kind == "obj":
            if next_value[0] == "num":
                if (index + 3 < len(tokens) and tokens[index + 2][0] == "obj"
                        and tokens[index + 2][1][0] == "num"
                        and tokens[index + 3][0] == "op"
                        and tokens[index + 3][1] == b"R"):
                    out[key] = ("ref", (int(next_value[1]),
                                        int(tokens[index + 2][1][1])))
                    index += 4
                    continue
            out[key] = next_value
        index += 2
    return out


def _pdf_number(value, default: float = 0.0) -> float:
    if isinstance(value, tuple) and value and value[0] == "num":
        try:
            return float(value[1])
        except (TypeError, ValueError):
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pdf_pages(objects: dict[int, dict]) -> list[tuple[int, dict]]:
    pages = []
    for number, obj in sorted(objects.items()):
        if b"/Page" not in obj["head"]:
            continue
        info = _pdf_dict(obj["head"])
        kind = info.get("Type")
        if not (isinstance(kind, tuple) and kind[0] == "name"
                and kind[1] == b"Page"):
            continue
        pages.append((number, info))
    return pages


def _pdf_contents(page: dict) -> list[int]:
    contents = page.get("Contents")
    if not contents:
        return []
    if contents[0] == "ref":
        return [contents[1][0]]
    if contents[0] == "array":
        return [item[1][0] for item in contents[1] if item[0] == "ref"]
    return []


def _pdf_page_matrix(page: dict) -> tuple[float, ...]:
    """The page's own /Rotate, so an upright drawing stays upright."""
    rotate = int(_pdf_number(page.get("Rotate"), 0)) % 360
    box = page.get("MediaBox")
    width = height = 0.0
    if isinstance(box, tuple) and box[0] == "array":
        values = [_pdf_number(item) for item in box[1]]
        if len(values) >= 4:
            width, height = values[2] - values[0], values[3] - values[1]
    if rotate == 90:
        return (0.0, -1.0, 1.0, 0.0, 0.0, width)
    if rotate == 180:
        return (-1.0, 0.0, 0.0, -1.0, width, height)
    if rotate == 270:
        return (0.0, 1.0, -1.0, 0.0, height, 0.0)
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _pdf_run(content: bytes, objects: dict[int, dict], contours: list[dict],
             stats: dict, matrix, *, resources: dict, depth: int,
             source: str) -> None:
    """One content stream. Painted subpaths become contours."""
    lexer = _PdfLexer(content)
    state = [tuple(matrix)]
    operands: list[tuple] = []
    pieces: list[dict] = []
    current: tuple[float, float] | None = None
    start_point: tuple[float, float] | None = None

    def transform() -> _Matrix:
        return _Matrix(state[-1])

    def flush() -> None:
        nonlocal pieces, current, start_point
        if pieces:
            contours.append({"pieces": pieces, "closed": False, "layer": "pdf",
                             "source": source})
        pieces = []
        current = None
        start_point = None

    for kind, value in lexer.tokens():
        if kind == "obj":
            if value[0] == "array_open":
                operands.append(("array_start", None))
            elif value[0] == "array_close":
                items, frame = [], []
                while operands and operands[-1][0] != "array_start":
                    frame.append(operands.pop())
                if operands:
                    operands.pop()
                items.extend(reversed(frame))
                operands.append(("array", items))
            else:
                operands.append(value)
            continue

        op = value
        numbers = [item[1] for item in operands if item[0] == "num"]
        if op == b"q":
            state.append(state[-1])
        elif op == b"Q":
            if len(state) > 1:
                state.pop()
        elif op == b"cm" and len(numbers) >= 6:
            state[-1] = _mat_mul(tuple(numbers[-6:]), state[-1])
        elif op == b"m" and len(numbers) >= 2:
            flush()
            current = transform()(numbers[-2], numbers[-1])
            start_point = current
        elif op == b"l" and len(numbers) >= 2:
            point = transform()(numbers[-2], numbers[-1])
            if current is None:
                current = point
                start_point = point
            pieces.append({"t": "line", "p1": current, "p2": point})
            current = point
        elif op in (b"c", b"v", b"y") and len(numbers) >= 4:
            matrix_now = transform()
            if op == b"c" and len(numbers) >= 6:
                c1 = matrix_now(*numbers[-6:-4])
                c2 = matrix_now(*numbers[-4:-2])
                end = matrix_now(*numbers[-2:])
            elif op == b"v":
                c1 = current or matrix_now(*numbers[-4:-2])
                c2 = matrix_now(*numbers[-4:-2])
                end = matrix_now(*numbers[-2:])
            else:
                c1 = matrix_now(*numbers[-4:-2])
                end = matrix_now(*numbers[-2:])
                c2 = end
            p0 = current or c1
            points = _cubic_points(p0, c1, c2, end)
            pieces.append({"t": "poly", "pts": points})
            stats["curves"] += 1
            current = end
        elif op == b"re" and len(numbers) >= 4:
            flush()
            x, y, w, h = numbers[-4:]
            matrix_now = transform()
            corners = [matrix_now(x, y), matrix_now(x + w, y),
                       matrix_now(x + w, y + h), matrix_now(x, y + h)]
            pieces = [{"t": "line", "p1": a, "p2": b}
                      for a, b in zip(corners, corners[1:] + corners[:1])]
            current = corners[0]
            start_point = corners[0]
        elif op == b"h":
            if pieces and start_point is not None and current is not None:
                if _dist(current, start_point) > 1e-9:
                    pieces.append({"t": "line", "p1": current,
                                   "p2": start_point})
                current = start_point
        elif op in (b"S", b"s", b"f", b"F", b"f*", b"B", b"b", b"B*", b"b*"):
            flush()
        elif op == b"n":
            pieces = []
            current = None
            start_point = None
        elif op in (b"Tj", b"TJ", b"'", b'"'):
            stats["text_runs"] += 1
        elif op in (b"W", b"W*"):
            stats["clip_ops"] += 1
        elif op == b"Do" and depth < 3:
            name = None
            for item in reversed(operands):
                if item[0] == "name":
                    name = item[1]
                    break
            if name is not None:
                _pdf_xobject(name, objects, contours, stats, state[-1],
                             resources=resources, depth=depth + 1,
                             source=source)
        operands = []


def _pdf_resources(objects: dict[int, dict], page: dict) -> dict:
    """The page's /Resources dict, resolved through its reference."""
    holder = page.get("Resources")
    if not (isinstance(holder, tuple) and holder[0] == "ref"):
        return {}
    target = objects.get(holder[1][0])
    return _pdf_dict(target["head"]) if target else {}


def _pdf_xobject(name: bytes, objects: dict[int, dict], contours: list[dict],
                 stats: dict, matrix, *, resources: dict, depth: int,
                 source: str) -> None:
    """A Form XObject: a nested content stream with its own /Matrix.

    Resolved through the page's /Resources, which is how a PDF names it. The
    first version of this searched every object in the file for a /XObject
    entry, which finds the first one it happens to meet - the wrong form for
    any drawing with more than one.
    """
    table_ref = resources.get("XObject")
    if not (isinstance(table_ref, tuple) and table_ref[0] == "ref"):
        return
    table_obj = objects.get(table_ref[1][0])
    if not table_obj:
        return
    table = _pdf_dict(table_obj["head"])
    entry = table.get(name.decode("latin-1"))
    if not (isinstance(entry, tuple) and entry[0] == "ref"):
        return
    form = objects.get(entry[1][0])
    if not form:
        return
    info = _pdf_dict(form["head"])
    sub_type = info.get("Subtype")
    if not (isinstance(sub_type, tuple) and sub_type[0] == "name"
            and sub_type[1] == b"Form"):
        stats["skipped"]["image_xobject"] = stats["skipped"].get(
            "image_xobject", 0) + 1
        return
    form_matrix = _pdf_matrix(info.get("Matrix"))
    stats["form_xobjects"] += 1
    content = _pdf_decode(form)
    if not content:
        return
    inner_resources = _pdf_resources(objects, info) or resources
    _pdf_run(content, objects, contours, stats, _mat_mul(form_matrix, matrix),
             resources=inner_resources, depth=depth + 1, source=source)


def _pdf_matrix(value) -> tuple[float, ...]:
    if isinstance(value, tuple) and value[0] == "array":
        numbers = [_pdf_number(item) for item in value[1]]
        if len(numbers) >= 6:
            return tuple(numbers[:6])
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
