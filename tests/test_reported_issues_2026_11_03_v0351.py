# -*- coding: utf-8 -*-
"""0.3.51 - the 2026-11-03 surface-quality report, measured before it was changed.

The report asks for three things CATIA cannot give over automation, so the
measurement round (scripts/b30_v0351_probe.py, b30_v0351_probe2.py,
b30_v0351_healing.py, b30_v0351_healing2.py) settled what is actually available:

1. coordinate points need not be tree features. Measured on B30: a 90-point
   spline whose points are never appended to the geometrical set survives save +
   close + reopen with the tree holding ONE element and the curve still
   188.496 mm for r=30 (analytic 188.496). The 636 Point features the report
   counted were therefore a choice, and the default is now the tree-less route;
2. AddNewHealing exists and accepts one argument plus a `Continuity` property,
   but it does NOT soften a G0 fold: a 90 deg join stayed 90.0 deg at Continuity
   0, 1 and 2, with the same triangle count (4 before, 4 after);
3. zebra, curvature comb and Match are not reachable - the viewer answers
   RenderingMode and returns an unresolvable attribute for Zebra /
   EnvironmentMapping / AnalysisMode, and HybridShapeFactory has no
   AddNewMatch. So the quality question is answered from the mesh instead, which
   is what catia_check_surface does.

Deleting 91 orphan points also took 49.9 s the old way (each name re-walked the
whole tree and was selected and deleted on its own); the batch now walks once.
"""

from __future__ import annotations

import json
import struct

import pytest


# ───────────────────────── 1. the mesh really answers "is it smooth"

def _write_binary_stl(path, faces):
    """faces: [((nx, ny, nz), [(x, y, z), (x, y, z), (x, y, z)]), ...]"""
    blob = b""
    for normal, verts in faces:
        blob += struct.pack("<3f", *normal)
        for vertex in verts:
            blob += struct.pack("<3f", *vertex)
        blob += struct.pack("<H", 0)
    open(path, "wb").write(b"\0" * 80 + struct.pack("<I", len(faces)) + blob)


def _write_ascii_stl(path, faces):
    lines = ["solid test"]
    for normal, verts in faces:
        lines.append("  facet normal %f %f %f" % normal)
        lines.append("    outer loop")
        for vertex in verts:
            lines.append("      vertex %f %f %f" % vertex)
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    open(path, "w", encoding="ascii").write("\n".join(lines) + "\n")


