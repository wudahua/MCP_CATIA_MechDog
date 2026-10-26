# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-26 (final) batch: four reports on 0.3.24.

All four agree that the two 0.3.24 headline fixes hold (the mesh box is now a real
rotation of the part, and the STL export no longer puts up a "Save As"). What they
found is the shape of thing this fork keeps producing: the right answer in one
place and a plausible-but-wrong one next to it.

* ``_no_active_assembly_text`` listed **13 .CATPart files as "Open assemblies"**
  because it probed ``.Product``, which a PartDocument answers with a stub - while
  ``add_component``, ten lines away, judged the same question by document type.
* ``occurrence`` meant two things: the position in a full listing (1..70) and the
  ordinal among instances of one part when used in ``{part_number, occurrence}``.
* ``cells_scanned`` / ``vertices_used`` are per-PART costs repeated on every
  instance entry, and ``cells_scanned`` was read by an acceptance report as a mesh
  grid count rather than a topology-cell count.
* ``points_route`` appeared only on the degraded path, so "the batch worked" and
  "this build has no such field" looked identical.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------------------
# 1. "Open assemblies" means assemblies.
# --------------------------------------------------------------------------

class _Doc:
    def __init__(self, name):
        self.Name = name
        self.FullName = name
        self.Type = "Product" if name.endswith(".CATProduct") else "Part"
        self.Part = object() if name.endswith(".CATPart") else None
        self.Product = None if name.endswith(".CATPart") else object()


def _assembly_tools(names, active="PLN.CATPart"):
    from catia_mcp.tools.assembly import AssemblyTools

    docs = [_Doc(name) for name in names]

    class _Docs:
        Count = len(docs)

        @staticmethod
        def Item(index):
            return docs[index - 1]

    tools = AssemblyTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=_Docs(),
        active_document=_Doc(active),
        part_of=lambda doc: doc.Part if doc.Part else (_ for _ in ()).throw(
            RuntimeError("no Part")),
        product_of=lambda doc: doc.Product,
    )
    return tools


def test_open_assemblies_lists_only_product_documents():
    """A PartDocument answers .Product with a stub - that is not an assembly."""
    tools = _assembly_tools([
        "P00_Assembly.CATProduct",
        "P07_FrontEarRing.CATPart",
        "P06_FrontCap.CATPart",
        "ASMX.CATProduct",
        "PLN24.CATPart",
    ])
    with pytest.raises(RuntimeError) as error:
        tools._list_components()
    text = str(error.value)
    assert "P00_Assembly.CATProduct" in text
    assert "ASMX.CATProduct" in text
    # The 13-in-16 defect: these are parts and must not be offered as assemblies.
    for part in ("P07_FrontEarRing.CATPart", "P06_FrontCap.CATPart",
                 "PLN24.CATPart"):
        assert part not in text


def test_the_assembly_list_and_the_insert_list_agree():
    """Same judgement, same helper: `_is_product_document`."""
    from catia_mcp.tools.assembly import AssemblyTools

    listing = __import__("inspect").getsource(
        AssemblyTools._no_active_assembly_text)
    assert "_is_product_document" in listing
    assert "product_of" not in listing


# --------------------------------------------------------------------------
# 2. occurrence is the ordinal among instances of one part.
# --------------------------------------------------------------------------

class _Comp:
    def __init__(self, name, part_number):
        self.Name = name
        self.PartNumber = part_number
        self.Position = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0,
                         0.0, 0.0, 0.0]
        self.ReferenceProduct = SimpleNamespace(Parent=object())


def _components_tools(comps):
    from catia_mcp.tools.assembly import AssemblyTools

    class _Products:
        Count = len(comps)

        @staticmethod
        def Item(index):
            return comps[index - 1]

    tools = AssemblyTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None,
        documents=SimpleNamespace(Count=0, Item=lambda i: None),
        active_document=_Doc("ASM.CATProduct"),
        part_of=lambda doc: doc.Part if doc.Part is not None else (
            _ for _ in ()).throw(RuntimeError("no Part")),
        product_of=lambda doc: doc.Product,
        get_active_product=lambda: SimpleNamespace(Products=_Products()),
    )
    tools._position_components = lambda position: list(position)
    return tools


