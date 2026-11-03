# -*- coding: utf-8 -*-
"""0.3.55 - what healing is actually for, measured on the case that asked for it.

The 0.3.53 model report said its five seams are "same source sections,
geometrically continuous, only parametrically broken" and asked whether
AddNewHealing would close them, since 0.3.51 had only tested a 90 degree fold.
The answer is no - but getting there required discovering that 0.3.51's test was
invalid on its own terms:

    HybridShapeHealing exposes AddBodyToHeal(iBody), SetMergingDistance,
    SetTangencyAngle, SetDistanceObjective, SetSharpnessAngle and
    ReplaceToHealElement. With NO body registered the feature is a pass-through
    that updates cleanly and changes nothing - which is exactly what 0.3.51
    measured, and what this repository's own probe reproduced (Continuity 0, 1
    and 2 gave byte-identical meshes) before the type info was read.

Measured with the body registered (scripts/b30_v0355_heal_pieces.py,
b30_v0355_verify_heal.py), every state isolated in its own STL:

    two lofts 0.05 mm apart          112 tris,   0 interior, 112 open edges
    heal C1, merging 0.5             112 tris,  24 interior,  64 open  <- closed
    heal C1, merging 0.001           112 tris,   0 interior, 112 open  <- no-op
    two lofts sharing a section,     0.52 deg before, 0.52 deg at C1 and C2
    two walls at 90 deg              4 tris / 90.0 deg before and after

So healing closes gaps up to the merging distance and changes nothing about
continuity. That is a real capability (stitching) and it now ships as
catia_gsd_heal; the "it does not smooth" half is stated in the schema, in the
reply and in README.
"""

from __future__ import annotations

import json

import pytest


# ───────────── 1. the tool drives the calls that matter

def _gsd():
    from catia_mcp.tools.gsd import GSDTools

    calls = {"bodies": [], "merging": [], "continuity": [], "names": []}

    class _Healed:
        Name = ""

        def __init__(self):
            self.Continuity = None
            self.NoOfBodiesToHeal = 0

        def AddBodyToHeal(self, ref):          # noqa: N802 - COM spelling
            calls["bodies"].append(ref)
            self.NoOfBodiesToHeal += 1

        def SetMergingDistance(self, value):   # noqa: N802
            calls["merging"].append(value)

    healed = _Healed()

    class _Factory:
        def AddNewHealing(self, ref):          # noqa: N802
            return healed

    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: object()),
    })()
    tools._ref = lambda name: f"ref:{name}"
    tools._factory = lambda: _Factory()

    def finish(shape, name, message):
        calls["names"].append(name)
        calls["continuity"].append(shape.Continuity)
        return message

    tools._finish = finish
    return tools, calls


def test_heal_registers_every_element():
    """Without AddBodyToHeal the feature heals nothing - the 0.3.51 mistake."""
    tools, calls = _gsd()
    tools.execute("catia_gsd_heal", {"elements": ["A", "B"], "continuity": 1,
                                     "merging_distance": 0.5, "name": "H"})
    assert calls["bodies"] == ["ref:A", "ref:B"]
    assert calls["merging"] == [0.5]
    assert calls["continuity"] == [1]
    assert calls["names"] == ["H"]


def test_heal_defaults_are_tangent_and_a_tenth_of_a_millimetre():
    tools, calls = _gsd()
    tools.execute("catia_gsd_heal", {"elements": ["A"]})
    assert calls["continuity"] == [1]
    assert calls["merging"] == [0.1]


def test_the_reply_states_both_halves():
    """A caller reading only the reply must know this will not smooth a seam."""
    tools, _ = _gsd()
    reply = tools.execute("catia_gsd_heal", {"elements": ["A", "B"]})
    assert "closes gaps" in reply
    assert "does NOT change continuity" in reply
    assert "0.52 deg" in reply


# ───────────── 2. validation before CATIA is touched

def test_heal_refuses_an_empty_element_list():
    from catia_mcp.results import ArgumentRejected

    tools, calls = _gsd()
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_gsd_heal", {"elements": []})
    assert getattr(caught.value, "stage", None) == "validate"
    assert calls["bodies"] == []


