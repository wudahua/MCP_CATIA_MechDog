"""0.3.110: what refuses a projection is the support's KIND, not its angle.

The 2026-11-11 seventh report (§3) asked for an acceptance case that separates
"reopened from disk with InWorkObject on a GSD_Set" from a fresh document,
because it had hit PROJECT_FAILED three times and blamed those two conditions.
Four cells on B30 (scripts/b30_v0410_audit.py) showed both conditions are
irrelevant - 84.0 % fresh, 84.0 % reopened with the body, 84.0 % with the set -
and that the one support that DOES refuse is a curve.

That also falsified two sentences this project had been shipping, so these tests
pin the corrections. The measurement itself needs CATIA; what is checked here is
that the advice no longer states a cause that four cells failed to reproduce.
"""

from __future__ import annotations

import inspect
import re

from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS
from catia_mcp.tools.gsd import GSDTools


def _project_source() -> str:
    return inspect.getsource(GSDTools._project)


def _advice() -> str:
    found = re.findall(r"next_actions=\[(.*?)\n\s*\]", _project_source(), re.S)
    assert found, "PROJECT_FAILED lost its next_actions"
    return " ".join(found)


def test_the_failure_no_longer_blames_parallel_or_perpendicular_geometry():
    text = _project_source()
    assert "PARALLEL is what fails" not in text
    assert "makes every point land on the same spot" not in text
    assert "perpendicular flattens the curve onto a line" not in text


def test_the_failure_quotes_the_four_measured_cells_instead():
    text = _project_source()
    assert "2026-11-12" in text
    assert "CURVE" in text
    # The three supports that took it must be named, or the correction is a
    # negative nobody can act on.
    for expected in ("LIES ON", "EXTRACTED as a SURFACE", "PARALLEL to the sketch"):
        assert expected in text, expected


def test_the_advice_starts_with_the_support_kind():
    advice = _advice()
    assert "CHECK THE SUPPORT'S KIND" in advice
    assert "catia_curve_on_surface" in advice


def test_the_advice_no_longer_sends_a_parallel_plane_to_extrude():
    advice = _advice()
    # The old advice was "there is no projection to make: extrude the element".
    assert "there is no projection to make" not in advice
    assert "do not " in advice and "extrude just to reach a parallel plane" in advice
    # It says the opposite now, with the number attached.
    assert "plain translation" in advice


def test_the_advice_calls_the_curve_reason_likely_and_not_measured():
    advice = _advice()
    assert "likely reason" in advice
    # The advice may say WHY it thinks a curve is refused, but it must not present
    # that as a measurement - the four cells measured the refusal, not its cause.
    assert "NOT measured" in advice
    assert "what IS measured" in advice


def test_the_ceiling_records_the_reopen_result():
    entry = next(item for item in _CAPABILITY_CEILINGS
                 if "first projection" in item["member"])
    detail = entry["detail"]
    assert "84.0 %" in detail
    assert "REOPENING is not a variable" in detail
    assert "b30_v0410_audit" in detail


def test_the_readme_table_no_longer_says_a_parallel_sketch_is_refused(monkeypatch):
    from pathlib import Path

    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "refused — parallel or perpendicular is degenerate" not in text
    assert "lies on (0.0000 mm)" in text
    assert "extracted as a curve | **refused**" in text
