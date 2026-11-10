"""0.3.93: the three ways this server used to break a model and say it hadn't.

A 2026-11-10 report (hand-built bottle, ~200 calls) hit all three in one session:

  * `catia_boolean(remove)` answered success on a tool body that never reached
    the material, and the part was broken afterwards - `catia_update_part` then
    answered "方法 Update 失败"
  * `catia_list_bodies` kept answering with the last valid volume and face count
    while the tree was in error, so the fake success looked confirmed
  * `catia_delete_feature` said "features that depended on it are now in error"
    and named none of them; the real culprit was a projection inside a
    geometrical set, which the culprit probe never visited

The first two are code-level and reproducible with doubles, which is what this
file does. The probe is now shared (catia_mcp/update_errors.py) and covers both
trees, so the same fix serves `catia_update_part`, `catia_delete_feature` and the
boolean's own verification.
"""
from __future__ import annotations

import json
import types

import pytest

from catia_mcp.results import CodedError, FeatureRejected

from test_modelling_gaps import FakeSolidBody, _pd_with_bodies


# ── the probe sees both trees ──────────────────────────────────────────


def _element(name, fails):
    def update():
        if fails:
            raise RuntimeError("(0, 'CATIAPart', '方法 UpdateObject 失败')")
    return types.SimpleNamespace(Name=name, update_marker=update)


class _Shapes:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index - 1]


def _part_with(body_features, set_elements):
    """A part whose UpdateObject fails for the elements named in the sets."""
    body = types.SimpleNamespace(Name="PartBody",
                                 Shapes=_Shapes([_element(n, f) for n, f
                                                 in body_features]))
    hybrid_shapes = _Shapes([_element(n, f) for n, f in set_elements])
    hybrid = types.SimpleNamespace(Name="GSD_Set",
                                   HybridShapes=hybrid_shapes,
                                   HybridBodies=_Shapes([]))

    part = types.SimpleNamespace(
        Bodies=_Shapes([body]),
        HybridBodies=_Shapes([hybrid]))

    def update_object(element):
        element.update_marker()
    part.UpdateObject = update_object
    return part


def test_the_probe_walks_geometrical_sets_too():
    """The 2026-11-10 culprit was `图案_投影` in GSD_Set. The old walk only knew
    bodies[].Shapes, so it reported "no single feature reproduces it" about an
    element it had never asked."""
    from catia_mcp.update_errors import failing_elements

    part = _part_with([("凸台.1", False)], [("图案_投影", True)])
    assert failing_elements(part) == ["图案_投影"]

    # ...and nested sets, which is where a projection often ends up.
    nested = types.SimpleNamespace(Name="Sub_Set",
                                   HybridShapes=_Shapes([_element("Extract.4", True)]),
                                   HybridBodies=_Shapes([]))
    part.HybridBodies.Item(1).HybridBodies = _Shapes([nested])
    assert set(failing_elements(part)) == {"图案_投影", "Extract.4"}


def test_every_culprit_is_named_not_just_the_first():
    from catia_mcp.update_errors import describe, failing_elements

    part = _part_with([("图案_刻蚀", True)], [("图案_投影", True)])
    culprits = failing_elements(part)
    assert culprits == ["图案_刻蚀", "图案_投影"]
    assert describe(culprits).startswith("2 elements fail on their own")


# ── catia_update_part ──────────────────────────────────────────────────


def _measurement_tools(part):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: part,
        refresh_display=lambda: None)
    return tools


def test_update_part_names_the_gsd_culprit():
    part = _part_with([("凸台.1", False)], [("图案_投影", True)])

    def broken_update():
        raise RuntimeError("(0, 'CATIAPart', '方法 Update 失败')")
    part.Update = broken_update

    with pytest.raises(RuntimeError) as caught:
        _measurement_tools(part)._update_part()

    text = str(caught.value)
    assert "'图案_投影' is the element that fails on its own" in text
    assert "catia_delete_feature(feature_name='图案_投影')" in text


def test_a_successful_update_clears_the_error_flag():
    part = _part_with([("凸台.1", False)], [])
    part.Update = lambda: None
    tools = _measurement_tools(part)
    tools.conn.part_in_error = "old failure"

    assert "updated successfully" in tools._update_part()
    assert tools.conn.part_in_error is None


# ── catia_list_bodies ──────────────────────────────────────────────────


def test_list_bodies_marks_stale_numbers_after_a_failed_update():
    """168607.5 mm3 twice, on a part whose update had already failed: CATIA does
    not flag it, so the server has to."""
    tools, conn, part = _pd_with_bodies(["PartBody"])
    conn.part_in_error = "(0, 'CATIAPart', '方法 Update 失败')"
    conn.hso = types.SimpleNamespace(Clear=lambda: None, Add=lambda o: None,
                                     Count=0)

    payload = json.loads(tools._list_bodies())

    assert payload["stale"] is True
    assert "方法 Update 失败" in payload["stale_because"]
    assert "catia_update_part" in payload["stale_note"]
    assert list(payload)[0] == "stale"          # before the numbers it qualifies


