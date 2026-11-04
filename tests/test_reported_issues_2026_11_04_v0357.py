# -*- coding: utf-8 -*-
"""0.3.57 - a sliver is not a fold, and the documents CATIA opens for you.

The 2026-11-04 report did two things: it corrected its own root cause (the
124 deg it read on a thin wall was a sliver along a seam, not the inner skin of
a thin wall after all) and it asked for the split that would have told it so in
one call instead of a round of excluding radii one at a time:

    "A mixed edge whose two endpoints differ in radius by about the wall
    thickness and share a z is a cross-wall sliver. Report it separately."

The implementation does the same job without having to guess the wall thickness:
each VERTEX is assigned the skin its triangles mostly belong to, and an edge
whose endpoints are on opposite skins runs through the wall. That keeps the one
case that must NOT be flagged - a rim fold, where both endpoints sit on the same
skin and only the adjacent triangles disagree - out of the bucket, which is
exactly what a naive "edges at one radius" rule would have broken: on a 0.3 mm
tube all 64 rim edges read 90.0 deg at r=10 and are perfectly healthy.

Fixtures are synthesised here rather than exported from CATIA, so this runs in CI
with no session: a 36-segment thin-walled tube (outer r=10, inner r=9.7), clean,
then with one triangle made to bridge the wall, then with three.
"""

from __future__ import annotations

import math

import pytest


# ───────────── 1. a synthesised thin-walled tube, clean and bridged

N = 36
R_OUT, R_IN, Z0, Z1 = 10.0, 9.7, 0.0, 10.0


def _point(radius: float, angle: float, z: float):
    return (radius * math.cos(angle), radius * math.sin(angle), z)


def _tri(a, b, c, want):
    """Wind (a, b, c) so its normal points along ``want``."""
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    cross = [ab[1] * ac[2] - ab[2] * ac[1],
             ab[2] * ac[0] - ab[0] * ac[2],
             ab[0] * ac[1] - ab[1] * ac[0]]
    return [a, b, c] if sum(cross[i] * want[i] for i in range(3)) >= 0 else [a, c, b]


def _tube(bridges: tuple[int, ...] = ()):
    """Closed thin-walled tube; ``bridges`` lists segments replaced by a sliver."""
    faces = []
    for index in range(N):
        t0 = 2 * math.pi * index / N
        t1 = 2 * math.pi * (index + 1) / N
        ob0, ob1 = _point(R_OUT, t0, Z0), _point(R_OUT, t1, Z0)
        ot0, ot1 = _point(R_OUT, t0, Z1), _point(R_OUT, t1, Z1)
        ib0, ib1 = _point(R_IN, t0, Z0), _point(R_IN, t1, Z0)
        it0, it1 = _point(R_IN, t0, Z1), _point(R_IN, t1, Z1)
        mid = (t0 + t1) / 2
        outward = (math.cos(mid), math.sin(mid), 0.0)
        inward = (-math.cos(mid), -math.sin(mid), 0.0)
        if index in bridges:
            # One triangle from the outer skin to the inner one, and its
            # neighbour on the inner skin: the shared edge runs THROUGH the wall.
            faces.append(_tri(ob0, ob1, it0, outward))
            faces.append(_tri(ob1, ib0, it0, inward))
        else:
            faces.append(_tri(ob0, ob1, ot1, outward))
            faces.append(_tri(ob0, ot1, ot0, outward))
        faces.append(_tri(ib0, it1, ib1, inward))
        faces.append(_tri(ib0, it0, it1, inward))
        faces.append(_tri(ot0, ot1, it1, (0, 0, 1)))
        faces.append(_tri(ot0, it1, it0, (0, 0, 1)))
        faces.append(_tri(ob0, ib1, ob1, (0, 0, -1)))
        faces.append(_tri(ob0, ib0, ib1, (0, 0, -1)))
    return faces


def _write_stl(path, faces) -> str:
    lines = ["solid tube"]
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
    lines.append("endsolid tube")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="ascii", newline="\n")
    return str(path)


def _stats(tmp_path, name: str, bridges):
    from catia_mcp.tools.measurement import _fold_stats

    return _fold_stats(_write_stl(tmp_path / name, _tube(bridges)), 15.0, 200000, None, 8)


def test_a_clean_thin_wall_has_no_cross_wall_edges(tmp_path):
    """The rim is the case that must not be swept into the sliver bucket."""
    stats = _stats(tmp_path, "clean.stl", ())
    sides = stats["fold_deg_by_side"]
    assert sides["cross_wall"]["edges"] == 0
    assert sides["mixed"]["edges"] > 0
    assert sides["mixed"]["max"] == pytest.approx(90.0, abs=0.5), (
        "the 64 rim edges of a 0.3 mm tube are real 90 deg folds")
    assert all(not crease["cross_wall"] for crease in stats["worst_creases"])
    assert "seam_hint" not in stats


