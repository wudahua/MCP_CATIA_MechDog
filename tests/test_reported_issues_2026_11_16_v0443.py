"""2026-11-16 report (round 14): probing a surface at points, and two P1 claims.

P0 of the report was "there is no way to ask a free-form surface where it is at
this station, and no equivalent", with a shallow dome left unbuilt because the
surface height had to be reconstructed from the modelling JSON. The primitive was
one call away: `Measurable.GetMinimumDistancePoints` returns the CLOSEST POINT,
and that was verified on B30 against a cone with an analytic answer (see
`catia_mcp/fidelity.py` and `scripts/b30_v0443_probe_surface_probe.py`).

What is pinned here, all of it without CATIA:

  * the arithmetic of a nearest reading - closest point, distance, normal,
    and the two-sided check that the API's distance agrees with the vector it
    returned (a reading that disagrees with itself is not a reading);
  * the height-along-an-axis iteration, including the case where it does NOT
    converge: a surface that runs away from the probe must come back
    `converged: false` rather than an offset that looks like a hit;
  * the self-calibration: stepping 0.5 mm off the first foot point has to read
    0.5 mm back, and a reading that fails to do so is marked NOT CALIBRATED;
  * argument validation before any CATIA session is touched;
  * P1-1: `catia_delete_feature` on a BODY's name now says "that is a body, use
    catia_delete_body" instead of "no feature named ...", which is what the
    report hit (the body-delete tool has existed since 0.3.x);
  * P1-2: the two descriptions that disagreed about coordinate points now name
    the condition - measured 2026-11-16: create_points=false leaves nothing
    behind, create_points=true leaves N Point features.
"""
from __future__ import annotations

import inspect

import pytest

from catia_mcp import fidelity
from catia_mcp.results import ArgumentRejected, ArgumentOutOfRange


# ── an analytic surface: the plane z = 10 ───────────────────────────────────
def plane_nearest(where):
    """Closest point on z=10, and the distance the API would report."""
    point = [float(where[0]), float(where[1]), 10.0]
    return point, abs(float(where[2]) - 10.0)


def test_a_nearest_reading_carries_point_distance_and_normal():
    rows = fidelity.nearest_rows([[1.0, 2.0, 13.0]], plane_nearest)

    assert len(rows) == 1
    row = rows[0]
    assert row["surface_point"] == [1.0, 2.0, 10.0]
    assert row["distance_mm"] == 3.0
    assert row["normal"] == [0.0, 0.0, 1.0], "the foot-to-probe vector is the normal"
    assert row["agrees"] is True
    assert "on_surface_or_inside" not in row


def test_a_distance_that_disagrees_with_its_own_vector_is_flagged():
    """The API's number and the vector it returned must measure the same thing.

    They are two independent readings of one fact; a build where they differ is
    a build whose closest point cannot be trusted, and the row has to say so
    instead of quoting a number that looks fine on its own.
    """

    def lying(where):
        return [float(where[0]), float(where[1]), 10.0], 99.0

    row = fidelity.nearest_rows([[0.0, 0.0, 12.0]], lying)[0]
    assert row["distance_mm"] == 2.0
    assert row["api_distance_mm"] == 99.0
    assert row["agrees"] is False


def test_a_point_on_the_surface_is_named_rather_than_measured_as_zero():
    """0.0000 mm from a BODY means "on it or inside it" - measured on B30, an
    inside point hands back itself as the closest point. Naming that state is
    what keeps the number from being read as "touching"."""
    row = fidelity.nearest_rows([[0.0, 0.0, 10.0]], plane_nearest)[0]
    assert row["distance_mm"] == 0.0
    assert row["on_surface_or_inside"] is True
    assert row["normal"] is None, "a zero vector has no direction to report"


def test_a_failed_probe_does_not_take_the_other_probes_with_it():
    def flaky(where):
        if where[2] >= 11.0:
            raise RuntimeError("方法 GetMinimumDistancePoints 失败")
        return plane_nearest(where)

    rows = fidelity.nearest_rows([[0.0, 0.0, 12.0], [0.0, 0.0, 5.0]], flaky)
    assert "error" in rows[0]
    assert rows[1]["distance_mm"] == 5.0


