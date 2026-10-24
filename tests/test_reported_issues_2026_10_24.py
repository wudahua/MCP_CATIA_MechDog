"""Pins the 2026-10-24 report: sketch repair, patterns, assembly measurement."""

from __future__ import annotations

import math

import pytest

from catia_mcp.tools.measurement import MeasurementTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


# ------------------------------------------------ assembly placement algebra

def test_a_point_is_moved_by_a_times_p_plus_t() -> None:
    """CATIA stores the three axis images then T: world = A*p + T.

    A 90 deg turn about Z maps local X to world Y, so a point at (10,0,0) has to
    land at (100,10,0) for T=(100,0,0). This is the arithmetic the whole
    assembly bounding box and COG depend on.
    """
    A = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    world = MeasurementTools._to_world(A, [100.0, 0.0, 0.0], [10.0, 0.0, 0.0])
    assert [round(v, 9) for v in world] == [100.0, 10.0, 0.0]


def test_identity_placement_leaves_a_point_alone() -> None:
    A = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    assert MeasurementTools._to_world(A, [0.0, 0.0, 0.0], [3.0, 4.0, 5.0]) == \
        [3.0, 4.0, 5.0]


# ------------------------------------------------------- pattern seed choice

class _Shapes:
    def __init__(self, names):
        self._names = names
        self.Count = len(names)

    def Item(self, key):
        if isinstance(key, int):
            return type("F", (), {"Name": self._names[key - 1]})()
        for name in self._names:
            if name == key:
                return type("F", (), {"Name": name})()
        raise RuntimeError("no such feature")


class _Body:
    def __init__(self, names):
        self.Shapes = _Shapes(names)


def test_the_pattern_seed_is_a_feature_not_the_body() -> None:
    """Patterning the body is why every circular pattern failed at update."""
    body = _Body(["凸台.1", "凹槽.1"])
    assert PartDesignTools._pattern_seed(body, None).Name == "凹槽.1"
    assert PartDesignTools._pattern_seed(body, "凸台.1").Name == "凸台.1"


def test_the_pattern_seed_names_what_is_available() -> None:
    with pytest.raises(RuntimeError) as caught:
        PartDesignTools._pattern_seed(_Body(["凸台.1"]), "凹槽.9")
    assert "凸台.1" in str(caught.value)


# ------------------------------------------------------ sketch profile echo

class _Sketch:
    Name = "草图.1"


def _sketcher() -> SketcherTools:
    tools = SketcherTools(connection=None)
    tools._active_sketch = _Sketch()
    return tools


def test_the_closed_profile_is_echoed_with_its_size_and_length() -> None:
    tools = _sketcher()
    tools._profile_ends = []
    tools._note_ends((-20.0, -10.0), (20.0, -10.0))
    tools._note_element("line", x1=-20.0, y1=-10.0, x2=20.0, y2=-10.0)
    tools._profile_ends.extend([])
    summary = tools._profile_summary()
    assert "1 line(s)" in summary
    assert "bbox (-20.0000, -10.0000) .. (20.0000, -10.0000)" in summary
    assert "outline 40.000 mm" in summary


def test_a_circle_is_counted_with_its_circumference() -> None:
    tools = _sketcher()
    tools._profile_ends = []
    tools._note_element("circle", cx=0.0, cy=0.0, radius=10.0,
                        sweep_rad=2 * math.pi)
    summary = tools._profile_summary()
    assert "1 circle(s)" in summary
    assert "outline 62.832 mm" in summary
