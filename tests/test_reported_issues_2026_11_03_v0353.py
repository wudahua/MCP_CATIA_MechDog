# -*- coding: utf-8 -*-
"""0.3.53 - three reports of 2026-11-03, and what each one actually asked for.

Report 1 (0.3.52 acceptance, surface session): 0.3.52 passed 6/6 and 4/4, and
then added two process findings that are real and were both fixed here:

  * the test count was inflated by 3. `pytest tests` collected 888 while the
    release note said 891, because three tests lived in a root-level
    test_server.py that only the CI command collected. Fixed by moving the file
    into tests/ so every command gives one number.
  * the reply said two elements sharing a name make later calls "pick one of two
    at random". Measured: the OLD element keeps the name and keeps answering to
    it, deterministically. The wording was wrong, not the mechanism.

Report 2 (100% gap list): asked for three P0 tools on the grounds that the
factory "already declares" them. Measured on B30 (b30_v0353_signatures.py reads
the real parameter lists out of ITypeInfo, b30_v0353_fillet.py and
b30_v0353_corner.py try them):

  * AddNewBoundaryOfSurface(Surface) - ONE argument, builds and updates,
    boundary of a 30 mm extrusion of a r=10 circle measured 125.664 mm. Shipped
    here as catia_gsd_boundary.
  * AddNewFilletBiTangent(el1, el2, radius, o1, o2, trimMode, relimitation) -
    works only for one orientation pair out of four on a 90 degree corner
    (o1=-1, o2=+1), and all nine combinations fail between two disjoint
    parallel cylinders. Geometry not yet verified, so no tool this version.
  * AddNewSpine() - no arguments at all, and UpdateObject fails without
    sections, which the typelib does not name. Not usable yet.
  * catia_gsd_extract already existed (the gap list says it does not).

Report 3 (curvature deadlock): the failure mode is "adjacent sections whose
angular features are out of phase make the interpolation too tight to offset by
0.3 mm". Measured on B30 on an isolated three-ring lobed loft with a 30 degree
lobe shift - far more than the 10-12 degrees reported - it BUILT and THICKENED
0.3 mm, at 40 and 90 points per section. So the doc says that, rather than
repeating a mechanism that did not reproduce, and points at the joined seams.
The same report asked for a region filter on catia_check_surface; that ships
here, because "which ring is too tight" was the question the whole-part median
could not answer.
"""

from __future__ import annotations

import json
import os

import pytest


# ───────────────────────── 1. the test count had two answers