def test_one_bridging_triangle_is_flagged_and_the_rim_is_not(tmp_path):
    stats = _stats(tmp_path, "bridged.stl", (5,))
    sides = stats["fold_deg_by_side"]
    assert sides["cross_wall"]["edges"] == 1
    flagged = [crease for crease in stats["worst_creases"] if crease["cross_wall"]]
    assert len(flagged) == 1
    assert flagged[0]["fold_deg"] > 90.0
    # It sits between the skins, not on either of them.
    assert R_IN < flagged[0]["r"] < R_OUT
    rim = [crease for crease in stats["worst_creases"] if not crease["cross_wall"]]
    assert rim and all(crease["side"] == "mixed" for crease in rim)


def test_three_bridges_raise_the_seam_hint_with_a_radius_and_a_next_step(tmp_path):
    stats = _stats(tmp_path, "bridges.stl", (3, 10, 20))
    assert stats["fold_deg_by_side"]["cross_wall"]["edges"] == 3
    hint = stats.get("seam_hint")
    assert hint, "three cross-wall extremes at one radius is what the hint is for"
    assert "SEAM SLIVER" in hint
    assert "r " in hint and "mm" in hint
    assert "region" in hint, "the hint has to say how to confirm it"
    assert "cross_wall" in hint


def test_cross_wall_is_documented_as_a_subset_of_mixed():
    """Adding the two rows up would double-count; the report asked for a split."""
    from catia_mcp.tools.measurement import _fold_stats

    document = _fold_stats.__doc__ or ""
    assert "cross_wall" in document


# ───────────── 2. diagnose now says how many documents are open

class _Doc:
    def __init__(self, name, full_name):
        self.Name = name
        self.FullName = full_name


class _Docs:
    def __init__(self, docs):
        self._docs = docs
        self.Count = len(docs)

    def Item(self, index):                 # noqa: N802 - COM spelling
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


def _diagnostics():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    return DiagnosticsTools(connection=None)


INSTALL = r"D:\Program Files\Dassault Systemes\B30\win_b64"


def test_open_documents_lists_them_and_counts_the_shipped_ones():
    app = _App([_Doc("mine.CATPart", r"F:\work\mine.CATPart"),
                _Doc("CATPipingSample.CATfct",
                     INSTALL + r"\resources\samples\CATPipingSample.CATfct"),
                _Doc("CATWaveguideSample.CATfct",
                     INSTALL + r"\resources\samples\CATWaveguideSample.CATfct")], INSTALL)
    block = _diagnostics()._open_documents(app)
    assert block["count"] == 3
    assert block["names"][0] == "mine.CATPart"
    assert block["shipped_with_catia"] == 2
    assert set(block["shipped_names"]) == {"CATPipingSample.CATfct",
                                           "CATWaveguideSample.CATfct"}
    assert "note" not in block, "3 documents is not worth a lecture"


def test_the_note_appears_above_five_and_says_what_to_look_at():
    docs = [_Doc(f"d{index}.CATPart", rf"F:\work\d{index}.CATPart") for index in range(6)]
    docs.append(_Doc("CATPipingSample.CATfct",
                     INSTALL + r"\resources\samples\CATPipingSample.CATfct"))
    block = _diagnostics()._open_documents(_App(docs, INSTALL))
    assert block["count"] == 7
    note = block["note"]
    assert "7 documents are open" in note
    assert "1 of them shipped with CATIA" in note
    # The note must not claim the count is the cost: eight empty documents
    # changed nothing on B30, and a wrong cause is worse than no cause.
    assert "changed nothing" in note


def test_open_documents_never_raises():
    class Broken:
        @property
        def Documents(self):               # noqa: N802
            raise RuntimeError("no Documents")

    block = _diagnostics()._open_documents(Broken())
    assert "unavailable" in block


# ───────────── 3. the seam advice lives where the seam is made

def test_join_says_where_to_put_a_seam_on_a_thin_wall():
    from catia_mcp.tools.gsd import GSDTools

    described = [tool for tool in GSDTools(connection=None).get_tool_definitions()
                 if tool["name"] == "catia_gsd_join"][0]
    text = described["description"]
    assert "WHERE TO PUT THE SEAM" in text
    assert "CURVED" in text
    assert "cross_wall" in text


def test_check_surface_declares_the_split():
    from catia_mcp.tools.measurement import MeasurementTools

    described = [tool for tool in MeasurementTools(connection=None).get_tool_definitions()
                 if tool["name"] == "catia_check_surface"][0]
    text = described["description"]
    assert "cross_wall" in text
    assert "seam_hint" in text
    assert "NOT 'cross_wall'" in text or "not cross_wall" in text.lower()


def test_readme_records_the_older_release_cannot_open_newer_files():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    known = text.split("## Known limits", 1)[1]
    assert "may not open on an older one" in known
    assert "B32" in known
