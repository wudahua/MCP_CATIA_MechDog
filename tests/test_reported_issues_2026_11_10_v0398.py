# -*- coding: utf-8 -*-
"""0.3.98: the engraving chain three reports could not find - and it was there.

The 2026-11-10 reports asked for "cut text into a curved face" and every route
through the surface was refused, so 0.3.95 declined to wrap an engrave tool at
all and wrote the refusal into the CHANGELOG. That refusal was right about the
SURFACE route and wrong about the capability: measured on B30 2026-11-10, this
chain runs end to end with tools that already existed -

    plane normal to the curve -> circle of radius = depth -> sweep -> CloseSurface
    -> boolean(remove)

Because the tube is centred on a curve that lies on the surface, half of it is
inside the material, so the groove depth is the radius, along the surface normal.
A r=4 trace on a r=40 cylinder: 5.9657 mm3 tube (2*pi^2*R*r^2 = 5.97), 2.974 mm3
removed - the half.

The same session also found that the periodic-spline entry 0.3.97 shipped was
wrong: it merged two different objects.
"""
from __future__ import annotations

import inspect
import types

import pytest

import catia_mcp.tools.part_design as part_design
from catia_mcp.results import (ArgumentOutOfRange, ArgumentRejected,
                               CodedError, FeatureRejected,
                               PreconditionRejected)
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.sketcher import SketcherTools


def _definition(tool_name: str, tools=None) -> dict:
    tools = tools or GSDTools(connection=None)
    return next(d for d in tools.get_tool_definitions() if d["name"] == tool_name)


class _Bodies:
    def __init__(self, names):
        self._names = list(names)
        self.Count = len(self._names)

    def Item(self, key):
        if isinstance(key, int):
            return types.SimpleNamespace(Name=self._names[key - 1])
        return types.SimpleNamespace(Name=key)

    def remove(self, name: str) -> None:
        if name in self._names:
            self._names.remove(name)
        self.Count = len(self._names)


class _Part:
    """A part that answers for NAMES and can be asked to update.

    0.3.101 made the cleanup look every name up again after deleting it and then
    ask the part to update, so a part that cannot answer either question would
    make the new checks untestable - and a reply that says "nothing was left
    behind" without those two answers is what the 2026-11-11 report caught.
    """

    def __init__(self, body_names, tree_names):
        self.Bodies = _Bodies(body_names)
        self.tree = set(tree_names)
        self.broken = False
        self.updates = 0

    def FindObjectByName(self, name):
        name = str(name)
        return types.SimpleNamespace(Name=name) if name in self.tree else None

    def Update(self):
        self.updates += 1
        if self.broken:
            raise RuntimeError("方法 Update 失败")


class _Conn:
    def __init__(self, body_names=("零件几何体",), tree_names=()):
        self._part = _Part(body_names,
                           set(body_names) | {str(n) for n in tree_names})
        self.active_document = types.SimpleNamespace(Name="P.CATPart")

    def ensure_connected(self):
        return None

    def get_active_part(self):
        return self._part


# ── the tool exists, and says what it does and does not do ─────────────


def test_engrave_is_registered_with_curve_and_depth() -> None:
    schema = _definition("catia_surface_engrave")["inputSchema"]
    assert schema["required"] == ["curve", "depth"]
    assert set(schema["properties"]) == {"curve", "depth", "target_body",
                                         "position", "name"}


def test_engrave_description_states_the_normal_and_the_glyph_limit() -> None:
    description = _definition("catia_surface_engrave")["description"]
    # The whole point of the tool: depth follows the normal, not an axis.
    assert "SURFACE NORMAL" in description
    # And what it is NOT, so nobody expects a filled letter.
    assert "NOT A FILLED GLYPH" in description
    # The measurement that proves it, with its numbers.
    assert "5.9657" in description and "2.974" in description


def test_engrave_names_its_five_steps_in_the_right_order() -> None:
    source = inspect.getsource(GSDTools._surface_engrave)
    # Skip the docstring: it names the steps too, in prose.
    body = source.split('"""', 2)[2]
    order = [body.index('{"curve": curve, "position": position'),
             body.index('"support_plane": plane_name'),
             body.index('"guide": curve'),
             body.index("_close_surface"),
             body.index('"catia_boolean"')]
    assert order == sorted(order)


def test_engrave_cleans_up_on_failure() -> None:
    source = inspect.getsource(GSDTools._surface_engrave)
    # The outer handler is the one right after the boolean's measurement.
    tail = source.split("after = pd._body_volume_mm3(part, target)", 1)[1]
    assert "except Exception as error:" in tail
    assert "_engrave_cleanup" in tail.split("raise", 1)[0]


# ── arguments are refused before CATIA is touched ─────────────────────


def _tools(body_names=("零件几何体",)) -> GSDTools:
    return GSDTools(connection=_Conn(body_names))


