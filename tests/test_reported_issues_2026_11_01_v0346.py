# -*- coding: utf-8 -*-
"""Regression tests for the 2026-11-01 report on 0.3.45: the assembly mesh route.

0.3.45 taught `_part_box_entry` a third value of `source` - "mesh", the one you
get when you ASK for every part's mesh - and left every reader of that field
comparing against "mesh_fallback" alone. So the whole promise of that release
("method='mesh' on an assembly now means every part from its mesh") died on its
first real call with `'float' object is not subscriptable`: a flat point cloud
handed to a function that indexes it as triplets, after the STL export had
already been paid for.

The fix is not the two lines the report suggested - there were five readers
asking the same question by hand, and the counters were three of them. There is
one predicate now (`measurement._from_mesh`) and a test at the bottom of this
file that refuses a sixth spelling.

Reported 2026-11-01 by the 20-part / 46-instance acceptance assembly on B32 SP6:
`catia_get_bounding_box(document="…CATProduct", method="mesh")` -> STEP_EXCEPTION
after 1.3 s, and `component="P08_post.1"` -> the same after 4.6 s of export.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


class _Part:
    def __init__(self, name: str):
        self.Name = name
        self.MainBody = SimpleNamespace(Name="PartBody")


class _Doc:
    def __init__(self, name: str, kind: str = "Part"):
        self.Name = name
        self.FullName = name
        self.Type = kind


def _row(name: str, part: _Part, translation=(0.0, 0.0, 0.0)):
    return {"index": 1, "name": name, "part_number": name.split(".")[0],
            "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": list(translation), "part": part, "nested_doc": None,
            "kind": "part", "problems": []}


# A drum-shaped part: its mesh extremes are on the curved face, so its mesh cloud
# reaches ±12 while its four corner vertices only reach ±10. That 2 mm is exactly
# the kind of shortfall this whole route exists to stop being under-reported.
_MESH_CLOUD = [
    -12.0, -10.0, -5.0, 12.0, -10.0, -5.0, 12.0, 10.0, -5.0, -12.0, 10.0, -5.0,
    -12.0, -10.0, 5.0, 12.0, -10.0, 5.0, 12.0, 10.0, 5.0, -12.0, 10.0, 5.0,
]
_CORNER_POINTS = [[-10.0, -10.0, -5.0], [10.0, 10.0, 5.0]]


def _tools(mesh_parts: set[str] | None = None):
    """An assembly of two instances of one part. `mesh_parts` names parts that
    only have a mesh cloud; every other part answers from vertices."""
    from catia_mcp.tools.measurement import MeasurementTools, reset_session_books

    reset_session_books()
    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    part = _Part("DRUM")
    asm = _Doc("ASM.CATProduct", "Product")
    tools._components_doc = lambda doc, only=None: (
        [row for row in (_row("DRUM.1", part), _row("DRUM.2", part, (100.0, 0.0, 0.0)))
         if only is None or row["name"] == only]
        if str(doc.Name) == "ASM.CATProduct" else [])
    tools._part_identity = lambda p: str(p.Name)

    def entry(p, body, include_curved, force_mesh=False):
        from catia_mcp.tools.measurement import MESH_ASKED_FOR, MESH_NO_VERTEX

        want_mesh = force_mesh or str(p.Name) in (mesh_parts or set())
        if want_mesh:
            return {"points": list(_MESH_CLOUD), "used": len(_MESH_CLOUD) // 3,
                    "source": "mesh" if force_mesh else "mesh_fallback",
                    "mesh_reason": MESH_ASKED_FOR if force_mesh else MESH_NO_VERTEX,
                    "mesh_vertices": len(_MESH_CLOUD) // 3,
                    "truncated": False, "copy_note": ""}
        return {"points": [list(p) for p in _CORNER_POINTS], "used": 8,
                "source": "vertices", "mesh_vertices": None, "copy_note": "",
                "cells": 12, "nls_chars": 80}

    tools._part_box_entry = entry
    return tools, asm


# ────────────────────────── the crash, on both assembly routes

def test_the_whole_assembly_mesh_route_returns_a_box_instead_of_crashing():
    """The reported call. Before 0.3.46 this raised TypeError after the export."""
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "mesh"))
    # Two instances, each ±12 in x locally, the second placed 100 mm along x.
    assert reply["min"]["x"] == pytest.approx(-12.0)
    assert reply["max"]["x"] == pytest.approx(112.0)
    # The mesh reaches 12 where the vertices reach 10 - the 2 mm this route buys.
    assert reply["dimensions"]["length_x"] == pytest.approx(124.0)
    assert reply["complete"] is True


def test_one_component_mesh_route_returns_a_box_instead_of_crashing():
    """The report's second call, and the one that paid 4.6 s before dying."""
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "mesh", only="DRUM.1"))
    assert reply["min"]["x"] == pytest.approx(-12.0)
    assert reply["max"]["x"] == pytest.approx(12.0)
    assert reply["instances"]["total"] == 1


