"""Pins the fix for the 2026-10-23 topology report (edge drift and friends).

The report's own numbers are used as fixtures: the NLS of a real edge, the pair
of faces that bound it, the 17.6112 -> 9.8429 mm shortening after a neighbouring
fillet, and the 3.5 um gap that made a pad fail.
"""

from __future__ import annotations

import pytest

from catia_mcp.tools.gsd import GSDTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools
from catia_mcp.topo_search import edge_fingerprint, face_keys

# Verbatim from the reporter's P08 camera body: an edge bounded by two faces of
# different features, which is what makes it identifiable at all.
REAL_EDGE = (
    "Selection_REdge:(Edge:(Face:(Brp:(Pad.6;2);None:();Cf12:());"
    "Face:(Brp:(Sketch.7;2);None:();Cf12:());None:(Limits1:();Limits2:());"
    "Cf12:());Pad.6_ResultOUT;Z0;G8782)"
)


# ------------------------------------------------------- stable edge references

def test_face_keys_reads_the_bounding_faces() -> None:
    assert face_keys(REAL_EDGE) == ["Pad.6;2", "Sketch.7;2"]
    assert face_keys("") == []


def test_fingerprint_carries_length_and_faces() -> None:
    assert edge_fingerprint(["Pad.6;2", "Sketch.7;2"], 17.6112) == \
        "L17.6112|Pad.6;2|Sketch.7;2"
    assert edge_fingerprint(["Pad.6;2"], None) == "L?|Pad.6;2"


@pytest.mark.parametrize("spec,expected", [
    ("Edge.30", (30, [], None)),
    ("edge.4", (4, [], None)),
    ("L17.6112|Pad.6;2|Sketch.7;2", (None, ["Pad.6;2", "Sketch.7;2"], 17.6112)),
    ({"length_mm": 17.6112, "faces": ["Pad.6;2"]}, (None, ["Pad.6;2"], 17.6112)),
    ({"index": 7}, (7, [], None)),
])
def test_edge_specs_parse(spec, expected) -> None:
    assert PartDesignTools._parse_edge_spec(spec) == expected


def test_an_unreadable_edge_spec_says_what_is_accepted() -> None:
    with pytest.raises(ValueError) as caught:
        PartDesignTools._parse_edge_spec("the long one near the top")
    assert "Edge.N" in str(caught.value)
    assert "fingerprint" in str(caught.value)


def _edge(index, faces, length):
    return {"index": index, "name": f"Edge.{index}", "cell": object(),
            "bounded_by": faces, "length": length,
            "fingerprint": edge_fingerprint(faces, length)}


def test_a_fingerprint_survives_renumbering_and_a_shorter_edge() -> None:
    """The reported drift: Edge.30 -> Edge.34 and 17.6112 -> 9.8429 mm.

    The faces are what identify the edge; the length only breaks ties, because a
    neighbouring fillet legitimately shortens it.
    """
    table = [
        _edge(30, ["Pad.6;2", "Sketch.7;2"], 17.6112),
        _edge(31, ["Pad.5;1", "Pad.6;0"], 40.0),
    ]
    found = PartDesignTools._match_edge(
        table, ["Pad.6;2", "Sketch.7;2"], 9.8429, "L9.8429|Pad.6;2|Sketch.7;2")
    assert found["index"] == 30

    # Same body after a rebuild: the numbering moved, the faces did not.
    table_after = [
        _edge(33, ["Pad.5;1", "Pad.6;0"], 40.0),
        _edge(34, ["Pad.6;2", "Sketch.7;2"], 9.8429),
    ]
    found = PartDesignTools._match_edge(
        table_after, ["Pad.6;2", "Sketch.7;2"], 17.6112, "L17.6112|Pad.6;2|Sketch.7;2")
    assert found["index"] == 34


def test_an_ambiguous_spec_refuses_instead_of_picking_one() -> None:
    table = [
        _edge(1, ["Pad.6;2", "Sketch.7;2"], 17.6112),
        _edge(2, ["Pad.6;2", "Sketch.7;2"], 17.6112),
    ]
    with pytest.raises(RuntimeError) as caught:
        PartDesignTools._match_edge(table, ["Pad.6;2", "Sketch.7;2"], None, "L?|Pad.6;2")
    assert "matches 2 edges" in str(caught.value)


def test_a_spec_that_matches_nothing_lists_the_nearest_edges() -> None:
    table = [_edge(1, ["Pad.6;2"], 17.6), _edge(2, ["Pad.5;1"], 40.0)]
    with pytest.raises(RuntimeError) as caught:
        PartDesignTools._match_edge(table, ["Pad.9;9"], 17.6, "L17.6000|Pad.9;9")
    message = str(caught.value)
    assert "No edge matches" in message
    assert "L17.6000|Pad.6;2" in message          # nearest first


# ------------------------------------------------- sketch axes and gap warning

@pytest.mark.parametrize("plane,expect", [
    ("xy", "H = X, V = Y, normal = Z"),
    ("yz", "H = Y, V = Z, normal = X"),
    ("zx", "H = Z, V = X, normal = Y"),     # the one that cost a rebuild
])
def test_sketch_reply_names_the_axes(plane, expect) -> None:
    assert expect in SketcherTools._axis_note(plane)


def test_sketch_reply_is_honest_about_a_face_frame() -> None:
    note = SketcherTools._axis_note({"type": "face", "index": 3})
    assert "cannot" in note and "offset" in note


def _sketcher():
    tools = SketcherTools(connection=None)
    tools._profile_ends = []
    return tools


def test_a_closed_rectangle_raises_no_warning() -> None:
    tools = _sketcher()
    tools._note_ends((-20, -10), (20, -10))
    tools._note_ends((20, -10), (20, 10))
    tools._note_ends((20, 10), (-20, 10))
    tools._note_ends((-20, 10), (-20, -10))
    assert tools._profile_warning() == ""


def test_a_three_micron_gap_is_named_with_its_coordinates() -> None:
    """The reporter's P08 cheek plate: r=11.43 with a rounded 26.394 deg."""
    tools = _sketcher()
    tools._note_ends((10.239, 5.0800), (30.0, 0.0))
    tools._note_ends((30.0, 0.0), (10.2390, 5.0835))    # 3.5 um off
    warning = tools._profile_warning()
    assert "not closed" in warning
    assert "0.0035" in warning
    assert "10.2390" in warning and "5.0835" in warning


def test_a_deliberately_open_curve_is_not_nagged_about() -> None:
    """An open spline is a sweep guide, not a mistake."""
    tools = _sketcher()
    tools._note_ends((0.0, 0.0), (100.0, 0.0))
    assert tools._profile_warning() == ""


# --------------------------------------------------------------- misc contracts

def test_gsd_join_names_the_argument_it_wants() -> None:
    tools = GSDTools(connection=None)
    with pytest.raises(ValueError) as caught:
        tools._join({})
    message = str(caught.value)
    assert "'elements'" in message and "surfaces" in message


def test_gsd_join_needs_two_elements() -> None:
    tools = GSDTools(connection=None)
    with pytest.raises(ValueError) as caught:
        tools._join({"elements": ["C1"]})
    assert "at least two" in str(caught.value)


def test_pocket_through_all_both_directions_is_two_cuts() -> None:
    import inspect

    source = inspect.getsource(PartDesignTools._pocket)
    # The mirror used to be skipped for through_all, which silently removed half
    # the material (reported 2026-10-23: 1608.8 mm3 instead of 3217.6).
    assert "if symmetric and not single_sided:" in source
    assert "mirror.FirstLimit.LimitMode = 2" in source
