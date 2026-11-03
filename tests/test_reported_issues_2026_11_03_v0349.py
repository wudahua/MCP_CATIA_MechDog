# -*- coding: utf-8 -*-
"""0.3.49 - the 2026-11-03 surface report, measured on B30 before it was changed.

Six findings survived the measurement round; this file pins the five that are
decidable without CATIA. Everything asserted here was first observed on a live
B30 session (scripts/b30_loft_surface_claims.py, b30_surface_probe2.py,
b30_surface_probe3.py):

1. a spline through coordinates leaves one Point feature per point in the tree -
   90 of them for a 90-point ring, which is what made a geometrical set hold 666
   elements and 28 s to hide; the redundant per-point UpdateObject inside
   _point_ref is gone, and the reply now states how many tree elements the call
   created. It is NOT a speed fix: a cold-session A/B on B30 (one CATIA process
   per condition) measured 3.50/4.68 s with the update and 4.08/4.19 s without it;
   the 5x seen inside one session was drift (2.35 s vs 13.65 s for equal work);
2. a datum point section is ACCEPTED by AddSectionToLoft and then fails the
   update, a solid Vertex.N is refused by the same call - both must be refused
   before anything is built;
3. the loft failure text blamed the curve type; measured, the failing trio
   fails as circles, 12-point splines and 90-point splines alike;
4. a split now says which side it kept (orientation +1 = the cutter's normal
   side, measured with the STL: z 10..40 against 0..10);
5. the mesh bounding box is the envelope of the VISIBLE document, not the solid
   (10 mm pad + a surface at z 30..35 read 35 shown, 10 hidden).

pywin32_build is checked here too - it answered "unknown" on every machine.
"""

from __future__ import annotations

import json

import pytest


# ───────────────────────── 1. coordinate points are not updated one by one

class _Point:
    Name = "点.1"


class _Geoset:
    Name = "GSD_Set"

    def __init__(self):
        self.appended = []

    def AppendHybridShape(self, shape):
        self.appended.append(shape)


class _Factory:
    def __init__(self):
        self.splines = []

    def AddNewPointCoord(self, x, y, z):
        return _Point()

    def AddNewSpline(self):
        spline = type("S", (), {"points": [], "AddPoint": lambda self, r: self.points.append(r),
                                "SetClosing": lambda self, v: None})()
        self.splines.append(spline)
        return spline

    def AddNewLoft(self):
        return type("L", (), {
            "AddSectionToLoft": lambda self, ref, orientation, closing=None: None})()


class _Part:
    Name = "PART"

    def __init__(self):
        self.updated = []

    def CreateReferenceFromObject(self, obj):
        return f"ref:{getattr(obj, 'Name', obj)}"

    def UpdateObject(self, feature):
        self.updated.append(feature)


def _gsd():
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    part = _Part()
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
    })()
    tools._factory = lambda: factory
    tools._get_geoset = lambda: geoset
    return tools, part


factory = _Factory()
geoset = _Geoset()


def test_coordinate_points_are_not_updated_one_by_one():
    """The update is redundant - the feature-level one cascades into the point.

    Not a speed claim: see the module docstring for the cold-session numbers,
    where the update cost nothing measurable.

    As of 0.3.51 the points are also not appended to the geometrical set unless
    the caller asks (create_points=true), so the tree grows by the spline only.
    """
    tools, part = _gsd()
    tools._coordinate_points = 0
    geoset.appended.clear()
    tools._point_ref([1.0, 2.0, 3.0])
    # _point_ref on its own still builds the point in the tree: line and
    # plane_3points take one or three of them, so there is no clutter to avoid
    # and callers may want to reference them by name afterwards.
    assert len(geoset.appended) == 1, "a lone coordinate point is still a feature"
    assert part.updated == [], "the point was updated on its own"
    assert tools._coordinate_points == 1

    geoset.appended.clear()
    tools._point_refs([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], in_tree=False)
    assert geoset.appended == [], "points became tree features without being asked"

    geoset.appended.clear()
    tools._point_refs([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], in_tree=True)
    assert len(geoset.appended) == 2, "create_points=true must still build the points"


