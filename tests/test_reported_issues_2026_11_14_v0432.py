"""0.3.130 - the 2026-11-14 (v0.3.128) report's blueprint-side items.

N3 (page), N4 (clip), N5 (ascii_name / collision), N6 (Bezier -> arc),
N7 (size histogram), N8 (region frame), N9 (max_aspect), N10 (drives).
Each test names the number the report used, so the code and the report cannot
drift apart.
"""

from __future__ import annotations

import math
import os

import pytest

from catia_mcp import blueprint
from catia_mcp.results import ArgumentRejected
from catia_mcp.tools.blueprint import BlueprintTools, _aspect as aspect_of
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.sketcher import SketcherTools


# ── N7: the size histogram ───────────────────────────────────────────────────


def test_the_histogram_counts_every_contour_exactly_once():
    """4196 contours and a `min_size` found by bisection is what this replaces."""
    diagonals = [0.04, 0.3, 0.5, 0.99, 1.0, 2.5, 3.0, 7.0, 15.0, 60.0]
    table = blueprint.size_histogram(diagonals)
    assert sum(row["contours"] for row in table) == len(diagonals)
    labels = {row["mm"]: row["contours"] for row in table}
    assert labels["<0.5"] == 2           # 0.04 and 0.3; 0.5 is NOT below 0.5
    assert labels["0.5-1"] == 2          # 0.5, 0.99 - half-open bands
    assert labels["2-3"] == 1            # 2.5
    assert labels["3-5"] == 1            # 3.0
    assert labels["5-10"] == 1           # 7.0
    assert labels[">=50"] == 1           # 60.0


def test_the_histogram_of_nothing_is_all_zero_and_still_has_the_bands():
    table = blueprint.size_histogram([])
    assert len(table) == 9
    assert all(row["contours"] == 0 for row in table)


# ── N9: how thin is too thin ─────────────────────────────────────────────────


def test_aspect_is_the_long_side_over_the_short_one():
    # Measured on the report's sheet: the frame's bottom edge, 346 x 0.1693 mm.
    assert aspect_of((0, 0, 346.0045, 0.1693)) == pytest.approx(2043.74, abs=0.01)
    assert aspect_of((0, 0, 10, 10)) == 1.0
    # A zero-extent box has no aspect at all - None, not infinity.
    assert aspect_of((5, 5, 5, 5)) is None


# ── N6: a circle drawn as four Beziers comes back as arcs ────────────────────

K = 0.5522847498


def _quarter(radius: float = 10.0):
    """One quarter of a circle centred at the origin, as PDF software writes it."""
    p0 = (radius, 0.0)
    c1 = (radius, radius * K)
    c2 = (radius * K, radius)
    end = (0.0, radius)
    return p0, c1, c2, end


def test_a_quarter_circle_bezier_is_fitted_back_to_a_real_arc():
    p0, c1, c2, end = _quarter()
    piece = blueprint._cubic_as_arc(p0, c1, c2, end, 0.02)
    assert piece is not None, "four of these are exactly how a circle is stored"
    assert piece["t"] == "arc"
    assert piece["r"] == pytest.approx(10.0, abs=1e-3)
    assert piece["c"][0] == pytest.approx(0.0, abs=1e-3)
    assert piece["ccw"] is True


def test_a_bezier_that_is_not_circular_is_left_to_the_sampler():
    """A 90-degree turn was never an arc, and pretending it is moves the wall."""
    piece = blueprint._cubic_as_arc((0, 0), (10, 0), (10, 0), (0, 10),
                                             0.02)
    assert piece is None


def test_the_sampler_densifies_as_the_tolerance_tightens():
    p0, c1, c2, end = _quarter()
    coarse = blueprint._cubic_steps(p0, c1, c2, end, 0.5)
    fine = blueprint._cubic_steps(p0, c1, c2, end, 0.01)
    assert 4 <= coarse < fine <= 128


# ── N8: `region` is compared in the DRAWING's frame ──────────────────────────


SVG_TWO_BOXES = b"""<svg xmlns="http://www.w3.org/2000/svg" width="100mm"
 height="50mm" viewBox="0 0 100 50">
<rect x="10" y="10" width="10" height="10"/>
<rect x="60" y="20" width="10" height="10"/>
</svg>"""


def _svg(tmp_path, name="two.svg"):
    path = tmp_path / name
    path.write_bytes(SVG_TWO_BOXES)
    return str(path)


def test_the_reply_reports_both_frames_and_they_differ_after_the_shift(tmp_path):
    drawing = blueprint.read_drawing(_svg(tmp_path), origin="bbox")
    # Measured, not assumed: SVG is y-DOWN, so the viewBox's y=10 box arrives at
    # y=-20..-10 in the drawing frame - and that frame is what `region` compares
    # against. The x minimum is the plain one.
    assert drawing["drawing_bounds_mm"] == pytest.approx([10.0, -30.0, 70.0, -10.0],
                                                         abs=1e-6)
    assert drawing["bounds_mm"][0] == pytest.approx(0.0, abs=1e-6)
    assert drawing["origin_shift_mm"] == pytest.approx([-10.0, 30.0], abs=1e-6)


