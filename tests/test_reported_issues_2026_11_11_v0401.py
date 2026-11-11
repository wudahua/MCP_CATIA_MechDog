# -*- coding: utf-8 -*-
"""0.3.101: the guard that only worked in the session that made the curve.

The 2026-11-11 report is about two replies that each looked at one thing and
spoke about another:

  * catia_gsd_project reported "created" and a bare 4.000 mm when the element was
    a sketch it had not drawn - no share, no warning, in exactly the case users
    hit (reopen yesterday's part). Measured on B30 2026-11-11: the curves INSIDE
    a sketch CAN be measured one by one, so the guard does not need the session
    record at all.

  * catia_surface_engrave reported "Nothing this call created was left behind
    ... Bodies now: 零件几何体" while the point-on-curve element it had added was
    still in the tree, and catia_update_part named that element as what stops
    every later update - 2/2 reproducible. Only BODIES were checked.

The same probes also corrected two statements the package had been shipping:
guide curves may be OPEN (a 50 mm segment removed 19.5829 mm3 of a predicted
19.635), and a curve from catia_gsd_project is not automatically engraveable (a
closed spline's projection refuses).
"""
from __future__ import annotations

import types

import pytest

import catia_mcp.tools.part_design as part_design
from catia_mcp.results import PreconditionRejected
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools


# ── a sketch whose curves can be measured, and one whose cannot ────────


class _Curve:
    """A sketch element: a curve answers StartPoint/EndPoint, a point does not."""

    def __init__(self, name, length=None, construction=False, curve=True):
        self.Name = name
        self.length = length
        self._construction = construction
        if curve:
            self.StartPoint = object()
            self.EndPoint = object()

    @property
    def Construction(self):
        return self._construction


class _Geos:
    def __init__(self, elements):
        self._elements = list(elements)
        self.Count = len(self._elements)

    def Item(self, index):
        return self._elements[index - 1]


class _Part:
    def __init__(self, elements, sketch_name="草图.1"):
        self._sketch_name = sketch_name
        self._geos = _Geos(elements)

    def FindObjectByName(self, name):
        if str(name) == self._sketch_name:
            return types.SimpleNamespace(GeometricElements=self._geos)
        return None

    def CreateReferenceFromObject(self, obj):
        return obj


class _Spa:
    def GetMeasurable(self, reference):
        if reference.length is None:
            raise RuntimeError("方法 Length 失败")
        return types.SimpleNamespace(Length=reference.length)


class _Conn:
    def __init__(self, part):
        self._part = part
        self.active_document = types.SimpleNamespace(
            Name="P.CATPart",
            GetWorkbench=lambda _name: _Spa())

    def ensure_connected(self):
        return None

    def get_active_part(self):
        return self._part


def _tools(elements) -> GSDTools:
    return GSDTools(connection=_Conn(_Part(elements)))


def test_a_rectangle_measures_from_its_four_lines() -> None:
    """B30 2026-11-11: 40x10 rectangle, 100.000 mm by hand, 100.0000 measured -
    the absolute axis refuses and is skipped, not counted as a failure."""
    tools = _tools([_Curve("绝对轴", None, curve=False),
                    _Curve("直线.1", 40.0), _Curve("直线.2", 10.0),
                    _Curve("直线.3", 40.0), _Curve("直线.4", 10.0)])
    measured = tools._sketch_outline_length_mm("草图.1")
    assert measured is not None
    total, read, unread = measured
    assert abs(total - 100.0) < 1e-9
    assert read == 4 and unread == 0


def test_construction_geometry_is_not_part_of_the_outline() -> None:
    """Measured 2026-11-11: a construction line reports Construction=True and a
    length of 10.000 - counting it would make a complete projection look
    incomplete, which is a warning on a good shape."""
    tools = _tools([_Curve("直线.1", 40.0),
                    _Curve("直线.2", 10.0, construction=True)])
    total, read, unread = tools._sketch_outline_length_mm("草图.1")
    assert total == 40.0
    assert read == 1 and unread == 0