def test_heal_refuses_a_continuity_out_of_range():
    from catia_mcp.results import ArgumentOutOfRange

    tools, _ = _gsd()
    with pytest.raises(ArgumentOutOfRange):
        tools.execute("catia_gsd_heal", {"elements": ["A"], "continuity": 7})


def test_heal_refuses_a_zero_merging_distance():
    """0 would merge nothing and read like 'healing did not work'."""
    from catia_mcp.results import ArgumentOutOfRange

    tools, _ = _gsd()
    with pytest.raises(ArgumentOutOfRange):
        tools.execute("catia_gsd_heal", {"elements": ["A"],
                                        "merging_distance": 0})


def test_heal_is_declared_with_documented_limits():
    from catia_mcp.tools.gsd import GSDTools

    described = [tool for tool in GSDTools(connection=None).get_tool_definitions()
                 if tool["name"] == "catia_gsd_heal"]
    assert len(described) == 1
    text = described[0]["description"]
    assert "AddBodyToHeal" in text
    assert "does not change CONTINUITY".lower() in text.lower()
    assert "90" in text
    assert described[0]["inputSchema"]["required"] == ["elements"]


# ───────────── 3. the crease list is no longer capped at eight

def _bent_strip(tmp_path):
    """A strip of 12 quads, each 5 degrees off the previous one.

    Every quad contributes two triangles, so each edge along the strip belongs
    to exactly two triangles - a manifold fan with creases at every joint,
    which is enough to make the old fixed cap of 8 bite.
    """
    import math

    path = tmp_path / "bent.stl"
    radius, step = 100.0, 5.0
    points = []
    for index in range(13):
        angle = math.radians(index * step)
        points.append((round(radius * math.sin(angle), 6),
                       round(radius * math.cos(angle), 6)))
    faces = []
    for index in range(12):
        (y0, z0), (y1, z1) = points[index], points[index + 1]
        near0, near1 = (0.0, y0, z0), (10.0, y0, z0)
        far0, far1 = (0.0, y1, z1), (10.0, y1, z1)
        faces.append([near0, near1, far1])
        faces.append([near0, far1, far0])

    # The facet normal is computed from its own vertices: writing the same
    # normal for every facet would understate the angles and the fixture would
    # prove nothing.
    lines = ["solid t"]
    for verts in faces:
        ax = [verts[1][i] - verts[0][i] for i in range(3)]
        bx = [verts[2][i] - verts[0][i] for i in range(3)]
        cross = [ax[1] * bx[2] - ax[2] * bx[1],
                 ax[2] * bx[0] - ax[0] * bx[2],
                 ax[0] * bx[1] - ax[1] * bx[0]]
        length = math.sqrt(sum(c * c for c in cross)) or 1.0
        normal = [round(c / length, 6) for c in cross]
        lines.append(f"facet normal {normal[0]} {normal[1]} {normal[2]}")
        lines.append("  outer loop")
        for vertex in verts:
            lines.append(f"    vertex {vertex[0]} {vertex[1]} {vertex[2]}")
        lines.append("  endloop")
        lines.append("endfacet")
    lines.append("endsolid t")
    with open(path, "w", encoding="ascii", newline=chr(10)) as handle:
        handle.write(chr(10).join(lines) + chr(10))
    return str(path)

def test_top_creases_lists_more_than_eight_when_asked(tmp_path):
    from catia_mcp.tools.measurement import _fold_stats

    path = _bent_strip(tmp_path)
    few = _fold_stats(path, 1.0, 1000, None, 8)
    many = _fold_stats(path, 1.0, 1000, None, 40)
    assert few["creases"] > 8, "the fixture must produce more than eight"
    assert len(few["worst_creases"]) == 8
    assert len(many["worst_creases"]) == many["creases"]
    assert many["worst_creases_shown"] == len(many["worst_creases"])


def test_the_total_crease_count_survives_a_small_top(tmp_path):
    """'creases' is the total; the list is a window on it, never the count."""
    from catia_mcp.tools.measurement import _fold_stats

    path = _bent_strip(tmp_path)
    stats = _fold_stats(path, 1.0, 1000, None, 2)
    assert len(stats["worst_creases"]) == 2
    assert stats["worst_creases_shown"] == 2
    assert stats["creases"] > 2