def test_region_selects_in_the_frame_the_reply_reports_as_drawing_bounds(tmp_path):
    """The workflow the tool itself recommends: read, then set region from it."""
    everything = blueprint.read_drawing(_svg(tmp_path), origin="bbox")
    x0, y0, x1, y1 = everything["drawing_bounds_mm"]
    # The second box, named in the frame `drawing_bounds_mm` reports - y=-30..-20
    # there, because SVG's y axis points down.
    drawing = blueprint.read_drawing(_svg(tmp_path), origin="bbox",
                                     region=[58, -32, 72, -18])
    assert drawing["counts"]["contours"] == 1
    assert drawing["counts"]["dropped"]["region"] == 1
    # And what came out is that box, moved to the origin by origin=bbox.
    box = blueprint.contour_bbox(drawing["contours"][0])
    assert box[2] - box[0] == pytest.approx(10.0, abs=1e-6)
    assert x1 > x0 and y1 > y0


def test_two_calls_with_the_same_filters_agree_on_the_count(tmp_path):
    """read_drawing and import_drawing share one parse, so they cannot disagree."""
    first = blueprint.read_drawing(_svg(tmp_path), region=[58, -32, 72, -18])
    second = blueprint.read_drawing(_svg(tmp_path), region=[58, -32, 72, -18])
    assert first["counts"] == second["counts"]


# ── N9: max_aspect removes a drawing frame's edge line ───────────────────────


THIN_FRAME_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="400mm"
 height="300mm" viewBox="0 0 400 300">