def test_a_spline_and_its_control_points() -> None:
    """B30 2026-11-11: an open 4-point spline measured 73.7960 while its four
    control points refused - the points must not be counted as failures."""
    tools = _tools([_Curve("绝对轴", None, curve=False)]
                   + [_Curve(f"点.{i}", None, curve=False) for i in range(1, 5)]
                   + [_Curve("样条线.1", 73.796)])
    total, read, unread = tools._sketch_outline_length_mm("草图.1")
    assert abs(total - 73.796) < 1e-9
    assert read == 1 and unread == 0


def test_a_curve_that_will_not_measure_is_counted_not_hidden() -> None:
    """An unread curve makes the total a LOWER bound and the share an UPPER
    bound - the direction that hides a fragment, so it is counted."""
    tools = _tools([_Curve("直线.1", 40.0), _Curve("样条线.1", None)])
    total, read, unread = tools._sketch_outline_length_mm("草图.1")
    assert total == 40.0
    assert read == 1 and unread == 1


def test_a_sketch_with_no_measurable_curve_answers_nothing() -> None:
    tools = _tools([_Curve("绝对轴", None, curve=False),
                    _Curve("点.1", None, curve=False)])
    assert tools._sketch_outline_length_mm("草图.1") is None


def test_a_name_that_is_not_a_sketch_answers_nothing() -> None:
    tools = _tools([_Curve("直线.1", 40.0)])
    assert tools._sketch_outline_length_mm("不存在") is None


# ── the source length: exact, partial, or unknown ─────────────────────


def test_a_sketch_source_is_exact_when_every_curve_answers() -> None:
    tools = _tools([_Curve("直线.1", 40.0), _Curve("直线.2", 10.0)])
    total, origin, exact = tools._source_length_mm("草图.1")
    assert total == 50.0
    assert "sketch" in origin and "2 curves" in origin
    assert exact is True


def test_a_sketch_source_is_NOT_exact_when_a_curve_refuses() -> None:
    tools = _tools([_Curve("直线.1", 40.0), _Curve("样条线.1", None)])
    total, _origin, exact = tools._source_length_mm("草图.1")
    assert total == 40.0
    assert exact is False          # a lower bound, and the caller must say so


def test_a_source_that_cannot_be_read_at_all_is_unknown() -> None:
    tools = _tools([_Curve("绝对轴", None, curve=False)])
    tools.conn.get_active_part = lambda: _Part([])  # type: ignore[method-assign]
    total, origin, exact = tools._source_length_mm("草图.1")
    assert total is None and origin == "" and exact is False


# ── what catia_gsd_project says in each of the three cases ────────────


def _project_tools(source, exact, projected, element="草图.45"):
    tools = GSDTools(connection=None)
    tools._ref = lambda name, *a, **k: None  # type: ignore[method-assign]
    tools._factory = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AddNewProject=lambda a, b: types.SimpleNamespace())
    tools._get_geoset = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AppendHybridShape=lambda shape: None)
    tools._completed = lambda shape, name, message, geoset: message  # type: ignore[method-assign]
    tools._finish = lambda shape, name, message: message  # type: ignore[method-assign]
    tools._curve_length_mm = lambda reference: projected  # type: ignore[method-assign]
    tools._source_length_mm = lambda name: (source, "measured on the sketch's 2 curves",
                                            exact)  # type: ignore[method-assign]
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: types.SimpleNamespace(
            CreateReferenceFromObject=lambda obj: obj,
            FindObjectByName=lambda name: None))   # so the probe reads "gone"
    return tools


def test_exact_source_reports_the_share_and_no_warning_when_whole() -> None:
    reply = _project_tools(50.0, True, 50.0)._project(
        {"element": "草图.45", "support": "Wall"})
    assert "100.0 % of the 50.000 mm" in reply
    assert "WARNING" not in reply


def test_exact_source_warns_when_only_part_landed() -> None:
    reply = _project_tools(63.96, True, 4.0)._project(
        {"element": "草图.45", "support": "Wall"})
    assert reply.startswith(" WARNING")
    assert "6.3 %" in reply and "MINIMUM" in reply


def test_partial_source_reports_a_CEILING_not_a_measurement() -> None:
    """The 2026-11-11 failure mode: without a source length the reply said
    "created" and nothing else. With a partial one it can only bound the share
    from above, and an upper bound hides fragments - so it warns."""
    reply = _project_tools(40.0, False, 8.0)._project(
        {"element": "草图.45", "support": "Wall"})
    assert reply.startswith(" WARNING")
    assert "at most 20.0 %" in reply
    assert "LOWER bound" in reply


