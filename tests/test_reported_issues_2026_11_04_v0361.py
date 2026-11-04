# -*- coding: utf-8 -*-
"""0.3.61 - the verdict that contradicted the model it was measuring.

Two reports on 0.3.60, both about the same failure mode from different ends: a
number the mesh cannot support, quoted as geometry.

**The verdict.** On a surface CATIA had just thickened by 0.3 mm,
``check_surface(thickness=0.3)`` answered "cannot be built without the two faces
crossing". Reproduced on B30 the same day with this project's own delivered
bottle base: 103 edges below 0.3 mm, the tightest a 0.0888 mm radius across a
3.3218 mm edge - 2.7 % of its own length, with 7.16 deg of fold, which is a bend
of about 27 mm, not 0.09. The reply's own ``limits`` string had said since 0.3.51
that "a radius below the edge length is smaller than this mesh can measure, not a
measured value". Nothing acted on it.

So it acts now: edges whose radius is under a tenth of their own length are
counted in ``excluded.unresolvable_edges`` and kept out of the ranking and the
verdict, and the verdict is decided by the 5th percentile rather than by one
edge. Same surface after: min 0.1412, p5 1.326, 49 edges below 0.3 -
INCONCLUSIVE, which is what the mesh can honestly say.

A second rule - comparing the circumcentre radius with L/(2 sin(f/2)) and
dropping disagreements - was written, measured and REMOVED: it excluded 3388 of
9411 edges and changed neither the minimum nor the count below the offset. A rule
that removes a third of the evidence and moves no conclusion would later be
quoted as if it did something.

**The failure message.** ``thick_surface`` now retries once by itself (the
operator is session-sensitive: reported failing on the last step of a 13-step
plan, then succeeding called alone, then succeeding again with everything
visible), says so when the retry is what worked, and its diagnosis now names the
SCOPE of the mesh it read (every visible element, not the one being thickened)
and gives a CONDITIONAL prescription when the tight edge sits on an open border.

Also here, all found while reproducing the above: an export that writes an empty
mesh (37 bytes, 0 triangles, reported as "ok") is refused and the file deleted;
an empty mesh no longer gets the "loaded inside an assembly" paragraph, which it
never was; ``catia_export`` switches CATIA's file alerts off, because exporting
over an existing file raised a modal "overwrite?" and hung; ``catia_gsd_revolve``
takes ``axis='z'`` like rotate does; the plane aliases are in the SCHEMA now (a
client validates before the server can normalise, so 0.3.60's aliases were
unreachable); and the tensor probe no longer walks assemblies that cannot contain
the part (3103.8 ms -> 42.0 ms with no product open).
"""

from __future__ import annotations

import math
import types

import pytest

from catia_mcp.tools import gsd as gsd_module
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.gsd import GSDTools, _missing_key_error
from catia_mcp.tools.measurement import _curvature_stats


# ───────────── fixtures whose answer is known by construction

def _cylinder(path, radius=10.0, segments=36, height=10.0, dx=0.0, dz=0.0) -> str:
    """An open tube. ``dx``/``dz`` shift it, so two tubes can share one file."""
    lines = ["solid cyl"]
    for index in range(segments):
        t0 = 2 * math.pi * index / segments
        t1 = 2 * math.pi * (index + 1) / segments
        def p(r, t, z):
            return (r * math.cos(t) + dx, r * math.sin(t), z + dz)
        a0, a1 = p(radius, t0, 0.0), p(radius, t1, 0.0)
        b0, b1 = p(radius, t0, height), p(radius, t1, height)
        for verts in ([a0, a1, b1], [a0, b1, b0]):
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


# ───────────── 1. the mesh cannot resolve it, so it is not a measurement

def test_a_cylinder_is_not_dropped_by_the_resolution_rule(tmp_path):
    """The regression this rule nearly shipped: L 30, R 10 is L/R = 3, legitimate.

    A tube one band tall has seam-direction edges 10 mm long with a 10 mm radius -
    the edge runs along a direction with no curvature. An earlier draft excluded
    anything with radius < edge length and threw away the whole cylinder.
    """
    stats = _curvature_stats(_cylinder(tmp_path / "tube.stl", segments=36), 0.3, 3)
    assert 9.5 <= stats["min_radius_mm"] <= 10.2
    assert stats["excluded"]["unresolvable_edges"] == 0, (
        "a r=10 cylinder is the case the rule must NOT touch")