def test_a_right_angle_fold_reads_ninety_degrees(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    path = tmp_path / "fold.stl"
    # Two triangles sharing the edge (0,0,0)-(1,0,0), one lying in z=0 and one
    # in y=0: the dihedral across their shared edge is exactly 90 degrees.
    _write_binary_stl(path, [
        ((0, 0, 1), [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
        ((0, 1, 0), [(0, 0, 0), (1, 0, 0), (0, 0, 1)]),
    ])
    stats = _fold_stats(str(path), 25.0, 1000)
    assert stats["triangles"] == 2
    assert stats["interior_edges"] == 1
    assert stats["fold_deg"]["max"] == 90.0
    assert stats["creases"] == 1
    # Six edges across two triangles, but the shared one counts once: four
    # distinct borders plus the fold.
    assert stats["boundary_edges"] == 4


def test_coplanar_triangles_read_zero(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    path = tmp_path / "flat.stl"
    _write_binary_stl(path, [
        ((0, 0, 1), [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
        ((0, 0, 1), [(1, 0, 0), (1, 1, 0), (0, 1, 0)]),
    ])
    stats = _fold_stats(str(path), 25.0, 1000)
    assert stats["fold_deg"]["max"] == 0.0
    assert stats["creases"] == 0, "a flat pair is not a crease"


def test_an_ascii_stl_is_read_as_well_as_a_binary_one(tmp_path):
    """CATIA writes ASCII when the export options say so.

    Reading such a file as binary raised "unpack requires a buffer of 48 bytes"
    the first time this was written - the header's triangle count and the file
    length simply do not agree.
    """
    from catia_mcp.tools.measurement import _fold_stats

    binary = tmp_path / "b.stl"
    ascii_path = tmp_path / "a.stl"
    faces = [
        ((0, 0, 1), [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
        ((0, 1, 0), [(0, 0, 0), (1, 0, 0), (0, 0, 1)]),
    ]
    _write_binary_stl(binary, faces)
    _write_ascii_stl(ascii_path, faces)
    left = _fold_stats(str(binary), 25.0, 1000)
    right = _fold_stats(str(ascii_path), 25.0, 1000)
    assert left["triangles"] == right["triangles"] == 2
    assert left["fold_deg"]["max"] == right["fold_deg"]["max"] == 90.0


def test_the_triangle_cap_is_reported_not_silently_applied(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    path = tmp_path / "many.stl"
    _write_binary_stl(path, [
        ((0, 0, 1), [(i, 0, 0), (i + 1, 0, 0), (i, 1, 0)]) for i in range(10)])
    stats = _fold_stats(str(path), 25.0, 3)
    assert stats["triangles"] == 3


# ───────────────────────── 2. deleting a batch selects once

class _Item:
    def __init__(self, name):
        self.Name = name


class _Items:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, index):
        return self._items[index - 1]


class _Sets:
    def __init__(self, sets):
        self._sets = sets
        self.Count = len(sets)

    def Item(self, index):
        return self._sets[index - 1]


class _HybridBody:
    def __init__(self, name, shapes):
        self.Name = name
        self.HybridShapes = _Items(shapes)
        self.HybridSketches = _Items([])
        self.HybridBodies = _Items([])


class _Selection:
    def __init__(self):
        self.added = []
        self.deletes = 0
        self.clears = 0

    def Clear(self):
        self.clears += 1
        self.added = []

    def Add(self, item):
        self.added.append(item)

    def Delete(self):
        self.deletes += 1


def _part_with(names):
    shapes = [_Item(name) for name in names]
    return type("P", (), {
        "HybridBodies": _Sets([_HybridBody("GSD_Set", shapes)]),
        "Update": staticmethod(lambda: None),
    })()


def test_a_batch_delete_walks_the_tree_once_and_selects_once():
    """91 points took 49.9 s when every name was looked up and deleted alone."""
    from catia_mcp.tools.part_design import PartDesignTools

    names = [f"点.{index}" for index in range(1, 92)]
    part = _part_with(names)
    selection = _Selection()
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "get_active_part": staticmethod(lambda: part),
        "hso": selection,
        "refresh_display": staticmethod(lambda: None),
    })()

    removed, kept = tools._delete_owned(names)
    assert kept == []
    assert len(removed) == 91
    assert selection.deletes == 1, "the batch was deleted one element at a time"
    assert len(selection.added) == 0, "the selection was not cleared afterwards"


def test_a_name_that_is_already_gone_counts_as_removed():
    from catia_mcp.tools.part_design import PartDesignTools

    part = _part_with(["点.1"])
    selection = _Selection()
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "get_active_part": staticmethod(lambda: part),
        "hso": selection,
        "refresh_display": staticmethod(lambda: None),
    })()
    removed, kept = tools._delete_owned(["点.1", "点.2"])
    assert kept == []
    assert set(removed) == {"点.1", "点.2"}


# ───────────────────────── 3. the described span limit stays described

def test_the_loft_description_warns_that_building_is_not_thickenable():
    from catia_mcp.tools.gsd import GSDTools

    text = [tool for tool in GSDTools(connection=None).get_tool_definitions()
            if tool["name"] == "catia_gsd_multi_section_surface"][0]["description"]
    assert "SPAN LIMIT" in text
    assert "2-3 section pieces" in text


def test_the_thick_surface_description_names_the_loft_curvature_cause():
    from catia_mcp.tools.gsd import GSDTools

    text = [tool for tool in GSDTools(connection=None).get_tool_definitions()
            if tool["name"] == "catia_gsd_thick_surface"][0]["description"]
    assert "A SMOOTH LOFT THAT BUILT IS NOT A LOFT THAT CAN BE THICKENED" in text


def test_check_surface_says_what_it_cannot_do():
    """The tool must not imply it produces zebra stripes."""
    from catia_mcp.tools.measurement import MeasurementTools

    text = [tool for tool in MeasurementTools(connection=None).get_tool_definitions()
            if tool["name"] == "catia_check_surface"][0]["description"]
    assert "ZEBRA CHECK ITSELF DOES NOT EXIST HERE" in text
    assert "AddNewMatch" in text


def test_check_surface_is_reachable_through_execute():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    captured = {}
    tools._check_surface = (lambda crease, cap, doc, region=None, top=8,
                                   thickness=None: captured.update(
        {"crease": crease, "cap": cap, "doc": doc, "region": region, "top": top,
         "thickness": thickness}) or "{}")
    tools.execute("catia_check_surface", {"crease_deg": 15, "cap": 1000,
                                          "document": "X.CATPart"})
    assert captured == {"crease": 15.0, "cap": 1000, "doc": "X.CATPart",
                        "region": None, "top": 8, "thickness": None}


def test_check_surface_defaults_are_the_documented_ones():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    captured = {}
    tools._check_surface = (lambda crease, cap, doc, region=None, top=8,
                                   thickness=None: captured.update(
        {"crease": crease, "cap": cap, "region": region, "top": top,
         "thickness": thickness}) or "{}")
    tools.execute("catia_check_surface", {})
    assert captured["thickness"] is None, "0.3.58 added it as an opt-in argument"
    assert captured["crease"] == 25.0
    assert captured["cap"] == 600000