def test_unreadable_source_says_it_cannot_tell() -> None:
    reply = _project_tools(None, False, 4.0)._project(
        {"element": "草图.45", "support": "Wall"})
    assert reply.startswith(" WARNING")
    assert "COMPLETENESS UNKNOWN" in reply
    assert "catia_surface_engrave" in reply


def test_an_unmeasurable_projection_also_warns() -> None:
    reply = _project_tools(50.0, True, None)._project(
        {"element": "草图.45", "support": "Wall"})
    assert "COMPLETENESS UNKNOWN" in reply


# ── the point that stayed behind ──────────────────────────────────────


class _PointFactory:
    def __init__(self, point):
        self._point = point

    def AddNewPointOnCurveFromPercent(self, curve, position, orientation):
        return self._point

    def AddNewPlaneNormal(self, curve, point):
        return "PLANE"


class _PlanePart:
    """A part where the point is added and then refuses to update."""

    def __init__(self, point_removed):
        self.point_removed = point_removed
        self.updated = []

    def UpdateObject(self, obj):
        raise RuntimeError("方法 Update 失败")

    def FindObjectByName(self, name):
        if str(name) == "C_at0.5":
            return None if self.point_removed else types.SimpleNamespace(Name=name)
        return None

    def CreateReferenceFromObject(self, obj):
        return obj


def _plane_tools(point_removed: bool):
    part = _PlanePart(point_removed)
    point = types.SimpleNamespace(Name="C_at0.5")
    conn = types.SimpleNamespace(ensure_connected=lambda: None,
                                 get_active_part=lambda: part)
    tools = GSDTools(connection=conn)
    tools._factory = lambda: _PointFactory(point)  # type: ignore[method-assign]
    tools._get_geoset = lambda: types.SimpleNamespace(  # type: ignore[method-assign]
        AppendHybridShape=lambda shape: None)
    tools._ref = lambda name, *a, **k: "REF"  # type: ignore[method-assign]
    tools._on_curve_note = lambda *a, **k: ""  # type: ignore[method-assign]
    tools._free_name = lambda part, wanted: ("C_at0.5", True)  # type: ignore[method-assign]
    tools._drop_element = lambda shape: None  # type: ignore[method-assign]
    return tools, part


def test_a_refused_point_is_taken_back_out_before_the_error_is_raised() -> None:
    tools, _part = _plane_tools(point_removed=True)
    with pytest.raises(PreconditionRejected) as caught:
        tools._plane_normal_to_curve({"curve": "C", "position": 0.5})
    error = caught.value
    assert getattr(error, "code", "") == "POINT_ON_CURVE_FAILED"
    assert "removed again" in str(error)
    assert getattr(error, "modified", True) is False


def test_a_point_that_will_not_go_says_so_and_is_not_called_clean() -> None:
    """The 2026-11-11 P0: 'Nothing this call created was left behind' was said
    over an element that stopped every later update."""
    tools, _part = _plane_tools(point_removed=False)
    with pytest.raises(PreconditionRejected) as caught:
        tools._plane_normal_to_curve({"curve": "C", "position": 0.5})
    error = caught.value
    assert "COULD NOT BE REMOVED" in str(error)
    assert getattr(error, "modified", False) is True


# ── the cleanup: names are looked up again, and the tree must update ──


class _CleanupPart(_Part):
    def __init__(self, elements, leftover=()):
        super().__init__(elements)
        self.leftover = set(leftover)
        self.Bodies = types.SimpleNamespace(Count=0)

    def FindObjectByName(self, name):
        return (types.SimpleNamespace(Name=name) if str(name) in self.leftover
                else None)

    def Update(self):
        return None


class _NoopPD:
    def execute(self, name, args):
        return "ok"


def _cleanup_tools(leftover=(), culprits=None):
    part = _CleanupPart([], leftover)
    tools = GSDTools(connection=_Conn(part))
    tools._tree_update_culprits = lambda p: culprits  # type: ignore[method-assign]
    return tools


