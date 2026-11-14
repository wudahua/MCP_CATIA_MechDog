"""0.3.126: elliptic sweep sections, plan pre-checks and rollback, clean screenshots.

No CATIA needed. The COM-facing halves were measured on B30 the same day
(scripts/b30_v0428_audit.py, b30_v0428_audit_clean.py); what is tested here is the
part that decides, so it cannot drift silently.

The three issues are the 2026-11-14 report's P0-4, P1-1 and P1-3.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.measurement import _hide_body_sketches
from catia_mcp.tools.part_design import PartDesignTools, _material_names
from catia_mcp.tools.planner import PlannerTools
from catia_mcp.tools.sketcher import SketcherTools


def _bound_planner() -> PlannerTools:
    """A planner with the three tools whose PRE_CHECKS are under test.

    Bound rather than bare: an unbound planner answers "no problems" by design
    (there is nothing to ask), so testing against it would prove nothing.
    """
    planner = PlannerTools(connection=None)
    planner.bind({
        "catia_sketch_ellipse": SketcherTools(connection=None),
        "catia_solid_sweep_variable": GSDTools(connection=None),
        "catia_solid_loft": PartDesignTools(connection=None),
        "catia_pad": PartDesignTools(connection=None),
    })
    return planner


# ── tiny stand-ins for the COM collections ──────────────────────────────────


class _Collection:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index: int):
        return self._items[index - 1]


class _Node:
    def __init__(self, name: str):
        self.Name = name


def _body(shapes=(), sketches=(), hybrids=()):
    return SimpleNamespace(Shapes=_Collection([_Node(n) for n in shapes]),
                           Sketches=_Collection([_Node(n) for n in sketches]),
                           HybridShapes=_Collection([_Node(n) for n in hybrids]))


def _part(bodies=(), sets=()):
    return SimpleNamespace(Bodies=_Collection(list(bodies)),
                           HybridBodies=_Collection(list(sets)))


def _conn(part):
    return SimpleNamespace(get_active_part=lambda: part)


# ── P1-1: semantic pre-checks ───────────────────────────────────────────────


def test_the_ellipse_rule_the_report_died_on_is_refused_before_step_one():
    """The report's exact numbers: major=5.2, minor=5.8, at step 10 of 21."""
    planner = _bound_planner()
    problems = planner._semantic_problems([
        {"tool": "catia_sketch_ellipse", "args": {"major": 5.2, "minor": 5.8}}])

    assert len(problems) == 1
    assert problems[0]["step"] == 1
    assert problems[0]["code"] == "BAD_ARGUMENT"
    assert "shorter than minor" in problems[0]["message"]


def test_a_good_ellipse_passes_the_same_check():
    planner = _bound_planner()
    assert planner._semantic_problems([
        {"tool": "catia_sketch_ellipse", "args": {"major": 5.8, "minor": 5.2}},
        {"tool": "catia_sketch_ellipse", "args": {"major": 4, "minor": 4}},
    ]) == []


def test_a_non_numeric_axis_is_left_to_the_schema():
    """Saying a fault twice reads as two faults - the schema already names this."""
    planner = _bound_planner()
    assert planner._semantic_problems([
        {"tool": "catia_sketch_ellipse", "args": {"major": "wide",
                                                  "minor": 5.0}}]) == []


def test_a_bad_station_table_is_refused_by_the_planner_too():
    planner = _bound_planner()
    problems = planner._semantic_problems([
        {"tool": "catia_solid_sweep_variable",
         "args": {"spine": "Spine", "sections": [[0, 0.5], [0.4, 8], [0.2, 3]]}}])

    assert len(problems) == 1
    # The tool's own code, not a planner-invented one: a dry run that answers a
    # different code from the run is the thing a dry run must not do.
    assert problems[0]["code"] == "T_NOT_INCREASING"


def test_an_elliptic_station_table_passes():
    planner = _bound_planner()
    assert planner._semantic_problems([
        {"tool": "catia_solid_sweep_variable",
         "args": {"spine": "S", "sections": [[0, 5.4, 4.8], [1, 2.7, 2.4]],
                  "major_axis": [1, 0, 0]}}]) == []


def test_the_zero_major_axis_is_refused():
    planner = _bound_planner()
    problems = planner._semantic_problems([
        {"tool": "catia_solid_sweep_variable",
         "args": {"spine": "S", "sections": [[0, 5], [1, 2]],
                  "major_axis": [0, 0, 0]}}])
    assert problems and problems[0]["code"] == "BAD_MAJOR_AXIS"


