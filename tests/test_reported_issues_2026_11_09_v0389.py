"""0.3.89: a refusal that names the number, and a first save with no rule to miss.

Two 0.3.88 reports. The first one's headline was a path shape where - on the
reporter's machine - the staged save allegedly never ran: an ASCII ancestor
folder containing a SPACE, with a Chinese subfolder under it. `_path_is_ascii` is
`str(path).isascii()` on the whole string (no split, no prefix), and that exact
path was measured through the staged route on B30 (two `save_as` calls, 40701
bytes on disk). A rule that cannot be defended is not worth keeping, so since
0.3.89 EVERY first save takes the staged route - there is no folder shape left to
miss.

The second report is the one that produced real code: `catia_gsd_split` refused a
circle cutter with nothing but `方法 UpdateObject 失败`, and the reporter spent
three control experiments working out why. Measured on B30 2026-11-09, on one
40x40 PLANE patch:

    circle r=6 lying on the patch       split OK, 1486.903 mm2 kept
    the same circle 3 mm above it       fail; GetMinimumDistance = 3.0000 mm
    circle over a five-lobe loft        fail the same way
    after that failure                  every NEW point also refuses to update

So a closest-approach measurement both pre-empts the case that never touches the
element and survives the case that does.
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


class _Split:
    def __init__(self) -> None:
        self.Name = ""


class _Factory:
    def __init__(self) -> None:
        self.splits = 0

    def AddNewHybridSplit(self, element, cutter, orientation):
        self.splits += 1
        return _Split()


def _split_tools(gap=0.0, update_error=None):
    """GSDTools with just the COM surface `_split` touches."""
    from catia_mcp.tools.gsd import GSDTools

    factory = _Factory()
    part = types.SimpleNamespace(
        Name="PART", InWorkObject=None,
        FindObjectByName=lambda name: None,
        CreateReferenceFromObject=lambda obj: ("ref", obj),
        UpdateObject=lambda shape: None,
    )
    tools = GSDTools(connection=None)
    tools.conn = types.SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: part,
        active_document=types.SimpleNamespace(Selection=None),
        refresh_display=lambda: None,
    )
    tools._factory = lambda: factory                      # type: ignore[method-assign]
    tools._ref = lambda name: ("ref", name)               # type: ignore[method-assign]
    tools._cutter_gap = lambda elem, cutter: gap          # type: ignore[method-assign]

    def finish(shape, name, message):
        if update_error is not None:
            raise RuntimeError(update_error)
        return message

    tools._finish = finish                                # type: ignore[method-assign]
    return tools, factory


# ── a cutter that never reaches the element is refused before building ───


def test_a_cutter_that_misses_the_element_is_refused_with_its_distance():
    """The report's case 1 measured 3.0000 mm - that number IS the diagnosis."""
    tools, factory = _split_tools(gap=3.0)
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_split",
                      {"element": "PATCH", "cutter": "CIRCLE_3MM_ABOVE"})
    error = caught.value
    assert error.code == "SPLIT_CUTTER_MISSES"
    assert "3.0000 mm" in str(error)
    assert "Nothing was created" in str(error)
    assert error.modified is False
    assert factory.splits == 0, "nothing may be built once the gap is known"


def test_the_miss_refusal_names_both_ways_a_cutter_ends_up_off_the_surface():
    tools, _factory = _split_tools(gap=0.25)
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_split", {"element": "PATCH", "cutter": "C"})
    text = str(caught.value)
    assert "curve drawn where the surface is not" in text
    assert "plane offset past the geometry" in text
    assert "catia_measure_distance" in text


# ── a refusal at update says what was measured, and what it cannot know ──


def test_the_failed_update_reports_the_distance_and_refuses_to_guess():
    tools, factory = _split_tools(gap=0.0, update_error="方法 UpdateObject 失败")
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_split",
                      {"element": "BASE", "cutter": "CIRCLE_R8"})
    error = caught.value
    text = str(error)
    assert error.code == "SPLIT_FAILED"
    assert error.modified is False
    assert "0.0000 mm" in text
    # 0.3.95: the number is a MINIMUM and the message says so. A 2026-11-10
    # report with three cutters got "closest approach 0.0000" three times and
    # rebuilt a cutter that already lay on the surface - a minimum cannot
    # answer "does the whole contour follow the element".
    assert "MINIMUM" in text
    assert "NOT prove the contour" in text
    # 0.3.119: this used to assert "no way to sample along a curve", and that
    # sentence was FALSE - AddNewPointOnCurveFromPercent does update (the engrave
    # depends on it), and catia_measure_deviation samples a curve at N points.
    # The message now points at that tool instead of denying it exists.
    assert "no way to sample along a curve" not in text
    assert "catia_measure_deviation" in text
    # The cases the measurements DO separate, named.
    assert "join or an array" in text
    assert "measured-unsupported combination" in text
    # The measured control cases, so the caller does not repeat the experiment.
    assert "1486.903" in text
    assert "PLANE patch" in text
    # And the route that needs no coplanar centre.
    assert "cap_center=true" in text
    assert "OWN border" in text
    # The error state is itself a cause of a second failure.
    assert "do not read a second failure as a second cause" in text


def test_an_unmeasurable_gap_is_reported_as_such_not_as_zero():
    tools, _factory = _split_tools(gap=None, update_error="方法 UpdateObject 失败")
    with pytest.raises(CodedError) as caught:
        tools.execute("catia_gsd_split", {"element": "BASE", "cutter": "PLANE"})
    assert "not measurable after the failure" in str(caught.value)


def test_a_split_that_works_still_names_the_side_it_kept():
    tools, factory = _split_tools(gap=0.0)
    reply = tools.execute("catia_gsd_split",
                          {"element": "PATCH", "cutter": "CIRCLE_ON"})
    assert factory.splits == 1
    assert "the side the cutter's normal points to" in reply


# ── what the descriptions promise ───────────────────────────────────────


def test_the_split_description_warns_that_the_sample_patch_was_a_plane():
    """The 40x40 success sample is a PLANE. Saying so is the whole point."""
    from catia_mcp.tools.gsd import GSDTools

    doc = [d for d in GSDTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_gsd_split"][0]
    text = doc["description"]
    assert "THAT PATCH IS A PLANE" in text
    assert "LIE ON" in text
    assert "SPLIT_CUTTER_MISSES" in text and "SPLIT_FAILED" in text
    assert "cap_center=true" in text


def test_a_first_save_is_not_gated_on_the_folder_any_more():
    """0.3.88 said "when the folder is non-ASCII"; that rule is what a report
    could not reproduce, so it is gone rather than defended."""
    from catia_mcp.tools.document import DocumentTools

    doc = [d for d in DocumentTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_new_part"][0]
    text = doc["description"]
    assert "Since 0.3.89 EVERY first save takes the staged route" in text
    assert "when the folder is non-ASCII: SaveAs into %TEMP%" not in text
