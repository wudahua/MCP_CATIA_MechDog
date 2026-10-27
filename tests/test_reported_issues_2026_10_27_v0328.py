# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-27 batch: two reports on 0.3.27.

The serious one is the assembly inertia. `get_inertia` on an assembly that holds
a SUB-ASSEMBLY dropped the sub-assembly with "part not loaded" and returned the
partial sum under the assembly's own name: a two-level model holding
60 000 + 60 000 mm3 reported `volume_mm3: 60000.0` and `center_of_gravity_mm.x:
0.0` - no `complete`, no count of what was left out, and a `note` that described
the aggregation as if it were the whole. The same assembly's bounding box said
`complete: false` and `instances: {total: 2, measured: 1, skipped: 1}`, so the
two aggregators disagreed about what "complete" means.

The rest are the same class of defect one level down: `get_inertia` publishes a
key set that depends on HOW it was called, and a failure message points at
`catia_diagnose` for state that `catia_diagnose` did not report.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest


# ─────────────────────────────────────────────── a fake CATIA hierarchy
class _Doc:
    def __init__(self, name: str, kind: str = "Part", product: Any = None,
                 part: Any = None):
        self.Name = name
        self.FullName = name
        self.Type = kind
        self.Product = product
        # A PartDocument answers `.Part`; a ProductDocument does not. That is the
        # whole distinction the tools keep having to make.
        self.Part = part


class _Part:
    """A part whose measurable answers fixed numbers, and counts its reads."""

    def __init__(self, name: str, volume_mm3: float, area_mm2: float,
                 cog: list[float]):
        self.Name = name
        self.reads = 0
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=1))
        # 1 mm3 = 1e-9 m3; 1 mm2 = 1e-6 m2 - the units SPA speaks.
        self._measurable = SimpleNamespace(Volume=volume_mm3 / 1e9,
                                           Area=area_mm2 / 1e6,
                                           cog=list(cog))
        self.Parent = SimpleNamespace(FullName=name + ".CATPart",
                                      GetWorkbench=lambda workbench: self)

    def CreateReferenceFromObject(self, body: Any) -> Any:
        return object()

    def GetMeasurable(self, ref: Any) -> Any:
        self.reads += 1
        return self._measurable


def _tools(rows_by_assembly: dict[str, list[dict[str, Any]]], active: Any = None):
    """MeasurementTools whose component rows are given per assembly name."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None,
                                 active_document=active,
                                 app=None)
    # GetCOG goes through SystemService.Evaluate in production; the number itself
    # is not what these tests are about.
    tools._read_measurable_array = (
        lambda measurable, method, count: list(measurable.cog))

    def components_doc(doc: Any, only: str | None = None) -> list[dict[str, Any]]:
        rows = rows_by_assembly.get(str(doc.Name), [])
        if only is None:
            return rows
        return [r for r in rows
                if (r.get("name") or "") == only or (r.get("part_number") or "") == only]

    tools._components_doc = components_doc
    return tools


def _row(name: str, part: Any = None, kind: str = "part", translation=None,
         nested: Any = None, problems=None) -> dict[str, Any]:
    matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return {"index": 1, "name": name, "part_number": name.split(".")[0],
            "matrix": matrix, "translation": translation or [0.0, 0.0, 0.0],
            "part": part, "nested_doc": nested, "kind": kind,
            "problems": list(problems or [])}


# ─────────────────────────────────────── 1. a sub-assembly is not a dead link
class _Product:
    def __init__(self, children: list[Any]):
        self.Products = SimpleNamespace(Count=len(children),
                                        Item=lambda index: children[index - 1])


class _Child:
    def __init__(self, name: str, parent: Any):
        self.Name = name
        self.PartNumber = name.split(".")[0]
        self.Position = object()
        self.ReferenceProduct = SimpleNamespace(Parent=parent)


def _assembly_tools(sub_doc: Any, part_doc: Any) -> Any:
    """Real `_components_doc`, stubbed placement - the classification is the point."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    doc = _Doc("ASM27D.CATProduct", "ProductDocument",
               _Product([_Child("P26A.1", part_doc), _Child("ASM27B.1", sub_doc)]))
    sub_doc.Product = _Product([])
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, active_document=doc)
    tools._placement = lambda child: ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                                       [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0])

    def part_of(target: Any) -> Any:
        part = getattr(target, "Part", None)
        if part is None:
            raise RuntimeError(
                f"Active document '{target.Name}' exposes no 'Part'. Open or "
                "create a Part document first.")
        return part

    tools.conn.part_of = part_of
    tools.conn.product_of = lambda target: target.Product
    return tools, doc


