# -*- coding: utf-8 -*-
"""0.3.59 - a duplicated sheet is not a 0.034 mm radius.

Reported 2026-11-04 against 0.3.58: a failed ThickSurface reported its tightest
radius as 0.034 mm at r=32.99, z=123.04 - the open top border of a skirt surface
that the split was about to cut away, where there was no feature to widen. The
report's own reading was "that is the surface ending, not curvature", and it was
right about the conclusion while the mechanism needed measuring:

* a plain open patch does NOT read 180 deg at its border (measured on B30: a
  30 mm open tube's worst fold is 22.50 deg, its chording, and a flat patch's is
  0.00) - so "the surface ends here" is not what produces a steep dihedral;
* two COINCIDENT patches with opposite orientation do: every edge they share
  measures exactly 180.00 deg, because the two triangles face opposite ways.
  That is a duplicated or double-sided sheet, which is what a split-and-copied
  surface leaves behind, and it is where "radius = facet size / pi" comes from.

So 0.3.59 judges fold-back (>= 175 deg) before anything else, keeps those edges
out of the radius ranking and out of the thickness verdict, counts them, and says
so - in `excluded.fold_back_edges` and in the failure message itself, so the
number cannot silently vanish either.
"""

from __future__ import annotations

import math


# ───────────── fixtures whose answer is known by construction

def _write(path, faces, name="fixture") -> str:
    lines = ["solid " + name]
    for verts in faces:
        ab = [verts[1][i] - verts[0][i] for i in range(3)]
        ac = [verts[2][i] - verts[0][i] for i in range(3)]
        cross = [ab[1] * ac[2] - ab[2] * ac[1],
                 ab[2] * ac[0] - ab[0] * ac[2],
                 ab[0] * ac[1] - ab[1] * ac[0]]
        length = math.sqrt(sum(c * c for c in cross)) or 1.0
        lines.append("facet normal %.6f %.6f %.6f"
                     % tuple(c / length for c in cross))
        lines.append("  outer loop")
        for vertex in verts:
            lines.append("    vertex %.6f %.6f %.6f" % vertex)
        lines.append("  endloop")
        lines.append("endfacet")
    lines.append("endsolid " + name)
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return str(path)


def _cylinder_faces(radius=10.0, segments=36, height=30.0, bands=1, dx=0.0):
    """An open tube. ``bands`` splits the height so edges exist at several z.

    One band is enough to measure a radius, but its only interior edges are the
    vertical ones, all of them at z = height/2 - which makes a region filter look
    like a no-op. Two bands put interior edges at both z = 7.5 and z = 22.5.
    """
    faces = []
    levels = [height * step / bands for step in range(bands + 1)]
    for band in range(bands):
        low, high = levels[band], levels[band + 1]
        for index in range(segments):
            t0 = 2 * math.pi * index / segments
            t1 = 2 * math.pi * (index + 1) / segments
            p = lambda r, t, z: (r * math.cos(t) + dx,  # noqa: E731
                                 r * math.sin(t), z)
            a0, a1 = p(radius, t0, low), p(radius, t1, low)
            b0, b1 = p(radius, t0, high), p(radius, t1, high)
            faces.append([a0, a1, b1])
            faces.append([a0, b1, b0])
    return faces


def _double_sheet(dx=0.0):
    """Two coincident quad sheets, opposite winding, opposite diagonals.

    This is the shape two overlapping patches export as: the same four corners,
    one split along AC, the other along BD and wound the other way. Every border
    edge is then shared by one triangle of each sheet, and the two normals are
    opposite - 180.00 deg, which is not a bend.
    """
    a = (dx + 0.0, 0.0, 0.0)
    b = (dx + 40.0, 0.0, 0.0)
    c = (dx + 40.0, 0.0, 60.0)
    d = (dx + 0.0, 0.0, 60.0)
    return [[a, b, c], [a, c, d], [a, d, b], [b, d, c]]


# ───────────── 1. fold-back is excluded, and counted

