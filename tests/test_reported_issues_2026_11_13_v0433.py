"""0.3.131 - the 2026-11-13 report's two remaining items (P1-5, P2-3).

P1-5 asked for `list_edges(coords=true, samples=N)`. What was on file here said
"a topological edge cannot be sampled"; measured on B30 2026-11-15 that is true
only of a CLOSED rim, so the tool now does it and the tests below hold the part
that is answerable without CATIA: the arguments are refused before a session is
opened, and the rule the reply states is the rule the code implements.

P2-3 asked for `crop=`. There is no capture region on this build and no image
decoder in this server, so what exists is `zoom=` - and a zoom that cannot work
is refused before anything is captured.
"""

from __future__ import annotations

import pytest

from catia_mcp.results import ArgumentRejected
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.part_design import PartDesignTools


class _Conn:
    """A connection that fails loudly if anything tries to reach CATIA."""

    def __init__(self) -> None:
        self.touched = False

    def ensure_connected(self):
        self.touched = True
        raise AssertionError("ensure_connected was called before the argument check")

    @property
    def active_document(self):
        self.touched = True
        raise AssertionError("the document was touched before the argument check")


def _edges() -> tuple[PartDesignTools, _Conn]:
    conn = _Conn()
    tools = PartDesignTools.__new__(PartDesignTools)
    tools.conn = conn
    return tools, conn


def _export() -> tuple[ExportTools, _Conn]:
    conn = _Conn()
    tools = ExportTools.__new__(ExportTools)
    tools.conn = conn
    return tools, conn


# ── P1-5: samples ────────────────────────────────────────────────────────────


def test_two_points_is_the_smallest_sample_and_one_is_refused():
    """One point describes nothing - it is not "the edge in less detail"."""
    tools, conn = _edges()
    with pytest.raises(ArgumentRejected) as error:
        tools.execute("catia_list_edges", {"samples": 1})
    assert error.value.code == "BAD_ARGUMENT"
    assert "two points" in str(error.value)
    assert conn.touched is False, "a typo must not need a session"


def test_a_sample_count_that_is_not_a_number_is_refused_by_name():
    tools, conn = _edges()
    with pytest.raises(ArgumentRejected) as error:
        tools.execute("catia_list_edges", {"samples": "every"})
    assert "samples" in str(error.value)
    assert conn.touched is False


def test_more_than_fifty_points_per_edge_is_refused_before_reading():
    """Every point is a real object and a rebuild of its own."""
    tools, conn = _edges()
    with pytest.raises(ArgumentRejected) as error:
        tools.execute("catia_list_edges", {"samples": 51})
    assert "50" in str(error.value)
    assert conn.touched is False


def test_zero_samples_is_the_default_and_still_means_no_samples():
    """samples=0 must reach the listing, not be mistaken for a bad argument."""
    tools, conn = _edges()

    seen: dict[str, object] = {}

    def _capture(*args):
        seen["args"] = args
        raise RuntimeError("reached the listing")

    tools._list_edges = _capture  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        tools.execute("catia_list_edges", {"samples": 0})
    assert seen["args"][-1] == 0, "samples=0 must arrive as 0, not be dropped"


def test_the_sample_schema_says_which_edges_refuse_and_where_it_was_measured():
    tools, _conn = _edges()
    definition = [tool for tool in tools.get_tool_definitions()
                  if tool["name"] == "catia_list_edges"][0]
    samples = definition["inputSchema"]["properties"]["samples"]
    text = samples["description"]
    assert samples["maximum"] == 50
    assert "samples_unavailable" in text, "the refusal has to be announced"
    assert "0/2" in text and "18/18" in text, "the measurement is the claim"


def test_coords_and_samples_are_two_different_questions():
    tools, _conn = _edges()
    definition = [tool for tool in tools.get_tool_definitions()
                  if tool["name"] == "catia_list_edges"][0]
    props = definition["inputSchema"]["properties"]
    assert "ONE point" in props["coords"]["description"]
    assert "rebuild" in props["samples"]["description"]


# ── P2-3: zoom instead of crop ───────────────────────────────────────────────


@pytest.mark.parametrize("value", [0, -1, 0.01, 25, "close"])
def test_a_zoom_that_cannot_be_a_magnification_is_refused(value):
    tools, conn = _export()
    with pytest.raises(ArgumentRejected) as error:
        tools.execute("catia_screenshot", {"file_path": "x.jpg", "zoom": value})
    assert error.value.code == "BAD_ARGUMENT"
    assert conn.touched is False, "a typo must not open a session"


def test_the_screenshot_schema_states_why_there_is_no_crop():
    tools, _conn = _export()
    definition = [tool for tool in tools.get_tool_definitions()
                  if tool["name"] == "catia_screenshot"][0]
    description = definition["description"]
    assert "NO 'crop'" in description
    assert "zoom" in definition["inputSchema"]["properties"]
    zoom = definition["inputSchema"]["properties"]["zoom"]
    assert "24 668" in zoom["description"], "the pixel measurement is the evidence"
    assert "RESTORED" in zoom["description"], "the camera has to be put back"
