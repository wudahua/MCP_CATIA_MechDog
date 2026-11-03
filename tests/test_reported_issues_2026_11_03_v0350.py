# -*- coding: utf-8 -*-
"""0.3.50 - the 0.3.49 acceptance receipt and the sheet-metal report.

Six findings from the two 2026-11-03 reports, all measured on B30 first:

1. catia_set_view had front/back and top/bottom swapped (and Y as the up vector
   on the side views), so 'front' produced a top-down picture and 'top' an
   upside-down elevation. The two identical screenshots the report called "async"
   were the same wrong view twice. The table is pinned here, including the two
   invariants that make a wrong table impossible to miss: sight and up are
   perpendicular, and no two views share a sight direction.
2. catia_screenshot printed the REQUESTED size while writing the 3D window's
   client area (900x900 asked, 300x400 written). It now reads the size out of
   the file it just wrote.
3. catia_gsd_join failed with 167 characters of raw COM even though the rule was
   in its docstring. The failure now names the elements and the cause.
4. The mesh-box warning fired on geometrical sets that were all hidden. It is
   now driven by the set's own visibility flag ((0,0) shown / (0,1) hidden,
   measured on B30).
5. Deleting a coordinate spline left its points behind (six stayed after a
   six-point spline was deleted), and there was no batch delete. Both exist now.
6. Sheet metal: the server has none; catia_diagnose now says what can be said.
"""

from __future__ import annotations

import io
import json
import os
import struct
import tempfile

import pytest


# ───────────────────── 1. the view table

class _Viewpoint:
    def __init__(self):
        self.sight = None
        self.up = None
        self.calls = []

    def PutSightDirection(self, vector):
        self.sight = tuple(round(float(v), 6) for v in vector)
        self.calls.append(("sight", self.sight))

    def PutUpDirection(self, vector):
        self.up = tuple(round(float(v), 6) for v in vector)
        self.calls.append(("up", self.up))


class _Viewer:
    def __init__(self):
        self.Viewpoint3D = _Viewpoint()
        self.reframed = 0

    def Reframe(self):
        self.reframed += 1


def _export():
    from catia_mcp.tools.export import ExportTools

    tools = ExportTools(connection=None)
    viewer = _Viewer()
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "active_window": type("W", (), {"ActiveViewer": viewer})(),
    })()
    return tools, viewer


@pytest.mark.parametrize("view,sight,up", [
    ("front", (0, 1, 0), (0, 0, 1)),
    ("back", (0, -1, 0), (0, 0, 1)),
    ("top", (0, 0, -1), (0, 1, 0)),
    ("bottom", (0, 0, 1), (0, -1, 0)),
    ("left", (1, 0, 0), (0, 0, 1)),
    ("right", (-1, 0, 0), (0, 0, 1)),
    ("isometric", (-1, -1, -1), (-1, -1, 2)),
])
def test_view_table_is_the_catia_one(view, sight, up):
    tools, viewer = _export()
    assert tools.execute("catia_set_view", {"view": view}) == f"View set to: {view}"
    assert viewer.Viewpoint3D.sight == sight
    assert viewer.Viewpoint3D.up == up
    assert viewer.reframed == 1


def test_every_view_is_a_perpendicular_unit_pair_and_unique():
    tools, _ = _export()
    seen = {}
    for view in ("front", "back", "top", "bottom", "left", "right", "isometric"):
        viewer = tools.conn.active_window.ActiveViewer
        tools.execute("catia_set_view", {"view": view})
        sight = viewer.Viewpoint3D.sight
        up = viewer.Viewpoint3D.up
        assert abs(sum(a * b for a, b in zip(sight, up))) < 1e-9, f"{view}: not perpendicular"
        assert sight not in seen, f"{view} shares its sight with {seen.get(sight)}"
        seen[sight] = view


# ───────────────────── 2. the screenshot reports what it wrote

def _jpeg(path: str, width: int, height: int) -> None:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">H", height) + struct.pack(">H", width) + b"\x03" + b"\x00" * 9)
    with open(path, "wb") as handle:
        handle.write(b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")


def test_screenshot_reports_the_size_it_wrote(tmp_path):
    tools, viewer = _export()
    path = tmp_path / "shot.jpg"

    def capture(fmt, target):
        _jpeg(target, 300, 400)

    viewer.CaptureToFile = capture
    viewer.Update = lambda: None
    reply = tools.execute("catia_screenshot",
                          {"file_path": str(path), "width": 900, "height": 900})
    assert "300x400 px" in reply
    assert "asked for 900x900" in reply
    assert "900x900 px" not in reply, "the reply must not repeat the request as fact"


def test_screenshot_does_not_complain_when_the_size_matches(tmp_path):
    tools, viewer = _export()
    path = tmp_path / "shot.jpg"
    viewer.CaptureToFile = lambda fmt, target: _jpeg(target, 900, 900)
    viewer.Update = lambda: None
    reply = tools.execute("catia_screenshot",
                          {"file_path": str(path), "width": 900, "height": 900})
    assert "900x900 px" in reply
    assert "asked for" not in reply


def test_bmp_size_is_read_too(tmp_path):
    from catia_mcp.tools.export import ExportTools

    path = tmp_path / "shot.bmp"
    header = b"BM" + b"\x00" * 16 + struct.pack("<i", 640) + struct.pack("<i", -480)
    with open(path, "wb") as handle:
        handle.write(header + b"\x00" * 32)
    assert ExportTools._image_size(str(path)) == "640x480"


# ───────────────────── 3. a join failure says what joined

def _gsd():
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "refresh_display": staticmethod(lambda: None),
    })()
    return tools


