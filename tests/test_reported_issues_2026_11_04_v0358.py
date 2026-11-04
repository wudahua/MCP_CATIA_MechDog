# -*- coding: utf-8 -*-
"""0.3.58 - the number a failed ThickSurface never gave, and a hint that lied.

Two reports on 0.3.57, both about things it introduced or claimed:

1. `seam_hint` fired on an OPEN surface patch. The caller had a model whose
   thickening had failed, so only the surface was left; the skirt's top edge
   (r=33, z=110 - a BORDER) came back as three cross-wall creases at 180.0 deg
   and the hint told them to exclude r=33, which on a 66 mm bottle excludes
   everything. A patch has one skin, so the outer/inner split has nothing to
   split: 0.3.58 withholds cross_wall (and the hint) whenever boundary_edges > 0
   and says why in `skin_note`.

2. `catia_gsd_thick_surface` failed with "update failed" and no number. The
   caller rebuilt a 22-section model twice before working out by hand that the
   valley walls had a 0.19-0.24 mm radius against a 0.3 mm wall. The mesh can
   answer that: `_curvature_stats` estimates a radius per interior edge and the
   failure message now carries the tightest one and where it is.

The estimator's accuracy is pinned here on geometry whose radius is known -
`radius = dual length / normal angle` reads a few percent LOW, which is the
direction that makes a surface look tighter than it is, so the tests assert the
bias rather than pretending it is exact.
"""

from __future__ import annotations

import math

import pytest


# ───────────── a cylinder whose radius is known exactly

def _cylinder(path, radius=10.0, segments=72, height=10.0) -> str:
    faces = []
    for index in range(segments):
        t0 = 2 * math.pi * index / segments
        t1 = 2 * math.pi * (index + 1) / segments
        p = lambda r, t, z: (r * math.cos(t), r * math.sin(t), z)  # noqa: E731
        a0, a1 = p(radius, t0, 0.0), p(radius, t1, 0.0)
        b0, b1 = p(radius, t0, height), p(radius, t1, height)
        faces.append([a0, a1, b1])
        faces.append([a0, b1, b0])
    lines = ["solid cyl"]
    for verts in faces:
        ab = [verts[1][i] - verts[0][i] for i in range(3)]
        ac = [verts[2][i] - verts[0][i] for i in range(3)]
        cross = [ab[1] * ac[2] - ab[2] * ac[1],
                 ab[2] * ac[0] - ab[0] * ac[2],
                 ab[0] * ac[1] - ab[1] * ac[0]]
        length = math.sqrt(sum(c * c for c in cross)) or 1.0
        lines.append("facet normal %.6f %.6f %.6f"
                     % tuple(c / length for c in cross))
        lines.append("  outer loop")
        for vertex in verts:
            lines.append("    vertex %.6f %.6f %.6f" % vertex)
        lines.append("  endloop")
        lines.append("endfacet")
    lines.append("endsolid cyl")
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return str(path)


# ───────────── 1. the radius estimate is a radius, not a shape factor

