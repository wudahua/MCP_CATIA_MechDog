# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-25 v0.3.16 batch (three reports).

The two that mattered most were both about memory outliving its meaning: the
sketch outline record was keyed by sketch NAME only, so every part's 草图.1
shared one record - the profile echo of a fresh part listed the rectangle drawn
in the PREVIOUS part (reproduced on B30: "1 circle(s), 1 rectangle(s)" for a
part that only ever got a circle), and `gsd_spline(element=)` could hand GSD
another document's corners.
"""

from __future__ import annotations

import inspect

import pytest


# --------------------------------------------------------------------------
# 1. Sketch memory is per document, not per name.
# --------------------------------------------------------------------------

class _Doc:
    def __init__(self, name):
        self.Name = name


class _Conn:
    def __init__(self, doc="A.CATPart"):
        self.sketch_outlines = {}
        self.active_document = _Doc(doc)


def _sketcher(doc="A.CATPart"):
    from catia_mcp.tools.sketcher import SketcherTools

    return SketcherTools(_Conn(doc))


def test_the_same_sketch_name_in_two_documents_does_not_share_a_record():
    a, b = _sketcher("A.CATPart"), _sketcher("B.CATPart")
    # Same connection would be the real case; here the point is the key.
    a._drawn[a._sketch_key("草图.1")] = [{"kind": "rectangle"}]
    assert b._sketch_key("草图.1") != a._sketch_key("草图.1")
    assert b._drawn.get(b._sketch_key("草图.1")) is None


def test_the_key_carries_the_document_name():
    sketcher = _sketcher("V16.CATPart")
    assert sketcher._sketch_key("草图.1") == "V16.CATPart::草图.1"


def test_no_document_still_produces_a_usable_key():
    from catia_mcp.tools.sketcher import SketcherTools

    sketcher = SketcherTools(connection=None)
    assert sketcher._sketch_key("草图.1").endswith("::草图.1")


def test_the_echo_reads_only_the_current_sketch():
    sketcher = _sketcher()
    sketcher.conn.active_document = _Doc("B.CATPart")
    key = sketcher._sketch_key("草图.1")
    sketcher._drawn[key] = [{"kind": "circle", "cx": 0, "cy": 0, "radius": 3,
                             "sweep_rad": 6.283185307179586}]
    sketcher._drawn["A.CATPart::草图.1"] = [{"kind": "rectangle", "x1": -5, "y1": -5,
                                            "x2": 5, "y2": 5}]
    summary = sketcher._profile_summary("草图.1")
    assert "1 circle(s)" in summary
    assert "rectangle" not in summary


def test_creating_a_sketch_clears_its_record():
    from catia_mcp.tools.sketcher import SketcherTools

    source = inspect.getsource(SketcherTools._create_sketch)
    assert "self._drawn[key] = []" in source
    assert 'record["elements"] = []' in source


# --------------------------------------------------------------------------
# 2. gsd_spline(element=) says what it needs, and does not silently borrow.
# --------------------------------------------------------------------------

def _gsd(outlines=None, doc="SAVED.CATPart"):
    from catia_mcp.tools.gsd import GSDTools

    class _Conn:
        def __init__(self):
            self.sketch_outlines = outlines or {}
            self.active_document = _Doc(doc)

    return GSDTools(_Conn())


def test_a_reopened_file_gets_a_readable_refusal_naming_the_document():
    gsd = _gsd()
    with pytest.raises(RuntimeError) as error:
        gsd._outline_points("草图.3", True)
    message = str(error.value)
    assert "No outline recorded" in message
    assert "SAVED.CATPart" in message
    assert "reopening a file does not restore them" in message
    assert "'points'" in message


def test_a_record_from_another_document_is_not_used():
    gsd = _gsd({"OTHER.CATPart::草图.1": {"placement": {"base": "xy", "distance": 0},
                                          "elements": []}})
    with pytest.raises(RuntimeError) as error:
        gsd._outline_points("草图.1", True)
    assert "No outline recorded" in str(error.value)


def test_a_record_without_its_sketch_is_refused():
    """The record can outlive the sketch: CATIA renumbers on delete."""
    gsd = _gsd({"SAVED.CATPart::草图.1": {
        "placement": {"base": "xy", "distance": 0.0},
        "elements": [{"kind": "rectangle", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]}})
    gsd._sketch_exists = lambda name: False
    with pytest.raises(RuntimeError) as error:
        gsd._outline_points("草图.1", True)
    assert "is not in 'SAVED.CATPart'" in str(error.value)


# --------------------------------------------------------------------------
# 3. measure_topology: another document, and "there is no solid".
# --------------------------------------------------------------------------

def test_measure_topology_takes_a_document():
    from catia_mcp.tools.planner import PlannerTools

    doc = [d for d in PlannerTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_measure_topology"][0]
    assert "document" in doc["inputSchema"]["properties"]
    assert "component of an open assembly" in doc["description"]


class _Shapes:
    def __init__(self, count):
        self.Count = count


class _Body:
    def __init__(self, name, count):
        self.Name = name
        self.Shapes = _Shapes(count)


def test_an_empty_body_is_reported_as_having_no_solid():
    from catia_mcp.tools.planner import PlannerTools

    planner = PlannerTools(connection=None)
    body = _Body("零件几何体", 0)
    planner._topology_target = lambda document: (type("P", (), {"Name": "E"})(), body)
    counts = planner._count_topology()
    assert counts["solid"] is False
    assert "no solid at all" in counts["note_empty"]


def test_a_body_with_features_is_marked_as_a_solid():
    from catia_mcp.tools.planner import PlannerTools

    class _EmptySelection:
        """No cells found: the search runs, finds nothing, and the body is solid."""

        Count = 0

        def Clear(self):
            pass

        def Add(self, item):
            pass

        def Search(self, query):
            pass

    class _Conn:
        hso = _EmptySelection()

    planner = PlannerTools(connection=_Conn())
    planner._topology_target = lambda document: (type("P", (), {"Name": "P"})(),
                                                 _Body("PartBody", 3))
    counts = planner._count_topology()
    assert counts["solid"] is True
    assert "note_empty" not in counts


def test_an_unknown_document_lists_the_open_ones():
    from catia_mcp.tools.planner import PlannerTools

    class _Docs:
        Count = 0

    class _Conn:
        documents = _Docs()

        def resolve_document(self, name):
            # The single resolver, as the real connection provides it: no match,
            # no ambiguity. (0.3.36 moved this rule out of the planner, which used
            # to own a second copy of the "first hit wins" loop.)
            return None, []

    planner = PlannerTools(connection=_Conn())
    with pytest.raises(RuntimeError) as error:
        planner._topology_target("NOPE")
    assert "No open document matches" in str(error.value)
    assert "catia_open_document" in str(error.value)


# --------------------------------------------------------------------------
# 4. catia_delete_body.
# --------------------------------------------------------------------------

class _Bodies:
    def __init__(self, bodies):
        self._bodies = bodies
        self.Count = len(bodies)

    def Item(self, key):
        if isinstance(key, int):
            return self._bodies[key - 1]
        raise KeyError(key)


class _Sel:
    def __init__(self):
        self.deleted = []
        self.on_delete = None

    def Clear(self):
        pass

    def Add(self, item):
        self.added = item

    def Delete(self):
        self.deleted.append(self.added)
        if self.on_delete is not None:
            self.on_delete(self.added)


class _Part:
    def __init__(self, bodies):
        self.Bodies = _Bodies(bodies)
        self.MainBody = bodies[0]


def _part_tools(bodies, shrink=True):
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(bodies)

    class _Conn:
        hso = _Sel()
        active_body = None

        def ensure_connected(self):
            pass

        def get_active_part(self):
            return part

        def get_active_part_body(self):
            return part.MainBody

        def refresh_display(self):
            pass

    tools = PartDesignTools(_Conn())

    def drop(body):
        if body in part.Bodies._bodies:
            part.Bodies._bodies.remove(body)
            part.Bodies.Count = len(part.Bodies._bodies)

    if shrink:
        tools.conn.hso.on_delete = drop
    return tools, part


def _delete_and_shrink(tools, part, name, force=False):
    return tools._delete_body(name, force)


def test_a_deletion_that_did_not_take_is_reported_rather_than_claimed():
    tools, part = _part_tools([_Body("零件几何体", 1), _Body("SPARE", 0)],
                              shrink=False)
    with pytest.raises(RuntimeError) as error:
        tools._delete_body("SPARE")
    assert "still there" in str(error.value)


def test_deleting_an_empty_body_reports_the_new_count():
    tools, part = _part_tools([_Body("零件几何体", 1), _Body("SPARE", 0)])
    result = _delete_and_shrink(tools, part, "SPARE")
    assert "deleted (2 -> 1 bodies)" in result


def test_a_body_that_holds_features_is_refused_without_force():
    tools, _ = _part_tools([_Body("零件几何体", 1), _Body("USED", 2)])
    with pytest.raises(RuntimeError) as error:
        tools._delete_body("USED")
    assert "still holds 2 feature(s)" in str(error.value)
    assert "force=true" in str(error.value)


def test_force_deletes_and_says_what_went_with_it():
    tools, part = _part_tools([_Body("零件几何体", 1), _Body("USED", 2)])
    result = _delete_and_shrink(tools, part, "USED", force=True)
    assert "Its 2 feature(s) went with it" in result


def test_the_main_body_is_never_deleted():
    tools, _ = _part_tools([_Body("零件几何体", 1), _Body("SPARE", 0)])
    with pytest.raises(RuntimeError) as error:
        tools._delete_body("零件几何体")
    assert "MainBody" in str(error.value)


def test_an_unknown_body_lists_the_real_ones():
    tools, _ = _part_tools([_Body("零件几何体", 1)])
    with pytest.raises(RuntimeError) as error:
        tools._delete_body("NOPE")
    assert "零件几何体" in str(error.value)


def test_delete_body_is_registered():
    from catia_mcp.tools.part_design import PartDesignTools

    names = [d["name"] for d in PartDesignTools(connection=None).get_tool_definitions()]
    assert "catia_delete_body" in names


# --------------------------------------------------------------------------
# 5. Save As onto a file that is already open.
# --------------------------------------------------------------------------

def test_save_as_onto_an_open_file_names_the_holder():
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    holder = type("D", (), {"Name": "SAVED.CATPart"})()
    tools._document_at_path = lambda path: holder
    tools._component_usage = lambda: {"SAVED.CATPart": ["ASM.CATProduct"]}

    class _Active:
        Name = "NEW.CATPart"

    class _Conn:
        active_document = _Active()

    tools.conn = _Conn()
    with pytest.raises(RuntimeError) as error:
        tools._save_document("/tmp/SAVED.CATPart")
    message = str(error.value)
    assert "ASM.CATProduct" in message
    assert "overwrite=true does NOT help" in message
    assert "close" in message.lower()


# --------------------------------------------------------------------------
# 6. The failure texts that were asked for.
# --------------------------------------------------------------------------

def test_pad_no_material_checks_the_direction_before_blaming_the_profile():
    """0.3.19 replaced the blanket "retry once" with a measurement. 0.3.20 went
    further and got it wrong: the measurement was a SECOND pad built from a sketch
    the failed pad had already consumed, so it answered "nothing either way" for a
    direction that works, and removing it took the sketch with it (reported
    2026-10-26). It now flips the SAME node and re-measures, and a flip that
    produces material is a success, not a diagnosis."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._pad)
    assert "DirectionOrientation = 0 if direction == \"reverse\" else 1" in source
    assert "auto-flipped to reach material" in source
    assert "Both directions were MEASURED" in source
    assert "_sketch_present" in source
    # The throwaway probe is what destroyed the caller's sketch.
    assert "_pad_other_direction" not in source
    assert "this once more" not in source
    assert "//n" not in source


def test_the_three_face_hint_names_the_previous_fillet():
    from catia_mcp.tools.part_design import PartDesignTools

    hint = PartDesignTools._coplanar_hint(["E (L1.0|A;1|B;2|C;3)"])
    assert "shares an endpoint" in hint
    assert "edge_names" in hint


def test_thick_surface_documents_the_thin_section_limit():
    from catia_mcp.tools.gsd import GSDTools

    doc = [d for d in GSDTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_gsd_thick_surface"][0]
    text = doc["description"]
    assert "SMALLEST RADIUS OF CURVATURE" in text
    assert "1.27/1.27" in text and "2.54/0" in text
    assert "padded polygon" in text


def test_fill_failure_explains_closed_boundaries():
    from catia_mcp.tools.gsd import GSDTools

    source = inspect.getsource(GSDTools._fill)
    assert "CLOSED boundaries" in source
    assert "session-sensitive" in source


def test_sketch_spline_says_a_polyline_is_the_accurate_choice():
    from catia_mcp.tools.sketcher import SketcherTools

    doc = [d for d in SketcherTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_sketch_spline"][0]
    text = doc["description"]
    assert "IT IS NOT A POLYLINE" in text
    assert "+29.3 %" in text and "+0.48 %" in text
