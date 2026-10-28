# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-28 batch: two acceptance reports on 0.3.31.

Report 1 (B32) closed four of five items and left three narrow ones behind:

* the alias a rebuild writes was resolved by Part Design only - ``catia_pad``
  knew 草图.2 was 草图.3, ``catia_sketch_get_geometry`` answered "No sketch named
  '草图.2'" for the same live sketch - and the table held ONE hop, so the third
  retry with the caller's original name answered SKETCH_NOT_FOUND while the
  sketch sat in the tree as 草图.4;
* the rebuild path dropped the ``It held:`` echo 0.3.30 had, so the caller could
  not see the profile from the reply, could not read it from the sketch tools,
  and could not see it in the listing;
* ``sweep_route`` was published on the single-part route only, although the
  changelog said "both routes".

Report 2 wants the cold pass to be schedulable (``warm_up``) and the 5 394.6 ms
that no entry in ``parts`` owns to be published (``overhead_ms``), because a
share-of-total table built from ``parts`` alone was short by 23.6 %.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest


# ─────────────── 1. one definition of "what does this sketch name mean"
def test_a_second_rebuild_re_points_the_name_the_caller_holds():
    """retry 1 and 2 worked, retry 3 answered SKETCH_NOT_FOUND (reported)."""
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    conn.note_sketch_alias("草图.2", "草图.3", document="P.CATPart")
    assert conn.sketch_alias("草图.2", document="P.CATPart") == "草图.3"
    # CATIA deletes the rebuild too, and this server rebuilds it again.
    conn.note_sketch_alias("草图.3", "草图.4", document="P.CATPart")
    assert conn.sketch_alias("草图.2", document="P.CATPart") == "草图.4"
    # ...and the table stays one level deep, so a lookup never has to walk.
    assert conn.sketch_aliases["P.CATPart::草图.2"] == "草图.4"
    assert conn.sketch_alias("草图.3", document="P.CATPart") == "草图.4"


def test_a_chain_written_by_an_older_build_still_resolves():
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    conn.sketch_aliases = {"P::草图.2": "草图.3", "P::草图.3": "草图.4"}
    assert conn.sketch_alias("草图.2", document="P") == "草图.4"


def test_a_loop_in_the_table_terminates():
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    conn.sketch_aliases = {"P::草图.2": "草图.3", "P::草图.3": "草图.2"}
    assert conn.sketch_alias("草图.2", document="P") in ("草图.2", "草图.3")


def test_an_alias_belongs_to_one_document():
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    conn.note_sketch_alias("草图.2", "草图.3", document="A.CATPart")
    assert conn.sketch_alias("草图.2", document="A.CATPart") == "草图.3"
    assert conn.sketch_alias("草图.2", document="B.CATPart") is None


def test_the_listing_can_name_every_alias_of_a_live_sketch():
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    conn.note_sketch_alias("草图.2", "草图.3", document="P")
    conn.note_sketch_alias("草图.3", "草图.4", document="P")
    conn.note_sketch_alias("草图.7", "草图.8", document="P")
    assert conn.sketch_aliases_of("草图.4", document="P") == ["草图.2", "草图.3"]
    assert conn.sketch_aliases_of("草图.1", document="P") == []


# ─────────────── 2. every tool that takes a name uses that definition
def _sketch_tools(alias_to=None):
    from catia_mcp.tools.sketcher import SketcherTools

    class _Sketch:
        def __init__(self, name):
            self.Name = name

    class _Sketches:
        def __init__(self, names):
            self._names = [_Sketch(n) for n in names]
            self.Count = len(self._names)

        def Item(self, key):
            if isinstance(key, int):
                return self._names[key - 1]
            for sketch in self._names:
                if sketch.Name == key:
                    return sketch
            raise KeyError(key)

    body = SimpleNamespace(Sketches=_Sketches(["草图.1", "草图.3"]))
    conn = SimpleNamespace(
        get_active_part_body=lambda: body,
        sketch_alias=lambda name: alias_to,
        sketch_aliases_of=lambda name: ["草图.2"] if name == "草图.3" else [],
        last_sketch=None)
    tools = SketcherTools(connection=None)
    tools.conn = conn
    return tools