def test_missing_curve_is_named_not_a_key_error() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        _tools()._surface_engrave({"depth": 0.4})
    assert "curve" in str(caught.value)
    assert getattr(caught.value, "code", "") == "CURVE_REQUIRED"


def test_zero_or_negative_depth_is_refused() -> None:
    for depth in (0, -0.4):
        with pytest.raises(ArgumentOutOfRange) as caught:
            _tools()._surface_engrave({"curve": "Trace", "depth": depth})
        assert getattr(caught.value, "code", "") == "DEPTH_OUT_OF_RANGE"


def test_a_non_numeric_depth_says_what_it_wants() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        _tools()._surface_engrave({"curve": "Trace", "depth": "deep"})
    assert "depth" in str(caught.value)


def test_position_must_be_a_fraction_not_a_length() -> None:
    for position in (0.0, 1.0, 12.0):
        with pytest.raises(ArgumentOutOfRange) as caught:
            _tools()._surface_engrave(
                {"curve": "Trace", "depth": 0.4, "position": position})
        assert getattr(caught.value, "code", "") == "POSITION_OUT_OF_RANGE"


def test_unknown_target_body_lists_the_bodies() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        _tools()._surface_engrave({"curve": "Trace", "depth": 0.4,
                                   "target_body": "Nope"})
    message = str(caught.value)
    assert "'Nope'" in message and "零件几何体" in message


def test_body_lookup_is_by_name() -> None:
    part = _Conn(("A", "B")).get_active_part()
    assert GSDTools(connection=None)._body_by_name(part, "B").Name == "B"


# ── cleanup reports what survived instead of claiming success ─────────


class _FakePD:
    def __init__(self, fail_on=(), part=None):
        self.fail_on = set(fail_on)
        self.calls = []
        self.part = part

    def execute(self, name, args):
        self.calls.append((name, args))
        target = args.get("name") or args.get("feature_name")
        if target in self.fail_on:
            raise CodedError(f"cannot remove '{target}'", code="DELETE_FAILED")
        if self.part is not None and target:
            # a real delete takes the name out of the tree; the cleanup's second
            # look-up is what this makes observable
            self.part.tree.discard(str(target))
            if name == "catia_delete_body":
                self.part.Bodies.remove(str(target))
        return "removed"


@pytest.fixture()
def fake_pd(monkeypatch):
    def install(fail_on=(), part=None):
        holder = _FakePD(fail_on, part)
        monkeypatch.setattr(part_design, "PartDesignTools",
                            lambda conn: holder)
        return holder
    return install


def test_cleanup_removes_the_body_then_the_elements(fake_pd) -> None:
    conn = _Conn(body_names=("零件几何体", "Engrave_Trace"),
                 tree_names=("Engrave_Trace_N", "Engrave_Trace_P"))
    holder = fake_pd(part=conn.get_active_part())
    tools = GSDTools(connection=conn)
    note = tools._engrave_cleanup(["Engrave_Trace_N", "Engrave_Trace_P"],
                                  "Engrave_Trace")
    assert "left behind" in note and "could not" not in note
    names = [call[0] for call in holder.calls]
    assert names[0] == "catia_delete_body"
    assert names.count("catia_delete_feature") == 2


def test_cleanup_admits_what_it_could_not_remove(fake_pd) -> None:
    """0.3.100: a delete that fails on a part that will not answer for names is
    reported as UNVERIFIED, not as "may still hold them" - the 2026-11-10 report
    got that sentence while `catia_list_bodies` showed one body, because the
    boolean had already consumed the cutter."""
    conn = _Conn(tree_names=("Engrave_Trace_N", "Engrave_Trace_P"))
    fake_pd(fail_on={"Engrave_Trace_P"}, part=conn.get_active_part())
    tools = GSDTools(connection=conn)
    note = tools._engrave_cleanup(["Engrave_Trace_N", "Engrave_Trace_P"],
                                  "Engrave_Trace")
    assert "STILL in the tree" in note
    assert "Engrave_Trace_P" in note
    assert "Bodies now" in note


def test_a_consumed_cutter_body_is_not_reported_as_still_there(fake_pd) -> None:
    """The exact false alarm of the 2026-11-10 report: the boolean consumed the
    tool body (tool_body_consumed: true), so deleting it BY NAME failed and the
    reply said "could not remove body ... may still hold them" while the tree had
    one body."""
    conn = _Conn(body_names=("零件几何体",), tree_names=("Engrave_Trace_N",))
    holder = fake_pd(part=conn.get_active_part())
    tools = GSDTools(connection=conn)
    note = tools._engrave_cleanup(["Engrave_Trace_N"], "Engrave_Trace")
    assert "could not" not in note.lower()
    assert "already been consumed" in note
    assert "Bodies now: 零件几何体" in note
    # It must not try to delete a body that is gone.
    assert all(call[0] != "catia_delete_body" for call in holder.calls)


# ── a refusal keeps the code it chose, and gains the cleanup sentence ──