def test_list_bodies_is_silent_when_the_part_is_healthy():
    tools, conn, part = _pd_with_bodies(["PartBody"])
    conn.hso = types.SimpleNamespace(Clear=lambda: None, Add=lambda o: None,
                                     Count=0)
    assert "stale" not in json.loads(tools._list_bodies())


# ── catia_boolean ──────────────────────────────────────────────────────


def _boolean_harness(volumes):
    tools, conn, part = _pd_with_bodies(["PartBody", "Tool"])
    conn.active_body = part.Bodies.Item("PartBody")
    made = []

    class FakeFeature:
        def __init__(self):
            self.Name = ""
            part.Shapes_removed = True

    class FakeSF:
        def AddNewRemove(self, body):
            feature = FakeFeature()
            made.append(feature)
            return feature

        def AddNewIntersect(self, body):
            feature = FakeFeature()
            made.append(feature)
            return feature

    part.ShapeFactory = FakeSF()
    part.InWorkObject = None
    part.CreateReferenceFromObject = lambda o: o
    part.UpdateObject = lambda f: None
    part.Update = lambda: None

    readings = iter(volumes)
    tools._body_volume_mm3 = lambda part_, body_: next(readings)
    removed = []
    tools._remove_feature = lambda p, f, b: removed.append(f) or True
    return tools, part, made, removed


def test_a_remove_that_changed_nothing_is_refused():
    """The reported fake success: two attempts, volume 168607.5 both times, and
    "Boolean 'remove': ... feature '图案_刻蚀'" as the answer."""
    tools, part, made, removed = _boolean_harness([168607.5, 168607.5])

    with pytest.raises(FeatureRejected) as caught:
        tools._boolean({"operation": "remove", "tool_body": "Tool",
                        "target_body": "PartBody"})

    error = caught.value
    assert error.code == "NO_MATERIAL"
    assert error.rolled_back is True
    assert error.modified is False
    assert error.data["volume_before_mm3"] == 168607.5
    assert error.data["volume_after_mm3"] == 168607.5
    assert "removed no material" in str(error)
    assert "does not overlap" in str(error)
    # The dead node does not stay in the tree.
    assert removed == made


def test_a_remove_that_bit_reports_both_volumes():
    tools, part, made, removed = _boolean_harness([168902.1, 168607.5])

    reply = tools._boolean({"operation": "remove", "tool_body": "Tool",
                            "target_body": "PartBody"})

    assert "168902.1 -> 168607.5 mm3" in reply
    assert removed == []


def test_an_intersect_that_changes_nothing_is_not_called_a_failure():
    """A superset tool leaves the volume unchanged and that is correct - only
    'remove' is judged by the delta."""
    tools, part, made, removed = _boolean_harness([100.0, 100.0])

    reply = tools._boolean({"operation": "intersect", "tool_body": "Tool",
                            "target_body": "PartBody"})

    assert "Boolean 'intersect'" in reply
    assert removed == []


def test_a_boolean_that_breaks_the_part_is_not_reported_as_success():
    """UpdateObject accepted the feature; the TREE does not update. The old code
    never asked, so the next call was where the caller found out."""
    tools, part, made, removed = _boolean_harness([100.0, 90.0])

    def broken_update():
        raise RuntimeError("(0, 'CATIAPart', '方法 Update 失败')")
    part.Update = broken_update

    with pytest.raises(FeatureRejected) as caught:
        tools._boolean({"operation": "remove", "tool_body": "Tool",
                        "target_body": "PartBody"})

    assert caught.value.code == "PART_UPDATE_FAILED"
    assert caught.value.rolled_back is False       # left in place on purpose
    assert "no longer updates" in str(caught.value)
    assert "stale" in str(caught.value)
    assert tools.conn.part_in_error                # list_bodies will say so


# ── catia_delete_feature ───────────────────────────────────────────────


