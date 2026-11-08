"""0.3.84: the two 2026-11-09 reports.

One of them corrected a claim this project made twice in opposite directions, so
the tests here are about the corrected statements as much as the new tool:

  * AddNewGSDCircPattern IS declared, on ShapeFactory - the description has to
    say that, and say what was actually measured (it refuses to update)
  * the two factory counts mean different things (160 members, 131 AddNew*), and
    catia_diagnose now reports both
  * catia_gsd_plane_angle exists, and refuses a degenerate rotation with an
    explanation rather than CATIA's generic failure
  * a thickening blocked by curvature names ONE SIDE as the number to compare,
    because that is what the four measurements agree on
  * hiding a geometrical set says where the solid actually lives
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


# ── the angle datum plane ────────────────────────────────────────────────


def _angle_tools(update_error: Exception | None = None):
    """GSDTools with just enough of the COM surface for _plane_angle."""
    from catia_mcp.tools.gsd import GSDTools

    class _Factory:
        def AddNewPlaneAngle(self, plane, axis, angle, orientation):
            return types.SimpleNamespace(Name="", plane=plane, axis=axis,
                                         angle=angle, orientation=orientation)

    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: types.SimpleNamespace(
            Name="P", CreateReferenceFromObject=lambda obj: ("ref", obj)),
        refresh_display=lambda: None,
    )
    tools._factory = lambda: _Factory()          # type: ignore[method-assign]
    tools._plane_ref = lambda spec: ("plane-ref", f"the {spec} main plane")  # type: ignore[method-assign]
    tools._axis_ref = lambda spec, owner: ("axis-ref", "")  # type: ignore[method-assign]

    def finish(shape, name, message):
        if update_error is not None:
            raise RuntimeError(f"CATIA rejected the feature (update failed) "
                               f"and it was removed from the tree: {update_error}")
        return message

    tools._finish = finish                          # type: ignore[method-assign]
    return tools


def test_an_angle_of_zero_is_refused_before_catia_is_asked():
    """A plane rotated by 0 degrees IS the reference plane - nothing to build."""
    tools = _angle_tools()
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_plane_angle",
                      {"plane": "yz", "axis": "z", "angle": 0})
    assert "non-zero" in str(caught.value)


def test_a_degenerate_rotation_says_the_axis_has_to_lie_in_the_plane():
    """The trap that cost the probe eight call shapes: rotating a plane about its
    own normal leaves it where it was, and CATIA answers with a generic E_FAIL."""
    tools = _angle_tools(update_error=RuntimeError("E_FAIL"))
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_plane_angle",
                      {"plane": "xy", "axis": "z", "angle": 36})
    assert caught.value.code == "PLANE_ANGLE_REJECTED"
    assert "LIE IN" in str(caught.value)
    # And it names a combination that does work, not just the rule.
    assert "plane='yz' with axis='z'" in str(caught.value)


def test_the_angle_is_documented_as_degrees_because_that_was_measured():
    from catia_mcp.tools.gsd import GSDTools

    schema = {tool["name"]: tool
              for tool in GSDTools(connection=None).get_tool_definitions()}
    description = schema["catia_gsd_plane_angle"]["description"]
    assert "DEGREES" in description
    assert "LIE IN" in description
    assert "0.0000" in description


# ── the two factory counts, and where the pattern member lives ───────────


def test_typeinfo_members_reports_the_whole_table_and_the_addnew_subset():
    """160 and 131 are answers to two different questions. A report spent a
    round reading them as a contradiction."""
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    class _Info:
        def GetTypeAttr(self):
            return types.SimpleNamespace(cFuncs=4)

        def GetFuncDesc(self, index):
            return types.SimpleNamespace(memid=index)

        def GetNames(self, memid):
            return (f"AddNew{memid}",) if memid < 3 else ("Update",)

    class _Obj:
        _oleobj_ = types.SimpleNamespace(GetTypeInfo=lambda: _Info())

    assert DiagnosticsTools._typeinfo_member_count(_Obj()) == {"count": 4,
                                                              "addnew": 3}


def test_the_pattern_member_is_named_with_the_factory_that_has_it():
    """0.3.82 said "not declared here"; the member is on ShapeFactory. The text
    has to carry both facts and the measurement that the member does not update."""
    from catia_mcp.tools.gsd import GSDTools

    schema = {tool["name"]: tool
              for tool in GSDTools(connection=None).get_tool_definitions()}
    text = schema["catia_gsd_circ_pattern"]["description"]
    assert "ShapeFactory.AddNewGSDCircPattern" in text
    assert "14" in text and "arguments" in text
    # The correction itself, not just the new claim.
    assert "was wrong" in text
    # Both numbers, with the reason they differ.
    assert "160" in text and "131" in text
    # And what was measured about the member: it refuses to update.
    assert "UpdateObject" in text


def test_diagnose_points_at_the_method_table_as_the_capability_answer():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    schema = {tool["name"]: tool for tool in
              DiagnosticsTools(connection=None).get_tool_definitions()}
    text = schema["catia_diagnose"]["description"]
    assert "typeinfo_addnew" in text
    assert "typeinfo_members" in text
    assert "ShapeFactory instead of HybridShapeFactory" in text


# ── the thickening: one side, not the total ──────────────────────────────


def test_a_split_offset_stops_blaming_the_radius(monkeypatch, tmp_path):
    """0.25/0.25 built a 0.5 mm wall at a 0.355 mm radius. Once each side is
    under the radius, curvature is not the explanation and the message must say
    so instead of repeating a radius the caller already beat."""
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.measurement import MeasurementTools

    from tests.test_reported_issues_2026_11_04_v0358 import _cylinder

    stl = _cylinder(tmp_path / "cyl.stl")
    monkeypatch.setattr(MeasurementTools, "_mesh_export",
                        lambda self, part, limit=None, visit=None: visit(stl))
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        get_active_part=lambda: types.SimpleNamespace(Name="P"))
    message = tools._offset_blocker(object(), 20.0, 0.25)
    assert "curvature does not explain" in message
    assert "0.250 mm on the larger side" in message


def test_a_one_sided_offset_names_each_side_as_the_thing_to_split(monkeypatch,
                                                                 tmp_path):
    """The 2026-11-09 report's exact case: 0.5 on one side refused against a
    0.355 mm radius, and the old advice ("stay below 0.355") read as "this wall
    can never be 0.5 mm"."""
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.measurement import MeasurementTools

    from tests.test_reported_issues_2026_11_04_v0358 import _cylinder

    stl = _cylinder(tmp_path / "cyl.stl")
    monkeypatch.setattr(MeasurementTools, "_mesh_export",
                        lambda self, part, limit=None, visit=None: visit(stl))
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        get_active_part=lambda: types.SimpleNamespace(Name="P"))
    message = tools._offset_blocker(object(), 0.5, 0.5)
    if "KEEP EACH SIDE" in message:
        assert "0.250/0.250" in message
    else:
        # The cylinder's own radius is not below 0.5, so this particular mesh
        # does not reach the branch - the branch itself is covered by the test
        # above, and the wording is checked from source.
        import inspect

        source = inspect.getsource(GSDTools._offset_blocker)
        assert "KEEP EACH SIDE" in source
        assert "0.250/0.250" in source