def test_the_ci_command_collects_the_server_tests():
    """It moved into tests/ in 0.3.53: `pytest tests` must see it.

    The report's finding was a process one - 888 collected against 891 claimed,
    because test_server.py sat in the repo root and only the CI command named
    it. A number nobody can reproduce is not a measurement, so the file moved
    and the commands were unified to `pytest tests -q`.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    assert os.path.exists(os.path.join(here, "test_server.py"))
    assert not os.path.exists(os.path.join(os.path.dirname(here), "test_server.py"))


# ───────────────────────── 2. a missing element is a validation failure

def test_missing_element_is_rejected_not_a_com_error():
    """Observed 2026-10-31, open for seven versions: `{}` reached the COM layer.

    ``_pick`` raised a plain ValueError, which carries no code and no stage, so
    the server fell back to COM_ERROR / failure_stage "execute" and attached the
    "check in_work_object" paragraph - for a call that never touched CATIA.
    """
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.measurement import _pick

    with pytest.raises(ArgumentRejected) as caught:
        _pick({}, "element1", "elem1_name", "element1_name")
    error = caught.value
    assert getattr(error, "stage", None) == "validate"
    assert "element1" in str(error)


def test_missing_element_names_every_accepted_spelling():
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.measurement import _pick

    with pytest.raises(ArgumentRejected) as caught:
        _pick({"nope": 1}, "element1", "elem1_name")
    assert "elem1_name" in str(caught.value)


# ───────────────────────── 3. step_warnings must not fire on an empty list

def test_an_empty_warnings_array_is_not_a_warning():
    """Observed 2026-10-31, open for seven versions.

    Every envelope carries ``"warnings": []``. The old test was
    ``"WARNING" in result.upper()``, and that KEY contains the word - so any
    tool that always emits the key put every one of its steps into
    step_warnings and drowned the real one.
    """
    from catia_mcp.tools.planner import PlannerTools

    envelope = json.dumps({"ok": True, "modified": True, "warnings": []})
    assert PlannerTools._step_has_warning(envelope) is False


def test_a_real_warning_still_counts():
    from catia_mcp.tools.planner import PlannerTools

    assert PlannerTools._step_has_warning("Done. WARNING: 2 elements were left")
    assert PlannerTools._step_has_warning(
        json.dumps({"ok": True, "warnings": ["stale view"]}))
    # The word may sit in the middle of a paragraph, which is how the tools
    # actually write it - a line-start match would miss all of them.
    assert PlannerTools._step_has_warning("a. WARNING: b")
    assert PlannerTools._step_has_warning("plain text, nothing to see") is False


# ───────────────────────── 4. an unverified name says so

def test_an_unreadable_tree_says_the_name_could_not_be_checked():
    """Trading one silent duplicate for another would be worse than the bug."""
    from catia_mcp.tools.gsd import GSDTools

    class _Blind:
        def FindObjectByName(self, name):            # noqa: N802 - COM spelling
            raise RuntimeError("COM went away")

    tools = GSDTools(connection=None)
    name, verified = tools._free_name(_Blind(), "P")
    assert (name, verified) == ("P", False)


def test_the_reply_admits_an_unchecked_name():
    from catia_mcp.tools.gsd import GSDTools

    class _Part:
        def FindObjectByName(self, name):            # noqa: N802
            raise RuntimeError("COM went away")

    part = _Part()
    part.InWorkObject = None

    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
        "note_owned": staticmethod(lambda name, points: None),
    })()
    tools._safe_update = lambda shape: None

    class _Shape:
        Name = ""

    geoset = type("G", (), {"Name": "GSD_Set"})()
    message = tools._completed(_Shape(), "P", "Surface created.", geoset)
    assert "could not be checked" in message
    assert "two with that name" in message
    assert "'GSD_Set'" in message


# ───────────────────────── 5. the region filter

def _stl(path, faces):
    """Write an ASCII STL: each face is (normal, [v0, v1, v2])."""
    with open(path, "w", encoding="ascii") as handle:
        handle.write("solid test\n")
        for normal, verts in faces:
            handle.write(f"facet normal {normal[0]} {normal[1]} {normal[2]}\n")
            handle.write("  outer loop\n")
            for vertex in verts:
                handle.write(f"    vertex {vertex[0]} {vertex[1]} {vertex[2]}\n")
            handle.write("  endloop\nendfacet\n")
        handle.write("endsolid test\n")


def _right_angle_mesh(tmp_path):
    """Two triangles sharing an edge along X: one flat in z=0, one standing up.

    The shared edge runs from (0,0,0) to (10,0,0), so every fold statistic
    about this mesh belongs to an edge whose midpoint is (5, 0, 0): r = 5,
    theta = 0, z = 0. A 90 degree fold is the only interior edge.
    """
    path = tmp_path / "corner.stl"
    _stl(path, [
        ((0, 0, 1), [(0, 0, 0), (10, 0, 0), (0, 10, 0)]),
        ((0, -1, 0), [(0, 0, 0), (10, 0, 0), (0, 0, 10)]),
    ])
    return str(path)


def test_the_whole_mesh_sees_the_fold(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_right_angle_mesh(tmp_path), 25.0, 600000)
    assert stats["fold_deg"]["max"] == 90.0
    assert stats["creases"] == 1
    assert "interior_edges_total" not in stats


def test_a_region_that_contains_the_edge_keeps_it(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_right_angle_mesh(tmp_path), 25.0, 600000,
                        {"z": (-1.0, 1.0)})
    assert stats["fold_deg"]["max"] == 90.0
    assert stats["interior_edges"] == 1
    assert stats["interior_edges_outside_region"] == 0


def test_a_region_that_misses_the_edge_reports_it_as_excluded(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_right_angle_mesh(tmp_path), 25.0, 600000,
                        {"z": (5.0, 20.0)})
    assert stats["interior_edges"] == 0
    assert stats["interior_edges_outside_region"] == 1
    assert stats["interior_edges_total"] == 1
    assert stats["fold_deg"]["max"] == 0.0
    assert "outside it were not counted" in stats["region_note"]


def test_the_radial_and_angular_filters_agree_with_the_midpoint(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    path = _right_angle_mesh(tmp_path)
    inside = _fold_stats(path, 25.0, 600000, {"r": (4.0, 6.0)})
    outside = _fold_stats(path, 25.0, 600000, {"r": (20.0, 30.0)})
    assert inside["interior_edges"] == 1
    assert outside["interior_edges"] == 0
    # the midpoint of the shared edge is (5, 0, 0): theta 0, i.e. +X. A wedge
    # spanning 0 contains it, a 90..270 degree wedge does not.
    assert _fold_stats(path, 25.0, 600000,
                       {"theta_deg": (-15.0, 15.0)})["interior_edges"] == 1
    assert _fold_stats(path, 25.0, 600000,
                       {"theta_deg": (90.0, 270.0)})["interior_edges"] == 0


def test_an_angle_range_may_wrap_across_zero(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    path = _right_angle_mesh(tmp_path)
    wrapped = _fold_stats(path, 25.0, 600000, {"theta_deg": (350.0, 10.0)})
    assert wrapped["interior_edges"] == 1


def test_a_region_with_lo_above_hi_is_refused_except_for_angles():
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.measurement import _region_bounds

    with pytest.raises(ArgumentRejected):
        _region_bounds({"r": [10, 1]})
    assert _region_bounds({"theta_deg": [350, 10]}) is not None
    assert _region_bounds({}) is None
    assert _region_bounds({"z": [None, 5]}) == {"z": (float("-inf"), 5.0)}


def test_the_region_reaches_the_report(tmp_path, monkeypatch):
    """The reply has to carry what the figures describe, not just the figures."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    captured = {}

    def fake_export(part, limit, visit):
        visit(_right_angle_mesh(tmp_path))
        captured["called"] = True

    class _Part:
        Parent = type("P", (), {"Name": "REGION.CATPart"})()

    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: _Part()),
    })()
    tools._mesh_export = fake_export
    tools._visible_hybrid_sets = lambda part: (None, 0)
    payload = json.loads(tools._check_surface(
        25.0, 600000, None, {"z": [5, 20]}))
    assert payload["region"] == {"z": [5, 20]}
    assert payload["interior_edges_outside_region"] == 1
    assert "region only" in payload["how_to_read"]


