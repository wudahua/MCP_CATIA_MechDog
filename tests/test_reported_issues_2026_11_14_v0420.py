"""0.3.118 - batch engrave: many curves, ONE cutter body, ONE boolean.

What the report asked for (2026-11-12, P1-4) was a way to engrave a ten-stroke
word without ten booleans and ten cutter bodies. The route was measured on B30
2026-11-13, three cells, one fresh document each
(scripts/b30_v0420_probe_batch_engrave.py):

    catia_gsd_join on three disjoint tubes   REFUSED 'UpdateObject failed', and
                                            CATIA removed the feature
    three CloseSurfaces in ONE tool body     75.3982 mm3 body, one boolean
                                            removed 37.699 mm3
    three separate engraves (control)        removed 37.698 mm3

So the tests below pin the shape of that decision: one body, one boolean, every
curve resolved before anything is built, and a reply that calls its number a
TOTAL. The volume judgement itself is ``_engrave_outcome``'s, tested elsewhere.
"""

from __future__ import annotations

import pytest

from catia_mcp.results import (ArgumentOutOfRange, ArgumentRejected,
                                PreconditionRejected)
from catia_mcp.tools import part_design as part_design_module
from catia_mcp.tools.gsd import GSDTools


class _Body:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Item:
    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index - 1]


class _Part:
    def __init__(self, bodies=("零件几何体",)) -> None:
        self.Bodies = _Item([_Body(name) for name in bodies])

    def CreateReferenceFromObject(self, obj):
        return obj


class _Conn:
    def __init__(self, bodies=("零件几何体",)) -> None:
        self._part = _Part(bodies)
        self.active_body = self._part.Bodies.Item(1)

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self):
        return self._part

    def get_active_part_body(self):
        return self.active_body


CUTTER = 75.3982236861547          # three 50 mm r=0.4 tubes
BEFORE = 24000.0                   # the 60x40x10 block
AFTER = 23962.30088723369          # the control's own result


class _FakePD:
    """Records what the engrave asks of PartDesignTools.

    ``volumes`` is consumed in call order, so a test states the three numbers the
    real tool would read: the cutter body before the boolean, the target before
    it, and the target after it.
    """

    instances: list["_FakePD"] = []

    def __init__(self, conn) -> None:
        self.conn = conn
        self.calls: list[tuple[str, dict]] = []
        self.volumes: list[float] = []
        self.fail_on: set[str] = set()
        _FakePD.instances.append(self)

    def execute(self, name, args):
        self.calls.append((name, args))
        if name in self.fail_on:
            raise ArgumentRejected(f"{name} refused", code="BOOLEAN_FAILED",
                                   stage="execute")
        if name == "catia_boolean":
            return ("Boolean 'remove': 'Word' into '零件几何体' (feature "
                    "'移除.1'). Volume of '零件几何体': 24000.0 -> 23962.3 mm3.")
        return f"{name} done"

    def _body_volume_mm3(self, part, body):
        if not self.volumes:
            raise RuntimeError("no volume left to hand out")
        return self.volumes.pop(0)