def test_a_one_section_loft_is_refused():
    planner = _bound_planner()
    problems = planner._semantic_problems([
        {"tool": "catia_solid_loft", "args": {"sections": ["Sketch.1"]}}])
    assert problems and problems[0]["code"] == "NOT_ENOUGH_SECTIONS"


def test_a_tool_without_pre_checks_is_not_pretended_to_be_checked():
    planner = _bound_planner()
    assert planner._semantic_problems([
        {"tool": "catia_pad", "args": {"depth": -1}}]) == []


def test_the_precheck_names_point_at_real_methods():
    """A typo in PRE_CHECKS would silently disable the check, which is worse than
    having none: dry_run would keep answering PLAN_VALID."""
    for owner in (GSDTools, PartDesignTools):
        for tool, method in getattr(owner, "PRE_CHECKS", {}).items():
            assert callable(getattr(owner, method)), f"{owner.__name__}.{method}"
    for tool, method in SketcherTools.PRE_CHECKS.items():
        assert callable(getattr(SketcherTools, method))


def test_the_ellipse_check_is_the_very_one_the_tool_uses():
    """Both paths must refuse with one voice, or the dry run is a lie."""
    from catia_mcp.tools.sketcher import SketcherTools

    with pytest.raises(Exception) as from_tool:
        SketcherTools._check_ellipse_axes(5.2, 5.8)
    with pytest.raises(Exception) as from_planner:
        SketcherTools(connection=None)._precheck_ellipse({"major": 5.2,
                                                          "minor": 5.8})
    assert str(from_tool.value) == str(from_planner.value)
    assert getattr(from_tool.value, "code", "") == "BAD_ARGUMENT"


# ── P1-1: what the failed plan built, and taking it back ────────────────────


def test_the_snapshot_names_everything_a_plan_could_have_built():
    part = _part(
        bodies=[_body(shapes=["Pad.1"], sketches=["草图.1"],
                      hybrids=["Datum_xy_20", "Pad.1"])],
        sets=[SimpleNamespace(Name="GSD_Set",
                              HybridShapes=_Collection([_Node("Spine")]),
                              HybridSketches=_Collection([]),
                              HybridBodies=_Collection([]))])
    names = PlannerTools(_conn(part))._snapshot_names()

    assert set(names) == {"Pad.1", "草图.1", "Datum_xy_20", "GSD_Set", "Spine"}


def test_the_snapshot_is_none_when_there_is_no_part():
    conn = SimpleNamespace(get_active_part=lambda: (_ for _ in ()).throw(
        RuntimeError("no part")))
    assert PlannerTools(conn)._snapshot_names() is None


def test_created_since_lists_only_the_new_names_in_tree_order():
    before = _part(bodies=[_body(shapes=["Pad.1"])])
    after = _part(bodies=[_body(shapes=["Pad.1", "Pad.2", "Pocket.3"])])
    planner = PlannerTools(_conn(after))
    assert planner._created_since(
        PlannerTools._snapshot_names(PlannerTools(_conn(before)))) == [
        "Pad.2", "Pocket.3"]


def test_rollback_deletes_newest_first_and_verifies_from_the_tree():
    """Newest first is the only order that works: a solid cannot go before the
    sections it was built from."""
    deleted: list[list[str]] = []

    class _Deleter:
        def execute(self, tool, args):
            deleted.append(list(args["names"]))
            return "deleted"

    tree = {"Pad.1", "Sketch.1", "Pad.2"}

    def current():
        # The stand-in honours the order: everything the deleter was given is
        # gone afterwards, so the tree read-back is what proves it.
        remaining = _part(bodies=[_body(shapes=[name for name in
                                                ["Pad.1", "Sketch.1", "Pad.2"]
                                                if name in tree])])
        return remaining

    planner = PlannerTools(connection=None)
    planner.bind({"catia_delete_feature": _Deleter()})
    planner._snapshot_names = lambda: {
        name: index for index, name in enumerate(["Pad.1", "Sketch.1", "Pad.2"])
        if name in tree}

    report = planner._rollback(["Sketch.1", "Pad.2"])
    assert deleted == [["Pad.2", "Sketch.1"]]
    # The stub never removes anything from `tree`, so nothing counts as deleted
    # and everything is reported as left - the point is that the answer comes
    # from the read-back, not from the deleter's own "deleted".
    assert report["verified"] is True
    assert report["deleted"] == []
    assert report["left"] == ["Pad.2", "Sketch.1"]
    assert current() is not None