# ───────────────────────── 6. the boundary tool

def test_boundary_passes_exactly_one_reference():
    """Measured: the typelib declares AddNewBoundaryOfSurface(Surface), one arg."""
    from catia_mcp.tools.gsd import GSDTools

    calls = []

    class _Factory:
        def AddNewBoundaryOfSurface(self, *args):     # noqa: N802
            calls.append(args)
            return type("B", (), {"Name": ""})()

    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {"ensure_connected": staticmethod(lambda: None)})()
    tools._ref = lambda name: f"ref:{name}"
    tools._factory = lambda: _Factory()
    tools._finish = lambda shape, name, message: message
    reply = tools.execute("catia_gsd_boundary", {"surface": "S_LOFT"})
    assert calls == [("ref:S_LOFT",)]
    assert "Boundary of 'S_LOFT'" in reply


def test_boundary_is_a_declared_tool_with_a_required_surface():
    from catia_mcp.tools.gsd import GSDTools

    tools = [tool for tool in GSDTools(connection=None).get_tool_definitions()
             if tool["name"] == "catia_gsd_boundary"]
    assert len(tools) == 1
    assert tools[0]["inputSchema"]["required"] == ["surface"]
    assert "does NOT smooth a seam" in tools[0]["description"]


def test_the_boundary_description_does_not_promise_a_heal():
    """0.3.51 measured that healing does nothing; the new tool must not imply it."""
    from catia_mcp.tools.gsd import GSDTools

    text = [tool["description"]
            for tool in GSDTools(connection=None).get_tool_definitions()
            if tool["name"] == "catia_gsd_boundary"][0]
    assert "no Match" in text
    assert "stays G0 unless the surfaces are rebuilt tangent" in text


# ───────────────────────── 7. what the reports asked for and did not get

def test_the_loft_note_says_the_phase_shift_alone_did_not_reproduce():
    """Honest doc: the report's mechanism is quoted as a report, not as a fact."""
    from catia_mcp.tools.gsd import GSDTools

    text = [tool["description"]
            for tool in GSDTools(connection=None).get_tool_definitions()
            if tool["name"] == "catia_gsd_multi_section_surface"][0]
    assert "OUT OF" in text and "PHASE" in text
    assert "built AND thickened 0.3 mm" in text
    assert "region={'z': [...]}" in text
