# -*- coding: utf-8 -*-
"""0.3.83: the four items the 0.3.82 acceptance report raised.

The first one is the report's P1 and it is this package's own doing: 0.3.82's
``catia_sketch_copy`` description promised "or the name of a datum plane" and
the resolver answered "Unknown plane" for every one of them, so the two tools
added that same day could not be chained. The rest are a wrong profile summary
for a closed spline, a join refusal that asserted a cause it had not measured,
and a failure that left visible copies without saying so.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from catia_mcp.results import ArgumentRejected, PreconditionRejected


# ── a sketch support given as a datum plane's name ─────────────────────────

def _typed(name: str, type_name: str):
    """A tree node whose type information reports ``type_name``."""
    info = SimpleNamespace(GetDocumentation=lambda index: (type_name,))
    return SimpleNamespace(Name=name,
                           _oleobj_=SimpleNamespace(GetTypeInfo=lambda: info))


def _sketcher(part_find=None):
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    part = SimpleNamespace(
        FindObjectByName=lambda name: (part_find or {}).get(name),
        CreateReferenceFromObject=lambda obj: ("ref", obj.Name),
        HybridBodies=SimpleNamespace(Count=0),
    )
    return tools, part, SimpleNamespace()


def test_a_datum_plane_name_is_accepted_as_a_sketch_support():
    plane = _typed("Plane_NormValley", "Plane")
    tools, part, body = _sketcher({"Plane_NormValley": plane})

    reference, description = tools._resolve_support(part, body, "Plane_NormValley", None)

    assert reference == ("ref", "Plane_NormValley")
    assert "datum plane" in description


def test_a_name_that_is_not_a_plane_is_refused_with_its_type():
    """A surface is a tree element too - saying "unknown plane" for it would send
    the caller looking for a spelling mistake."""
    surface = _typed("BaseSurf360", "Surface")
    tools, part, body = _sketcher({"BaseSurf360": surface})

    with pytest.raises(ArgumentRejected) as caught:
        tools._resolve_support(part, body, "BaseSurf360", None)

    assert caught.value.code == "SUPPORT_NOT_PLANAR"
    assert "not a plane" in str(caught.value)


def test_an_unknown_name_lists_the_datum_planes_that_do_exist(monkeypatch):
    tools, part, body = _sketcher({})
    monkeypatch.setattr(tools, "_datum_plane_names",
                        lambda p: ["Plane_Norm", "Plane_Off20"])

    with pytest.raises(ArgumentRejected) as caught:
        tools._resolve_support(part, body, "Plane_Typo", None)

    assert caught.value.code == "PLANE_NOT_FOUND"
    assert "Plane_Norm" in str(caught.value) and "Plane_Off20" in str(caught.value)


def test_datum_plane_names_are_found_inside_a_geometrical_set():
    """A plane is a HybridShape INSIDE the set, not a child set: walking
    HybridBodies alone answered "Datum planes here: none" for a part holding two."""
    from catia_mcp.tools.sketcher import SketcherTools

    shapes = SimpleNamespace(
        Count=2,
        Item=lambda index: (_typed("Plane_Norm", "Plane"),
                            _typed("Valley", "Spline"))[index - 1],
    )
    geoset = SimpleNamespace(Name="Geometrical Set.1", HybridShapes=shapes,
                             HybridBodies=SimpleNamespace(Count=0))
    part = SimpleNamespace(HybridBodies=SimpleNamespace(
        Count=1, Item=lambda index: geoset))

    assert SketcherTools(connection=None)._datum_plane_names(part) == ["Plane_Norm"]


# ── the profile summary of a closed spline ─────────────────────────────────

def test_a_closed_spline_reports_all_four_sides_and_its_real_extent():
    """Reported 2026-11-09: a 6 x 6 square came back as bbox (10,10)..(10,10)
    and outline 18.000 mm. The bbox came from the endpoint PAIRING (a closed
    spline repeats its first point) and the length from the points that were
    passed in, which is three sides of four."""
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(sketch_outlines={})
    key = tools._sketch_key("草图.4")
    tools._drawn = {key: [{"kind": "spline", "points": 4, "chord": 24.0,
                          "pts": [[10, 10], [16, 10], [16, 16], [10, 16]],
                          "closed": True}]}
    tools._profile_ends = [(10.0, 10.0), (10.0, 10.0)]
    tools._profile_closed_curves = 1

    summary = tools._profile_summary("草图.4")

    assert "bbox (10.0000, 10.0000) .. (16.0000, 16.0000)" in summary
    assert "outline 24.000 mm" in summary
    # And the number is labelled for what it is.
    assert "LOWER BOUND" in summary


def test_a_rectangle_still_reports_its_four_corners():
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    tools.conn = SimpleNamespace(sketch_outlines={})
    key = tools._sketch_key("草图.1")
    tools._drawn = {key: [{"kind": "rectangle", "x1": 0, "y1": 0, "x2": 20, "y2": 12}]}
    tools._profile_ends = [(0.0, 0.0), (0.0, 12.0), (20.0, 12.0), (20.0, 0.0)]

    summary = tools._profile_summary("草图.1")

    assert "bbox (0.0000, 0.0000) .. (20.0000, 12.0000)" in summary
    assert "outline 64.000 mm" in summary
    assert "LOWER BOUND" not in summary


def test_an_arc_that_sweeps_past_a_quarter_includes_that_extreme():
    """Endpoints alone under-report: a 90-degree arc from 0 to 90 reaches the
    top of its circle in y, which neither endpoint is."""
    from catia_mcp.tools.sketcher import SketcherTools

    points = SketcherTools._element_points(
        {"kind": "arc", "cx": 0, "cy": 0, "radius": 10,
         "start_rad": 0.0, "sweep_rad": 3.141592653589793 / 2})

    assert (10.0, 0.0) in points and (0.0, 10.0) in points


# ── the join refusal says what was measured ────────────────────────────────

def _gsd_with_gap(gap: float | None):
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)

    class Measurable:
        def __init__(self, distance):
            self._distance = distance

        def GetMinimumDistance(self, other):
            return self._distance

    class Spa:
        def GetMeasurable(self, ref):
            return Measurable(gap if gap is not None else 0.0)

    if gap is None:
        def explode(ref):
            raise RuntimeError("no measurement")
        spa = SimpleNamespace(GetMeasurable=explode)
    else:
        spa = Spa()

    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(GetWorkbench=lambda name: spa))
    tools._ref = lambda name: name
    return tools


def test_a_touching_pair_is_reported_as_touching_not_as_a_shared_axis():
    tools = _gsd_with_gap(0.0)

    said = tools._join_diagnosis(None, "SECTOR", "SECTOR_r120")

    assert "touch (0.0000 mm apart)" in said
    assert "SAME edge" in said
    # The old message's invented cause must not come back.
    assert "meeting the same axis" not in said


def test_a_pair_that_does_not_touch_reports_the_distance():
    tools = _gsd_with_gap(80.0)

    said = tools._join_diagnosis(None, "PATCH", "PATCH_r180")

    assert "80.0000 mm apart" in said
    assert "do not touch" in said


def test_an_unmeasurable_pair_claims_no_cause():
    tools = _gsd_with_gap(None)

    said = tools._join_diagnosis(None, "A", "B")

    assert "does not claim a cause" in said


def test_the_refusal_names_the_copies_and_the_one_call_that_removes_them(
        monkeypatch):
    """P3: 20 leftover rotations were left visible, and visible geometry enters
    the STL that bounding-box and check-surface measure."""
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 get_active_part=lambda: SimpleNamespace(
                                     UpdateObject=lambda target: None))
    monkeypatch.setattr(tools, "_ref", lambda name: name)
    monkeypatch.setattr(tools, "_axis_ref", lambda spec, owner: ("axis", ""))
    monkeypatch.setattr(tools, "_free_name", lambda part, wanted: (wanted, True))
    monkeypatch.setattr(tools, "_get_geoset",
                        lambda: SimpleNamespace(AppendHybridShape=lambda shape: None))
    monkeypatch.setattr(tools, "_join_diagnosis", lambda part, a, b: "")

    class Factory:
        def AddNewRotate(self, ref, axis, angle):
            return SimpleNamespace(Name="")

        def AddNewJoin(self, first, second):
            return SimpleNamespace(Name="", AddElement=lambda ref: None)

    monkeypatch.setattr(tools, "_factory", lambda: Factory())
    monkeypatch.setattr(tools, "_finish",
                        lambda shape, name, message: (_ for _ in ()).throw(
                            RuntimeError("update failed")))

    with pytest.raises(PreconditionRejected) as caught:
        tools._circ_pattern({"element": "PATCH", "count": 2, "axis": "z"})

    assert caught.value.code == "PATTERN_JOIN_FAILED"
    text = str(caught.value)
    assert "VISIBLE" in text
    assert "catia_delete_feature(names=['PATCH_r180'])" in text