@pytest.fixture(autouse=False)
def noop_pd(monkeypatch):
    monkeypatch.setattr(part_design, "PartDesignTools", lambda conn: _NoopPD())


def test_a_clean_tree_is_reported_with_the_update_that_vouches_for_it(noop_pd):
    note = _cleanup_tools(culprits=None)._engrave_cleanup(["A_at0.5"], None)
    assert "Nothing this call created was left behind" in note
    assert "the part still updates" in note


def test_an_element_that_survives_its_delete_is_named(noop_pd):
    note = _cleanup_tools(leftover={"A_at0.5"}, culprits=None)._engrave_cleanup(
        ["A_at0.5"], None)
    assert "STILL in the tree" in note and "A_at0.5" in note
    assert "Nothing this call created was left behind" not in note


def test_a_tree_that_will_not_update_is_never_called_clean(noop_pd):
    note = _cleanup_tools(culprits=["A_at0.5"])._engrave_cleanup(["A_at0.5"], None)
    assert "no longer updates" in note
    assert "'A_at0.5' is the element that fails on its own" in note
    assert "Nothing this call created was left behind" not in note


def test_an_unnameable_update_failure_is_still_declared(noop_pd):
    note = _cleanup_tools(culprits=[])._engrave_cleanup([], None)
    assert "no single element reproduces it" in note


def test_a_body_that_was_never_made_is_not_called_consumed(noop_pd):
    """A refusal before catia_new_body leaves no cutter, so "the boolean
    consumed it" would explain away a body that simply does not exist."""
    note = _cleanup_tools(culprits=None)._engrave_cleanup(
        [], "Engrave_X", body_was_created=False)
    assert "consumed" not in note
    assert "Nothing this call created was left behind" in note


def test_the_culprit_walk_is_skipped_when_the_part_updates() -> None:
    calls = []

    class _Updating(_CleanupPart):
        def Update(self):
            calls.append("update")

    part = _Updating([])
    tools = GSDTools(connection=_Conn(part))
    assert tools._tree_update_culprits(part) is None
    assert calls == ["update"]


# ── the two statements this version had to correct ────────────────────


def test_the_ceiling_allows_an_open_guide_because_it_was_measured() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "engraving" in e["member"])
    detail = entry["detail"]
    assert "GUIDE MAY BE OPEN" in detail
    assert "19.5829" in detail and "2026-11-11" in detail


def test_the_ceiling_lists_which_curves_carry_the_parameter() -> None:
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "engraving" in e["member"])
    detail = entry["detail"]
    for said in ("gsd_line YES", "OPEN", "CLOSED gsd_spline NO", "SKETCH"):
        assert said in detail, said


def test_the_reopen_claim_is_marked_seen_once_not_reproduced() -> None:
    """0.3.105 withdraws the "same session" advice 0.3.101 published.

    Five controlled cells on two geometries - 376.9911 mm3 before and after a
    reopen on a pad with a line on it, 7.9958 mm3 before and after on a lofted
    bottle with a projected glyph - could not reproduce the failure the advice was
    based on, and part.Update() first changed nothing either. A ceiling that keeps
    saying "unknown, so keep them in one session" is a claim without a measurement.
    """
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "saved, closed and reopened" in e["member"])
    assert entry["status"] == "SEEN_ONCE_NOT_REPRODUCED"
    detail = entry["detail"]
    for said in ("9.991", "376.9911", "7.9958", "WITHDRAWN", "NOT REPRODUCED"):
        assert said in detail, said


def test_the_only_mention_of_one_session_is_the_retraction() -> None:
    """Quoting a withdrawn instruction is fine; leaving it standing is not."""
    for entry in _CAPABILITY_CEILINGS:
        if "SAME session" in entry["detail"]:
            assert "WITHDRAWN" in entry["detail"], entry["member"]


def test_the_engrave_schema_names_the_curves_it_accepts() -> None:
    tools = GSDTools(connection=None)
    schema = next(d for d in tools.get_tool_definitions()
                  if d["name"] == "catia_surface_engrave")
    curve = schema["inputSchema"]["properties"]["curve"]["description"]
    assert "OPEN gsd_spline" in curve
    assert "closed spline" in curve
