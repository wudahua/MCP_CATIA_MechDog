"""0.3.128: reading DXF, SVG and PDF drawings into a sketch (P0-1).

The parsers are pure, so they are tested here against files whose geometry is
known by hand - not against the sample drawings in the workspace, which are
evidence that the parser RUNS, not that it is right.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import zlib

import pytest

from catia_mcp import blueprint
from catia_mcp.blueprint import (
    contour_bbox,
    contour_is_closed,
    contour_points,
    parse_dxf,
    parse_pdf,
    parse_svg,
    read_drawing,
    segment_count,
    stitch_contours,
)
from catia_mcp.results import ArgumentOutOfRange, ArgumentRejected
from catia_mcp.tools.blueprint import BlueprintTools, MAX_ELEMENTS


# ── file builders ────────────────────────────────────────────────────────
def dxf(body: str, units: int = 4, blocks: str = "") -> bytes:
    text = ("0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n" f"{units}\n0\nENDSEC\n")
    if blocks:
        text += "0\nSECTION\n2\nBLOCKS\n" + blocks + "0\nENDSEC\n"
    text += "0\nSECTION\n2\nENTITIES\n" + body + "0\nENDSEC\n0\nEOF\n"
    return text.encode("utf-8")


def tag(code: int, value) -> str:
    return f"{code}\n{value}\n"


def dxf_line(x1, y1, x2, y2) -> str:
    return ("0\nLINE\n" + tag(8, "OUTLINE") + tag(10, x1) + tag(20, y1)
            + tag(11, x2) + tag(21, y2))


def dxf_circle(cx, cy, r, layer="HOLES") -> str:
    return ("0\nCIRCLE\n" + tag(8, layer) + tag(10, cx) + tag(20, cy)
            + tag(40, r))


def dxf_polyline(points, closed=True, layer="OUTLINE") -> str:
    body = "0\nLWPOLYLINE\n" + tag(8, layer) + tag(90, len(points))
    body += tag(70, 1 if closed else 0)
    for x, y, bulge in points:
        body += tag(10, x) + tag(20, y)
        if bulge:
            body += tag(42, bulge)
    return body


def write(tmp_path, name: str, data: bytes) -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def pdf_bytes(content: bytes, mediabox: str = "0 0 200 100",
              compress: bool = False) -> bytes:
    """A PDF with the objects in the order this parser scans for.

    No xref table: the reader does not use one (a broken xref is common in
    exported drawings, and `N 0 obj` is unambiguous), so a test file does not
    need one either.
    """
    stream = zlib.compress(content) if compress else content
    filt = b" /Filter /FlateDecode" if compress else b""
    parts = [
        b"%PDF-1.4\n",
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox ["
        + mediabox.encode() + b"] /Contents 4 0 R /Resources << >> >> endobj\n",
        b"4 0 obj << /Length " + str(len(stream)).encode() + filt
        + b" >> stream\n" + stream + b"\nendstream endobj\n",
        b"trailer << /Root 1 0 R >>\n%%EOF\n",
    ]
    return b"".join(parts)


SVG_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="{view}">
{body}
</svg>
"""


# ── DXF ──────────────────────────────────────────────────────────────────
def test_a_closed_dxf_polyline_is_one_contour_with_four_edges():
    parsed = parse_dxf(dxf(dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0),
                                        (0, 20, 0)])))
    assert len(parsed["contours"]) == 1
    contour = parsed["contours"][0]
    assert segment_count(contour) == 4
    assert contour_is_closed(contour)
    assert contour_bbox(contour) == (0.0, 0.0, 40.0, 20.0)


def test_a_bulge_is_an_arc_of_the_right_centre_and_radius():
    """bulge 1 on a 40 mm chord is a semicircle of radius 20 about its middle.

    This is the arithmetic a chord-only reader gets wrong in a way that still
    looks like a solid: the pad is 1 570 mm3 smaller and nobody notices.
    """
    parsed = parse_dxf(dxf(dxf_polyline([(0, 0, 1), (40, 0, 0)])))
    pieces = parsed["contours"][0]["pieces"]
    assert len(pieces) == 2
    arc = pieces[0]
    assert arc["t"] == "arc"
    assert arc["c"] == pytest.approx((20.0, 0.0))
    assert arc["r"] == pytest.approx(20.0)
    assert arc["ccw"] is True
    # and it bulges DOWN from that chord: CCW from 180 degrees to 0.
    assert contour_points({"pieces": [arc]})[0][0] == pytest.approx(0.0, abs=1e-9)