def test_rollback_says_unverified_when_the_tree_cannot_be_read():
    class _Deleter:
        def execute(self, tool, args):
            return "deleted"

    planner = PlannerTools(connection=None)
    planner.bind({"catia_delete_feature": _Deleter()})
    planner._snapshot_names = lambda: None

    report = planner._rollback(["A", "B"])
    assert report["verified"] is False
    assert report["deleted"] == []


def test_rollback_of_nothing_is_verified_and_empty():
    report = PlannerTools(connection=None)._rollback([])
    assert report == {"requested": 0, "names": [], "deleted": [], "left": [],
                      "verified": True, "error": None}


# ── P1-3: clean modes ───────────────────────────────────────────────────────


def test_clean_true_keeps_its_old_meaning():
    """A plan written for 0.3.125 must not start hiding sketches on its own."""
    assert ExportTools._clean_mode(True) == "sets"
    assert ExportTools._clean_mode("solID_only") == "solid_only"
    assert ExportTools._clean_mode(False) == ""
    assert ExportTools._clean_mode(None) == ""


def test_an_unknown_clean_mode_is_refused_with_the_two_that_exist():
    with pytest.raises(Exception) as caught:
        ExportTools._clean_mode("everything")
    assert getattr(caught.value, "code", "") == "BAD_CLEAN_MODE"
    assert "solid_only" in str(caught.value)


def test_the_body_sketches_are_hidden_and_recorded_for_restore():
    hidden: list[str] = []

    class _Selection:
        def Clear(self):
            pass

        def Add(self, node):
            self._node = node

        class _Vis:
            def __init__(self, outer):
                self._outer = outer

            def GetShow(self):
                return 0

            def SetShow(self, value):
                hidden.append((self._outer._node.Name, value))

        @property
        def VisProperties(self):
            return _Selection._Vis(self)

    part = _part(bodies=[_body(shapes=["Pad.1"], sketches=["草图.1", "草图.2"])])
    records, info = _hide_body_sketches(part, _Selection())

    assert info["sketch_hidden"] == 2
    assert [name for name, _value in hidden] == ["草图.1", "草图.2"]
    # 1 = hidden in CATIA's convention (0 shown), which is also what the restore
    # writes back - getting this backwards would leave the sketches hidden.
    assert all(value == 1 for _name, value in hidden)
    assert [len(record) for record in records] == [2, 2]


def test_body_hybrid_shapes_are_never_touched_by_the_sketch_pass():
    """The measured failure: body.HybridShapes holds the SOLID, and hiding it
    emptied the viewport (B30, 2026-11-14)."""
    touched: list[str] = []

    class _Selection:
        def Clear(self):
            pass

        def Add(self, node):
            touched.append(node.Name)

        @property
        def VisProperties(self):
            class _Vis:
                def GetShow(self):
                    return 0

                def SetShow(self, value):
                    pass
            return _Vis()

    part = _part(bodies=[_body(shapes=["Body"], sketches=["草图.1"],
                               hybrids=["Datum_xy_20", "Body"])])
    _hide_body_sketches(part, _Selection())
    # The read and the write each Add the node, so the sketch appears twice;
    # what matters is that no HybridShapes name ever reaches the selection.
    assert set(touched) == {"草图.1"}


def test_material_names_are_the_body_shapes():
    body = _body(shapes=["Pad.1", "Pocket.2"], hybrids=["Datum_xy_20", "Pad.1"])
    assert _material_names(body) == {"Pad.1", "Pocket.2"}
    assert _material_names(SimpleNamespace()) == set()


# ── P0-4: the elliptic station, end to end in arithmetic ────────────────────


def test_a_circle_station_and_an_ellipse_station_can_be_neighbours():
    stations = GSDTools._sweep_stations([[0, 4], [0.5, 3, 6], [1, 2]])
    assert stations == [(0.0, 4.0, 4.0), (0.5, 3.0, 6.0), (1.0, 2.0, 2.0)]
    # Every station is positive, and the volume formula is defined for both.
    assert GSDTools._swept_volume(stations, 30.0) > 0


def test_the_volume_formula_refuses_nothing_and_matches_a_cylinder():
    """Sanity on the one case with an obvious answer: an elliptical prism."""
    stations = GSDTools._sweep_stations([[0, 5, 3], [1, 5, 3]])
    assert GSDTools._swept_volume(stations, 20.0) == pytest.approx(
        math.pi * 5 * 3 * 20, rel=1e-12)