def test_a_component_that_is_an_assembly_is_called_that_not_a_dead_link():
    sub = _Doc("ASM27B.CATProduct", "ProductDocument")
    part = _Doc("P26A.CATPart", "PartDocument", None, part=object())
    tools, doc = _assembly_tools(sub, part)

    rows = tools._components_doc(doc)
    by_name = {row["name"]: row for row in rows}
    assert by_name["P26A.1"]["kind"] == "part"
    sub_row = by_name["ASM27B.1"]
    assert sub_row["kind"] == "sub_assembly"
    assert sub_row["nested_doc"] is sub
    # "part not loaded" sent the caller to catia_open_document, where opening it
    # changes nothing - the component really is an assembly (reported 2026-10-27).
    assert "part not loaded" not in sub_row["problems"][0]
    assert "sub-assembly" in sub_row["problems"][0]
    # ...and it has to say what DOES work for its own box.
    assert "get_bounding_box(document='ASM27B.CATProduct')" in sub_row["problems"][0]


# ───────────────────────────────────────── 2. the totals include the sub-tree
def test_a_nested_assembly_is_added_to_the_totals_not_dropped():
    leaf = _Part("P26A", 60000.0, 12000.0, [0.0, 0.0, 5.0])
    sub = _Doc("ASM27B.CATProduct", "ProductDocument")
    top = _Doc("ASM27D.CATProduct", "ProductDocument")
    tools = _tools({
        "ASM27D.CATProduct": [
            _row("P26A.1", part=leaf),
            _row("ASM27B.1", kind="sub_assembly", nested=sub,
                 translation=[100.0, 0.0, 0.0]),
        ],
        # The sub-assembly holds ONE instance of the same part, at its own origin.
        "ASM27B.CATProduct": [_row("P26A.1", part=leaf)],
    })

    reply = json.loads(tools._assembly_inertia(doc=top))
    # True value: 60 000 + 60 000 mm3, COG x = (60 000*0 + 60 000*100)/120 000 = 50.
    assert reply["volume_mm3"] == 120000.0
    assert reply["center_of_gravity_mm"]["x"] == 50.0
    assert reply["center_of_gravity_mm"]["z"] == 5.0
    assert reply["complete"] is True
    assert reply["instances"] == {"total": 2, "measured": 2, "skipped": 0}
    assert reply["assembly_depth_max"] == 2
    # Flattened leaves, each with the path that produced it.
    paths = sorted(entry["path"] for entry in reply["components"])
    assert paths == ["ASM27B.1/P26A.1", "P26A.1"]
    # And the parts list adds up to the total, which is what makes it checkable.
    assert round(sum(e["volume_mm3"] for e in reply["components"]), 4) == 120000.0


def test_a_component_that_could_not_be_measured_says_the_total_is_partial():
    good = _Part("P26A", 60000.0, 12000.0, [0.0, 0.0, 5.0])
    top = _Doc("ASM27D.CATProduct", "ProductDocument")
    tools = _tools({"ASM27D.CATProduct": [
        _row("P26A.1", part=good),
        _row("P30.1", part=None, kind="unknown",
             problems=["part not loaded: Active document 'P30' exposes no 'Part'"]),
    ]})

    reply = json.loads(tools._assembly_inertia(doc=top))
    # The number is still there (a partial answer beats an exception), but every
    # field that could make it look complete has to say otherwise.
    assert reply["volume_mm3"] == 60000.0
    assert reply["complete"] is False
    assert reply["instances"] == {"total": 2, "measured": 1, "skipped": 1}
    assert "PARTIAL" in reply["incomplete_because"]
    assert "not this assembly's total" in reply["incomplete_because"]
    # `component` is the part NUMBER, as everywhere else (P30.1's part is P30).
    assert reply["skipped"][0]["component"] == "P30"
    assert reply["skipped"][0]["path"] == "P30.1"
    assert reply["skipped"][0]["kind"] == "unknown"


