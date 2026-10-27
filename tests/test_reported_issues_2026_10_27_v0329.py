# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-27 follow-up: the assembly BOX now recurses.

`get_inertia` started expanding sub-assemblies in 0.3.28, and `get_bounding_box`
on the same document still listed the sub-assembly in `skipped`. That was honest
(`complete: false`) but it left a nested assembly unbounded - and it made the two
aggregators disagree about what "complete" means, which is the complaint that
opened this round. The walk below is the same one, one level deeper: placements
compose on the way down and every part is still bounded once per identity.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _Doc:
    def __init__(self, name: str, kind: str = "ProductDocument", product=None):
        self.Name = name
        self.FullName = name
        self.Type = kind
        self.Product = product


class _Part:
    def __init__(self, name: str):
        self.Name = name
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=1))


def _plate(cx: float = 0.0, cy: float = 0.0, z0: float = 0.0,
           hx: float = 50.0, hy: float = 30.0, hz: float = 10.0):
    """The 8 corners of a box - a real part's vertex list, not two corners."""
    return [[cx + sx * hx, cy + sy * hy, z0 + sz * hz]
            for sx in (-1, 1) for sy in (-1, 1) for sz in (0, 1)]


def _row(name: str, part=None, kind: str = "part", translation=None,
         nested=None, problems=None):
    return {"index": 1, "name": name, "part_number": name.split(".")[0],
            "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": translation or [0.0, 0.0, 0.0],
            "part": part, "nested_doc": nested, "kind": kind,
            "problems": list(problems or [])}


def _box_tools(rows_by_assembly, points_by_part):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    tools._components_doc = (
        lambda doc, only=None: rows_by_assembly.get(str(doc.Name), []))

    def part_box_entry(part, body, include_curved):
        points = points_by_part[str(part.Name)]
        return {"points": points, "used": len(points), "source": "vertices",
                "mesh_vertices": None, "copy_note": "", "cells": 12,
                "nls_chars": 100 * len(points)}

    tools._part_box_entry = part_box_entry
    tools._part_identity = lambda part: str(part.Name)
    return tools


def _walk(tools, doc):
    return json.loads(tools._assembly_box(doc, "vertices"))


# ────────────────────────────────── the case the report is about
def test_a_part_inside_a_sub_assembly_is_bounded_not_skipped():
    plate = _Part("P28A")
    sub = _Doc("SUB.CATProduct")
    top = _Doc("TOP.CATProduct")
    tools = _box_tools(
        {
            "TOP.CATProduct": [
                _row("P28A.1", part=plate),
                _row("SUB.1", kind="sub_assembly", nested=sub,
                     translation=[100.0, 0.0, 0.0]),
            ],
            "SUB.CATProduct": [_row("P28A.1", part=plate)],
        },
        {"P28A": _plate()},
    )

    reply = _walk(tools, top)
    # Two instances of one 100x60x10 plate, the second one 100 mm along x.
    assert reply["min"] == {"x": -50.0, "y": -30.0, "z": 0.0}
    assert reply["max"] == {"x": 150.0, "y": 30.0, "z": 10.0}
    assert reply["dimensions"]["length_x"] == 200.0
    assert reply["complete"] is True
    assert reply["assembly_depth_max"] == 2
    assert reply["instances"] == {"total": 2, "measured": 2, "skipped": 0}
    # The path is the only thing that says which level an instance came from.
    assert sorted(entry["path"] for entry in reply["components"]) == [
        "P28A.1", "SUB.1/P28A.1"]
    # ONE part, bounded once, however many levels it appears at.
    assert reply["part_boxes"] == 1
    assert reply["parts"][0]["instances"] == 2
    assert "skipped" not in reply


def test_the_two_placements_compose_so_a_rotated_sub_assembly_lands_right():
    plate = _Part("P28A")
    sub = _Doc("SUB.CATProduct")
    top = _Doc("TOP.CATProduct")
    # 90 degrees about Z: (x, y) -> (-y, x). A +-5 / +-10 plate becomes
    # x -10..10, y -5..5 - so a box computed from the UNrotated corners would
    # still be right, and one computed from the wrong matrix would not be.
    rows = [
        _row("SUB.1", kind="sub_assembly", nested=sub),
    ]
    rows[0]["matrix"] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    tools = _box_tools(
        {"TOP.CATProduct": rows,
         "SUB.CATProduct": [_row("P28A.1", part=plate)]},
        {"P28A": _plate(hx=5.0, hy=10.0, hz=1.0)},
    )

    reply = _walk(tools, top)
    assert reply["min"] == {"x": -10.0, "y": -5.0, "z": 0.0}
    assert reply["max"] == {"x": 10.0, "y": 5.0, "z": 1.0}
    assert reply["components"][0]["path"] == "SUB.1/P28A.1"
    assert reply["components"][0]["depth"] == 2