def test_a_negative_bulge_turns_the_other_way():
    parsed = parse_dxf(dxf(dxf_polyline([(0, 0, -1), (40, 0, 0)])))
    arc = parsed["contours"][0]["pieces"][0]
    assert arc["ccw"] is False
    assert arc["c"] == pytest.approx((20.0, 0.0))
    assert arc["r"] == pytest.approx(20.0)


def test_insunits_sets_the_millimetre_factor():
    inch = parse_dxf(dxf(dxf_circle(0, 0, 1), units=1))
    assert inch["mm_per_unit"] == 25.4
    assert inch["units"] == "inch"
    mm = parse_dxf(dxf(dxf_circle(0, 0, 1), units=4))
    assert mm["mm_per_unit"] == 1.0


def test_no_insunits_is_millimetres_and_says_so():
    parsed = parse_dxf(dxf(dxf_circle(0, 0, 1), units=0))
    assert parsed["mm_per_unit"] == 1.0
    assert any("MILLIMETRES" in note for note in parsed["notes"])


def test_old_style_polyline_collects_its_vertices():
    body = ("0\nPOLYLINE\n" + tag(8, "0") + tag(70, 1)
            + "0\nVERTEX\n" + tag(10, 0) + tag(20, 0)
            + "0\nVERTEX\n" + tag(10, 10) + tag(20, 0)
            + "0\nVERTEX\n" + tag(10, 10) + tag(20, 10)
            + "0\nSEQEND\n")
    parsed = parse_dxf(dxf(body))
    contour = parsed["contours"][0]
    assert segment_count(contour) == 3
    assert contour_is_closed(contour)


def test_an_insert_lands_its_block_at_the_insertion_point():
    blocks = ("0\nBLOCK\n" + tag(2, "HOLE") + tag(10, 0) + tag(20, 0)
              + dxf_circle(0, 0, 2) + "0\nENDBLK\n")
    body = ("0\nINSERT\n" + tag(2, "HOLE") + tag(10, 30) + tag(20, 10)
            + tag(41, 2) + tag(42, 2) + tag(50, 0))
    parsed = parse_dxf(dxf(body, blocks=blocks))
    circle = parsed["contours"][0]["pieces"][0]
    assert circle["c"] == pytest.approx((30.0, 10.0))
    assert circle["r"] == pytest.approx(4.0)      # scaled by 41/42
    assert parsed["stats"]["inserts"] == 1


def test_text_and_dimensions_are_counted_not_imported():
    body = dxf_line(0, 0, 10, 0) + "0\nTEXT\n" + tag(1, "40") + "0\nDIMENSION\n"
    parsed = parse_dxf(dxf(body))
    assert len(parsed["contours"]) == 1
    assert parsed["stats"]["skipped"]["text"] == 1
    assert parsed["stats"]["skipped"]["dimension"] == 1


def test_dwg_is_refused_with_the_way_out():
    with pytest.raises(ArgumentRejected) as caught:
        blueprint.detect_format("part.dwg")
    message = str(caught.value)
    assert "DXF" in message and "Nothing was read" in message


# ── SVG ──────────────────────────────────────────────────────────────────
def test_svg_shapes_land_at_their_stated_millimetres():
    body = ('<g transform="translate(10,10)">'
            '<rect x="0" y="0" width="40" height="20"/>'
            '<circle cx="20" cy="10" r="3"/></g>')
    data = SVG_TEMPLATE.format(width="100mm", height="60mm", view="0 0 100 60",
                               body=body).encode()
    parsed = parse_svg(data)
    assert parsed["mm_per_unit"] == 1.0
    boxes = sorted(contour_bbox(c) for c in parsed["contours"])
    assert boxes[0] == pytest.approx((10.0, -30.0, 50.0, -10.0))   # the plate
    # the hole sits at cx=20 inside a translate(10,10), so x 27..33 - the
    # first version of this line forgot the translate on x only.
    assert boxes[1] == pytest.approx((27.0, -23.0, 33.0, -17.0))


def test_svg_units_are_read_from_width_and_viewbox():
    body = '<rect x="0" y="0" width="10" height="10"/>'
    data = SVG_TEMPLATE.format(width="50mm", height="50mm", view="0 0 100 100",
                               body=body).encode()
    parsed = parse_svg(data)
    assert parsed["mm_per_unit"] == 0.5           # 50 mm across 100 user units
    # parse_svg leaves the numbers in user units; read_drawing applies the
    # factor. Checking both halves separately is the point of the split.
    assert contour_bbox(parsed["contours"][0])[2] == pytest.approx(10.0)