def test_the_sketch_tools_resolve_the_alias_too():
    """catia_pad knew 草图.2 was 草图.3, catia_sketch_get_geometry did not."""
    tools = _sketch_tools(alias_to="草图.3")
    sketch, how = tools._locate_sketch("草图.2")
    assert sketch.Name == "草图.3"
    assert how == "by alias '草图.3'"


def test_the_sketch_tools_error_names_the_alias():
    tools = _sketch_tools(alias_to=None)
    with pytest.raises(RuntimeError) as caught:
        tools._locate_sketch("草图.2")
    message = str(caught.value)
    assert "No sketch named '草图.2'" in message
    # A usable name that the list would otherwise hide.
    assert "草图.3 (alias: 草图.2)" in message


def test_part_design_and_the_sketcher_share_one_resolver():
    from catia_mcp.tools.part_design import PartDesignTools
    from catia_mcp.tools.sketcher import SketcherTools

    # One reader of the table (the connection), not one per tool group.
    for owner in (PartDesignTools._sketch_alias, SketcherTools._sketch_alias):
        source = inspect.getsource(owner)
        assert "sketch_alias" in source, owner
        assert "resolve(sketch_name)" in source, owner
    locate = inspect.getsource(SketcherTools._locate_sketch)
    assert "_sketch_alias" in locate
    # The rebuild writes through the connection too, so a second rollback
    # re-points the caller's original name instead of writing a second hop.
    rebuild = inspect.getsource(SketcherTools.rebuild_lost_sketch)
    assert "note_sketch_alias" in rebuild


# ─────────────── 3. the rebuilt sketch still says what it holds
def test_the_rebuilt_sketch_says_what_it_holds(monkeypatch):
    """0.3.31 rebuilt the sketch and dropped the profile echo."""
    from catia_mcp.tools.part_design import PartDesignTools
    from catia_mcp.tools.sketcher import SketcherTools

    monkeypatch.setattr(SketcherTools, "rebuild_lost_sketch",
                        lambda self, part, body, name: ("草图.3", ""))
    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="P.CATPart"),
        sketch_outlines={"P.CATPart::草图.2": {
            "placement": {"base": "xy", "distance": 0.0},
            "elements": [{"kind": "rectangle", "x1": -10.0, "y1": -5.0,
                          "x2": 10.0, "y2": 5.0}]}})
    said = tools._recover_lost_sketch(object(), object(), "草图.2", False)
    assert "re-created from its recorded definition as '草图.3'" in said
    assert "It held:" in said
    assert "rectangle (-10, -5) .. (10, 5)" in said
    # Already rebuilt: do not tell the caller to rebuild it again.
    assert "the rebuilt sketch holds the same elements" in said
    assert "catia_create_sketch + the same drawing calls" not in said


def test_the_hint_still_tells_a_caller_to_rebuild_when_nothing_was_rebuilt():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools.conn = SimpleNamespace(
        active_document=SimpleNamespace(Name="P.CATPart"),
        sketch_outlines={"P.CATPart::草图.7": {
            "placement": {"base": "xy", "distance": 20.0},
            "elements": [{"kind": "rectangle", "x1": 30.0, "y1": 15.0,
                          "x2": 35.0, "y2": 25.0}]}})
    said = tools._sketch_rebuild_hint("草图.7")
    assert "rebuilds it" in said
    assert "the rebuilt sketch holds" not in said