def test_a_sub_assembly_whose_own_children_all_fail_is_reported_by_path():
    top = _Doc("ASM27D.CATProduct", "ProductDocument")
    sub = _Doc("ASM27B.CATProduct", "ProductDocument")
    good = _Part("P26A", 60000.0, 12000.0, [0.0, 0.0, 5.0])
    tools = _tools({
        "ASM27D.CATProduct": [
            _row("P26A.1", part=good),
            _row("ASM27B.1", kind="sub_assembly", nested=sub, translation=[10.0, 0, 0]),
        ],
        "ASM27B.CATProduct": [
            _row("P99.1", part=None, problems=["part not loaded: broken link"]),
        ],
    })

    reply = json.loads(tools._assembly_inertia(doc=top))
    assert reply["complete"] is False
    paths = [entry["path"] for entry in reply["skipped"]]
    # The path is the only thing that says WHICH level went missing.
    assert "ASM27B.1/P99.1" in paths
    assert reply["volume_mm3"] == 60000.0


def test_an_empty_sub_assembly_does_not_vanish_silently():
    top = _Doc("ASM27D.CATProduct", "ProductDocument")
    sub = _Doc("ASM27B.CATProduct", "ProductDocument")
    good = _Part("P26A", 60000.0, 12000.0, [0.0, 0.0, 5.0])
    tools = _tools({
        "ASM27D.CATProduct": [
            _row("P26A.1", part=good),
            _row("ASM27B.1", kind="sub_assembly", nested=sub),
        ],
        "ASM27B.CATProduct": [],
    })

    reply = json.loads(tools._assembly_inertia(doc=top))
    assert reply["complete"] is False
    assert "holds no components" in reply["skipped"][0]["error"]


# ───────────────────────────────── 3. area is additive, and so is its cm2 form
def test_surface_area_is_aggregated_and_published_in_both_units():
    leaf = _Part("P26A", 60000.0, 12000.0, [0.0, 0.0, 5.0])
    top = _Doc("ASM26.CATProduct", "ProductDocument")
    tools = _tools({"ASM26.CATProduct": [_row("P26A.1", part=leaf),
                                         _row("P26A.2", part=leaf,
                                              translation=[10.0, 0, 0])]})

    reply = json.loads(tools._assembly_inertia(density=2700.0, doc=top))
    assert reply["area_mm2"] == 24000.0
    assert reply["area_cm2"] == 240.0
    assert reply["mass_kg"] == round(2700.0 * 120000.0 / 1e9, 6)
    assert reply["mass_g"] == round(2700.0 * 120000.0 / 1e6, 3)
    assert reply["density_kg_m3"] == 2700.0


def test_one_part_is_measured_once_however_many_instances_it_has():
    leaf = _Part("P26A", 60000.0, 12000.0, [0.0, 0.0, 5.0])
    top = _Doc("ASM26.CATProduct", "ProductDocument")
    tools = _tools({"ASM26.CATProduct": [
        _row("P26A.1", part=leaf), _row("P26A.2", part=leaf),
        _row("P26A.3", part=leaf, translation=[5.0, 0, 0]),
    ]})

    reply = json.loads(tools._assembly_inertia(doc=top))
    assert reply["instances"]["measured"] == 3
    assert leaf.reads == 1, "volume/area/COG are properties of the PART"
    assert reply["volume_mm3"] == 180000.0