def test_two_coincident_sheets_are_fold_back_and_yield_no_radius(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    stats = _curvature_stats(_write(tmp_path / "pair.stl", _double_sheet()), 0.3, 3)
    assert stats["excluded"]["fold_back_edges"] == 4
    assert stats["edges_measured"] == 0, (
        "the only edges left are the two flat diagonals, which carry no bend")
    assert stats["min_radius_mm"] is None, (
        "0.3.58 published a number here; there is no radius to publish")
    assert "folds back" in stats["note"]
    sample = stats["excluded"]["fold_back_sample"][0]
    assert sample["fold_deg"] == 180.0
    assert sample["at_open_border"] is False, (
        "these edges are interior - both sheets cover them - so the border test "
        "alone would NOT have caught the report's case")
    verdict = stats["thickness_check"]["verdict"]
    assert "cannot explain" in verdict
    assert "0.034" not in verdict and "Widen" not in verdict


def test_a_duplicated_sheet_does_not_change_the_radius_of_the_real_geometry(tmp_path):
    """The point of the fix: real curvature survives, the duplicate is ignored."""
    from catia_mcp.tools.measurement import _curvature_stats

    alone = _curvature_stats(
        _write(tmp_path / "tube.stl", _cylinder_faces()), 3.0, 3)
    both = _curvature_stats(
        _write(tmp_path / "tube_pair.stl",
               _cylinder_faces() + _double_sheet(dx=100.0)), 3.0, 3)

    assert both["min_radius_mm"] == alone["min_radius_mm"]
    assert 9.0 < both["min_radius_mm"] < 11.0, (
        "the r=10 tube is what the radius has to describe")
    assert both["excluded"]["fold_back_edges"] == 4
    assert both["thickness_check"]["edges_tighter_than_thickness"] == 0
    verdict = both["thickness_check"]["verdict"]
    assert "no resolved edge bends tighter" in verdict
    assert "fold-back" in verdict, (
        "the excluded set has to be named, or the caller cannot tell whether the "
        "number they saw before was dropped or fixed")


def test_a_sheet_shared_by_more_than_two_triangles_is_also_not_curvature(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    # Same sheet twice with the same winding: every edge now carries four
    # triangles. 0.3.58 skipped those silently; the count is reported now.
    faces = _cylinder_faces(segments=12)
    stats = _curvature_stats(
        _write(tmp_path / "twice.stl", faces + [list(reversed(f)) for f in faces]),
        0.3, 3)
    assert stats["excluded"]["ambiguous_edges"] > 0
    assert "more than two triangles" in stats["excluded"]["ambiguous_note"]


# ───────────── 2. region now scopes the curvature block too

def test_region_scopes_the_curvature_block_not_only_the_fold_block(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    path = _write(tmp_path / "tube.stl", _cylinder_faces(height=30.0, bands=2))
    whole = _curvature_stats(path, 0.3, 3)
    lower = _curvature_stats(path, 0.3, 3, {"z": (0.0, 10.0)})

    assert lower["edges_measured"] < whole["edges_measured"], (
        "half a tube has half the interior edges")
    assert lower["excluded"]["outside_region"] > 0
    assert lower["region"] == {"z": [0.0, 10.0]}
    assert "inside the region" in lower["region_note"]
    assert all(edge["z"] <= 10.0 for edge in lower["tightest_edges"])
    assert lower["min_radius_mm"] is not None and lower["min_radius_mm"] < 11.0, (
        "the r=10 reading survives the region; only its scope shrinks")


# ───────────── 3. what the failure message says

def _blocker(monkeypatch, stl_path, total):
    from catia_mcp.tools import measurement as measurement_module
    from catia_mcp.tools.gsd import GSDTools

    def fake_export(self, part, limit=None, visit=None):
        visit(stl_path)
        return [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], None, 0, None

    monkeypatch.setattr(measurement_module.MeasurementTools, "_mesh_export",
                        fake_export)
    return GSDTools(connection=None)._offset_blocker(object(), total)


def test_the_failure_message_does_not_offer_to_widen_a_duplicated_sheet(
        monkeypatch, tmp_path):
    path = _write(tmp_path / "pair.stl", _double_sheet())
    message = _blocker(monkeypatch, path, 0.3)
    assert "MEASURED" in message
    assert "fold back" in message and "EXCLUDED" in message
    assert "Widen" not in message, (
        "there is no feature to widen; sending the caller to look for one is "
        "what cost the report two rebuilds")


def test_the_failure_message_still_names_a_real_radius(monkeypatch, tmp_path):
    path = _write(tmp_path / "tube.stl", _cylinder_faces())
    tight = _blocker(monkeypatch, path, 20.0)
    assert "Widen" in tight and "less than the 20.000 mm" in tight
    assert "OPEN BORDER" in tight, (
        "this tube's edges end on a border, and the message has to say so: it is "
        "the one thing that distinguishes a real bend there from the mesh ending")

    loose = _blocker(monkeypatch, path, 0.3)
    assert "does not explain" in loose
    assert "Widen" not in loose


# ───────────── 4. the docstring that 0.3.58 left describing the old formula

def test_the_estimator_documents_the_formula_it_uses():
    from catia_mcp.tools.measurement import _curvature_stats

    doc = _curvature_stats.__doc__ or ""
    assert "kappa" not in doc, (
        "0.3.58's docstring still said kappa = angle / edge length, which is the "
        "estimator that read 57.3 mm on a r=10 cylinder - a reader would have "
        "taken the implementation for a bug")
    assert "CIRCUMCENTRES" in doc
    stats = _curvature_stats.__globals__  # noqa: F841 - import stays honest
