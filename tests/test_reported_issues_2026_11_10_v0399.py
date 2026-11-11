"""0.3.99 - the 2026-11-10 report on a five-lug PET bottle base.

Four things are pinned here, and each one is a claim the release makes:

* the mesh tools measure the SOLID: construction geometry is hidden for the
  export and every flag is put back;
* `brief` drops the prose a run_plan pays for at every step;
* the tutorial's three loft parameters get a coded answer instead of a JSON
  Schema error;
* `catia_shape_audit` can tell a lobed shape from a smooth bowl, which is the
  check the report said did not exist.
"""

from __future__ import annotations

import json
import math
import types

import pytest

from catia_mcp.results import ArgumentRejected


# ── a fake geometrical set ──────────────────────────────────────────────

class _Node:
    def __init__(self, name: str, kind: str, inner: list | None = None):
        self.Name = name
        self._kind = kind
        inner = inner or []
        # CATIA puts a geometrical set INSIDE a set in HybridBodies, and shapes
        # in HybridShapes - the walk has to see both kinds or a nested set is
        # skipped, which is the 0.3.73 bug in this same area.
        self.HybridShapes = _Collection(
            [item for item in inner if item._kind != "HybridBody"])
        self.HybridBodies = _Collection(
            [item for item in inner if item._kind == "HybridBody"])
        self.HybridSketches = _Collection([])
        self._oleobj_ = types.SimpleNamespace(
            GetTypeInfo=lambda: types.SimpleNamespace(
                GetDocumentation=lambda _id: (kind, "", "", "")),
            QueryInterface=lambda *_a: self)

    def __str__(self) -> str:
        return self.Name


class _Collection:
    def __init__(self, items: list):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index - 1]


class _Selection:
    """Records SetShow per node, so a restore can be checked exactly."""

    def __init__(self):
        self.state: dict[str, int] = {}
        self._current = None
        self.VisProperties = types.SimpleNamespace(GetShow=self._get,
                                                   SetShow=self._set)

    def Clear(self):
        self._current = None

    def Add(self, node):
        self._current = node

    def _get(self):
        return (0, self.state.get(self._current.Name, 0))

    def _set(self, value):
        self.state[self._current.Name] = int(value)


def _part_with_set(children: list[_Node]) -> types.SimpleNamespace:
    geoset = _Node("BottleBase_GSD", "HybridBody", children)
    return types.SimpleNamespace(HybridBodies=_Collection([geoset]))


def test_construction_geometry_is_hidden_and_the_solid_kept() -> None:
    """Hiding a set that CARRIES the solid is how the report got a blank
    window: the ThickSurface is an element of the set, so it has to stay."""
    from catia_mcp.tools.measurement import _hide_construct_geometry

    surface = _Node("Lobe_0_72", "HybridShape")
    solid = _Node("加厚曲面.1", "HybridShape")
    part = _part_with_set([surface, solid])
    selection = _Selection()

    records, info = _hide_construct_geometry(part, selection)

    assert selection.state["Lobe_0_72"] == 1        # hidden
    assert selection.state["加厚曲面.1"] == 0        # kept: it is the solid
    assert info["hidden"] == 2          # the set itself and the surface
    assert "加厚曲面.1" in info["kept"]  # the solid is NOT one of the hidden
    assert len(records) == 3                        # the set and its contents


def test_every_flag_is_put_back_afterwards() -> None:
    from catia_mcp.tools.measurement import (_hide_construct_geometry,
                                             _restore_visibility)

    hidden_already = _Node("Old", "HybridShape")
    shown = _Node("Live", "HybridShape")
    part = _part_with_set([hidden_already, shown])
    selection = _Selection()
    selection.state["Old"] = 1                      # was already hidden

    records, _info = _hide_construct_geometry(part, selection)
    assert selection.state["Old"] == 1 and selection.state["Live"] == 1

    restored = _restore_visibility(records, selection)
    assert restored == len(records)
    assert selection.state["Old"] == 1              # still hidden, as it was
    assert selection.state["Live"] == 0             # back on screen


