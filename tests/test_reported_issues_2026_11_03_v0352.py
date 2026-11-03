# -*- coding: utf-8 -*-
"""0.3.52 - duplicate names, measured on B30 before anything was changed.

The 2026-11-03 fourth report says deleting a loft and rebuilding it under the
same name breaks the shared-edge topology, so join/split/thick all fail, and
offers "CATIA gives the new element a new internal id while the old topology
association survives" as the cause. Measured on B30 (scripts/b30_v0352_reloft.py,
b30_v0352_join.py), none of it reproduced:

  * delete one loft, rebuild it under the same name, join      -> OK
  * delete BOTH lofts, rebuild both, join                      -> OK
  * also delete and rebuild the SHARED section, then join      -> OK
  * two lofts taking geometrically identical but DIFFERENT
    curve objects for the shared section, join                 -> OK, so join
    compares coincidence, not object identity
  * sections of 60/90/120/180/600 points (0.60 deg apart, 0.29 mm of arc at
    r=28), two lofts sharing one, join                          -> OK at every
    density, so the report's "below 1 deg the border stops joining" is not a
    property of the spline or of join either

What DID reproduce is the thing that makes such a session unfalsifiable: CATIA
lets two elements carry the same name, assigning Name does not check, and this
server produced them. Two lofts named C_P, a delete that removes one of them, and
FindObjectByName still answering to the name - so "I deleted it and rebuilt it"
can be true and still leave the old element in place, which is exactly the
symptom the report chased for two hours.
"""

from __future__ import annotations

import pytest


# ───────────────────────── fake part with a name table

class _Elem:
    def __init__(self, name):
        self.Name = name


class _Items:
    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        return self._items[index - 1]


class _Sets:
    def __init__(self, sets):
        self._sets = sets

    @property
    def Count(self):
        return len(self._sets)

    def Item(self, index):
        return self._sets[index - 1]


class _HybridBody:
    def __init__(self, name, shapes=(), sketches=(), children=()):
        self.Name = name
        self.HybridShapes = _Items(list(shapes))
        self.HybridSketches = _Items(list(sketches))
        self.HybridBodies = _Sets(list(children))


class _Part:
    def __init__(self, *sets):
        self.HybridBodies = _Sets(list(sets))
        self.Bodies = _Sets([])

    def FindObjectByName(self, name):
        for hybrid_body in self.HybridBodies._sets:
            if hybrid_body.Name == name:
                return hybrid_body
            for item in hybrid_body.HybridShapes._items:
                if item.Name == name:
                    return item
        return None

    def Update(self):
        return None


class _Selection:
    def __init__(self):
        self.added = []
        self.deletes = 0

    def Clear(self):
        self.added = []

    def Add(self, item):
        self.added.append(item)

    def Delete(self):
        self.deletes += 1


# ───────────────────────── 1. a new element does not steal a taken name

def test_a_taken_name_gets_a_suffix():
    from catia_mcp.tools.gsd import GSDTools

    part = _Part(_HybridBody("GSD_Set", [_Elem("P_04_06")]))
    tools = GSDTools(connection=None)
    assert tools._free_name(part, "P_04_06") == "P_04_06.2"
    assert tools._free_name(part, "P_04_07") == "P_04_07"


def test_the_suffix_keeps_climbing():
    from catia_mcp.tools.gsd import GSDTools

    part = _Part(_HybridBody("GSD_Set",
                             [_Elem("P"), _Elem("P.2"), _Elem("P.3")]))
    assert GSDTools(connection=None)._free_name(part, "P") == "P.4"


def test_the_reply_says_the_name_had_to_change():
    """A silent rename is fine; a silent DUPLICATE is what cost two hours."""
    from catia_mcp.tools.gsd import GSDTools

    part = _Part(_HybridBody("GSD_Set", [_Elem("P")]))
    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
    })()
    updated = []
    tools._safe_update = lambda shape: updated.append(shape)
    geoset = _HybridBody("GSD_Set", [])
    shape = _Elem("Multi-sections Surface.1")
    message = tools._completed(shape, "P", "Surface created.", geoset)
    assert shape.Name == "P.2"
    assert "already taken" in message and "'P.2'" in message
    assert updated == [shape]


def test_a_free_name_is_kept_as_given():
    from catia_mcp.tools.gsd import GSDTools

    part = _Part(_HybridBody("GSD_Set", []))
    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "refresh_display": staticmethod(lambda: None),
    })()
    tools._safe_update = lambda shape: None
    geoset = _HybridBody("GSD_Set", [])
    shape = _Elem("x")
    message = tools._completed(shape, "P", "Surface created.", geoset)
    assert shape.Name == "P"
    assert "already taken" not in message


# ───────────────────────── 2. a duplicate name cannot be deleted blindly

def test_deleting_by_a_name_two_elements_share_is_refused():
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(_HybridBody("GSD_Set", [_Elem("C_P"), _Elem("C_P")]))
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
    })()
    with pytest.raises(ArgumentRejected) as caught:
        tools.execute("catia_delete_feature", {"feature_name": "C_P"})
    text = str(caught.value)
    assert "2 elements" in text
    # The refusal has to point at a route that works, not just say no.
    assert "names=['C_P']" in text


def test_every_matching_object_is_collected_not_just_the_first():
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(_HybridBody("GSD_Set", [_Elem("A"), _Elem("A"), _Elem("B")]))
    found = PartDesignTools._map_geometrical_sets(part, {"A", "B"})
    assert len(found["A"]) == 2
    assert len(found["B"]) == 1


def test_names_deletes_every_duplicate_and_says_how_many():
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(_HybridBody("GSD_Set", [_Elem("C_P"), _Elem("C_P")]))
    selection = _Selection()
    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "get_active_part": staticmethod(lambda: part),
        "hso": selection,
        "refresh_display": staticmethod(lambda: None),
    })()
    reply = tools.execute("catia_delete_feature", {"names": ["C_P"]})
    assert "Deleted 2 element(s) for 1 name(s) given." in reply
    assert "'C_P' x2" in reply
    assert selection.deletes == 1, "the duplicates were deleted one at a time"
    assert len(selection.added) == 0


def test_nested_sets_are_searched_for_duplicates_too():
    from catia_mcp.tools.part_design import PartDesignTools

    inner = _HybridBody("Inner", [_Elem("P")])
    outer = _HybridBody("Outer", [_Elem("P")], children=[inner])
    part = _Part(outer)
    found = PartDesignTools._map_geometrical_sets(part, {"P"})
    assert len(found["P"]) == 2


# ───────────────────────── 3. what the report claimed, pinned as NOT true

def test_join_does_not_care_which_curve_object_the_section_is():
    """Two lofts can share a section GEOMETRICALLY through different objects.

    Measured on B30 2026-11-03: circles A_C2 and A_C2b are built from the same
    parameters, one loft ends on the first and the next starts on the second, and
    the join still succeeds. So a rebuilt section does not by itself break a
    join, which is what the report's "new internal id" theory requires.
    """
    from catia_mcp.tools import gsd as gsd_module

    source = gsd_module.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    # The join failure text has to stay a diagnosis of the EDGE, not of an id.
    assert "A join needs a SHARED EDGE" in text