def test_an_unreadable_sub_assembly_still_says_the_box_is_incomplete():
    top = _Doc("TOP.CATProduct")
    tools = _box_tools(
        {"TOP.CATProduct": [_row("SUB.1", kind="sub_assembly", nested=None)]},
        {})

    reply = _walk(tools, top)
    assert reply["complete"] is False
    assert reply["skipped"][0]["kind"] == "sub_assembly"
    assert reply["skipped"][0]["path"] == "SUB.1"
    assert "could not be read" in reply["skipped"][0]["error"]


def test_an_empty_sub_assembly_is_named_not_ignored():
    top = _Doc("TOP.CATProduct")
    sub = _Doc("SUB.CATProduct")
    tools = _box_tools({"TOP.CATProduct": [
        _row("SUB.1", kind="sub_assembly", nested=sub),
    ], "SUB.CATProduct": []}, {})

    reply = _walk(tools, top)
    assert reply["complete"] is False
    assert "holds no components" in reply["skipped"][0]["error"]


def test_nesting_stops_at_the_depth_limit_and_says_which_branch():
    plate = _Part("P28A")
    chain = [_Doc(f"L{i}.CATProduct") for i in range(12)]
    rows = {doc.Name: [_row(f"L{i + 1}.1", kind="sub_assembly",
                            nested=chain[i + 1])]
            for i, doc in enumerate(chain[:-1])}
    rows[chain[-1].Name] = [_row("P28A.1", part=plate)]
    tools = _box_tools(rows, {"P28A": _plate()})

    reply = _walk(tools, chain[0])
    assert reply["complete"] is False
    message = reply["skipped"][0]["error"]
    assert "nested deeper than" in message
    assert reply["skipped"][0]["depth"] == 8


def test_one_part_at_two_levels_is_bounded_once():
    plate = _Part("P28A")
    sub = _Doc("SUB.CATProduct")
    top = _Doc("TOP.CATProduct")
    tools = _box_tools(
        {"TOP.CATProduct": [
            _row("P28A.1", part=plate),
            _row("SUB.1", kind="sub_assembly", nested=sub,
                 translation=[200.0, 0.0, 0.0]),
        ], "SUB.CATProduct": [_row("P28A.1", part=plate)]},
        {"P28A": _plate()},
    )

    calls = {"n": 0}
    original = tools._part_box_entry

    def counting(part, body, include_curved):
        calls["n"] += 1
        return original(part, body, include_curved)

    tools._part_box_entry = counting
    reply = _walk(tools, top)
    assert calls["n"] == 1, "the local box does not depend on the placement"
    assert reply["part_boxes"] == 1
    assert reply["parts"][0]["instances"] == 2
    assert reply["max"]["x"] == 250.0


def test_the_parent_rotation_is_applied_after_the_child_one():
    """A·(B·p), never B·(A·p).

    Both levels rotate here, so the multiplication ORDER shows up in the answer -
    with only one rotation the two orders give the same matrix and the test would
    pass either way. Rz90 then Rx90 sends (1,0,0) to (0,1,0); the other order
    sends it to (0,0,1).
    """
    plate = _Part("P28A")
    sub = _Doc("SUB.CATProduct")
    top = _Doc("TOP.CATProduct")
    rz90 = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    rx90 = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
    sub_row = _row("SUB.1", kind="sub_assembly", nested=sub)
    sub_row["matrix"] = rz90
    part_row = _row("P28A.1", part=plate)
    part_row["matrix"] = rx90
    tools = _box_tools({"TOP.CATProduct": [sub_row],
                        "SUB.CATProduct": [part_row]},
                       {"P28A": [[1.0, 0.0, 0.0]]})

    reply = _walk(tools, top)
    assert reply["min"] == {"x": 0.0, "y": 1.0, "z": 0.0}
    assert reply["max"] == {"x": 0.0, "y": 1.0, "z": 0.0}


def test_a_nested_instance_is_not_found_by_component_name_and_the_error_says_so():
    """`component=` still matches TOP-LEVEL components; the message must not
    pretend the instance does not exist."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    tools._components_doc = lambda doc, only=None: []
    with pytest.raises(RuntimeError) as error:
        tools._assembly_box(_Doc("TOP.CATProduct"), "vertices", "P28A.1")
    assert "TOP-LEVEL" in str(error.value)
    assert "sub-assembly" in str(error.value)