def test_join_failure_names_the_elements_and_the_cause():
    tools = _gsd()
    tools._ref = lambda name: f"ref:{name}"
    tools._find_element = lambda name: object()
    tools._com_type_name = lambda element: "HybridShapeExtrude"
    tools._factory = lambda: type("F", (), {"AddNewJoin": lambda self, a, b: object()})()

    def boom(shape, name, message):
        raise RuntimeError("CATIA rejected the feature (update failed)")

    tools._finish = boom
    with pytest.raises(RuntimeError) as caught:
        tools.execute("catia_gsd_join", {"elements": ["Surf_Bottom", "SkirtTrim"]})
    text = str(caught.value)
    assert "Joined: 'Surf_Bottom' (HybridShapeExtrude), 'SkirtTrim'" in text
    assert "SHARED EDGE" in text
    assert "JOINING FIRST and SPLITTING AFTERWARDS" in text


# ───────────────────── 4. the mesh warning follows the visibility flag

class _VisProps:
    def __init__(self, states, selection):
        self.states = states
        self.selection = selection

    def GetShow(self):
        return (0, self.states[self.selection.current])


class _Selection:
    def __init__(self, states):
        # keyed by the node name _Sets hands out: {"set1": 0, "set2": 1}
        self.states = {f"set{i + 1}": state for i, state in enumerate(states)}
        self.current = None
        self.cleared = 0

    def Clear(self):
        self.current = None
        self.cleared += 1

    def Add(self, node):
        self.current = node

    @property
    def VisProperties(self):
        return _VisProps(self.states, self)


class _Sets:
    def __init__(self, states):
        self.Count = len(states)

    def Item(self, index):
        return f"set{index}"


class _HybridPart:
    Parent = type("P", (), {"Name": "V.CATPart"})()

    def __init__(self, states):
        self.HybridBodies = _Sets(states)
        self.MainBody = type("B", (), {"Shapes": type("S", (), {"Count": 1})()})()


def _measurement(states):
    from catia_mcp.tools.measurement import MeasurementTools

    tools = MeasurementTools(connection=None)
    tools.conn = type("C", (), {
        "ensure_connected": staticmethod(lambda: None),
        "hso": _Selection(states),
    })()
    tools._part_identity = lambda p: (_ for _ in ()).throw(RuntimeError("none"))
    tools._mesh_corners = lambda p: ([[0, 0, 0], [5, 5, 10]], 36, "")
    return tools


def test_mesh_box_is_silent_when_every_set_is_hidden():
    payload = json.loads(_measurement([1, 1])._part_bbox(_HybridPart([1, 1]), "mesh"))
    assert "hidden" in payload["box_scope"]
    assert "warnings" not in payload


def test_mesh_box_warns_when_a_set_is_shown():
    payload = json.loads(_measurement([0, 1])._part_bbox(_HybridPart([0, 1]), "mesh"))
    assert "VISIBLE" in payload["box_scope"]
    assert "1 of them are shown" in payload["warnings"][0]


# ───────────────────── 5. delete: batch and cascade

def test_owned_points_are_remembered_per_document_and_owner():
    from catia_mcp.connection import CATIAConnection

    conn = CATIAConnection()
    conn.sketch_scope = lambda: "P.CATPart"
    conn.note_owned("Spline.1", ["点.1", "点.2"])
    assert conn.owned_by("Spline.1") == ["点.1", "点.2"]
    assert conn.owned_by("Spline.2") == []
    assert conn.forget_owned("Spline.1") == ["点.1", "点.2"]
    assert conn.owned_by("Spline.1") == []


def _part_design():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    tools.conn = type("C", (), {"ensure_connected": staticmethod(lambda: None)})()
    return tools


def test_batch_delete_aggregates_and_reports_each_failure():
    tools = _part_design()

    def one(args, forced):
        if forced == "bad":
            raise RuntimeError("No feature named 'bad'")
        return f"Deleted element '{forced}'."

    tools._delete_one = one
    reply = tools.execute("catia_delete_feature", {"names": ["a", "bad", "c"]})
    assert "Deleted 2 of 3 named element(s)." in reply
    assert "bad" in reply and "No feature named" in reply


def test_batch_delete_refuses_to_guess_between_forms():
    from catia_mcp.results import ArgumentRejected

    tools = _part_design()
    with pytest.raises(ArgumentRejected):
        tools.execute("catia_delete_feature",
                      {"names": ["a"], "feature_name": "b"})


# ───────────────────── 6. sheet metal: installed vs reachable

def test_sheetmetal_probe_reports_the_dlls_beside_the_running_exe(tmp_path):
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    for name in DiagnosticsTools._SHEETMETAL_DLLS:
        (tmp_path / name).write_bytes(b"")
    app = type("A", (), {"FullName": str(tmp_path / "CNEXT.exe")})()
    probe = DiagnosticsTools(connection=None)._sheetmetal_probe(app)
    assert probe["installed"].startswith("yes")
    assert set(probe["files"].values()) == {"present"}
    assert "none - the server has no wall" in probe["server_tools"]
    assert "licence" in probe


def test_sheetmetal_probe_says_when_the_files_are_missing(tmp_path):
    from catia_mcp.tools.diagnostics import DiagnosticsTools

    app = type("A", (), {"FullName": str(tmp_path / "CNEXT.exe")})()
    probe = DiagnosticsTools(connection=None)._sheetmetal_probe(app)
    assert probe["installed"].startswith("partly")