class _StepPD:
    """A PartDesignTools stand-in for the body/boolean half of the chain."""

    def __init__(self, fail=None, volume=100.0):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail or {}
        self.volume = volume

    def execute(self, name, args):
        self.calls.append((name, args))
        if name in self.fail:
            raise self.fail[name]
        return "ok"

    def _body_volume_mm3(self, part, body):
        return self.volume


def _engraving_tools() -> GSDTools:
    tools = GSDTools(connection=_Conn())
    tools._ref = lambda name: name  # type: ignore[method-assign]
    tools._curve_length_mm = lambda ref: 10.0  # type: ignore[method-assign]
    tools._plane_normal_to_curve = (  # type: ignore[method-assign]
        lambda args: "Plane created (origin 'Trace_at0.5')")
    tools._circle = lambda args: "circle"  # type: ignore[method-assign]
    tools._sweep = lambda args: "sweep"  # type: ignore[method-assign]
    tools._close_surface = lambda args: "closed"  # type: ignore[method-assign]
    return tools


def test_a_boolean_refusal_keeps_its_own_code(monkeypatch) -> None:
    """FeatureRejected carries .code without subclassing CodedError.

    A `isinstance(..., CodedError)` test therefore missed it and wrapped
    TOOL_DOES_NOT_TOUCH into a generic ENGRAVE_FAILED - losing the one sentence
    that says how far away the tube was (measured on B30: 200.0579 mm).
    """
    monkeypatch.setattr(part_design, "PartDesignTools",
                        lambda conn: _StepPD(fail={"catia_boolean": FeatureRejected(
                            "Nothing to remove: it is 200.0579 mm away", False,
                            code="TOOL_DOES_NOT_TOUCH")}))
    tools = _engraving_tools()
    with pytest.raises(Exception) as caught:
        tools._surface_engrave({"curve": "Trace", "depth": 0.4,
                                "target_body": "零件几何体"})
    error = caught.value
    assert getattr(error, "code", None) == "TOOL_DOES_NOT_TOUCH"
    assert "200.0579" in str(error)
    # ...and the cleanup result travels with it.
    assert "Nothing this call created was left behind" in str(error)


def test_a_circle_that_cannot_carry_a_parameter_says_so(monkeypatch) -> None:
    monkeypatch.setattr(part_design, "PartDesignTools", lambda conn: _StepPD())
    tools = _engraving_tools()

    def refuse(args):
        raise PreconditionRejected(
            "CATIA refused a point at 0.5 along 'Cir'",
            code="POINT_ON_CURVE_FAILED", stage="execute")

    tools._plane_normal_to_curve = refuse  # type: ignore[method-assign]
    with pytest.raises(PreconditionRejected) as caught:
        tools._surface_engrave({"curve": "Cir", "depth": 0.4,
                                "target_body": "零件几何体"})
    error = caught.value
    assert getattr(error, "code", "") == "ENGRAVE_CURVE_UNPARAMETRISABLE"
    assert "catia_gsd_project" in str(error)
    # A circle and its extract both refuse; the message must not blame "not a curve".
    assert "not a curve" not in str(error).lower()
    assert "Nothing this call created was left behind" in str(error)


# ── the spline ceiling 0.3.97 got wrong, split into its two objects ────

def test_spline_ceilings_separate_the_gds_spline_from_the_sketch_one() -> None:
    by_member = {entry["member"]: entry for entry in _CAPABILITY_CEILINGS}
    gds = by_member["HybridShapeSpline.SetClosing"]
    sketch = by_member["Spline2D (sketch) periodicity"]
    assert gds["status"] == "AVAILABLE_WITH_LIMIT"
    assert sketch["status"] == "NOT_IN_TYPELIB"
    # The measurement that settles it, both numbers.
    assert "61.2161" in gds["detail"] and "77.8649" in gds["detail"]
    assert "read-only" in sketch["detail"].lower()
    # 0.3.97 shipped a single entry that merged them; it must not come back.
    assert not any("AddNewSpline (periodic)" == m for m in by_member)


def test_the_engraving_route_is_listed_as_available() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "SweepExplicit" in e["member"])
    assert entry["status"] == "AVAILABLE_WITH_LIMIT"
    assert "catia_surface_engrave" in entry["detail"]
    assert "CLOSED" in entry["detail"]


def test_sketch_spline_says_why_it_cannot_be_periodic() -> None:
    reply_source = inspect.getsource(SketcherTools._draw_spline)
    assert "Spline2D" in reply_source
    assert "READ-ONLY" in reply_source
    # And points at the tool that can.
    assert "catia_gsd_spline(closed=true)" in reply_source


def test_sketch_spline_schema_points_at_the_gds_spline() -> None:
    schema = _definition("catia_sketch_spline", SketcherTools(connection=None))
    closed = schema["inputSchema"]["properties"]["closed"]["description"]
    assert "SetClosing" in closed
    assert "catia_gsd_spline" in closed