def test_a_nested_set_is_reached_as_well() -> None:
    """A set inside a set: the outer flag does not reach the inner elements."""
    from catia_mcp.tools.measurement import _hide_construct_geometry

    inner = _Node("Inner", "HybridBody", [_Node("Deep", "HybridShape")])
    part = _part_with_set([inner])
    selection = _Selection()

    _records, info = _hide_construct_geometry(part, selection)

    assert selection.state["Deep"] == 1
    assert info["elements"] == 3          # outer set, inner set, the shape


# ── the profile: can it see a lobed shape? ──────────────────────────────

def _lobed_stl(path, lobes: int, base: float = 20.0, swing: float = 4.0,
               steps: int = 72, height: float = 10.0) -> str:
    """A closed side wall whose radius follows base + swing*cos(lobes*theta)."""

    def r(theta: float) -> float:
        return base + swing * math.cos(lobes * theta)

    ring = []
    for i in range(steps):
        # half a bin off the boundary: a vertex AT the boundary can land in
        # either bin depending on the last bit of the float, which empties the
        # other one and is a property of the test, not of the tool
        theta = 2.0 * math.pi * (i + 0.5) / steps
        radius = r(theta)
        x, y = radius * math.cos(theta), radius * math.sin(theta)
        ring.append((x, y))

    lines = ["solid lobed"]
    for i in range(steps):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % steps]
        for z0, z1 in ((0.0, height), (height, 0.0)):
            lines.append("facet normal 0 0 1")
            lines.append("  outer loop")
            lines.append(f"    vertex {x0} {y0} {z0}")
            lines.append(f"    vertex {x1} {y1} {z0}")
            lines.append(f"    vertex {x1} {y1} {z1}")
            lines.append("  endloop")
            lines.append("endfacet")
    lines.append("endsolid lobed")
    with open(path, "w", encoding="ascii") as handle:
        handle.write("\n".join(lines))
    return str(path)


def test_the_profile_finds_four_lobes(tmp_path) -> None:
    from catia_mcp.tools.measurement import _shape_profile

    profile = _shape_profile(_lobed_stl(tmp_path / "l4.stl", 4), 72, 100000)
    circle = profile["circumference"]

    assert circle["amplitude_mm"] == pytest.approx(8.0, abs=0.15)
    assert circle["peak_count"] == 4
    peaks = sorted(circle["peak_angles_deg"])
    for angle in peaks:
        # every peak on SOME multiple of 90, whichever way the bins fall
        assert min(abs(((angle - k * 90.0 + 180.0) % 360.0) - 180.0)
                   for k in range(4)) < 8.0, peaks
    assert profile["bins_filled"] == 72


def test_a_smooth_shape_has_no_amplitude(tmp_path) -> None:
    """The failure the tutorial's appendix describes: a bowl passes every other
    check, and the one thing it does not have is circumferential variation."""
    from catia_mcp.tools.measurement import _shape_profile

    profile = _shape_profile(_lobed_stl(tmp_path / "flat.stl", 1, swing=0.0),
                             72, 100000)
    assert profile["circumference"]["amplitude_mm"] < 0.01


def test_the_repeats_agree_when_the_shape_is_symmetric(tmp_path) -> None:
    """The fold check is the one that says 'five lugs, as designed'."""
    from catia_mcp.tools.measurement import _shape_profile

    profile = _shape_profile(_lobed_stl(tmp_path / "l4.stl", 4), 72, 100000)
    by_bin = profile["_r_by_bin"]
    repeat = len(by_bin) // 4
    worst = max(abs(by_bin[i] - by_bin[(i + k * repeat) % len(by_bin)])
                for i in range(repeat) for k in range(1, 4))
    assert worst < 0.01


def test_shape_audit_refuses_a_bin_count_it_cannot_use() -> None:
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    with pytest.raises(ArgumentRejected) as caught:
        tools._shape_audit(bins=4)
    assert caught.value.code == "BAD_ARGUMENT"


# ── the tutorial's loft parameters ──────────────────────────────────────

def _gsd():
    from catia_mcp.tools.gsd import GSDTools

    return GSDTools(connection=None)


def test_a_tangent_factor_is_refused_with_the_measurement() -> None:
    tools = _gsd()
    with pytest.raises(ArgumentRejected) as caught:
        tools._answer_tutorial_parameters({"tangent_factor": 1.5,
                                          "sections": ["a", "b"]})
    error = caught.value
    assert error.code == "NO_TANGENT_FACTOR"
    message = str(error)
    assert "1.5" in message
    assert "78" in message                     # the member count that settled it
    assert "3030.971" in message               # the plane measurement
    assert "extra SECTION" in message or "extra section" in message


