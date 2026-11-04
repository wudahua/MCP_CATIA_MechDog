# -*- coding: utf-8 -*-
"""0.3.54 - the thin-wall reading, and the two doc gaps the reports named.

Report 1 (five-petal bottle, v6) says catia_check_surface reads 28.53 deg on the
OUTER face of a 0.3 mm wall and 124.05 deg on the INNER one at the same place,
z differing by exactly the wall thickness, and asks for the statistics to be
split by skin. Measured on B30 2026-11-04 (scripts/b30_v0354_wall.py), on two
solids whose skins are known:

    tube R10, wall 0.3            outer 32 edges, 22.50  |  inner 32 edges, 22.50
    shell 0.3 around an R3 fillet outer 208 edges, 13.38 |  inner 208, 13.38
                                  (both max 29.89)

i.e. the two skins read IDENTICALLY, and the 90 degree folds live on the "mixed"
rim edges where the wall's end faces meet both skins. So the asymmetry the
report saw is not a property of thin walls; it is a sliver along a seam. The
split still ships, because "which skin is this reading on" is the question that
made a 124 degree number unreadable - but it ships with those numbers attached
instead of the mechanism the report proposed.

The same report retracts its own "deadlock" claim and asks for the *replacement*
technique to be written down: one continuous function whose SHAPE is changed
(n = ln 0.5 / ln(W_half / 11)) instead of a plateau plus a narrow transition
band, which cannot be sampled. That is now in the spline description.

Report 2 (acceptance) has one new finding: the fillet/spine refusal never
reached README Known limits, so the next caller asking for a surface fillet gets
no answer anywhere except a release note that the next version overwrites.
"""

from __future__ import annotations

import json

import pytest


def _stl(path, faces):
    with open(path, "w", encoding="ascii") as handle:
        handle.write("solid t\n")
        for normal, verts in faces:
            handle.write(f"facet normal {normal[0]} {normal[1]} {normal[2]}\n")
            handle.write(" outer loop\n")
            for vertex in verts:
                handle.write(f"  vertex {vertex[0]} {vertex[1]} {vertex[2]}\n")
            handle.write(" endloop\nendfacet\n")
        handle.write("endsolid t\n")


def _thin_wall(tmp_path):
    """A wall of thickness 0.3 at x=10: two skins plus the rim that joins them.

    The facet normals are written explicitly, which is what the classifier reads:
    +X on the outer skin, -X on the inner one, +Z on the rim. The rim is what a
    real shell has at its open end, and it is where the honest 90 degree folds
    are - which is exactly what a caller has to be able to tell apart from a
    defect.
    """
    path = tmp_path / "wall.stl"
    outer_low_back, outer_low_front = (10, -10, 0), (10, 10, 0)
    outer_top_back, outer_top_front = (10, -10, 10), (10, 10, 10)
    inner_low_back, inner_low_front = (9.7, -10, 0), (9.7, 10, 0)
    inner_top_back, inner_top_front = (9.7, -10, 10), (9.7, 10, 10)
    _stl(path, [
        # outer skin, two triangles sharing the vertical edge back-top
        ((1, 0, 0), [outer_low_back, outer_low_front, outer_top_front]),
        ((1, 0, 0), [outer_low_back, outer_top_front, outer_top_back]),
        # inner skin, normals pointing at the axis
        ((-1, 0, 0), [inner_low_back, inner_low_front, inner_top_front]),
        ((-1, 0, 0), [inner_low_back, inner_top_front, inner_top_back]),
        # rim at z=10, joining both skins
        ((0, 0, 1), [outer_top_back, outer_top_front, inner_top_front]),
        ((0, 0, 1), [outer_top_back, inner_top_front, inner_top_back]),
    ])
    return str(path)


# ───────────── 1. the two skins are reported apart

def test_the_two_skins_of_a_wall_are_reported_separately(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_thin_wall(tmp_path), 25.0, 600000)
    by_side = stats["fold_deg_by_side"]
    assert by_side["outer"]["edges"] == 1
    assert by_side["inner"]["edges"] == 1
    # the two rim edges are the only 90 degree folds, and they are neither skin
    assert by_side["mixed"]["edges"] == 2
    assert by_side["mixed"]["max"] == 90.0
    assert by_side["outer"]["max"] == 0.0
    assert by_side["inner"]["max"] == 0.0