# ─────────────────────────── 4. one key table for both part routes
class _BrokenMeasurable:
    @property
    def Volume(self) -> float:
        raise RuntimeError("no volume on a surface-only part")

    @property
    def Area(self) -> float:
        raise RuntimeError("no area either")

    cog = [1.0, 2.0, 3.0]


def test_a_quantity_that_cannot_be_read_is_null_with_a_reason():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._read_measurable_array = (
        lambda measurable, method, count: list(measurable.cog))
    fields = tools._measured_quantities(_BrokenMeasurable())
    assert fields["volume_mm3"] is None
    assert fields["volume_cm3"] is None
    assert "volume_error" in fields
    assert fields["area_mm2"] is None
    assert fields["area_cm2"] is None
    assert "area_error" in fields
    # Never a sentence where a number belongs: "unavailable: …" in a float field
    # broke the caller that tried to compare it.
    assert not isinstance(fields["volume_mm3"], str)
    assert fields["center_of_gravity_mm"] == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_both_part_routes_use_the_same_reader():
    """`area_cm2` existed on the active-part route only, for the SAME document."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    assert "_measured_quantities" in inspect.getsource(MT._part_inertia)
    assert "_measured_quantities" in inspect.getsource(MT._get_inertia)
    keys = MT(connection=None)._measured_quantities(
        SimpleNamespace(Volume=1e-6, Area=1e-3, cog=[0.0, 0.0, 0.0]))
    for key in ("volume_mm3", "volume_cm3", "area_mm2", "area_cm2",
                "center_of_gravity_mm"):
        assert key in keys, key


def test_a_documented_part_carries_the_density_related_keys_too():
    leaf = _Part("P27U", 8000.0, 1200.0, [0.0, 0.0, 2.5])
    leaf._measurable.Volume = 8000.0 / 1e9
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(ensure_connected=lambda: None, app=None)
    tools._read_measurable_array = (
        lambda measurable, method, count: list(measurable.cog))
    tools._tensor_for = lambda part: None
    reply = json.loads(tools._part_inertia(leaf, 2700.0, "P27U.CATPart"))
    for key in ("volume_mm3", "volume_cm3", "area_mm2", "area_cm2",
                "center_of_gravity_mm", "mass_kg", "mass_g", "density_kg_m3"):
        assert key in reply, key
    assert reply["volume_cm3"] == 8.0
    assert reply["mass_g"] == pytest.approx(21.6, abs=1e-3)


def test_a_placed_placement_composes():
    """Two rotations of 90 degrees about Z are one of 180 - not two of 90."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    rot90 = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert MT._mat_mul(rot90, rot90) == [[-1.0, 0.0, 0.0],
                                         [0.0, -1.0, 0.0],
                                         [0.0, 0.0, 1.0]]


# ────────────────────────────────────── 5. diagnose reports the session state
def _session_tools(document: Any, part: Any = None, selected: int = 3) -> Any:
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    tools = DiagnosticsTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        app=SimpleNamespace(),
        tool_count=101,
        active_document=document,
        hso=SimpleNamespace(Count=selected),
        get_active_part=lambda: part,
        get_active_part_body=lambda: part.MainBody,
    )
    return tools


class _SessionPart:
    def __init__(self) -> None:
        self.Name = "P27U"
        self.MainBody = SimpleNamespace(Name="PartBody",
                                        Shapes=SimpleNamespace(Count=2))
        self.InWorkObject = SimpleNamespace(Name="Pad.1")
        self.HybridBodies = SimpleNamespace(Count=4)


def test_session_snapshot_reads_the_five_things_a_method_failure_is_about():
    from catia_mcp.connection import session_line, session_snapshot

    conn = SimpleNamespace(active_document=_Doc("P27U.CATPart", "PartDocument"),
                           hso=SimpleNamespace(Count=3),
                           get_active_part=lambda: _SessionPart(),
                           get_active_part_body=lambda: _SessionPart().MainBody)
    snapshot = session_snapshot(conn)
    assert snapshot["document"] == "P27U.CATPart"
    assert snapshot["document_kind"] == "part"
    assert snapshot["in_work_object"] == "Pad.1"
    assert snapshot["body"] == "PartBody"
    assert snapshot["shape_count"] == 2
    assert snapshot["selected"] == 3
    assert snapshot["geometrical_sets"] == 4
    line = session_line(conn)
    assert line.startswith("document=P27U.CATPart")
    assert "in_work_object=Pad.1" in line
    assert "geometrical_sets=4" in line