class _BatchTools(GSDTools):
    """The engrave with every COM-touching step replaced by a recording stub."""

    def __init__(self, available=("D1", "D2", "D3"), bodies=("零件几何体",),
                 gap: float = 0.35) -> None:
        super().__init__(connection=_Conn(bodies))
        self.available = set(available)
        self.gap = gap
        self.steps: list[tuple[str, str]] = []
        self.helper_calls: list[tuple[str, bool]] = []
        self.cleanups: list[tuple[list[str], str, bool]] = []
        self.close_failures: set[str] = set()

    # ── the four elements ────────────────────────────────────────────────
    def _ref(self, name):
        if str(name) not in self.available:
            raise ArgumentRejected(
                f"Element '{name}' not found in 'Block.CATPart'.",
                code="ELEMENT_NOT_FOUND", stage="resolve")
        return str(name)

    def _curve_length_mm(self, reference):
        return 50.0

    def _plane_normal_to_curve(self, args):
        self.steps.append(("plane", args["name"]))
        return (f"Plane normal to '{args['curve']}' at 0.5. Its origin "
                f"'{args['curve']}_at0.5' sits on the curve.")

    def _circle(self, args):
        self.steps.append(("circle", args["name"]))

    def _sweep(self, args):
        self.steps.append(("sweep", args["name"]))

    def _close_surface(self, args):
        if args["surface"] in self.close_failures:
            raise ArgumentRejected("the sweep produced an open shell",
                                   code="ENGRAVE_TUBE_NOT_CLOSED",
                                   stage="execute")
        self.steps.append(("close", args["surface"]))

    # ── everything else ─────────────────────────────────────────────────
    def _body_by_name(self, part, name):
        return _Body(str(name))

    def _cutter_gap(self, first, second):
        return self.gap

    def _set_helper_visibility(self, name, show):
        self.helper_calls.append((name, show))

    def _engrave_cleanup(self, created, body_name, body_was_created=True):
        self.cleanups.append((list(created), str(body_name), body_was_created))
        return " Cleanup removed what the call had built."


@pytest.fixture()
def design(monkeypatch):
    """Install the stand-in and hand back the knobs a test turns.

    Keys: ``volumes`` (in call order), ``fail_on`` (tool names that raise) and
    ``made`` (the instances the engrave created).
    """
    setup: dict = {"volumes": [CUTTER, BEFORE, AFTER], "fail_on": set(),
                   "made": []}

    class Fake(_FakePD):
        def __init__(self, conn) -> None:
            _FakePD.__init__(self, conn)
            self.volumes = list(setup["volumes"])
            self.fail_on = set(setup["fail_on"])
            setup["made"].append(self)

    monkeypatch.setattr(part_design_module, "PartDesignTools", Fake)
    return setup


# ── the shape of the call ─────────────────────────────────────────────


def test_three_curves_cost_one_cutter_body_and_one_boolean(design) -> None:
    tools = _BatchTools()
    reply = tools._surface_engrave(
        {"curves": ["D1", "D2", "D3"], "depth": 0.4, "name": "Word"})

    design = design["made"][0]
    names = [name for name, _ in design.calls]
    assert names.count("catia_new_body") == 1
    assert names.count("catia_boolean") == 1
    # The tool body is named once and consumed by that single boolean.
    body = dict(design.calls)["catia_new_body"]["name"]
    assert dict(design.calls)["catia_boolean"]["tool_body"] == body
    assert "3 grooves" in reply and "ONE boolean" in reply


def test_each_curve_gets_its_own_four_elements(design) -> None:
    tools = _BatchTools()
    tools._surface_engrave({"curves": ["D1", "D2"], "depth": 0.4, "name": "W"})

    assert [name for kind, name in tools.steps if kind == "plane"] == ["W1_N", "W2_N"]
    assert [name for kind, name in tools.steps if kind == "circle"] == ["W1_P", "W2_P"]
    assert [name for kind, name in tools.steps if kind == "sweep"] == ["W1_T", "W2_T"]
    assert [name for kind, name in tools.steps if kind == "close"] == ["W1_T", "W2_T"]


def test_the_body_is_made_after_the_tubes_so_a_bad_sweep_leaves_none(design) -> None:
    tools = _BatchTools()
    tools._surface_engrave({"curves": ["D1", "D2"], "depth": 0.4, "name": "W"})

    design = design["made"][0]
    order = [name for name, _ in design.calls]
    # The sweeps are recorded by the stub; what matters is that every tube exists
    # before catia_new_body is asked for a body to put them in.
    assert tools.steps[-1][0] == "close"
    assert order.index("catia_new_body") < order.index("catia_boolean")
    assert len([kind for kind, _ in tools.steps if kind == "sweep"]) == 2


def test_helpers_puts_every_curves_four_elements_out_of_sight(design) -> None:
    tools = _BatchTools()
    tools._surface_engrave({"curves": ["D1", "D2"], "depth": 0.4, "name": "W"})

    hidden = [name for name, show in tools.helper_calls if show is False]
    assert sorted(hidden) == ["D1_at0.5", "D2_at0.5", "W1_N", "W1_P", "W1_T",
                              "W2_N", "W2_P", "W2_T"]