def test_delete_feature_names_the_causalities():
    """The sentence was "features that depended on it are now in error", with no
    name in it - the reporter found the culprit by deleting elements one by one."""
    from catia_mcp.tools import part_design as module

    tools, conn, part = _pd_with_bodies(["PartBody"])
    calls = {"n": 0}

    def broken_update():
        calls["n"] += 1
        raise RuntimeError("(0, 'CATIAPart', '方法 Update 失败')")
    part.Update = broken_update
    part.UpdateObject = lambda element: None

    victim = types.SimpleNamespace(Name="图案_刻蚀")
    tools._find_in_geometrical_sets = lambda p, target: (None, "", [])
    body = part.Bodies.Item(1)
    body.Shapes = type("S", (), {
        "Count": 1,
        "Item": lambda self, i: types.SimpleNamespace(Name="图案_刻蚀") if i == 1
                else None})()
    tools._sketch_names = lambda: []
    tools._find_in_geometrical_sets = lambda p, target: (victim, "feature", [])
    tools._map_geometrical_sets = lambda p, names: {}
    conn.hso = types.SimpleNamespace(Clear=lambda: None, Add=lambda o: None,
                                     Delete=lambda: None)
    tools.conn.owned_by = lambda name: []

    # The probe is what the reply leans on; give it one GSD culprit.
    culprits = {"value": ["图案_投影"]}
    tools._note_part_error = lambda message: setattr(conn, "part_in_error", message)
    module_failing = lambda p, limit=8: culprits["value"]

    import catia_mcp.tools.part_design as pd
    original = pd.failing_elements
    pd.failing_elements = module_failing
    try:
        reply = tools._delete_feature({"names": ["图案_刻蚀"]})
    finally:
        pd.failing_elements = original

    assert "'图案_投影' is the element that fails on its own" in reply
    assert "catia_delete_feature" in reply
    assert conn.part_in_error


# ── the rebuild does not need a healthy part ───────────────────────────


def test_a_lost_sketch_is_rebuilt_even_when_the_part_cannot_update():
    """`part.Update()` was the rebuild's last step, on a part that had just failed
    a feature - which is why "it could NOT be re-created: 方法 Update 失败" was
    reported about drawing calls that had all worked."""
    from catia_mcp.tools.sketcher import SketcherTools

    events = []

    class FakeSketch:
        Name = "草图.38"

        def OpenEdition(self):
            events.append("open")
            return types.SimpleNamespace()

        def CloseEdition(self):
            events.append("close")

    class Sketches:
        Count = 0

        def Add(self, support):
            events.append("add")
            return FakeSketch()

    body = types.SimpleNamespace(Name="PartBody", Sketches=Sketches())
    part = types.SimpleNamespace()

    def broken_update():
        raise RuntimeError("(0, 'CATIAPart', '方法 Update 失败')")
    part.Update = broken_update
    part.UpdateObject = lambda element: events.append("update-object")

    tools = SketcherTools(connection=types.SimpleNamespace(
        active_document=types.SimpleNamespace(Name="P.CATPart"),
        sketch_outlines={"P.CATPart::草图.37": {
            "placement": {"base": "xy"},
            "elements": [{"kind": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0}]}},
        note_sketch_alias=lambda *a, **k: None))
    tools._resolve_support = lambda part_, body_, placement, distance: (
        types.SimpleNamespace(Name="xy"), "")
    tools._replay_element = lambda factory, element: None

    new_name, problem = tools.rebuild_lost_sketch(part, body, "草图.37")

    assert problem == ""
    assert new_name == "草图.38"
    assert "update-object" in events        # the sketch was updated on its own


# ── the arc's angle form obeyed nothing ────────────────────────────────


def _arc_tools():
    from catia_mcp.tools.sketcher import SketcherTools

    class FakeFactory:
        def __init__(self):
            self.calls = []

        def CreateCircle(self, cx, cy, r, start, end):
            self.calls.append((cx, cy, r, start, end))

    tools = SketcherTools(connection=None)
    tools._active_factory = FakeFactory()
    tools._active_sketch = types.SimpleNamespace(Name="Sketch.1")
    tools._ensure_sketch_open = lambda: None
    return tools


def test_angles_given_in_descending_order_sweep_the_short_way():
    """Reported 2026-11-10: "I ask for 90 degrees and keep getting 270, and
    major=true/false makes no difference". CreateCircle only needs start < end,
    so this branch used to wrap every descending pair the long way round."""
    import math

    tools = _arc_tools()
    reply = tools._draw_arc(0, 0, 10, start_angle=90, end_angle=0)

    start, end = tools._active_factory.calls[0][3:]
    assert math.degrees(end - start) == pytest.approx(90, abs=1e-6)
    assert "SHORT way round" in reply


def test_the_angle_form_still_takes_the_long_way_when_asked():
    import math

    tools = _arc_tools()
    reply = tools._draw_arc(0, 0, 10, start_angle=90, end_angle=0, major=True)

    start, end = tools._active_factory.calls[0][3:]
    assert math.degrees(end - start) == pytest.approx(270, abs=1e-6)
    assert "SHORT way round" not in reply


def test_two_equal_angles_are_refused_not_drawn_as_a_full_circle():
    from catia_mcp.results import CodedError

    tools = _arc_tools()
    with pytest.raises(CodedError) as caught:
        tools._draw_arc(0, 0, 10, start_angle=45, end_angle=45)
    assert caught.value.code == "DEGENERATE_ARC"