def test_a_factor_of_one_is_accepted_because_it_is_all_there_is() -> None:
    _gsd()._answer_tutorial_parameters({"tangent_factor": 1.0})


def test_normal_to_profile_without_a_support_names_the_choice() -> None:
    tools = _gsd()
    with pytest.raises(ArgumentRejected) as caught:
        tools._answer_tutorial_parameters({"normal_to_profile": True})
    assert caught.value.code == "NO_NORMAL_TO_PROFILE"
    assert "TANGENT TO THE PLANE" in str(caught.value)
    assert "start_tangent" in str(caught.value)


def test_normal_to_profile_with_a_support_passes() -> None:
    _gsd()._answer_tutorial_parameters({"normal_to_profile": True,
                                        "start_tangent": "xy"})


def test_guide_influence_is_refused_with_what_does_exist() -> None:
    tools = _gsd()
    with pytest.raises(ArgumentRejected) as caught:
        tools._answer_tutorial_parameters({"guide_influence": "To Next Guide"})
    assert caught.value.code == "NO_GUIDE_INFLUENCE"
    assert "AddGuideWithTangent" in str(caught.value)


# ── the ceilings the report asked for ───────────────────────────────────

def test_the_ceilings_answer_the_four_questions_the_report_asked() -> None:
    from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS

    by_member = {entry["member"]: entry for entry in _CAPABILITY_CEILINGS}
    pattern = next(entry for name, entry in by_member.items()
                   if name.startswith("ShapeFactory.AddNewGSDCircPattern"))
    assert pattern["status"] == "UNSUPPORTED_ON_B30"
    # The contrast that makes the entry useful: a solid CAN be patterned.
    assert "catia_circ_pattern" in pattern["detail"]
    assert "16 of 16" in pattern["detail"]

    loft = next(entry for name, entry in by_member.items()
                if name.startswith("HybridShapeLoft"))
    assert loft["status"] == "NOT_IN_TYPELIB"
    assert "78 members" in loft["detail"]

    sketch = next(entry for name, entry in by_member.items()
                  if name.startswith("Sketch frame"))
    assert sketch["status"] == "NOT_IN_TYPELIB"

    viewer = next(entry for name, entry in by_member.items()
                  if name.startswith("Viewer3D"))
    assert viewer["status"] == "NOT_IN_TYPELIB"
    assert "HideTree" in viewer["detail"]


def test_every_ceiling_still_carries_a_status_and_a_detail() -> None:
    from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS

    allowed = {"UNSUPPORTED_ON_B30", "NOT_IN_TYPELIB", "DECLARED_NOT_MEASURED",
               "AVAILABLE_WITH_LIMIT", "MEASURED_UNEXPLAINED",
               "SEEN_ONCE_NOT_REPRODUCED"}
    for entry in _CAPABILITY_CEILINGS:
        assert entry["status"] in allowed, entry["member"]
        assert len(entry["detail"]) > 60, entry["member"]


# ── brief mode ─────────────────────────────────────────────────────────

def test_brief_keeps_the_numbers_and_drops_the_prose(tmp_path) -> None:
    from catia_mcp.tools.measurement import MeasurementTools

    stl = tmp_path / "two.stl"
    stl.write_text("solid t\n"
                   "facet normal 0 0 1\n  outer loop\n"
                   "    vertex 0 0 0\n    vertex 10 0 0\n    vertex 0 10 0\n"
                   "  endloop\nendfacet\n"
                   "endsolid t\n", encoding="ascii")

    tools = MeasurementTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: types.SimpleNamespace(
            Parent=types.SimpleNamespace(Name="X.CATPart")))
    tools._mesh_export = lambda part, limit, visit: visit(str(stl))
    tools._visible_hybrid_sets = lambda part: (0, 0)

    brief = json.loads(tools._check_surface(25.0, 600000, None, None, 8, None,
                                            False, True))
    full = json.loads(tools._check_surface(25.0, 600000, None, None, 8, None,
                                           False, False))
    assert "how_to_read" not in brief
    assert "triangles" in brief and "boundary_edges" in brief
    assert "how_to_read" in full
    assert brief["brief"].startswith("numbers only")