# ── height along an axis ────────────────────────────────────────────────────
def test_along_finds_the_height_the_nearest_reading_only_approximates():
    rows = fidelity.along_rows([[1.0, 2.0, 4.0]], [0.0, 0.0, 1.0], plane_nearest,
                               tolerance=1e-6)
    row = rows[0]
    assert row["converged"] is True
    assert row["offset_mm"] == pytest.approx(6.0, abs=1e-6)
    assert row["surface_point"] == [1.0, 2.0, 10.0]
    assert row["gap_mm"] == pytest.approx(0.0, abs=1e-6)
    assert row["iterations"] <= 3


def test_along_reports_not_converged_when_the_surface_runs_away():
    """A plane that is always 1 mm ahead of the probe can never be met. The row
    must say so: an offset that stopped moving is not a hit."""

    def running_away(where):
        point = [float(where[0]), float(where[1]), float(where[2]) + 1.0]
        return point, 1.0

    rows = fidelity.along_rows([[0.0, 0.0, 0.0]], [0.0, 0.0, 1.0],
                               running_away, tolerance=0.001, max_iterations=3)
    assert rows[0]["converged"] is False
    assert rows[0]["iterations"] == 3
    assert rows[0]["residual_mm"] == pytest.approx(1.0)


def test_along_refuses_a_zero_direction():
    """Coded, not a bare ValueError: a caller that branches on codes must not get
    COM_ERROR for a call that never reached CATIA."""
    with pytest.raises(ArgumentRejected) as caught:
        fidelity.along_rows([[0.0, 0.0, 0.0]], [0.0, 0.0, 0.0], plane_nearest)
    assert caught.value.code == "BAD_ARGUMENT"


# ── the reading is calibrated, or it is not quoted ─────────────────────────
def test_calibration_passes_when_a_known_step_reads_back():
    row = fidelity.nearest_rows([[0.0, 0.0, 13.0]], plane_nearest)[0]
    check = fidelity.calibration_row(row, plane_nearest, stencil_mm=0.5)
    assert check["ok"] is True
    assert check["read_mm"] == pytest.approx(0.5)
    assert check["foot_drift_mm"] == pytest.approx(0.0, abs=1e-9)


def test_calibration_fails_when_the_reading_is_not_a_surface_distance():
    def wrong(where):
        # always claims 0.25 mm, whatever it is asked: the control case
        return [float(where[0]), float(where[1]), 10.0], 0.25

    row = fidelity.nearest_rows([[0.0, 0.0, 12.0]], wrong)[0]
    check = fidelity.calibration_row(row, wrong, stencil_mm=0.5)
    assert check["ok"] is False, "a step of 0.5 mm that reads 0.25 is not calibrated"


def test_calibration_says_why_it_could_not_run():
    check = fidelity.calibration_row({"surface_point": None}, plane_nearest)
    assert check["ok"] is None
    assert "no surface point" in check["reason"]


# ── arguments are judged before CATIA is touched ───────────────────────────
def test_probe_points_accepts_three_numbers_and_nothing_else():
    assert _gsd_module()._probe_points([[0, 0, 0], [1.5, 2, 3]]) == \
        [[0.0, 0.0, 0.0], [1.5, 2.0, 3.0]]
    with pytest.raises(ArgumentRejected):
        _gsd_module()._probe_points([])
    with pytest.raises(ArgumentRejected):
        _gsd_module()._probe_points([[0, 0]])
    with pytest.raises(ArgumentRejected):
        _gsd_module()._probe_points([["a", 0, 0]])


def test_probe_points_has_a_ceiling_because_every_probe_is_an_element():
    with pytest.raises(ArgumentOutOfRange) as caught:
        _gsd_module()._probe_points([[0, 0, 0]] * 201)
    assert caught.value.code == "TOO_MANY_PROBES"


def test_a_malformed_direction_is_refused_rather_than_ignored():
    """Omitting direction means a DIFFERENT answer (nearest, not height), so a
    broken one must not quietly fall back to it."""
    assert _gsd_module()._probe_direction(None) is None
    assert _gsd_module()._probe_direction([0, -1, 0]) == [0.0, -1.0, 0.0]
    with pytest.raises(ArgumentRejected):
        _gsd_module()._probe_direction([0, 0])
    with pytest.raises(ArgumentRejected):
        _gsd_module()._probe_direction([0, 0, 0])