def test_a_mesh_entry_is_transformed_per_vertex_not_per_corner():
    """A rotated placement proves the flat cloud really went through the matrix.

    90 degrees about Z maps (x, y) -> (-y, x): the ±12 / ±10 part becomes
    x ±10, y ±12. A box built from the unrotated two corners cannot produce that.
    """
    tools, asm = _tools(mesh_parts={"DRUM"})
    rows = tools._components_doc(asm)[:1]
    rows[0]["matrix"] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    tools._components_doc = lambda doc, only=None: rows
    reply = json.loads(tools._assembly_box(asm, "mesh"))
    assert reply["min"]["x"] == pytest.approx(-10.0)
    assert reply["max"]["x"] == pytest.approx(10.0)
    assert reply["min"]["y"] == pytest.approx(-12.0)
    assert reply["max"]["y"] == pytest.approx(12.0)


# ────────────────────────── the counters, which also only knew one spelling

def test_the_two_route_counters_add_up_to_the_part_count():
    """0.3.45 answered by_vertices=0, by_mesh=0, part_boxes=1 in one reply."""
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "mesh"))
    assert reply["part_boxes_by_mesh"] == reply["part_boxes"]
    assert reply["part_boxes_by_vertices"] == 0
    assert (reply["part_boxes_by_mesh"] + reply["part_boxes_by_vertices"]
            == reply["part_boxes"])


def test_the_vertex_route_still_counts_itself_only():
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "vertices"))
    assert reply["part_boxes_by_vertices"] == reply["part_boxes"]
    assert reply["part_boxes_by_mesh"] == 0


def test_a_mesh_bounded_part_pays_for_an_export_not_for_topology():
    """The reason field has the same five-spellings problem, and it moved too."""
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "mesh"))
    assert reply["cold_reasons"] == {"mesh_export": reply["session_first_parts"]}
    assert reply["session_first_parts"] == reply["part_boxes"]


# ────────────────────────── the per-part detail, which knew one spelling too

def test_a_mesh_part_detail_says_which_route_ran_and_why():
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "mesh"))
    detail = reply["parts"][0]
    assert detail["source"] == "mesh"
    assert detail["points_route"] == "mesh"
    assert detail["sweep_route"] == "mesh"
    assert detail["mesh_vertices"] == len(_MESH_CLOUD) // 3
    # The reason differs from the fallback's: here the caller asked for it.
    assert "method='mesh'" in detail["source_note"]
    assert "no vertex lies at this part's extremum" not in detail["source_note"]
    # The dimensions come from the flat cloud, not from two corner triplets.
    assert detail["local_dimensions"]["length_x"] == pytest.approx(24.0)


def test_a_fallback_part_still_explains_itself_as_a_fallback():
    """The other spelling must not lose its own sentence in the merge."""
    tools, asm = _tools(mesh_parts={"DRUM"})
    rows = tools._components_doc(asm)[:1]
    tools._components_doc = lambda doc, only=None: rows
    # vertices asked for, but this part can only answer from its mesh
    original = tools._part_box_entry

    def entry(p, body, include_curved, force_mesh=False):
        # The fixture answers "mesh" for this part on any route; the fallback
        # spelling is what a VERTICES call produces when the part has no vertex
        # at an extremum, and that is the sentence under test here.
        made = original(p, body, include_curved, force_mesh)
        if made.get("source") == "mesh":
            made = dict(made, source="mesh_fallback")
        return made

    tools._part_box_entry = entry
    reply = json.loads(tools._assembly_box(asm, "vertices"))
    detail = reply["parts"][0]
    assert detail["source"] == "mesh_fallback"
    assert "no vertex lies at this part's extremum" in detail["source_note"]