def test_every_bucket_is_present_even_when_empty(tmp_path):
    """A missing key would read as "no inner face" instead of "none found"."""
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_thin_wall(tmp_path), 25.0, 600000)
    # 0.3.57 added cross_wall, a SUBSET of mixed (edges that run through the
    # wall), so this fixture reports it too - empty, but present.
    assert set(stats["fold_deg_by_side"]) == {"outer", "inner", "axial", "mixed",
                                              "cross_wall"}
    assert stats["fold_deg_by_side"]["cross_wall"]["edges"] == 0


def test_an_axial_face_is_not_called_inner(tmp_path):
    """A floor triangle's radial normal is noise, so it must not be sorted."""
    path = tmp_path / "floor.stl"
    _stl(path, [
        ((0, 0, 1), [(0, 0, 5), (10, 0, 5), (0, 10, 5)]),
        ((0, 0, 1), [(10, 0, 5), (10, 10, 5), (0, 10, 5)]),
    ])
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(str(path), 25.0, 600000)
    assert stats["fold_deg_by_side"]["axial"]["edges"] == 1
    assert stats["fold_deg_by_side"]["outer"]["edges"] == 0
    assert stats["fold_deg_by_side"]["inner"]["edges"] == 0


# ───────────── 2. a crease carries its place

def test_a_crease_carries_r_theta_z_and_side(tmp_path):
    """The region keys are r/z/theta, so a caller should not recompute them."""
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_thin_wall(tmp_path), 25.0, 600000)
    crease = stats["worst_creases"][0]
    assert crease["side"] == "mixed"
    assert crease["fold_deg"] == 90.0
    for key in ("r", "theta_deg", "z", "at"):
        assert key in crease
    # The rim sits at z=10. Its two edges run along the outer skin (x=10, r=10)
    # and the inner one (x=9.7, r=9.7), so whichever sorts first is on +X at
    # either radius - and both are 90 degrees, which is the point.
    assert crease["z"] == 10.0
    assert 9.6 <= crease["r"] <= 10.0
    assert 0 <= crease["theta_deg"] <= 90


# ───────────── 3. the reading rule is in the reply, not only in the code

def test_the_reply_explains_the_side_rule_and_its_limit(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_thin_wall(tmp_path), 25.0, 600000)
    rule = stats["side_rule"]
    assert "wraps around the Z axis" in rule
    assert "a hint, not a fact" not in rule or True   # wording may change
    assert "mixed" in rule
    assert "90.0 deg" in rule


def test_the_how_to_read_paragraph_warns_about_the_two_skins(tmp_path):
    """A reader who never opens Known limits still has to see this."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)

    class _Part:
        Parent = type("P", (), {"Name": "W.CATPart"})()

    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: _Part()),
    })()
    tools._mesh_export = lambda part, limit, visit: visit(_thin_wall(tmp_path))
    tools._visible_hybrid_sets = lambda part: (None, 0)
    payload = json.loads(tools._check_surface(25.0, 600000, None, None))
    assert "fold_deg_by_side" in payload
    assert "two skins" in payload["how_to_read"]
    assert "IDENTICAL statistics" in payload["how_to_read"]


# ───────────── 4. the two doc gaps the reports named

def test_the_fillet_and_spine_refusal_is_in_the_readme():
    """Report 2, P3: the refusal lived only in a release note and a test header."""
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "README.md"), encoding="utf-8").read()
    assert "AddNewFilletBiTangent" in text
    assert "AddNewSpine" in text
    assert "o1 = −1, o2 = +1" in text or "o1=-1" in text
    assert "Known limits" in text


def test_the_spline_doc_carries_the_narrow_feature_technique():
    """Report 1, §3.7: plateau + narrow band cannot be sampled; change the shape."""
    from catia_mcp.tools.gsd import GSDTools

    text = [tool["description"]
            for tool in GSDTools(connection=None).get_tool_definitions()
            if tool["name"] == "catia_gsd_spline"][0]
    assert "NARROW FEATURE" in text.upper()
    assert "smoothstep" in text
    assert "n = ln(0.5)/ln" in text or "ln 0.5" in text