# ── the reply says what one boolean can and cannot know ───────────────


def test_the_reply_calls_its_number_a_total(design) -> None:
    tools = _BatchTools()
    reply = tools._surface_engrave(
        {"curves": ["D1", "D2", "D3"], "depth": 0.4, "name": "Word"})

    assert "37.699 mm3" in reply          # removed
    assert "75.3982 mm3" in reply         # the one cutter body
    assert "150.000 mm of curve" in reply  # three 50 mm traces
    assert "THE NUMBER ABOVE IS A TOTAL" in reply
    assert "curve=)" in reply


def test_the_prediction_comes_from_the_body_so_overlaps_count_once(design) -> None:
    tools = _BatchTools()
    reply = tools._surface_engrave({"curves": ["D1", "D2"], "depth": 0.4,
                                    "name": "W"})
    # 75.3982 / 2 = 37.6991 is what HALF of the measured body predicts. The
    # shortfall note is not raised at ratio 1.00.
    assert "against the 37.699 mm3 the tubes lying half" in reply
    assert "NOTE: removed/predicted" not in reply


def test_the_boolean_feature_is_named_so_the_cut_can_be_taken_back(design) -> None:
    # 56 mm3 removed against 37.699 predicted is ratio 1.49 - the BURIED case,
    # which is where the undo hint has to be there (the cavity is invisible).
    design["volumes"] = [CUTTER, 24000.0, 23944.0]
    reply = _BatchTools()._surface_engrave(
        {"curves": ["D1", "D2"], "depth": 0.4, "name": "W"})

    assert "BURIED" in reply
    assert "catia_delete_feature('移除.1')" in reply


def test_a_single_curve_in_the_list_is_read_as_one_groove(design) -> None:
    tools = _BatchTools()
    reply = tools._surface_engrave({"curves": ["D1"], "depth": 0.4, "name": "W"})

    assert "Engraved 1 groove along 'D1'" in reply
    assert "groove along it." in reply


# ── refusing before CATIA is touched ─────────────────────────────────


def test_an_empty_list_and_no_curves_at_all_are_different_refusals() -> None:
    # An explicit but empty list is the batch path's own mistake.
    for curves in ([], ()):
        with pytest.raises(ArgumentRejected) as caught:
            GSDTools(connection=None)._surface_engrave(
                {"curves": curves, "depth": 0.4, "name": "W"})
        assert getattr(caught.value, "code", "") == "CURVES_REQUIRED"
    # Passing neither falls through to the single path, which says what it wants.
    for arguments in ({"curves": None, "depth": 0.4}, {"depth": 0.4}):
        with pytest.raises(ArgumentRejected) as caught:
            GSDTools(connection=None)._surface_engrave(arguments)
        assert getattr(caught.value, "code", "") == "CURVE_REQUIRED"
        assert "curves" in str(caught.value)


def test_the_same_curve_twice_is_refused_as_a_second_tube_that_cuts_nothing() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        GSDTools(connection=None)._surface_engrave(
            {"curves": ["D1", "D1"], "depth": 0.4, "name": "W"})
    assert getattr(caught.value, "code", "") == "CURVES_REPEATED"
    assert "D1" in str(caught.value)


def test_batch_shares_the_single_paths_depth_and_position_rules() -> None:
    with pytest.raises(ArgumentOutOfRange) as caught:
        GSDTools(connection=None)._surface_engrave(
            {"curves": ["D1"], "depth": 0})
    assert getattr(caught.value, "code", "") == "DEPTH_OUT_OF_RANGE"

    with pytest.raises(ArgumentOutOfRange) as caught:
        GSDTools(connection=None)._surface_engrave(
            {"curves": ["D1"], "depth": 0.4, "position": 1.0})
    assert getattr(caught.value, "code", "") == "POSITION_OUT_OF_RANGE"