def test_occurrence_is_the_ordinal_within_the_part_not_the_assembly():
    comps = [_Comp(f"P01 I{i:03d}", "P01") for i in range(1, 3)] + \
            [_Comp(f"P02 I{i:03d}", "P02") for i in range(3, 6)]
    payload = json.loads(_components_tools(comps)._list_components())

    assert payload["occurrence_scope"] == "per_part"
    seen = [(e["index"], e["part_number"], e["occurrence"])
            for e in payload["components"]]
    # index keeps the assembly position; occurrence restarts per part.
    assert seen == [(1, "P01", 1), (2, "P01", 2),
                    (3, "P02", 1), (4, "P02", 2), (5, "P02", 3)]


def test_a_filtered_listing_numbers_the_same_way():
    comps = [_Comp(f"P01 I{i:03d}", "P01") for i in range(1, 3)] + \
            [_Comp(f"P02 I{i:03d}", "P02") for i in range(3, 6)]
    payload = json.loads(
        _components_tools(comps)._list_components(part_number="P02"))
    assert [e["occurrence"] for e in payload["components"]] == [1, 2, 3]


def test_the_listing_documents_what_occurrence_means():
    from catia_mcp.tools.assembly import AssemblyTools

    schema = [d for d in AssemblyTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_list_components"][0]
    assert "occurrence_scope" in schema["description"]
    assert "AMONG INSTANCES OF THE SAME PART" in schema["description"]


# --------------------------------------------------------------------------
# 3. Per-part counts live on the part, once.
# --------------------------------------------------------------------------

def _rows(part_name, count, curved=False):
    class _Part:
        def __init__(self, name):
            self.Name = name
            self.MainBody = object()

    rows = []
    for index in range(1, count + 1):
        rows.append({
            "index": index, "name": f"{part_name} I{index:03d}",
            "part_number": part_name, "part": _Part(part_name),
            "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation": [0.0, 0.0, 0.0], "problems": [],
        })
    return rows


def _box_tools(rows, curved=()):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._components_doc = lambda doc, only=None: rows
    tools._part_identity = lambda part: part.Name
    tools._to_world = lambda matrix, translation, point: list(point)

    def local_box(part, body, stats=None):
        if part.Name in curved:
            return [], 0
        if stats is not None:
            stats["cells"] = 42
            stats["points_route"] = "batch"
        return [[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]], 8

    tools._local_box = local_box
    tools._mesh_cloud = lambda part: ([0.0, 0.0, 0.0, 2.0, 4.0, 6.0], 12, "")
    return tools


def test_per_part_counts_are_published_once_per_part():
    rows = _rows("bracket", 4)
    payload = json.loads(_box_tools(rows)._assembly_box(
        SimpleNamespace(Name="A.CATProduct"), "vertices"))

    assert len(payload["components"]) == 4
    assert len(payload["parts"]) == 1
    part = payload["parts"][0]
    assert part["part"] == "bracket"
    assert part["instances"] == 4
    assert part["vertices_used"] == 8
    assert part["cells_scanned"] == 42
    assert part["points_route"] == "batched"
    assert part["local_dimensions"] == {"length_x": 1.0, "length_y": 2.0,
                                        "length_z": 3.0}
    # The instance entries keep the geometry and nothing else.
    for entry in payload["components"]:
        assert "vertices_used" not in entry
        assert "cells_scanned" not in entry


def test_cells_scanned_says_what_it_counts():
    """A report read it as a mesh grid count; it is CATIA's topology cells."""
    rows = _rows("bracket", 1)
    payload = json.loads(_box_tools(rows)._assembly_box(
        SimpleNamespace(Name="A.CATProduct"), "vertices"))
    note = payload["parts"][0]["cells_note"]
    assert "topology cells" in note
    assert "BEFORE construction geometry" in note
    assert "not of mesh elements" in note


def test_a_mesh_part_reports_no_cell_count_and_keeps_its_caveat():
    rows = _rows("dish", 2)
    payload = json.loads(_box_tools(rows, curved={"dish"})._assembly_box(
        SimpleNamespace(Name="A.CATProduct"), "vertices"))
    part = payload["parts"][0]
    assert part["source"] == "mesh_fallback"
    assert part["mesh_vertices"] == 12
    assert "cells_scanned" not in part
    assert part["source_note"].startswith("no vertex lies at this part's extremum")


# --------------------------------------------------------------------------
# 4. points_route is present when the batch worked, too.
# --------------------------------------------------------------------------

def test_points_route_names_the_batch_on_the_happy_path():
    rows = _rows("bracket", 2)
    payload = json.loads(_box_tools(rows)._assembly_box(
        SimpleNamespace(Name="A.CATProduct"), "vertices"))
    assert payload["parts"][0]["points_route"] == "batched"


def test_the_route_names_are_explicit():
    from catia_mcp.tools.measurement import _route_name

    assert _route_name("batch") == "batched"
    assert _route_name(None) == "batched"
    assert _route_name("batch_retried") == "batched_retried"
    assert _route_name("per_point") == "per_point"


def test_the_single_part_box_publishes_the_route_too():
    """A 16-vertex call and an assembly call should not disagree about fields."""
    from catia_mcp.tools import measurement as M

    source = __import__("inspect").getsource(M.MeasurementTools._part_bbox)
    assert source.count('"points_route"') == 1
    assert '"cells_scanned"' in source
    active = __import__("inspect").getsource(M.MeasurementTools._get_bounding_box)
    assert source.count('"points_route"') + active.count('"points_route"') == 2


def test_every_call_says_what_it_cost():
    """`cells_scanned` is the work estimate; `elapsed_ms` is the measured price."""
    rows = _rows("bracket", 2) + _rows("plinth", 1)
    payload = json.loads(_box_tools(rows)._assembly_box(
        SimpleNamespace(Name="A.CATProduct"), "vertices"))

    assert isinstance(payload["elapsed_ms"], float)
    assert payload["elapsed_ms"] >= 0
    by_part = {p["part"]: p for p in payload["parts"]}
    assert len(by_part) == 2
    for detail in by_part.values():
        assert isinstance(detail["elapsed_ms"], float)
        assert detail["elapsed_ms"] >= 0
    # The total is a wall clock, so it cannot be shorter than any single part.
    assert payload["elapsed_ms"] >= max(p["elapsed_ms"] for p in payload["parts"])


def test_the_tool_description_warns_about_the_price():
    from catia_mcp.tools.measurement import MeasurementTools

    description = [d for d in MeasurementTools(connection=None).get_tool_definitions()
                   if d["name"] == "catia_get_bounding_box"][0]["description"]
    assert "MINUTES, not seconds" in description
    assert "elapsed_ms" in description
    assert "cells_scanned" in description
    assert "one at a time" in description


# --------------------------------------------------------------------------
# 5. component= does not read 70 placements to answer about one.
# --------------------------------------------------------------------------

def test_a_single_component_query_does_not_read_every_placement():
    from catia_mcp.tools.measurement import MeasurementTools

    class _Child:
        def __init__(self, name, part_number):
            self.Name = name
            self.PartNumber = part_number
            self.Position = object()
            self.ReferenceProduct = SimpleNamespace(Parent=object())

    children = [_Child(f"P{i:02d} I{i:03d}", f"P{i:02d}") for i in range(1, 71)]
    reads = {"position": 0, "part": 0}

    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        product_of=lambda doc: SimpleNamespace(
            Products=SimpleNamespace(Count=len(children),
                                     Item=lambda i: children[i - 1])),
        part_of=lambda doc: (_ for _ in ()).throw(RuntimeError("not a part")),
    )

    def placement(child):
        reads["position"] += 1
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0]

    tools._placement = placement

    rows = tools._components_doc(SimpleNamespace(Name="A.CATProduct"),
                                 only="P42 I042")
    assert len(rows) == 1
    assert rows[0]["name"] == "P42 I042"
    # One pose read for the match, not 70 for the list.
    assert reads["position"] == 1


def test_an_unfiltered_call_still_sees_every_component():
    from catia_mcp.tools.measurement import MeasurementTools

    class _Child:
        def __init__(self, name):
            self.Name = name
            self.PartNumber = name
            self.Position = object()
            self.ReferenceProduct = SimpleNamespace(Parent=object())

    children = [_Child(f"P{i:02d}") for i in range(1, 4)]
    tools = MeasurementTools(connection=None)
    tools.conn = SimpleNamespace(
        product_of=lambda doc: SimpleNamespace(
            Products=SimpleNamespace(Count=3, Item=lambda i: children[i - 1])),
        part_of=lambda doc: object(),
    )
    tools._placement = lambda child: (
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0])
    assert len(tools._components_doc(SimpleNamespace(Name="A"))) == 3
