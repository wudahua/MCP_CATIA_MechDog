# -*- coding: utf-8 -*-
"""0.3.121 - the two 0.3.120 acceptance reports, and what each item turned into.

Everything here is offline; every claim in it was measured on B30 2026-11-13
(scripts/b30_v0423_probe_view_faces.py, _view2.py, _faces.py, _face_verts.py,
_solid_tube.py). The theme is the one this project keeps meeting: a reply that
knew less than the measurement.

  1. set_view('front') was "tilted 15-20 degrees, the bottom of the bottle
     visible" in BOTH reports, and both blamed the direction vectors. Read back,
     the vectors were exactly right; Viewpoint3D.ProjectionMode read 0 =
     PERSPECTIVE. Forced to parallel now, and the camera is read back and printed.
  2. list_faces published area only, twice. A FACE reference does answer GetCOG
     (its own centre) and GetPlane - unlike a hybrid reference, which measures the
     body that carries it. There is no per-face bounding box anywhere in the API;
     that is written down instead of faked.
  3. Both reports asked for a solid tube along a curve - one built a
     constant-radius bend in six calls, the other 17 lofts for a handle dome.
     catia_solid_tube is that chain once.
  4. solid_loft's two undocumented semantics (auto-union in a body that already
     holds material, and the first/last section planes as end caps) are now said
     in its reply, because three reports ran into them.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.part_design import PartDesignTools


# ── stubs ────────────────────────────────────────────────────────────────────


class _Viewpoint:
    def __init__(self, sight=(0, 0, 0), up=(0, 0, 0)):
        self.ProjectionMode = 0          # CATIA's default on B30: perspective
        self.sight = tuple(sight)
        self.up = tuple(up)
        self.put = []

    def PutSightDirection(self, vector):
        self.sight = tuple(round(float(v), 6) for v in vector)
        self.put.append(("sight", self.sight))

    def PutUpDirection(self, vector):
        self.up = tuple(round(float(v), 6) for v in vector)
        self.put.append(("up", self.up))


class _Viewer:
    def __init__(self, viewpoint=None):
        self.Viewpoint3D = viewpoint or _Viewpoint()
        self.reframed = 0

    def Reframe(self):
        self.reframed += 1


class _SystemService:
    """SystemService.Evaluate, answering a fixed array for the view read-back."""

    def __init__(self, values=None, fail=False):
        self.values = values
        self.fail = fail
        self.calls = []

    def Evaluate(self, code, flag, func, params):
        self.calls.append(func)
        if self.fail:
            raise RuntimeError("执行脚本失败")
        # NOT coerced to float: a failed GetPlane inside CATIA leaves its nine
        # slots EMPTY, and those arrive here as None - the tool has to survive
        # them, so the fake must hand them over as they come.
        return list(self.values)


class _App:
    def __init__(self, system=None):
        self.SystemService = system or _SystemService()


def _export(viewpoint=None, system=None, with_app=True):
    tools = ExportTools(connection=None)
    viewer = _Viewer(viewpoint)
    conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "active_window": type("W", (), {"ActiveViewer": viewer})(),
    })()
    if with_app:
        conn.app = _App(system)
    tools.conn = conn
    return tools, viewer


# ── 1. set_view: the projection, not the direction vectors ──────────────────


def test_front_forces_parallel_projection_and_prints_the_read_back():
    """The tilt was ProjectionMode=0 (perspective); parallel is the fix.

    Measured on B30 2026-11-13 with two captures that differed in nothing else:
    mode 0 showed a 60x40x4 plate's TOP face in a front view, mode 1 showed a
    clean elevation.
    """
    viewpoint = _Viewpoint(sight=(0, 1, 0), up=(0, 0, 1))
    system = _SystemService([0, 1, 0, 0, 0, 1])
    tools, viewer = _export(viewpoint, system)
    reply = tools.execute("catia_set_view", {"view": "front"})
    assert viewpoint.ProjectionMode == 1, "parallel, or the picture is perspective"
    assert "parallel projection" in reply
    assert "Read back from CATIA: sight [0.0, 1.0, 0.0], up [0.0, 0.0, 1.0]" in reply
    assert "the camera took them" in reply
    assert viewer.reframed == 1


def test_perspective_is_still_available_when_asked_for():
    viewpoint = _Viewpoint()
    tools, _ = _export(viewpoint, _SystemService([0, 1, 0, 0, 0, 1]))
    reply = tools.execute("catia_set_view",
                          {"view": "front", "projection": "perspective"})
    assert viewpoint.ProjectionMode == 0
    assert "perspective projection" in reply


def test_a_put_that_catia_ignores_is_reported_not_photographed():
    """The 0.3.120 reports' own evidence, turned into a check.

    They saw a tilted picture and concluded the camera was not on -Y. Without a
    read-back, an ignored Put and a correct Put look the same. Here CATIA answers
    a different sight from the one asked for, and the reply says so.
    """
    viewpoint = _Viewpoint()
    tools, _ = _export(viewpoint, _SystemService([0, 0, -1, 0, 1, 0]))
    reply = tools.execute("catia_set_view", {"view": "front"})
    # 0.3.123 reworded this: a normalisation difference and an ignored Put have to
    # be told apart, so the sentence says which one it is and by how many degrees.
    assert "NOT the direction asked for" in reply
    assert "sight [0.0, 0.0, -1.0]" in reply


def test_an_unreadable_read_back_does_not_claim_the_camera_took_them():
    tools, _ = _export(_Viewpoint(), _SystemService(fail=True))
    reply = tools.execute("catia_set_view", {"view": "front"})
    assert "does NOT confirm" in reply
    assert "Read back from CATIA" not in reply


def test_direction_and_up_replace_the_named_table():
    viewpoint = _Viewpoint()
    tools, _ = _export(viewpoint, _SystemService([0, 0, -1, 0, 1, 0]))
    reply = tools.execute("catia_set_view",
                          {"direction": [0, 0, -1], "up": [0, 1, 0]})
    assert viewpoint.sight == (0.0, 0.0, -1.0)
    assert viewpoint.up == (0.0, 1.0, 0.0)
    assert reply.startswith("View set to custom direction:")


def test_a_direction_without_an_up_gets_one_that_is_not_parallel():
    viewpoint = _Viewpoint()
    tools, _ = _export(viewpoint, _SystemService([0, 0, -1, 0, 1, 0]))
    reply = tools.execute("catia_set_view", {"direction": [0, 0, -1]})
    assert viewpoint.up == (0.0, 1.0, 0.0), "Z would be parallel to a -Z sight"
    assert "up defaulted" in reply


def test_an_up_parallel_to_the_sight_is_refused_before_catia_is_touched():
    viewpoint = _Viewpoint()
    tools, _ = _export(viewpoint, _SystemService([0, 1, 0, 0, 0, 1]))
    with pytest.raises(Exception) as caught:
        tools.execute("catia_set_view",
                      {"direction": [0, 1, 0], "up": [0, -2, 0]})
    assert getattr(caught.value, "code", "") == "UP_PARALLEL_TO_SIGHT"
    assert viewpoint.put == [], "nothing may be put before the refusal"


def test_an_unknown_view_name_is_still_refused():
    tools, _ = _export()
    with pytest.raises(Exception) as caught:
        tools.execute("catia_set_view", {"view": "sideways"})
    assert getattr(caught.value, "code", "") == "UNKNOWN_VIEW"


def test_a_bad_projection_word_is_refused_and_names_both_choices():
    tools, _ = _export()
    with pytest.raises(Exception) as caught:
        tools.execute("catia_set_view",
                      {"view": "front", "projection": "orthographic"})
    assert getattr(caught.value, "code", "") == "BAD_ARGUMENT"
    assert "parallel" in str(caught.value) and "perspective" in str(caught.value)


# ── 2. list_faces: a face answers its own centre and plane ──────────────────


class _Cell:
    def __init__(self, name="Face.1"):
        self.Reference = name
        self.Value = name


class _Spa:
    def GetMeasurable(self, _reference):
        return object()


class _GeometryTools(PartDesignTools):
    """PartDesignTools with a canned SystemService array and a fake SPA."""

    def __init__(self, values):
        super().__init__(connection=None)
        self.conn = type("C", (), {
            "app": _App(_SystemService(values)),
        })()

    def _topo_measure(self, _part):
        return _Spa()


def _geom(values):
    tools = _GeometryTools(values)
    return tools._face_geometry(_Spa(), _Cell())


def test_a_face_reports_its_own_centroid_and_normal():
    """The 60x40x40 block's top face: centroid (30,20,40), normal +Z.

    GetPlane returned origin (0,0,40) with directions (1,0,0) and (0,1,0).
    """
    got = _geom([30.0, 20.0, 40.0, 0.0, 0.0, 40.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
                 1.0, 1.0])
    assert got["centroid"] == [30.0, 20.0, 40.0]
    assert got["plane_origin"] == [0.0, 0.0, 40.0]
    assert got["normal"] == [0.0, 0.0, 1.0]


def test_the_normal_keeps_catias_own_sense_and_is_not_flipped():
    """A first version flipped it away from the body's centre and was WRONG.

    On a 40x40x4 plate with a tube standing 20 mm above it, the body's centre of
    gravity is above the plate's top face, so the flip turned that face's normal
    inward (measured 2026-11-13). The plane is exact, the sense is CATIA's; the
    reply says "read it as a line, not as an arrow" instead of guessing.
    """
    got = _geom([20.0, 20.0, 4.0, 0.0, 0.0, 4.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
                 1.0, 1.0])
    assert got["normal"] == [0.0, 0.0, 1.0]


def test_a_curved_face_has_a_centroid_and_no_normal():
    """GetPlane raises on a cylinder; the Empty slots come back as None.

    float(None) used to take the centroid down with it - a 958 mm2 tube face
    listed with no centroid at all until the None handling was added.
    """
    got = _geom([20.0, 6.243, 26.243] + [None] * 9 + [1.0, 0.0])
    assert got["centroid"] == [20.0, 6.243, 26.243]
    assert "normal" not in got
    assert "geometry_unknown" not in got, "a curved face is an answer, not a defect"


def test_an_unreadable_centroid_says_so_on_that_face_only():
    got = _geom([None, None, None] + [0.0] * 9 + [0.0, 0.0])
    assert got["centroid_unknown"] is True
    assert "centroid" not in got


def test_the_listing_says_what_a_face_can_and_cannot_answer():
    """The description has to carry both halves, or the next caller assumes the
    missing one is a bug: a centroid and a plane are real per face, a bounding
    box is not."""
    definition = next(d for d in PartDesignTools(connection=None)
                      .get_tool_definitions() if d["name"] == "catia_list_faces")
    text = definition["description"]
    assert "CENTROID" in text and "NORMAL" in text
    assert "no per-face BOUNDING BOX" in text
    assert "44 members" in text


# ── 3. catia_solid_tube: argument errors happen before CATIA ────────────────


def _gsd():
    return GSDTools(connection=None)


@pytest.mark.parametrize("arguments,code", [
    ({}, "CURVE_REQUIRED"),
    ({"curve": "Spine"}, "RADIUS_REQUIRED"),
    ({"curve": "Spine", "radius": "fat"}, "RADIUS_REQUIRED"),
    ({"curve": "Spine", "radius": 0}, "RADIUS_OUT_OF_RANGE"),
    ({"curve": "Spine", "radius": -2}, "RADIUS_OUT_OF_RANGE"),
    ({"curve": "Spine", "radius": 5, "position": 1.5}, "POSITION_OUT_OF_RANGE"),
    ({"curve": "Spine", "radius": 5, "helpers": "tidy"}, "BAD_ARGUMENT"),
])
def test_solid_tube_refuses_bad_arguments_before_a_document_is_needed(arguments, code):
    """No document is open in this test on purpose: an argument error must not
    depend on CATIA, which is the lesson of the KeyError that escaped as
    'height' in 0.3.114 and of the enum that refused 'axis' in 0.3.120."""
    with pytest.raises(Exception) as caught:
        _gsd().execute("catia_solid_tube", arguments)
    assert getattr(caught.value, "code", "") == code


def test_solid_tube_is_registered_with_its_two_required_arguments():
    definition = next(d for d in _gsd().get_tool_definitions()
                      if d["name"] == "catia_solid_tube")
    assert definition["inputSchema"]["required"] == ["curve", "radius"]
    text = definition["description"]
    assert "pi*r^2*length" in text, "the volume is judged, not just printed"
    assert "CONSTANT SECTION ONLY" in text
    assert "cannot be deleted" in text


def test_a_tube_says_the_closesurface_owns_the_helpers_it_hides():
    """Engrave's sentence says "the boolean's history" - a tube has no boolean.

    Sharing the helper text was the point (one correction, not two); sharing the
    WRONG noun was the risk, so the owner is a parameter and this pins it.
    """
    tools = _gsd()
    hidden = []
    tools._set_helper_visibility = lambda name, show: hidden.append((name, show))
    text = tools._engrave_helpers(["a", "b"], "hide", owner="the CloseSurface")
    assert hidden == [("a", False), ("b", False)]
    assert "the CloseSurface's history" in text
    assert "boolean's history" not in text


# ── 4. the ceilings that were added with the fix ────────────────────────────


def test_the_three_new_ceilings_are_recorded_with_their_evidence():
    from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS

    members = {entry["member"]: entry for entry in _CAPABILITY_CEILINGS}
    view = members["a named view that is actually square-on"]
    assert view["status"] == "AVAILABLE_WITH_LIMIT"
    assert "ProjectionMode" in view["detail"] and "PARALLEL" in view["detail"]

    face = members["a face's own world bounding box"]
    assert face["status"] == "UNSUPPORTED_ON_B30"
    assert "44 members" in face["detail"]
    assert "GetCOG" in face["detail"]

    tube = members["a solid tube whose section changes along the spine"]
    assert tube["status"] == "UNSUPPORTED_ON_B30"
    assert "catia_solid_loft" in tube["detail"]
