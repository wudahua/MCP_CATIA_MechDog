# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-26 (last) batch: four reports on 0.3.23.

Two of them are new defects in the code this fork just added, and both are the
kind that answers confidently while being wrong:

* the mesh fallback transformed the LOCAL BOX'S TWO CORNERS into world space. A
  diagonal, rotated, is a line: its extent across the rotation is almost nothing,
  so a disc whose true world box is ±72.5 in x and y came back as ±100.29 / ±21.32
  - and because one axis grew, the aggregate min/max still looked like a full
  cover with ``complete: true`` (reported 2026-10-26 with the numbers).
* ``ExportData`` reached CATIA's file-alert path, so every mesh measurement left a
  modal "另存为" on the desktop and, unanswered, blocked the call for 89.4 s
  (reported 2026-10-26 with a raw-COM control: ``DisplayFileAlerts = False`` → 0
  dialogs, 0.00 s, same STL).

The other two are the interface hygiene items from the performance and the
acceptance reports: a per-part route count instead of prose, a part-level
``mesh_fallback`` list, and "no assembly is active" said out loud.
"""

from __future__ import annotations

import inspect
import json
import math

import pytest


# --------------------------------------------------------------------------
# 1. A rotated part's world box needs every vertex, not two corners.
# --------------------------------------------------------------------------

def _rotation_z(degrees: float) -> list[list[float]]:
    r = math.radians(degrees)
    return [[math.cos(r), -math.sin(r), 0.0],
            [math.sin(r), math.cos(r), 0.0],
            [0.0, 0.0, 1.0]]


def test_world_extent_rotates_every_vertex():
    from catia_mcp.tools.measurement import MeasurementTools

    # A 4 x 2 rectangle at z = 0, as a flat cloud.
    cloud = [-2.0, -1.0, 0.0, -2.0, 1.0, 0.0,
             2.0, -1.0, 0.0, 2.0, 1.0, 0.0]
    lo, hi = MeasurementTools._world_extent(cloud, _rotation_z(30), [0.0, 0.0, 0.0])
    # 2·cos30 + 1·sin30 = 2.2321, 2·sin30 + 1·cos30 = 1.8660
    assert lo[0] == pytest.approx(-2.2321, abs=1e-4)
    assert hi[0] == pytest.approx(2.2321, abs=1e-4)
    assert lo[1] == pytest.approx(-1.8660, abs=1e-4)
    assert hi[1] == pytest.approx(1.8660, abs=1e-4)


def test_two_corners_would_have_lost_a_third_of_the_width():
    """The bug this replaces: the diagonal alone reports 1.2321 where the truth is
    2.2321 - and no warning, because the box still looked plausible."""
    from catia_mcp.tools.measurement import MeasurementTools

    diagonal = [-2.0, -1.0, 0.0, 2.0, 1.0, 0.0]
    lo, hi = MeasurementTools._world_extent(diagonal, _rotation_z(30),
                                            [0.0, 0.0, 0.0])
    assert hi[0] == pytest.approx(1.2321, abs=1e-4)   # not 2.2321
    assert hi[1] == pytest.approx(1.8660, abs=1e-4)   # the y extent survives
    # i.e. the shortfall is in the axis the diagonal happens to collapse onto.
    assert hi[0] < 2.2321 * 0.6


def test_world_extent_applies_the_translation():
    from catia_mcp.tools.measurement import MeasurementTools

    lo, hi = MeasurementTools._world_extent([0.0, 0.0, 0.0, 1.0, 2.0, 3.0],
                                            [[1.0, 0.0, 0.0],
                                             [0.0, 1.0, 0.0],
                                             [0.0, 0.0, 1.0]],
                                            [10.0, -5.0, 2.5])
    assert lo == [10.0, -5.0, 2.5]
    assert hi == [11.0, -3.0, 5.5]


def test_a_rotated_mesh_part_gets_the_true_world_box_in_the_aggregate():
    """End to end through _assembly_box with a 30-degree placement."""
    from types import SimpleNamespace

    from catia_mcp.tools.measurement import MeasurementTools

    class _Part:
        Name = "disc"
        MainBody = object()

    tools = MeasurementTools(connection=None)
    tools._components_doc = lambda doc, only=None: [{
        "index": 1, "name": "disc I001", "part_number": "disc", "part": _Part(),
        "matrix": _rotation_z(30), "translation": [0.0, 0.0, 0.0], "problems": [],
    }]
    tools._part_identity = lambda part: "disc"
    tools._local_box = lambda part, body, stats=None: ([], 0)   # no vertices: curved
    # The four corners of a 4 x 2 rectangle, all at z = 0.
    tools._mesh_cloud = lambda part: ([-2.0, -1.0, 0.0, -2.0, 1.0, 0.0,
                                       2.0, -1.0, 0.0, 2.0, 1.0, 0.0], 4, "")
    tools._mesh_corners = lambda part: pytest.fail("the corner route is not enough")

    payload = json.loads(tools._assembly_box(SimpleNamespace(Name="A.CATProduct"),
                                             "vertices"))
    entry = payload["components"][0]
    assert entry["source"] == "mesh_fallback"
    assert entry["max"]["x"] == pytest.approx(2.2321, abs=1e-3)
    assert entry["max"]["y"] == pytest.approx(1.866, abs=1e-3)
    assert payload["dimensions"]["length_x"] == pytest.approx(4.4642, abs=1e-3)


# --------------------------------------------------------------------------
# 2. The STL export cannot leave a modal "Save As" behind.
# --------------------------------------------------------------------------

class _AlertApp:
    def __init__(self, raises: bool = False):
        self.DisplayFileAlerts = True
        self.raises = raises
        self.calls = 0

    def restore(self):
        return self.DisplayFileAlerts


class _ExportDoc:
    def __init__(self, app: _AlertApp):
        self.app = app

    def ExportData(self, path, fmt):
        # The point of the test: CATIA's alert state at the moment of the export.
        if self.app.DisplayFileAlerts:
            raise AssertionError("ExportData ran with file alerts ENABLED")
        self.app.calls += 1
        if self.app.raises:
            raise RuntimeError("export refused")
        with open(path, "wb") as handle:
            handle.write(b"solid x\nendsolid x\n")


def test_the_stl_export_runs_with_file_alerts_off_and_puts_them_back(tmp_path):
    from catia_mcp.tools.measurement import MeasurementTools

    app = _AlertApp()
    tools = MeasurementTools(connection=None)
    tools.conn = type("C", (), {"app": app})()

    target = tmp_path / "part.stl"
    tools._export_stl(_ExportDoc(app), str(target))

    assert app.calls == 1
    assert app.DisplayFileAlerts is True      # restored


def test_the_stl_export_puts_the_alert_flag_back_even_when_the_export_fails(tmp_path):
    from catia_mcp.tools.measurement import MeasurementTools

    app = _AlertApp(raises=True)
    tools = MeasurementTools(connection=None)
    tools.conn = type("C", (), {"app": app})()

    with pytest.raises(RuntimeError):
        tools._export_stl(_ExportDoc(app), str(tmp_path / "part.stl"))
    assert app.DisplayFileAlerts is True


def test_a_build_without_the_alert_flag_still_exports(tmp_path):
    """Not every release exposes DisplayFileAlerts; losing it must not lose the box."""
    from catia_mcp.tools.measurement import MeasurementTools

    class _NoFlag:
        @property
        def DisplayFileAlerts(self):
            raise AttributeError("not exposed")

        @DisplayFileAlerts.setter
        def DisplayFileAlerts(self, value):
            raise AttributeError("not exposed")

    class _Doc:
        def ExportData(self, path, fmt):
            with open(path, "wb") as handle:
                handle.write(b"solid x\nendsolid x\n")

    tools = MeasurementTools(connection=None)
    tools.conn = type("C", (), {"app": _NoFlag()})()
    target = tmp_path / "part.stl"
    tools._export_stl(_Doc(), str(target))
    assert target.exists()


def test_both_export_sites_go_through_the_guarded_helper():
    from catia_mcp.tools.measurement import MeasurementTools

    mesh = inspect.getsource(MeasurementTools._mesh_export)
    copy = inspect.getsource(MeasurementTools._mesh_from_copy)
    for source in (mesh, copy):
        assert "self._export_stl(" in source
        # The raw call must not survive beside the guarded one.
        assert ".ExportData(" not in source
    guarded = inspect.getsource(MeasurementTools._export_stl)
    assert ".ExportData(" in guarded
    # ... and it is the only method that calls it.
    callers = sorted(
        name for name, member in inspect.getmembers(MeasurementTools,
                                                    inspect.isfunction)
        if ".ExportData(" in inspect.getsource(member))
    assert callers == ["_export_stl"]


def test_the_temp_stl_name_is_unique(tmp_path):
    from catia_mcp.tools.measurement import MeasurementTools

    source = inspect.getsource(MeasurementTools._mesh_export)
    assert "uuid4().hex" in source


# --------------------------------------------------------------------------
# 3. The STL scan can hand over the cloud, not just the extent.
# --------------------------------------------------------------------------

def test_stl_scan_returns_the_cloud_and_respects_the_limit(tmp_path):
    from catia_mcp.tools.measurement import _stl_scan

    path = tmp_path / "tri.stl"
    path.write_text(
        "solid t\n"
        "facet normal 0 0 1\n outer loop\n"
        "  vertex 0 0 0\n  vertex 4 0 0\n  vertex 0 2 0\n"
        " endloop\nendfacet\nendsolid t\n", encoding="ascii")

    lo, hi, cloud, seen = _stl_scan(str(path))
    assert seen == 3
    assert cloud is None            # no limit, no cloud
    assert lo == [0.0, 0.0, 0.0] and hi == [4.0, 2.0, 0.0]

    lo, hi, cloud, seen = _stl_scan(str(path), limit=2)
    assert seen == 3
    assert cloud == [0.0, 0.0, 0.0, 4.0, 0.0, 0.0]   # capped, flattened
    assert lo == [0.0, 0.0, 0.0] and hi == [4.0, 2.0, 0.0]


def test_the_mesh_cloud_is_flat_and_reports_the_true_vertex_count():
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._mesh_export = lambda part, limit=None: (
        [0.0, 0.0, 0.0], [2.0, 3.0, 4.0], [0.0, 0.0, 0.0, 2.0, 3.0, 4.0],
        2, "")
    cloud, seen, note = tools._mesh_cloud(object())
    assert cloud == [0.0, 0.0, 0.0, 2.0, 3.0, 4.0]
    assert seen == 2
    assert note == ""


def test_an_empty_cloud_falls_back_to_the_two_corners():
    """A lower bound is still better than no box - but it must not crash."""
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._mesh_export = lambda part, limit=None: (
        [-1.0, -2.0, -3.0], [1.0, 2.0, 3.0], [], 0, "")
    cloud, seen, note = tools._mesh_cloud(object())
    assert cloud == [-1.0, -2.0, -3.0, 1.0, 2.0, 3.0]
    assert seen == 0


# --------------------------------------------------------------------------
# 4. The per-part cost: the assembly route batches its vertex read.
# --------------------------------------------------------------------------

def test_the_assembly_vertex_route_reads_its_points_in_one_call():
    from catia_mcp.tools.measurement import MeasurementTools

    source = inspect.getsource(MeasurementTools._local_box)
    assert "read_measurable_points" in source
    # The per-point reader stays only as the fallback.
    assert "fallback=" in source


# --------------------------------------------------------------------------
# 5. Route counts are numbers, not prose.
# --------------------------------------------------------------------------

def _rows(count_curved: int, count_flat: int):
    class _Part:
        def __init__(self, name):
            self.Name = name
            self.MainBody = object()

    rows = []
    index = 0
    for name, count, curved in (("disc", count_curved, True),
                                ("block", count_flat, False)):
        for _ in range(count):
            index += 1
            rows.append({
                "index": index, "name": f"{name} I{index:03d}",
                "part_number": name, "part": _Part(name),
                "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "translation": [0.0, 0.0, 0.0], "problems": [],
            })
    return rows


def test_route_counts_are_explicit_fields():
    from types import SimpleNamespace

    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools._components_doc = lambda doc, only=None: _rows(3, 2)
    tools._part_identity = lambda part: part.Name
    tools._local_box = lambda part, body, stats=None: (
        ([], 0) if part.Name == "disc" else ([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], 8))
    tools._mesh_cloud = lambda part: ([0.0, 0.0, 0.0, 1.0, 1.0, 1.0], 12, "")

    payload = json.loads(tools._assembly_box(SimpleNamespace(Name="A.CATProduct"),
                                             "vertices"))
    assert payload["part_boxes"] == 2
    assert payload["part_boxes_by_mesh"] == 1
    assert payload["part_boxes_by_vertices"] == 1
    assert payload["mesh_fallback"] == ["disc"]
    assert payload["mesh_fallback_instances"] == 3
    assert payload["complete"] is True


# --------------------------------------------------------------------------
# 6. "No assembly is active" is not "the assembly is empty".
# --------------------------------------------------------------------------

def _assembly_tools(active_name, open_products):
    from catia_mcp.tools.assembly import AssemblyTools

    class _Doc:
        def __init__(self, name):
            self.Name = name
            self.FullName = name
            self.Type = "Product" if name.endswith(".CATProduct") else "Part"
            self.Part = object() if name.endswith(".CATPart") else None
            self.Product = None if name.endswith(".CATPart") else object()

    tools = AssemblyTools(connection=None)
    docs = [_Doc(name) for name in open_products]

    class _Docs:
        Count = len(docs)

        @staticmethod
        def Item(index):
            return docs[index - 1]

    tools.conn = type("C", (), {
        "ensure_connected": lambda self: None,
        "documents": _Docs(),
        "active_document": _Doc(active_name),
        "part_of": lambda self, doc: doc.Part if doc.Part else (_ for _ in ()).throw(
            RuntimeError("no Part")),
        "product_of": lambda self, doc: doc.Product if doc.Product else (
            _ for _ in ()).throw(RuntimeError("no Product")),
        "get_active_product": lambda self: (_ for _ in ()).throw(
            RuntimeError("no Product")),
    })()
    return tools


def test_listing_components_on_a_part_says_no_assembly_is_active():
    tools = _assembly_tools("PLN.CATPart", ["PLN.CATPart", "ASM.CATProduct"])
    with pytest.raises(RuntimeError) as error:
        tools._list_components(names_only=True)
    text = str(error.value)
    assert "The active document is 'PLN.CATPart', a Part" in text
    assert "needs an assembly (Product) document to be active" in text
    assert "ASM.CATProduct" in text          # says what IS open
    assert "No components in the active assembly" not in text


def test_listing_components_with_no_assembly_open_says_so():
    tools = _assembly_tools("PLN.CATPart", ["PLN.CATPart"])
    with pytest.raises(RuntimeError) as error:
        tools._list_components()
    text = str(error.value)
    assert "no assembly is open" in text
    assert "catia_open_document" in text