def test_svg_arc_command_becomes_a_quarter_circle():
    """M 0 0 A 10 10 0 0 1 10 10 is a quarter of the circle about (0,10).

    sweep=1 asks for the POSITIVE angle direction, and SVG counts angles with y
    pointing down the page - so of the two centres that fit the chord, the one
    that works is (0,10), not (10,0). The first version of this test asserted
    (10,0) and the code was right.
    """
    body = '<path d="M 0 0 A 10 10 0 0 1 10 10"/>'
    data = SVG_TEMPLATE.format(width="100mm", height="100mm", view="0 0 100 100",
                               body=body).encode()
    parsed = parse_svg(data)
    piece = parsed["contours"][0]["pieces"][0]
    assert piece["t"] == "arc"
    # Y is flipped by then, so the SVG centre (0,10) reads (0,-10) here.
    assert piece["c"] == pytest.approx((0.0, -10.0), abs=1e-9)
    assert piece["r"] == pytest.approx(10.0)
    points = contour_points({"pieces": [piece]})
    middle = points[len(points) // 2]
    # midway along it is 45 degrees from the centre, i.e. (7.07, 2.93) flipped
    assert middle[0] == pytest.approx(10 * math.cos(math.pi / 4), abs=0.6)
    assert middle[1] == pytest.approx(-(10 - 10 * math.sin(math.pi / 4)), abs=0.6)


def test_svg_a_bezier_is_sampled_and_the_contour_says_poly():
    body = '<path d="M 0 0 C 10 0 20 10 20 20"/>'
    data = SVG_TEMPLATE.format(width="100mm", height="100mm", view="0 0 100 100",
                               body=body).encode()
    parsed = parse_svg(data)
    piece = parsed["contours"][0]["pieces"][0]
    assert piece["t"] == "poly"
    assert piece["pts"][0] == pytest.approx((0.0, 0.0))
    assert piece["pts"][-1] == pytest.approx((20.0, -20.0))


def test_svg_y_is_flipped_so_the_part_is_not_mirrored():
    """A rect from y=0 to y=20 in a 60-unit document is at the TOP of the page.

    After the flip it must sit at y = -20..0 (the caller's origin="bbox" then
    puts it at 0..20). Keeping SVG's y as it comes would mirror the part
    vertically and leave every dimension looking right.
    """
    body = '<rect x="0" y="0" width="10" height="20"/>'
    data = SVG_TEMPLATE.format(width="100mm", height="60mm", view="0 0 100 60",
                               body=body).encode()
    parsed = parse_svg(data)
    assert contour_bbox(parsed["contours"][0]) == pytest.approx((0.0, -20.0, 10.0, 0.0))


def test_svg_hidden_elements_are_skipped_and_counted():
    body = ('<rect x="0" y="0" width="10" height="10"/>'
            '<rect x="20" y="0" width="10" height="10" style="display:none"/>')
    data = SVG_TEMPLATE.format(width="100mm", height="100mm", view="0 0 100 100",
                               body=body).encode()
    parsed = parse_svg(data)
    assert len(parsed["contours"]) == 1
    assert parsed["stats"]["skipped"]["hidden"] == 1


# ── PDF ──────────────────────────────────────────────────────────────────
def test_pdf_paths_become_lines_at_the_right_millimetres():
    content = b"1 0 0 1 10 10 cm 0 0 m 40 0 l 40 20 l 0 20 l h S"
    parsed = parse_pdf(pdf_bytes(content))
    assert len(parsed["contours"]) == 1
    contour = parsed["contours"][0]
    assert segment_count(contour) == 4
    assert contour_is_closed(contour)
    # points come back as points; 25.4/72 is what read_drawing multiplies by
    box = contour_bbox(contour)
    assert box[0] == pytest.approx(10.0)
    assert box[2] - box[0] == pytest.approx(40.0, rel=1e-9)
    assert parsed["mm_per_unit"] == pytest.approx(25.4 / 72)


def test_pdf_flate_streams_are_decompressed():
    content = b"0 0 m 30 0 l 30 10 l 0 10 l h S"
    parsed = parse_pdf(pdf_bytes(content, compress=True))
    assert len(parsed["contours"]) == 1
    # three lines plus the one `h` adds to close them
    assert segment_count(parsed["contours"][0]) == 4
    assert contour_is_closed(parsed["contours"][0])


def test_pdf_text_runs_are_reported_rather_than_silently_dropped():
    content = (b"0 0 m 30 0 l 30 10 l 0 10 l h S "
               b"BT /F1 12 Tf 10 10 Td (40) Tj ET")
    parsed = parse_pdf(pdf_bytes(content))
    assert parsed["stats"]["text_runs"] == 1
    assert any("text run" in note for note in parsed["notes"])


def test_pdf_rect_operator_closes_itself():
    content = b"5 5 20 10 re S"
    parsed = parse_pdf(pdf_bytes(content))
    contour = parsed["contours"][0]
    assert segment_count(contour) == 4
    assert contour_is_closed(contour)


# ── stitching ────────────────────────────────────────────────────────────
def test_four_separate_edges_stitch_into_one_closed_loop():
    """This is the shape a CAD export actually writes: one entity per EDGE."""
    body = (dxf_line(0, 0, 30, 0) + dxf_line(30, 0, 30, 30)
            + dxf_line(30, 30, 0, 30) + dxf_line(0, 30, 0, 0))
    parsed = parse_dxf(dxf(body, units=4))
    assert len(parsed["contours"]) == 4
    stitched, info = stitch_contours(parsed["contours"], 1e-3)
    assert len(stitched) == 1
    assert stitched[0]["closed"] is True
    assert info["loops"] == 1
    assert segment_count(stitched[0]) == 4


def test_a_stitched_chain_keeps_each_piece_in_the_right_direction():
    """Reversing a contour has to reverse its arcs too, or a slot comes out
    with its caps inside out."""
    body = (dxf_polyline([(0, 0, 0), (10, 0, -1)])        # line, then a cap
            + dxf_line(10, 0, 0, 0))
    parsed = parse_dxf(dxf(body))
    stitched, _info = stitch_contours(parsed["contours"], 1e-3)
    assert len(stitched) == 1
    kinds = [piece["t"] for piece in stitched[0]["pieces"]]
    assert kinds.count("line") == 2 and kinds.count("arc") == 1


def test_an_ambiguous_joint_is_left_open_and_counted_not_guessed():
    """Three edges meeting at one point: the drawing does not say which two
    belong together, so the chain stops there and says so."""
    body = (dxf_line(0, 0, 10, 0) + dxf_line(10, 0, 10, 10)
            + dxf_line(10, 0, 20, 0))
    parsed = parse_dxf(dxf(body))
    stitched, info = stitch_contours(parsed["contours"], 1e-3)
    assert info["branches"] >= 1
    assert info["loops"] == 0


def test_a_closed_contour_is_left_alone_by_stitching():
    parsed = parse_dxf(dxf(dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0),
                                        (0, 20, 0)])))
    stitched, info = stitch_contours(parsed["contours"], 1e-3)
    assert len(stitched) == 1
    assert info["stitched"] == 0