def test_session_state_never_raises_and_says_why_it_is_empty():

    class _Nothing:
        def __getattr__(self, name):
            raise RuntimeError("no active document")

    from catia_mcp.connection import session_line, session_snapshot

    assert "document=" in session_line(_Nothing())
    snapshot = session_snapshot(_Nothing())
    assert snapshot["document"] is None
    assert "no active document" in snapshot["note"]


def test_the_session_block_names_the_document_kind_without_asking_Type():
    """`Document.Type` reads empty on the B30 binding, so the first version of
    this block answered `document_kind: null` for both kinds."""
    from catia_mcp.connection import session_snapshot

    def no_part():
        raise RuntimeError("exposes no 'Part'")

    conn = SimpleNamespace(active_document=_Doc("ASM26.CATProduct", ""),
                           hso=SimpleNamespace(Count=0),
                           get_active_part=no_part,
                           product_of=lambda doc: object())
    snapshot = session_snapshot(conn)
    assert snapshot["document_kind"] == "assembly"
    assert "has no Part" in snapshot["note"]


def test_diagnose_publishes_the_session_block_the_failure_message_points_at():
    tools = _session_tools(_Doc("P27U.CATPart", "PartDocument"), _SessionPart())
    report = json.loads(tools._diagnose())
    assert "session" in report
    assert report["session"]["in_work_object"] == "Pad.1"
    assert report["session"]["selected"] == 3
    assert report["session"]["line"].startswith("document=")


def test_the_failure_message_no_longer_promises_state_diagnose_does_not_have():
    from catia_mcp import server

    source = inspect.getsource(server.CATIAMCPServer._execute_tool)
    assert "reports both" not in source
    assert "'session' block" in source


# ─────────────────────────── 6. the cache claim, and the per-part NLS count
def test_the_mesh_cache_claim_states_the_condition_it_actually_depends_on():
    """`a part opened as its own document is exported in place every time` was
    not reproducible: opening an already-loaded component does not give it a
    window, so the copy route (and the cache) still runs (reported 2026-10-27)."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    tools = MT(connection=None)
    description = next(tool["description"] for tool in tools.get_tool_definitions()
                       if tool["name"] == "catia_get_bounding_box")
    assert "exported in place every time" not in description
    assert "changes ActiveDocument" in description
    assert "never cached" in description
    assert "catia_open_document cannot give an already-loaded component a window" \
        in description


def test_nls_characters_are_published_beside_the_cell_count():
    from catia_mcp.tools.measurement import _nls_fields

    assert _nls_fields({}) == {}
    plain = _nls_fields({"nls_chars": 1234})
    assert plain["nls_chars"] == 1234
    # Since 0.3.31 the meaning travels with the number on BOTH routes: the
    # single-part reply published the count without the caveat (report §P3-3).
    assert "not a cost predictor" in plain["nls_chars_note"]
    # A capped single-part sweep is a SAMPLE, and the reply has to say so.
    capped = _nls_fields({"nls_chars": 500, "cells": 1207, "nls_chars_cells": 200})
    assert capped["nls_chars"] == 500
    assert capped["nls_chars_cells"] == 200
    assert "not a cost predictor" in capped["nls_chars_note"]


def test_a_part_box_entry_carries_the_nls_count():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._local_box = lambda part, body, stats: (
        stats.update({"cells": 239, "nls_chars": 14340,
                      "points_route": "batch"}) or ([[0.0, 0.0, 0.0]], 1))
    entry = tools._part_box_entry(object(), object(), True)
    assert entry["cells"] == 239
    assert entry["nls_chars"] == 14340