def test_the_feature_listing_shows_the_aliases():
    from catia_mcp.tools.part_design import PartDesignTools

    class _Sketch:
        def __init__(self, name):
            self.Name = name

    class _Sketches:
        Count = 2

        def Item(self, index):
            return _Sketch(["草图.1", "草图.3"][index - 1])

    class _Shapes:
        Count = 1

        def Item(self, index):
            return SimpleNamespace(Name="凸台.1", Type="Pad")

    body = SimpleNamespace(Name="PartBody", Shapes=_Shapes(),
                           Sketches=_Sketches())
    part = SimpleNamespace(Bodies=SimpleNamespace(Count=1),
                           HybridBodies=SimpleNamespace(Count=0))
    conn = SimpleNamespace(
        ensure_connected=lambda: None,
        get_active_part=lambda: part,
        get_active_part_body=lambda: body,
        sketch_aliases_of=lambda name: ["草图.2"] if name == "草图.3" else [])
    tools = PartDesignTools(connection=None)
    tools.conn = conn
    payload = json.loads(tools._list_features())
    assert payload["sketch_count"] == 2
    assert payload["sketches"][1]["name"] == "草图.3"
    assert payload["sketches"][1]["aliases"] == ["草图.2"]
    assert "aliases" not in payload["sketches"][0]
    assert "older names" in payload["sketches_note"]


# ─────────────── 4. parts[] says which sweep route ran
class _Doc:
    Type = "ProductDocument"

    def __init__(self, name):
        self.Name = name


class _Part:
    def __init__(self, name):
        self.Name = name
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=1))


def _row(name, part):
    return {"index": 1, "name": name, "part_number": name.split(".")[0],
            "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": [0.0, 0.0, 0.0], "part": part, "nested_doc": None,
            "kind": "part", "problems": []}


def _assembly_tools(entries=None, skip=("part",)):
    from catia_mcp.tools import measurement as M

    entries = entries or {"P31A": {
        "points": [[0.0, 0.0, 0.0], [10.0, 6.0, 4.0]], "used": 8,
        "source": "vertices", "mesh_vertices": None, "copy_note": "",
        "cells": 60, "nls_chars": 2388, "points_route": "batched",
        "sweep_route": "one_call"}}
    tools = M.MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=None)
    parts = {name: _Part(name) for name in entries}
    tools._components_doc = lambda doc, only=None: [
        _row(f"{name}.1", part) for name, part in parts.items()]
    tools._part_box_entry = lambda part, body, include_curved: dict(
        entries[part.Name])
    tools._part_identity = lambda part: part.Name
    M._MEASURED_PARTS.clear()
    return tools


def test_the_assembly_parts_publish_the_sweep_route():
    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    assert reply["parts"][0]["sweep_route"] == "one_call"
    assert reply["parts"][0]["points_route"] == "batched"
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()


def test_a_mesh_part_says_mesh_rather_than_nothing():
    tools = _assembly_tools({"P19": {
        "points": [0.0, 0.0, 0.0, 10.0, 6.0, 4.0], "used": 8,
        "source": "mesh_fallback", "mesh_vertices": 8, "copy_note": "",
        "truncated": False}})
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    assert reply["parts"][0]["sweep_route"] == "mesh"
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()


# ─────────────── 5. the cost no part owns is published
def test_the_cost_no_part_owns_is_reported():
    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    assert reply["parts_ms"] == pytest.approx(
        sum(entry["elapsed_ms"] for entry in reply["parts"]))
    assert reply["overhead_ms"] == pytest.approx(
        max(reply["elapsed_ms"] - reply["parts_ms"], 0.0), abs=0.1)
    breakdown = reply["overhead_breakdown"]
    assert set(breakdown) == {"components_doc_ms", "walk_other_ms", "report_ms"}
    assert all(isinstance(value, float) for value in breakdown.values())
    assert "not attributable to any entry" in reply["overhead_note"]
    assert "short by this much" in reply["overhead_note"]
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()