<rect x="20" y="20" width="346" height="0.1693"/>
<rect x="150" y="120" width="40" height="40"/>
</svg>"""


def test_max_aspect_drops_the_frames_edge_line_and_keeps_the_part(tmp_path):
    path = tmp_path / "frame.svg"
    path.write_bytes(THIN_FRAME_SVG)
    loose = blueprint.read_drawing(str(path))
    assert loose["counts"]["contours"] == 2
    tight = blueprint.read_drawing(str(path), max_aspect=50)
    assert tight["counts"]["contours"] == 1
    assert tight["counts"]["dropped"]["thin"] == 1
    box = blueprint.contour_bbox(tight["contours"][0])
    assert box[2] - box[0] == pytest.approx(40.0, abs=1e-6)


def test_a_max_aspect_below_one_is_refused_before_the_file_is_read(tmp_path):
    with pytest.raises(Exception) as error:
        blueprint.read_drawing(str(tmp_path / "missing.svg"), max_aspect=0.5)
    assert "ASPECT_OUT_OF_RANGE" in str(getattr(error.value, "code", error.value))


# ── N3: page selection ───────────────────────────────────────────────────────


def test_a_page_number_outside_the_file_is_refused_with_the_real_count():
    """The reply has to say how many pages exist, because 'page 3' once did."""
    data = (b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>\n"
            b"endobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
    with pytest.raises(Exception) as error:
        blueprint.parse_pdf(data, page=3)
    assert "PAGE_OUT_OF_RANGE" in str(getattr(error.value, "code", error.value))
    assert "1 page" in str(error.value)


# ── N4: the clip, including geometry that lies ON its edge ───────────────────


CLIP = [0.0, 0.0, 56.69, 56.69]


def test_a_segment_lying_on_the_clip_edge_is_kept_not_dropped():
    """Liang-Barsky's p and q were swapped: q == 0 read as "parallel".

    Found by the 0.3.130 audit's own clip case - a square whose edge sits on the
    page box lost both boundary edges, so the clip left nothing to import and the
    reply blamed the drawing ("clipped out") instead of itself.
    """
    trimmed = blueprint._clip_segment((0.0, 0.0), (340.0, 0.0), CLIP)
    assert trimmed is not None
    assert trimmed[0] == pytest.approx((0.0, 0.0))
    assert trimmed[1][0] == pytest.approx(56.69)


def test_a_segment_crossing_the_clip_is_cut_to_it():
    start, end = blueprint._clip_segment((-50.0, 10.0), (100.0, 10.0), CLIP)
    assert start == pytest.approx((0.0, 10.0))
    assert end == pytest.approx((56.69, 10.0))


def test_a_segment_beside_the_clip_is_dropped():
    assert blueprint._clip_segment((-50.0, 10.0), (-10.0, 10.0), CLIP) is None
    # Parallel to an edge and outside it: no part of it is visible.
    assert blueprint._clip_segment((-50.0, 10.0), (-10.0, 70.0), CLIP) is None


# ── N5: ascii_name, and the collision it exists for ──────────────────────────


def test_ascii_name_replaces_the_derived_stem():
    path, title = DocumentTools._ascii_name_for(
        r"C:\parts\苹果AirPods一二代.CATPart", "AirPods_Gen2")
    assert os.path.basename(path) == "AirPods_Gen2.CATPart"
    assert title == "苹果AirPods一二代", "the wording still goes to the part number"


def test_the_derived_stem_is_unchanged_when_no_name_is_given():
    path, _ = DocumentTools._ascii_name_for(r"C:\parts\苹果AirPods一二代.CATPart")
    assert os.path.basename(path) == "AirPods.CATPart"


def test_a_non_ascii_ascii_name_is_refused_before_anything_is_created():
    with pytest.raises(ArgumentRejected) as error:
        DocumentTools._check_ascii_name("苹果")
    assert "ASCII" in str(error.value)
    DocumentTools._check_ascii_name("AirPods_2")       # fine
    DocumentTools._check_ascii_name(None)              # absent is fine


class _Doc:
    def __init__(self, full_name: str, part_number: str) -> None:
        self.Name = os.path.basename(full_name)
        self.FullName = full_name
        self.Product = type("P", (), {"PartNumber": part_number})()


class _Docs:
    def __init__(self, docs: list[_Doc]) -> None:
        self.items = docs

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Doc:
        return self.items[index - 1]


class _Conn:
    def __init__(self, docs: list[_Doc]) -> None:
        self.documents = _Docs(docs)


def _tools_with(docs: list[_Doc]) -> DocumentTools:
    tools = DocumentTools.__new__(DocumentTools)
    tools.conn = _Conn(docs)
    return tools


def test_a_different_part_at_the_plain_name_earns_a_suffix():
    """'苹果AirPods一二代' and '苹果AirPods参数化版' both project to AirPods."""
    tools = _tools_with([_Doc(r"C:\parts\AirPods.CATPart", "苹果AirPods参数化版")])
    plain = r"C:\parts\AirPods.CATPart"
    changed = tools._disambiguate_ascii(plain, "苹果AirPods一二代")
    assert changed is not None
    assert os.path.basename(changed).startswith("AirPods_")
    assert changed != plain


def test_the_suffix_is_derived_from_the_name_not_from_a_counter():
    """A retry after a restart has to land on the same file."""
    tools = _tools_with([_Doc(r"C:\parts\AirPods.CATPart", "别的零件")])
    first = tools._disambiguate_ascii(r"C:\parts\AirPods.CATPart", "苹果AirPods一二代")
    second = tools._disambiguate_ascii(r"C:\parts\AirPods.CATPart", "苹果AirPods一二代")
    assert first == second
    other = tools._disambiguate_ascii(r"C:\parts\AirPods.CATPart", "苹果AirPods二代")
    assert other != first, "two different names must not share one file"


def test_the_same_part_reopened_is_not_disambiguated():
    """A retry of the SAME call must get the ordinary 'already open' answer."""
    tools = _tools_with([_Doc(r"C:\parts\AirPods.CATPart", "苹果AirPods一二代")])
    assert tools._disambiguate_ascii(r"C:\parts\AirPods.CATPart",
                                     "苹果AirPods一二代") is None


def test_nothing_open_at_the_path_is_not_disambiguated():
    """Whether a closed file is this part cannot be read, so it is not guessed."""
    tools = _tools_with([])
    assert tools._disambiguate_ascii(r"C:\parts\AirPods.CATPart", "苹果") is None


# ── N10: which semi-axis does this constraint drive? ─────────────────────────


def test_a_written_name_says_which_semi_axis_it_drives():
    assert SketcherTools._drive_from_name("MajorRadius.1") == "major_radius"
    assert SketcherTools._drive_from_name("MinorRadius.3") == "minor_radius"
    assert SketcherTools._drive_from_name("Radius.2") == "radius"
    # CATIA's own localised name says nothing, and None is the honest answer.
    assert SketcherTools._drive_from_name("半径.5") is None


def test_the_three_drive_names_are_distinct_and_ascii():
    written = [value[0] for value in SketcherTools._DRIVE_NAMES.values()]
    keys = [value[1] for value in SketcherTools._DRIVE_NAMES.values()]
    assert len(set(written)) == 3 and len(set(keys)) == 3
    assert all(name.isascii() for name in written)


# ── the BlueprintTools reply carries the new numbers ─────────────────────────


def _blueprint(tmp_path):
    tools = BlueprintTools.__new__(BlueprintTools)
    path = tmp_path / "two.svg"
    path.write_bytes(SVG_TWO_BOXES)
    return tools, str(path)


def test_the_read_reply_carries_both_frames_and_the_histogram(tmp_path):
    import json

    tools, path = _blueprint(tmp_path)
    report = json.loads(tools._read_drawing({"file_path": path}))
    assert report["drawing_bounds_mm"][0] == pytest.approx(10.0, abs=1e-4)
    assert report["bounds_mm"][0] == pytest.approx(0.0, abs=1e-4)
    assert sum(row["contours"] for row in report["size_histogram_mm"]) == 2
    assert all("aspect" in contour for contour in report["contours"])