# ── filters and framing ──────────────────────────────────────────────────
def test_min_size_drops_the_small_contours_and_counts_them(tmp_path):
    body = (dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0)])
            + dxf_line(60, 0, 60.2, 0))
    path = write(tmp_path, "mixed.dxf", dxf(body))
    everything = read_drawing(path)
    assert everything["counts"]["contours"] == 2
    filtered = read_drawing(path, min_size=1.0)
    assert filtered["counts"]["contours"] == 1
    assert filtered["counts"]["dropped"]["size"] == 1


def test_layers_are_case_insensitive_and_counted(tmp_path):
    body = (dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0)])
            + dxf_circle(20, 10, 3))
    path = write(tmp_path, "layers.dxf", dxf(body))
    holes = read_drawing(path, layers=["holes"])
    assert holes["counts"]["contours"] == 1
    assert holes["counts"]["dropped"]["layer"] == 1


def test_a_degenerate_contour_is_dropped_so_smallest_mm_means_something(tmp_path):
    body = (dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0)])
            + dxf_line(60, 5, 60, 5))
    path = write(tmp_path, "degen.dxf", dxf(body))
    drawing = read_drawing(path)
    assert drawing["counts"]["dropped"]["degenerate"] == 1
    diagonals = sorted(math.hypot(b[2] - b[0], b[3] - b[1])
                       for b in (contour_bbox(c) for c in drawing["contours"]))
    assert diagonals[0] > 1.0