def test_the_cost_fields_come_before_the_instance_list():
    from catia_mcp.tools.measurement import _cost_first

    payload = {"dimensions": {}, "components": [], "parts": [{}] * 28,
               "elapsed_ms": 22875.1, "parts_ms": 17480.5,
               "overhead_ms": 5394.6, "overhead_note": "x",
               "overhead_breakdown": {}, "document": "A.CATProduct"}
    ordered = list(_cost_first(dict(payload)))
    assert ordered.index("overhead_ms") < ordered.index("components")
    assert ordered.index("parts_ms") < ordered.index("parts")


# ─────────────── 6. warm_up pays the cold pass on purpose
def test_warm_up_is_offered_and_described():
    from catia_mcp.tools.measurement import MeasurementTools

    definition = [d for d in MeasurementTools(connection=None).get_tool_definitions()
                  if d["name"] == "catia_get_bounding_box"][0]
    warm = definition["inputSchema"]["properties"]["warm_up"]
    assert warm["default"] is False
    assert "cold" in warm["description"].lower()
    # ...and the tool description says so too, where a caller reads the cost.
    assert "warm_up=true" in definition["description"]


def test_the_dispatch_uses_it():
    from catia_mcp.tools.measurement import MeasurementTools

    source = inspect.getsource(MeasurementTools.execute)
    assert 'arguments.get("warm_up")' in source
    assert "_warm_up_report(reply)" in source


def test_a_warm_up_returns_the_cost_and_not_the_geometry():
    tools = _assembly_tools()
    box = tools._assembly_box(_Doc("ASM.CATProduct"), "vertices")
    report = json.loads(tools._warm_up_report(box))
    assert report["warm_up"] is True
    # The geometry is deliberately absent: this call was not asked for a box.
    for gone in ("min", "max", "dimensions", "components"):
        assert gone not in report
    assert report["parts_measured"] == 1
    assert report["cold_parts"] == 1
    assert report["parts"][0]["part"] == "P31A"
    assert report["parts"][0]["first_measure"] is True
    assert "cells_scanned" in report["parts"][0]
    assert report["parts"][0]["sweep_route"] == "one_call"
    assert "cold_parts: 0" in report["warm_up_note"]
    assert "cannot be cached" in report["warm_up_note"]
    assert "mesh box" in report["warm_up_note"]
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()


def test_a_warm_up_on_one_part_reports_that_parts_regime():
    from catia_mcp.tools.measurement import MeasurementTools

    single = json.dumps({"min": {"x": 0.0}, "max": {"x": 1.0},
                         "first_measure": True, "elapsed_ms": 86.9,
                         "cells_scanned": 12, "sweep_route": "one_call",
                         "elapsed_note": "cold"})
    report = json.loads(MeasurementTools(connection=None)._warm_up_report(single))
    assert report["warm_up"] is True
    assert report["first_measure"] is True
    assert report["cells_scanned"] == 12
    assert report["sweep_route"] == "one_call"
    assert "min" not in report


def test_a_failed_measurement_is_not_reported_as_a_warm_up():
    from catia_mcp.tools.measurement import MeasurementTools

    failed = json.dumps({"ok": False, "code": "PART_NOT_LOADED",
                         "message": "part not loaded"})
    assert MeasurementTools(connection=None)._warm_up_report(failed) == failed


# ─────────────── 7. the cold note quotes this release's measurement
def test_the_cold_note_quotes_the_measurement_that_prompted_it():
    tools = _assembly_tools()
    reply = json.loads(tools._assembly_box(_Doc("ASM.CATProduct"), "vertices"))
    note = reply["elapsed_note"]
    # 0.3.34: the note now quotes a RANGE across versions plus THIS call's own
    # parts_ms / overhead_ms / elapsed_ms, instead of the one afternoon that
    # produced 173.4 s / 22.9 s (reported 2026-10-28, section 6).
    assert "7.6x" in note and "4.65x" in note
    assert "parts_ms" in note and "overhead_ms" in note
    assert f"{reply['parts_ms']}" in note
    assert "warm_up=true" in note
    from catia_mcp.tools import measurement as M
    M._MEASURED_PARTS.clear()