def test_a_missing_depth_is_a_coded_refusal_not_a_key_error() -> None:
    # 0.3.118: float(args["depth"]) on a missing key escaped as KeyError, which
    # no caller can act on. Both paths go through _engrave_common now.
    with pytest.raises(ArgumentRejected) as caught:
        GSDTools(connection=None)._surface_engrave({"curve": "D1"})
    assert "depth" in str(caught.value)


def test_every_curve_is_resolved_before_anything_is_built(design) -> None:
    tools = _BatchTools(available=("D1", "D2"))
    with pytest.raises(ArgumentRejected) as caught:
        tools._surface_engrave({"curves": ["D1", "D2", "TYPO"], "depth": 0.4,
                                "name": "W"})

    assert getattr(caught.value, "code", "") == "ELEMENT_NOT_FOUND"
    assert "'TYPO'" in str(caught.value)
    assert tools.steps == []            # nothing was drawn
    assert design["made"] == []      # and no body was asked for


# ── failure paths ────────────────────────────────────────────────────


def test_a_tube_that_will_not_close_cleans_up_the_whole_call(design) -> None:
    tools = _BatchTools()
    tools.close_failures = {"W2_T"}
    with pytest.raises(PreconditionRejected) as caught:
        tools._surface_engrave({"curves": ["D1", "D2"], "depth": 0.4,
                                "name": "W"})

    assert getattr(caught.value, "code", "") == "ENGRAVE_TUBE_NOT_CLOSED"
    created, body, made = tools.cleanups[0]
    assert body == "W" and made is True
    assert created == ["D1_at0.5", "W1_N", "W1_P", "W1_T",
                       "D2_at0.5", "W2_N", "W2_P", "W2_T"]
    assert "Cleanup removed" in str(caught.value)


def test_a_refused_boolean_reports_the_cutter_it_measured(design) -> None:
    design["fail_on"] = {"catia_boolean"}
    with pytest.raises(ArgumentRejected) as caught:
        _BatchTools()._surface_engrave({"curves": ["D1", "D2"], "depth": 0.4,
                                        "name": "W"})

    message = str(caught.value)
    assert "75.3982 mm3 in all" in message
    assert "100.000 mm of curve" in message
    assert "Cleanup removed" in message


def test_the_schema_offers_curves_and_only_depth_is_required() -> None:
    definitions = {item["name"]: item
                   for item in GSDTools(connection=None).get_tool_definitions()}
    schema = definitions["catia_surface_engrave"]["inputSchema"]

    assert schema["required"] == ["depth"]
    assert schema["properties"]["curves"]["type"] == "array"
    assert {"curve", "curves"} <= set(schema["properties"])


def test_the_description_says_join_is_not_the_route_and_why() -> None:
    definitions = {item["name"]: item
                   for item in GSDTools(connection=None).get_tool_definitions()}
    text = definitions["catia_surface_engrave"]["description"]

    assert "catia_gsd_join on three" not in text  # that belongs in the docstring
    assert "BATCH: pass 'curves'" in text
    assert "37.699" in text and "37.698" in text
    assert "REFUSED" in text


# ── a gap of 0.0000 is not a verdict ─────────────────────────────────


def test_a_successful_call_does_not_call_its_own_curves_grazing(design) -> None:
    # 0.3.118 nearly shipped this: a curve lying ON the surface measures 0.0000 mm
    # from it BY DEFINITION, so the gap alone says nothing about whether it cut.
    # On B30 2026-11-14 three traces at gap 0.0000 removed 37.699 mm3 while the
    # reply read "the curves GRAZE it instead of running through it".
    reply = _BatchTools(gap=0.0)._surface_engrave(
        {"curves": ["D1", "D2"], "depth": 0.4, "name": "W"})

    assert "0.0000 mm from" in reply
    assert "GRAZE" not in reply
    assert "still removes nothing" not in reply


def test_a_failed_call_still_states_the_grazing_conclusion(design) -> None:
    design["fail_on"] = {"catia_boolean"}
    with pytest.raises(ArgumentRejected) as caught:
        _BatchTools(gap=0.0)._surface_engrave(
            {"curves": ["D1"], "depth": 0.4, "name": "W"})

    assert "GRAZE" in str(caught.value)