# ────────────────────────── the sentence above the numbers

def test_an_all_mesh_assembly_says_every_box_came_from_the_mesh():
    tools, asm = _tools()
    reply = json.loads(tools._assembly_box(asm, "mesh"))
    assert reply["bounds"].startswith("every component's box came from its mesh")


def test_a_mixed_assembly_says_mixed_and_still_names_one_lower_bound():
    """One part answers from vertices, one can only answer from its mesh."""
    from catia_mcp.tools.measurement import MeasurementTools, reset_session_books

    reset_session_books()
    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    flat, drum = _Part("FLAT"), _Part("DRUM")
    asm = _Doc("ASM.CATProduct", "Product")
    tools._components_doc = lambda doc, only=None: [
        _row("FLAT.1", flat), _row("DRUM.1", drum)]
    tools._part_identity = lambda p: str(p.Name)
    tools._part_box_entry = lambda p, body, curved, force_mesh=False: (
        {"points": [list(v) for v in _CORNER_POINTS], "used": 8,
         "source": "vertices", "mesh_vertices": None, "copy_note": "",
         "cells": 12, "nls_chars": 80}
        if str(p.Name) == "FLAT" else
        {"points": list(_MESH_CLOUD), "used": len(_MESH_CLOUD) // 3,
         "source": "mesh_fallback", "mesh_vertices": len(_MESH_CLOUD) // 3,
         "truncated": False, "copy_note": ""})

    reply = json.loads(tools._assembly_box(asm, "vertices"))
    assert reply["bounds"].startswith("mixed: 1 instance(s)")
    assert reply["part_boxes_by_vertices"] == 1
    assert reply["part_boxes_by_mesh"] == 1
    assert reply["part_boxes"] == 2


# ────────────────────────── the guard: one spelling, or the test fails

def test_no_reader_compares_a_source_against_a_spelling():
    """The defect was a reader that spelled the question out and got it wrong.

    Five readers did; 0.3.46 routed all of them through `_from_mesh`, and the one
    that needed to know WHICH mesh route now reads the producer's `mesh_reason`
    instead of re-deriving it from the string. This test refuses the sixth:
    nowhere in the package may a `["source"]` / `.get("source")` value be compared
    against a spelling, and the pair itself may only be written in its definition.

    (`method == "mesh"` is a comparison against the ARGUMENT, not against a
    source, and is allowed - which is why this test looks at the operand rather
    than at the literal alone.)
    """
    package = Path(__file__).resolve().parents[1] / "catia_mcp"
    from catia_mcp.tools.measurement import MESH_SOURCES

    def reads_a_source(node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            key = node.slice
            return isinstance(key, ast.Constant) and key.value == "source"
        if isinstance(node, ast.Call):
            func = node.func
            return (getattr(func, "attr", None) == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "source")
        return False

    offenders: list[str] = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed_lines = {node.lineno for node in ast.walk(tree)
                         if isinstance(node, ast.Assign)
                         and any(getattr(t, "id", None) == "MESH_SOURCES"
                                 for t in node.targets)}
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) in allowed_lines:
                continue
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if not any(reads_a_source(o) for o in operands):
                    continue
                for operand in operands:
                    if (isinstance(operand, ast.Constant)
                            and operand.value in MESH_SOURCES):
                        offenders.append(
                            f"{path.name}:{node.lineno} compares a source against "
                            f"{operand.value!r} - call _from_mesh() instead")
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                values = {e.value for e in node.elts
                          if isinstance(e, ast.Constant)
                          and isinstance(e.value, str)}
                if values >= MESH_SOURCES:
                    offenders.append(
                        f"{path.name}:{node.lineno} writes the pair out by hand "
                        "- import MESH_SOURCES instead")
    assert not offenders, "\n".join(offenders)


def test_the_predicate_answers_for_both_spellings_and_nothing_else():
    from catia_mcp.tools.measurement import MESH_SOURCES, _from_mesh

    assert MESH_SOURCES == {"mesh", "mesh_fallback"}
    assert _from_mesh("mesh")
    assert _from_mesh("mesh_fallback")
    assert not _from_mesh("vertices")
    assert not _from_mesh(None)