def test_a_cylinder_of_radius_ten_reads_about_ten(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    stats = _curvature_stats(_cylinder(tmp_path / "cyl.stl"), 0.3, 3)
    radius = stats["tightest_smooth_radius_mm"]
    assert 9.5 <= radius <= 10.0, (
        f"a r=10 cylinder must read ~10, not {radius}; the estimate runs a few "
        f"percent low by construction")
    assert stats["edges_measured"] > 0
    worst = stats["tightest_edges"][0]
    assert set(worst) >= {"radius_mm", "fold_deg", "r", "theta_deg", "z"}


def test_the_estimate_runs_low_never_high(tmp_path):
    """A low radius makes a surface look tighter, which is the safe direction."""
    from catia_mcp.tools.measurement import _curvature_stats

    for segments in (24, 72):
        radius = _curvature_stats(_cylinder(tmp_path / f"c{segments}.stl",
                                            segments=segments),
                                  0.3, 1)["tightest_smooth_radius_mm"]
        assert radius <= 10.05, f"{segments} segments read {radius}, above the truth"


def test_the_thickness_check_counts_edges_that_cannot_take_the_offset(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    stl = _cylinder(tmp_path / "cyl.stl")
    tight = _curvature_stats(stl, 20.0, 3)
    check = tight["thickness_check"]
    assert check["edges_tighter_than_thickness"] > 0
    assert check["worst"]["radius_mm"] <= 10.0
    assert "bends tighter than the offset" in check["verdict"]
    assert check["worst"]["r"] > 5.0, "the location has to be on the surface"

    loose = _curvature_stats(stl, 0.3, 3)
    assert loose["thickness_check"]["edges_tighter_than_thickness"] == 0
    assert "not blocked by curvature" in loose["thickness_check"]["verdict"]


def test_no_thickness_means_no_verdict(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    assert "thickness_check" not in _curvature_stats(_cylinder(tmp_path / "c.stl"))


def test_the_limits_of_the_estimate_are_stated(tmp_path):
    from catia_mcp.tools.measurement import _curvature_stats

    stats = _curvature_stats(_cylinder(tmp_path / "c.stl"), 0.3)
    assert "facets" in stats["limits"]
    assert "crease" in stats["limits"]


# ───────────── 2. the failure message carries the measured radius

class _Conn:
    def __init__(self, part=None):
        self._part = part

    def ensure_connected(self):
        return None

    def get_active_part(self):
        return self._part


def test_a_failed_thickness_reports_the_radius_and_where(monkeypatch, tmp_path):
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.measurement import MeasurementTools

    stl = _cylinder(tmp_path / "cyl.stl")
    monkeypatch.setattr(MeasurementTools, "_mesh_export",
                        lambda self, part, limit=None, visit=None: visit(stl))
    tools = GSDTools(connection=None)
    tools.conn = _Conn(part=object())
    message = tools._offset_blocker(object(), 20.0)
    assert "MEASURED" in message
    assert "less than the 20.000 mm" in message
    assert "theta=" in message and "z=" in message
    assert "Widen that feature" in message


def test_a_failure_that_curvature_does_not_explain_says_so(monkeypatch, tmp_path):
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.measurement import MeasurementTools

    stl = _cylinder(tmp_path / "cyl.stl")
    monkeypatch.setattr(MeasurementTools, "_mesh_export",
                        lambda self, part, limit=None, visit=None: visit(stl))
    tools = GSDTools(connection=None)
    tools.conn = _Conn(part=object())
    message = tools._offset_blocker(object(), 0.3)
    assert "NOT below" in message
    assert "seam" in message


def test_when_the_mesh_cannot_be_read_the_reply_points_at_the_tool(monkeypatch):
    from catia_mcp.tools.gsd import GSDTools
    from catia_mcp.tools.measurement import MeasurementTools

    def boom(self, part, limit=None, visit=None):
        raise RuntimeError("the part is in an error state and will not export")

    monkeypatch.setattr(MeasurementTools, "_mesh_export", boom)
    tools = GSDTools(connection=None)
    tools.conn = _Conn(part=object())
    message = tools._offset_blocker(object(), 0.3)
    assert "catia_check_surface" in message
    assert "thickness" in message


# ───────────── 3. an open patch is not a wall

def _open_patch(path) -> str:
    """One quad, four boundary edges: a patch with a single skin."""
    verts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)]
    faces = [[verts[0], verts[1], verts[2]], [verts[0], verts[2], verts[3]]]
    lines = ["solid patch"]
    for tri in faces:
        lines += ["facet normal 0 0 1", "  outer loop"]
        lines += ["    vertex %.6f %.6f %.6f" % v for v in tri]
        lines += ["  endloop", "endfacet"]
    lines.append("endsolid patch")
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    return str(path)


def test_an_open_patch_withholds_cross_wall_and_says_why(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    stats = _fold_stats(_open_patch(tmp_path / "p.stl"), 15.0, 10000, None, 4)
    assert stats["boundary_edges"] == 4
    assert stats["fold_deg_by_side"]["cross_wall"]["edges"] == 0
    assert "seam_hint" not in stats
    assert "PATCH" in stats["skin_note"]


# ───────────── 4. documents: catalogs are named, and the note is about geometry

def _diagnostics():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    return DiagnosticsTools(connection=None)


class _Doc:
    def __init__(self, name, full_name):
        self.Name = name
        self.FullName = full_name


class _Docs:
    def __init__(self, docs):
        self._docs = docs
        self.Count = len(docs)

    def Item(self, index):                 # noqa: N802
        return self._docs[index - 1]


class _System:
    def __init__(self, root):
        self._root = root

    def Environ(self, name):               # noqa: N802
        return self._root


class _App:
    def __init__(self, docs, root):
        self.Documents = _Docs(docs)
        self.SystemService = _System(root)


INSTALL = r"D:\Program Files\Dassault Systemes\B32\win_b64"


def test_function_catalogs_are_counted_and_named():
    docs = [_Doc("mine.CATPart", r"F:\work\mine.CATPart"),
            _Doc("CATPipingSample.CATfct", INSTALL + r"\samples\CATPipingSample.CATfct"),
            _Doc("CATTubingSample.CATfct", INSTALL + r"\samples\CATTubingSample.CATfct")]
    block = _diagnostics()._open_documents(_App(docs, INSTALL))
    assert block["function_catalogs"] == 2
    assert set(block["function_catalog_names"]) == {"CATPipingSample.CATfct",
                                                    "CATTubingSample.CATfct"}
    assert block["shipped_with_catia"] == 2


def test_the_note_says_catalogs_cannot_be_closed_and_what_actually_costs():
    docs = [_Doc(f"d{i}.CATPart", rf"F:\work\d{i}.CATPart") for i in range(6)]
    docs.append(_Doc("CATPipingSample.CATfct", INSTALL + r"\samples\cat.CATfct"))
    note = _diagnostics()._open_documents(_App(docs, INSTALL))["note"]
    assert ".CATfct" in note and "re-mounts" in note
    assert "HOLD GEOMETRY" in note
    assert "1.09 s" in note and "1.6 s" in note
    assert "Eight EMPTY Parts changed nothing" in note


# ───────────── 5. the schema and the loft text carry the new findings

def test_check_surface_declares_thickness_and_the_patch_rule():
    from catia_mcp.tools.measurement import MeasurementTools

    tool = [t for t in MeasurementTools(connection=None).get_tool_definitions()
            if t["name"] == "catia_check_surface"][0]
    properties = tool["inputSchema"]["properties"]
    assert "thickness" in properties
    assert properties["thickness"]["exclusiveMinimum"] == 0
    text = tool["description"]
    assert "curvature" in text
    assert "boundary_edges" in text and "skin_note" in text


def test_the_loft_names_the_featureless_section_case():
    """The sentence belongs where the fold is explained: the failure message.

    That block explains why a loft self-intersects, and this is a second way to
    cause the same thing - so it goes in the message the caller reads on the
    failure, not in the schema they read before calling.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "catia_mcp/tools/gsd.py").read_text(
        encoding="utf-8")
    # The message is assembled from wrapped literals, so match what is contiguous.
    assert "NO feature beside one that carries a DEEP" in source
    assert "8.6 mm valley" in source, "the measured case has to be recognisable"
    assert "split the loft there" in source