def test_spline_reply_says_the_points_are_not_tree_elements():
    tools, part = _gsd()
    replies = []
    tools._finish = lambda shape, name, message: replies.append(message) or message
    tools.execute("catia_gsd_spline",
                  {"points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "closed": True})
    assert "3 of them were coordinate lists" in replies[0]
    assert "did NOT become tree elements" in replies[0]
    assert "create_points=true" in replies[0]


def test_spline_reply_counts_the_tree_elements_when_asked_for_them():
    tools, part = _gsd()
    replies = []
    tools._finish = lambda shape, name, message: replies.append(message) or message
    tools.execute("catia_gsd_spline",
                  {"points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "closed": True,
                   "create_points": True})
    assert "3 extra tree elements" in replies[0]


# ───────────────────────── 2. point / vertex sections are refused up front

def test_vertex_section_is_refused_by_name_alone():
    from catia_mcp.results import ArgumentRejected

    tools, _ = _gsd()
    with pytest.raises(ArgumentRejected) as caught:
        tools._refuse_point_sections(["Vertex.3", "Circle.1"])
    assert "Vertex.3" in str(caught.value)
    assert "invalid number of arguments" in str(caught.value)
    # The workaround has to be in the refusal, or the caller is left with a
    # section that can never be consumed.
    assert "catia_gsd_fill" in str(caught.value)


def test_datum_point_section_is_refused_before_the_loft_is_built():
    from catia_mcp.results import ArgumentRejected

    tools, _ = _gsd()
    tools._resolve_sections = lambda names: [(object(), f"ref:{n}") for n in names]
    tools._section_kind = lambda name, element=None: "point" if name == "P" else "curve"
    built = []
    tools._factory = lambda: built.append(1) or factory
    with pytest.raises(ArgumentRejected):
        tools._multi_section_surface({"sections": ["P", "C"]})
    assert built == [], "a point section must be refused before AddNewLoft"


def test_a_curve_section_is_not_refused_by_the_guard():
    tools, _ = _gsd()
    tools._section_kind = lambda name, element=None: "curve"
    tools._refuse_point_sections(["A", "B"])   # must not raise


def test_each_section_is_looked_up_once():
    """The guard and the loft share one resolution, not two (23 ms each)."""
    tools, _ = _gsd()
    asked = []

    def resolve(names):
        asked.extend(names)
        return [(object(), f"ref:{n}") for n in names]

    tools._resolve_sections = resolve
    tools._section_kind = lambda name, element=None: "curve"
    tools._ref = lambda name: asked.append(f"REF:{name}") or f"ref:{name}"
    tools._finish = lambda shape, name, message: message
    tools._multi_section_surface({"sections": ["A", "B", "C"]})
    assert asked == ["A", "B", "C"], f"each section resolved more than once: {asked}"


# Known-point detection is by COM type name: one cheap call, where measuring the
# element cost 46 ms per section (0.46 s on a ten-section loft, B30 2026-11-03).

class _TypeInfo:
    def __init__(self, name):
        self._name = name

    def GetDocumentation(self, index):
        return (self._name, "", 0, "")


class _Ole:
    def __init__(self, name):
        self._name = name

    def GetTypeInfo(self):
        return _TypeInfo(self._name)


class _Node:
    def __init__(self, type_name):
        self.Name = "node"
        self._oleobj_ = _Ole(type_name)


@pytest.mark.parametrize("type_name,expected", [
    ("HybridShapePointCoord", "point"),
    ("HybridShapePointOnSurface", "point"),
    ("HybridShapeCircleCtrRad", "curve"),
    ("HybridShapeSpline", "curve"),
    ("HybridShapeExtract", "curve"),
])
def test_section_kind_reads_the_com_type_name(type_name, expected):
    tools, _ = _gsd()
    tools._find_element = lambda name: _Node(type_name)
    assert tools._section_kind("X") == expected


def test_section_kind_says_unknown_when_nothing_can_be_judged():
    tools, _ = _gsd()
    tools._find_element = lambda name: object()      # no _oleobj_, no SPA workbench
    tools.conn.active_document = None
    tools.conn.get_active_part = staticmethod(lambda: object())
    assert tools._section_kind("X") == "unknown"


# ───────────────────────── 3. the loft failure names the real cause

def test_loft_failure_echoes_the_call_and_names_the_fold_first():
    tools, _ = _gsd()
    tools._resolve_sections = lambda names: [(object(), f"ref:{n}") for n in names]
    tools._section_kind = lambda name, element=None: "curve"

    def boom(shape, name, message):
        raise RuntimeError("CATIA rejected the feature (update failed)")

    tools._finish = boom
    with pytest.raises(RuntimeError) as caught:
        tools._multi_section_surface({"sections": ["S1", "S2", "S3"]})
    text = str(caught.value)
    # The call's own numbers, which the six-line speech never carried.
    assert "THIS CALL: sections: 3 (3 curve), guides: 0, ruled: false" in text
    # The measured discriminator, first, and the advice that does NOT work.
    assert "ARRANGEMENT THAT MAKES THE INTERPOLATED SURFACE FOLD" in text
    assert "12-point CLOSED SPLINES" in text
    assert "is not a fix for this" in text
    # The old text told the caller to swap a circle for a closed spline.
    assert "build the section as a closed catia_gsd_spline through the " \
           "corners or as catia_gsd_circle" not in text


# ───────────────────────── 4. split says which side it kept

@pytest.mark.parametrize("reverse,expected", [
    (False, "the side the cutter's normal points to"),
    (True, "the side the cutter's normal points away from"),
])
def test_split_reply_names_the_kept_side(reverse, expected):
    tools, _ = _gsd()
    sent = []

    def finish(shape, name, message):
        sent.append(message)
        return message

    tools._ref = lambda name: f"ref:{name}"
    tools._factory = lambda: type("F", (), {
        "AddNewHybridSplit": lambda self, a, b, o: (sent.append(o), object())[1]})()
    tools._finish = finish
    tools.execute("catia_gsd_split",
                  {"element": "CYL", "cutter": "MID", "reverse": reverse})
    assert expected in sent[-1]
    assert "reverse=true swaps the two" in sent[-1]
    assert sent[0] == (-1 if reverse else 1), "orientation sent to CATIA changed"


# ───────────────────────── 5. the mesh box says what it measured

class _Shapes:
    Count = 1


class _Body:
    Shapes = _Shapes()


class _Parent:
    Name = "V0348_BOX.CATPart"


class _BoxPart:
    MainBody = _Body()
    Parent = _Parent()

    def __init__(self, sets: int):
        self.HybridBodies = type("HB", (), {"Count": sets})()


def _measurement(part):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
    })()
    tools._part_identity = lambda p: (_ for _ in ()).throw(RuntimeError("no identity"))
    tools._mesh_corners = lambda p: ([[0, 0, 0], [5, 5, 10]], 36, "")
    return tools


def test_mesh_box_declares_the_document_not_the_solid():
    payload = json.loads(_measurement(_BoxPart(1))._part_bbox(_BoxPart(1), "mesh"))
    assert payload["max"]["z"] == 10
    assert "VISIBLE" in payload["box_scope"]
    assert payload["geometrical_sets"] == 1
    assert any("z max 35" in warning for warning in payload["warnings"])


def test_mesh_box_without_geometrical_sets_is_the_solid():
    payload = json.loads(_measurement(_BoxPart(0))._part_bbox(_BoxPart(0), "mesh"))
    assert payload["box_scope"] == "the solid (this part has no geometrical sets)"
    assert "warnings" not in payload


# ───────────────────────── 6. pywin32 build number

def test_pywin32_build_is_a_number_not_unknown():
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    tools = DiagnosticsTools(connection=None)
    info = tools._python_info(object())
    assert info["pywin32_build"] != "unknown"
    parts = info["pywin32_build"].split(".")
    assert len(parts) == 4 and all(part.isdigit() for part in parts), info