def test_the_resolution_rule_only_takes_radii_far_below_their_own_edge(tmp_path):
    """L/R > 10 is the threshold; L/R = 3 is kept (see above), so the rule bites
    only where the number cannot describe a single arc."""
    assert gsd_module is not None  # module import stays cheap
    from catia_mcp.tools.measurement import RESOLVABLE_LENGTH_RATIO
    assert RESOLVABLE_LENGTH_RATIO == 10.0


# ───────────── 2. the verdict follows the population, not the minimum

def test_an_offset_the_mesh_clearly_cannot_carry_still_says_so(tmp_path):
    """A r=1 tube against a 5 mm offset: EVERY edge is below it, p5 included."""
    stats = _curvature_stats(_cylinder(tmp_path / "small.stl", radius=1.0,
                                       segments=36, height=4.0), 5.0, 3)
    check = stats["thickness_check"]
    assert check["edges_tighter_than_thickness"] > 0
    assert check["radius_p5_mm"] < 5.0
    assert "cannot be built without the two faces crossing" in check["verdict"]
    assert "population of edges" in check["verdict"], (
        "the reason has to be that many edges agree, not that one edge was small")


def test_a_few_tight_edges_out_of_many_is_inconclusive_not_a_refusal(tmp_path):
    """The reported case: one tight reading, a population that is fine.

    Two tubes in one file - a wide one carrying most of the edges and a small one
    contributing a few tighter readings - is the shape of the reported surface:
    a handful of suspicious edges on a model that thickens.
    """
    path = tmp_path / "mixed.stl"
    wide = _cylinder(path, radius=10.0, segments=200, height=10.0)
    with open(wide, encoding="ascii") as handle:
        wide_text = handle.read()
    narrow = _cylinder(tmp_path / "narrow.stl", radius=0.2, segments=8, height=1.0,
                       dx=100.0)
    with open(narrow, encoding="ascii") as handle:
        narrow_text = handle.read()
    # Splice the second body INSIDE the first solid. Splitting on "endfacet" and
    # re-joining drops the keyword and the parser then reads one triangle.
    extra = narrow_text.split("\n", 1)[1].rsplit("endsolid", 1)[0]
    mixed = wide_text.replace("endsolid cyl", extra + "endsolid mixed")
    path.write_text(mixed, encoding="ascii", newline="\n")

    stats = _curvature_stats(str(path), 0.3, 3)
    check = stats["thickness_check"]
    assert check["edges_tighter_than_thickness"] > 0, "the tight edges must be seen"
    assert check["radius_p5_mm"] > 0.3, "but they must not be the population"
    assert check["verdict"].startswith("INCONCLUSIVE")
    assert "Do not re-design on the minimum alone" in check["verdict"]


def test_the_verdict_reports_the_percentile_it_used(tmp_path):
    stats = _curvature_stats(_cylinder(tmp_path / "t.stl"), 5.0, 2)
    assert stats["thickness_check"]["radius_p5_mm"] == pytest.approx(10.0, rel=0.05)


# ───────────── 3. the thick-surface retry

class _Feature:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Part:
    def __init__(self, factory) -> None:
        self.InWorkObject = None
        self.ShapeFactory = factory

    def UpdateObject(self, feature: object) -> None:
        return None


class _ShapeFactory:
    def __init__(self, fail_first: bool) -> None:
        self.fail_first = fail_first
        self.attempts = 0

    def AddNewThickSurface(self, ref, symmetric, thickness1, thickness2):
        self.attempts += 1
        return _Feature(f"Thick.{self.attempts}")


class _Conn:
    def __init__(self, factory: _ShapeFactory) -> None:
        self.factory = factory
        self.part = _Part(factory)

    def ensure_connected(self) -> None:
        return None

    def get_active_part(self):
        return self.part

    def get_active_part_body(self):
        return object()

    def refresh_display(self) -> None:
        return None


def _thick(fail_first: bool, succeed_on_retry: bool):
    factory = _ShapeFactory(fail_first)
    tools = GSDTools(connection=_Conn(factory))
    tools._ref = lambda name: ("ref", name)          # type: ignore[method-assign]
    tools._offset_blocker = lambda part, total: " [diagnosis]"  # type: ignore[method-assign]
    calls = {"n": 0}

    def fake_update(feature):
        calls["n"] += 1
        if calls["n"] == 1 or not succeed_on_retry:
            raise RuntimeError("CATIA rejected the feature (update failed)")
    tools._safe_update = fake_update                 # type: ignore[method-assign]
    return tools, factory