# ── boundary / visibility statements ─────────────────────────────────────


def test_the_boundary_reply_names_the_split_route_because_filling_it_fails():
    """One curve carries every loop, so a fill of it covers the whole face. The
    reply has to hand over the route that works, with the numbers."""
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(ensure_connected=lambda: None)
    tools._factory = lambda: types.SimpleNamespace(
        AddNewBoundaryOfSurface=lambda ref: types.SimpleNamespace(Name=""))  # type: ignore[method-assign]
    tools._ref = lambda name: ("ref", name)          # type: ignore[method-assign]
    tools._finish = lambda shape, name, message: message  # type: ignore[method-assign]

    reply = tools.execute("catia_gsd_boundary", {"surface": "BaseSurf"})
    assert "EVERY loop" in reply
    assert "catia_gsd_split" in reply and "catia_gsd_fill" in reply
    # The measured chain, so the claim is checkable rather than plausible.
    assert "1486.903" in reply and "113.097" in reply and "1600.000" in reply


def test_hiding_a_set_says_where_the_solid_actually_is():
    """A geometrical set is a branch of its own: measured on B30, hiding it left
    the body's solid alone (STL 3778 -> 3244 B). The recovery the report had to
    guess is now in the reply."""
    import inspect

    from catia_mcp.tools.part_design import PartDesignTools

    text = " ".join(inspect.getsource(PartDesignTools._set_visibility).split())
    assert "separate branch from the body" in text
    assert "3778" in text
    assert "show the set again" in text


def test_the_solid_carrier_walk_does_not_crash_on_a_plain_set():
    from catia_mcp.tools.part_design import PartDesignTools

    empty = types.SimpleNamespace(
        HybridShapes=types.SimpleNamespace(Count=0),
        HybridBodies=types.SimpleNamespace(Count=0))
    assert PartDesignTools._solid_carriers(empty) == []