def _gsd_module():
    import catia_mcp.tools.gsd as module

    return module


def test_the_tool_is_registered_and_says_what_it_is_not():
    from catia_mcp.tools.gsd import GSDTools

    tools = {tool["name"]: tool for tool in
             GSDTools.get_tool_definitions(GSDTools.__new__(GSDTools))}
    probe = tools["catia_surface_probe"]
    assert probe["inputSchema"]["required"] == ["surface", "points"]
    text = probe["description"]
    # the three facts a caller has to know before trusting a number
    assert "NOT the height on your" in text
    assert "cannot tell inside from on" in text.lower() or \
           "CANNOT TELL INSIDE FROM ON" in text
    assert "0.4128" in text, "the projection offset is the measured 'not this'"
    assert "calibrat" in text.lower()


# ── P1-1: a body named in catia_delete_feature ─────────────────────────────
class _Empty:
    Count = 0

    def Item(self, _index):  # pragma: no cover - never reached when Count is 0
        raise IndexError


class _Feature:
    def __init__(self, name: str):
        self.Name = name


class _Shapes:
    def __init__(self, count: int):
        self._items = [_Feature(f"Feature.{index}") for index in range(1, count + 1)]
        self.Count = count

    def Item(self, index):
        return self._items[index - 1]


class _Body:
    def __init__(self, name: str, features: int = 0):
        self.Name = name
        self.Shapes = _Shapes(features)
        self.Sketches = _Empty()


class _Bodies:
    def __init__(self, bodies):
        self._bodies = list(bodies)
        self.Count = len(self._bodies)

    def Item(self, key):
        if isinstance(key, int):
            return self._bodies[key - 1]
        for body in self._bodies:
            if body.Name == key:
                return body
        raise KeyError(key)


class _Part:
    def __init__(self, bodies):
        self.Bodies = _Bodies(bodies)
        self.HybridBodies = _Empty()


class _Conn:
    def __init__(self, part):
        self._part = part

    def ensure_connected(self):
        return True

    def get_active_part(self):
        return self._part


def _delete_one_with(target: str, bodies):
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=_Conn(_Part(bodies)))
    return tools._delete_one({"feature_name": target}, None)


def test_deleting_a_body_by_feature_name_points_at_the_body_tool():
    """The report's own case: an empty body left by a failed loft, visible in
    catia_list_bodies, and 'No feature named' for every attempt to remove it."""
    with pytest.raises(ArgumentRejected) as caught:
        _delete_one_with("HandleCrown", [_Body("PartBody"), _Body("HandleCrown")])

    error = caught.value
    assert error.code == "BODY_NOT_FEATURE"
    assert error.modified is False
    assert error.data["body"] == "HandleCrown"
    assert error.data["features_held"] == 0
    assert any("catia_delete_body" in action for action in error.next_actions)
    assert "empty" in str(error).lower()


def test_a_body_that_still_holds_features_is_reported_with_its_count():
    with pytest.raises(ArgumentRejected) as caught:
        _delete_one_with("DraftBody", [_Body("PartBody"), _Body("DraftBody", 3)])
    assert caught.value.data["features_held"] == 3
    assert "force=true" in str(caught.value)


def test_a_name_that_is_nothing_at_all_still_says_it_searched_the_bodies():
    with pytest.raises(ArgumentRejected) as caught:
        _delete_one_with("NoSuchThing", [_Body("PartBody")])
    assert caught.value.code != "BODY_NOT_FEATURE"
    assert "body list" in str(caught.value)


# ── P1-2: the two descriptions now name the condition ──────────────────────
def test_the_cascade_note_says_which_create_points_setting_it_is_about():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = {tool["name"]: tool for tool in PartDesignTools.get_tool_definitions(
        PartDesignTools(connection=None))}
    text = tools["catia_delete_feature"]["description"]
    assert "THIS ONLY APPLIES WHEN THE POINTS ARE IN THE TREE" in text
    assert "create_points=false" in text
    assert "2026-11-16" in text


def test_the_spline_reply_says_the_default_leaves_nothing_behind():
    import catia_mcp.tools.gsd as module

    source = inspect.getsource(module.GSDTools._splines)
    assert "cascade=true has nothing to remove" in source
    assert "2026-11-16" in source