def test_a_thick_surface_retries_once_before_it_reports_a_failure():
    tools, factory = _thick(fail_first=True, succeed_on_retry=True)
    message = tools._thick_surface({"surface": "S", "thickness1": 0.3})
    assert factory.attempts == 2, "one retry, not none and not three"
    assert "SECOND attempt" in message
    assert "session-sensitive" in message
    assert "do not re-design" in message


def test_two_failures_say_twice_and_keep_the_first_reason():
    tools, factory = _thick(fail_first=True, succeed_on_retry=False)
    with pytest.raises(RuntimeError) as caught:
        tools._thick_surface({"surface": "S", "thickness1": 0.3})
    text = str(caught.value)
    assert factory.attempts == 2
    assert "failed TWICE" in text
    assert "first attempt:" in text
    assert "[diagnosis]" in text


def test_a_first_attempt_that_works_says_nothing_about_retries():
    factory = _ShapeFactory(fail_first=False)
    tools = GSDTools(connection=_Conn(factory))
    tools._ref = lambda name: ("ref", name)          # type: ignore[method-assign]
    tools._safe_update = lambda feature: None        # type: ignore[method-assign]
    message = tools._thick_surface({"surface": "S", "thickness1": 0.3})
    assert factory.attempts == 1
    assert "SECOND" not in message


# ───────────── 4. an empty mesh is not an in-context refusal

class _Collection:
    def __init__(self, items: list) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int):
        return self._items[index - 1]


def _document(bodies: list, geosets: list):
    return types.SimpleNamespace(
        Part=types.SimpleNamespace(Bodies=_Collection(bodies),
                                   HybridBodies=_Collection(geosets)))


def test_an_empty_part_is_still_refused_before_the_call():
    tools = ExportTools(connection=None)
    with pytest.raises(ValueError, match="Nothing to export"):
        tools._assert_has_mesh_geometry(_document([], []), "stl")


def test_a_part_with_geometry_is_let_through_to_the_size_check():
    """Visibility cannot be read (HybridShape.Visible raises), so the empty
    export is caught AFTER the call - this guard must not try."""
    tools = ExportTools(connection=None)
    tools._assert_has_mesh_geometry(
        _document([], [types.SimpleNamespace(
            HybridShapes=types.SimpleNamespace(Count=2))]), "stl")


def test_an_empty_mesh_has_its_own_message_not_the_assembly_one():
    from catia_mcp.tools.measurement import EmptyMesh
    assert issubclass(EmptyMesh, RuntimeError)
    text = EmptyMesh.__doc__ or ""
    assert "in-context" in text, (
        "the class exists precisely to stop that paragraph swallowing the cause")


# ───────────── 5. the aliases have to be in the schema to be reachable

def test_the_plane_alias_is_declared_and_not_required():
    schemas = {tool["name"]: tool for tool in GSDTools(connection=None)
               .get_tool_definitions()}
    circle = schemas["catia_gsd_circle"]["inputSchema"]
    assert "support" in circle["properties"] and "plane" in circle["properties"]
    assert "support_plane" not in circle["required"], (
        "a required canonical name makes every alias unreachable - the client "
        "validates before the server can normalise")

    offset = schemas["catia_gsd_plane_offset"]["inputSchema"]
    assert "base_plane" not in offset["required"]
    assert "support" in offset["properties"]

    intersection = schemas["catia_gsd_intersection"]["inputSchema"]
    assert "cutter" in intersection["properties"]
    assert "element2" not in intersection["required"]


def test_a_missing_plane_names_all_three_spellings():
    message = _missing_key_error("catia_gsd_circle", {"center": [0, 0, 0], "radius": 5})
    assert "'support_plane' or 'support' or 'plane'" in message


def test_giving_any_spelling_satisfies_the_check():
    for spelling in ("support_plane", "support", "plane"):
        assert _missing_key_error(
            "catia_gsd_circle",
            {"center": [0, 0, 0], "radius": 5, spelling: "xy"}) is None


# ───────────── 6. revolve takes the axis shorthand

def test_revolve_accepts_an_origin_axis_like_rotate(tmp_path):
    tools = GSDTools(connection=_Conn(_ShapeFactory(False)))
    tools._ref = lambda name: ("ref", name)          # type: ignore[method-assign]
    tools._axis_ref = lambda spec, owner: (("axis", spec), " built")
    tools._factory = lambda: types.SimpleNamespace(   # type: ignore[method-assign]
        AddNewRevol=lambda profile, a1, a2, axis: _Feature("Rev"))
    tools._finish = lambda shape, name, message: message  # type: ignore[method-assign]
    message = tools._revolve({"profile": "P", "axis": "z", "angle1": 90})
    assert "about 'z'" in message and "built" in message