def test_region_keeps_contours_whose_centre_is_inside(tmp_path):
    body = (dxf_polyline([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
            + dxf_polyline([(100, 0, 0), (110, 0, 0), (110, 10, 0),
                            (100, 10, 0)]))
    path = write(tmp_path, "two.dxf", dxf(body))
    left = read_drawing(path, region=[-5, -5, 50, 50])
    assert left["counts"]["contours"] == 1
    assert left["counts"]["dropped"]["region"] == 1


def test_origin_bbox_puts_the_lower_left_corner_at_zero(tmp_path):
    body = dxf_polyline([(-30, 40, 0), (-10, 40, 0), (-10, 60, 0), (-30, 60, 0)])
    path = write(tmp_path, "offset.dxf", dxf(body))
    drawing = read_drawing(path)
    assert drawing["bounds_mm"] == pytest.approx([0.0, 0.0, 20.0, 20.0])
    assert drawing["origin_shift_mm"] == pytest.approx([30.0, -40.0])
    kept = read_drawing(path, origin="as_drawn")
    assert kept["bounds_mm"] == pytest.approx([-30.0, 40.0, -10.0, 60.0])


def test_scale_is_applied_after_the_files_own_units(tmp_path):
    """A 1-inch drawing at 1:100 is 2 540 mm across, not 100 mm."""
    body = dxf_polyline([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    path = write(tmp_path, "inch.dxf", dxf(body, units=1))
    drawing = read_drawing(path, scale=100)
    assert drawing["mm_per_file_unit"] == pytest.approx(2540.0)
    assert drawing["bounds_mm"][2] == pytest.approx(2540.0, abs=1e-6)


def test_a_non_ascii_path_reads_normally(tmp_path):
    """Only CATIA's SaveAs/Open care about ASCII names; reading does not."""
    body = dxf_polyline([(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)])
    path = write(tmp_path, "图纸-轮廓.dxf", dxf(body))
    assert read_drawing(path)["counts"]["contours"] == 1


def test_a_missing_file_is_refused_before_anything_else():
    with pytest.raises(ArgumentRejected) as caught:
        read_drawing("no-such-drawing.dxf")
    assert "No file" in str(caught.value)


def test_a_zero_scale_is_refused():
    with pytest.raises(ArgumentOutOfRange):
        read_drawing("anything.dxf", scale=0)


def test_an_unknown_extension_is_refused_without_guessing(tmp_path):
    path = write(tmp_path, "mystery.bin", b"\x00\x01\x02 not a drawing")
    with pytest.raises(ArgumentRejected) as caught:
        read_drawing(path)
    assert "DXF, SVG" in str(caught.value)


# ── the tool layer ───────────────────────────────────────────────────────
def test_read_drawing_tool_needs_no_catia_and_returns_json(tmp_path):
    """It is the tool a caller runs FIRST, on a machine busy with something
    else, so it must not connect."""
    body = dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0)])
    path = write(tmp_path, "plate.dxf", dxf(body))
    tools = BlueprintTools(connection=None)
    report = json.loads(tools.execute("catia_read_drawing", {"file_path": path}))
    assert report["format"] == "dxf"
    assert report["counts"]["contours"] == 1
    assert report["counts"]["closed"] == 1
    assert report["bounds_mm"] == pytest.approx([0.0, 0.0, 40.0, 20.0])
    assert report["layers"][0]["layer"] == "OUTLINE"


def test_import_refuses_rather_than_truncates_when_the_profile_is_too_big(tmp_path):
    body = "".join(dxf_line(index, 0, index, 10) for index in range(40))
    path = write(tmp_path, "many.dxf", dxf(body))
    tools = BlueprintTools(connection=None)
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_import_drawing",
                      {"file_path": path, "max_elements": 10})
    message = str(caught.value)
    assert "40 sketch elements" in message
    assert "Nothing was drawn" in message


def test_import_refuses_when_every_contour_was_filtered_out(tmp_path):
    body = dxf_polyline([(0, 0, 0), (40, 0, 0), (40, 20, 0), (0, 20, 0)])
    path = write(tmp_path, "only.dxf", dxf(body))
    tools = BlueprintTools(connection=None)
    with pytest.raises(Exception) as caught:
        tools.execute("catia_import_drawing",
                      {"file_path": path, "layers": ["nothing-here"]})
    assert "filtered out" in str(caught.value)


def test_the_import_limit_is_a_number_a_caller_can_reason_about():
    assert MAX_ELEMENTS == 2000
