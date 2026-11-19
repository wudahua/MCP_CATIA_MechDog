"""0.3.151: the arc that kept its radius, the cost that was an image, and the crop.

Everything here is offline. The measurements that need CATIA are in
``scripts/b30_v0472_acceptance_arc_and_cost.py`` (18/18 on B30 2026-11-19) and
the facts are quoted in the comments so the assertions are not the only source
of truth.

  1  the arc keeps its radius and moves its centre - the report's web arc
  2  a shallow arc is where the old three-point fit amplified the error
  3  the dog-bone gets two real fixes and a warning about the half circle
  4  an edge table without lengths still knows its faces, and fills on demand
  5  resolving Edge.N does not measure a single length
  6  the pad schema says FLIP, because that is what the code does
  7  catia_screenshot honours width/height (by resizing the window) and crops
"""
from __future__ import annotations

import json
import math
import struct

import pytest

from catia_mcp.results import ArgumentRejected, CodedError
from catia_mcp.tools.export import ExportTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools

# ── 1/2 the arc ───────────────────────────────────────────────────────────
WEB = {"type": "arc", "center": [15.0, 0.0], "radius": 72.367,
       "p1": [-55.222, 17.5], "p2": [-55.222, -17.5], "direction": "ccw"}


def test_the_web_arc_keeps_R72_367_instead_of_becoming_72_4594() -> None:
    """The 2026-11-19 report's §3.1, in numbers.

    Its ends are 0.0027 mm off the circle (the report says 0.0035 - its own
    arithmetic, same conclusion). 0.3.150 fitted a circle through the ends AND
    the arc's midpoint and answered R72.4594 with the centre 0.0924 mm away: a
    34x amplification, because that midpoint is computed from the radius rather
    than measured. Two features the drawing calls coaxial came out 0.09 mm apart.
    """
    plan = SketcherTools(connection=None)._plan_segment(1, WEB)
    assert plan["radius"] == pytest.approx(72.367, abs=1e-12), (
        "the radius is what two coaxial faces share; it must not move")
    drift = math.dist(plan["center"], WEB["center"])
    assert drift == pytest.approx(0.0028, abs=2e-4), (
        f"the centre moves by the size of the error, not 34x it - got {drift}")
    for end in (WEB["p1"], WEB["p2"]):
        assert math.dist(end, plan["center"]) == pytest.approx(72.367, abs=1e-9)


def test_the_shallower_the_arc_the_worse_the_three_point_fit_was() -> None:
    """The amplification is 1/(1-cos(sweep/2)), so it is the SWEEP that decides.

    A 28-degree arc amplifies by 34, a 90-degree one by 3.4, and a half circle
    not at all - which is why the old code looked fine on the profiles it was
    written for and destroyed a shallow web arc.
    """
    def amplification(sweep_degrees: float) -> float:
        return 1.0 / (1.0 - math.cos(math.radians(sweep_degrees) / 2.0))

    assert amplification(28) > 30
    assert amplification(90) == pytest.approx(3.41, abs=0.01)
    assert amplification(180) == pytest.approx(1.0, abs=1e-9)


def test_the_dog_bone_arc_is_refused_with_two_fixes_and_a_warning() -> None:
    with pytest.raises(ArgumentRejected) as caught:
        SketcherTools(connection=None)._plan_segment(1, {
            "type": "arc", "center": [-2.75, -1.10], "radius": 0.35,
            "p1": [-3.00, -1.10], "p2": [-2.75, -1.35], "direction": "ccw"})
    message = str(caught.value)
    assert "radius=0.2500" in message
    assert "0.1000" in message
    assert "R0.1768" in message and "NOT one of them" in message
    assert caught.value.data["centre_move_to_keep_radius_mm"] > 0.1


# ── 4/5 the edge table measures on demand ─────────────────────────────────
class _Cell:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Row:
    """One edge-table row: a Reference and the faces CATIA's NLS names."""

    def __init__(self, index: int, faces: list[str]) -> None:
        self.index = index
        self.faces = faces
        self.Reference = f"ref-{index}"


class _Spa:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def GetMeasurable(self, reference):
        self.asked.append(reference)
        return _Measurable()


class _Measurable:
    Length = 12.5


class _Part:
    def __init__(self) -> None:
        self.measured = 0


class _Conn:
    def __init__(self) -> None:
        self.spa = _Spa()
        self.part = _Part()

    def get_active_part(self):
        return self.part


def _tool_with_table(rows: list[dict]):
    conn = _Conn()
    tools = PartDesignTools(conn)
    tools._topo_measure = lambda part: conn.spa          # type: ignore[assignment]
    return tools, conn


def _row(index: int, faces: list[str]) -> dict:
    return {"index": index, "name": f"Edge.{index}", "cell": _Row(index, faces),
            "nls": "", "bounded_by": faces, "length": None, "fingerprint": None}


def test_a_length_free_row_still_resolves_Edge_N_and_measures_nothing_else() -> None:
    """The 0.3.151 win: Edge.N needs no Length, so no whole-body pass.

    Measured on B30 2026-11-19 on a 288-edge part: naming the edge took 4.5 s
    against 34.1 s when the table measured every edge first.
    """
    table = [_row(1, ["Pad.1;2", "Sketch.1;1"]), _row(2, ["Pad.1;2", "Sketch.1;4"])]
    tools, conn = _tool_with_table(table)
    labels, refs = tools._resolve_edge_specs(None, ["Edge.2"], table=table)
    assert refs == ["ref-2"]
    assert labels == ["Edge.2 (L12.5000|Pad.1;2|Sketch.1;4)"], (
        "the label quotes the length, so exactly ONE row is measured")
    assert conn.spa.asked == ["ref-2"], conn.spa.asked


def test_a_fingerprint_fills_the_lengths_it_needs_and_no_more() -> None:
    table = [_row(1, ["Pad.1;2", "Sketch.1;1"]), _row(2, ["Pad.1;2", "Sketch.1;4"])]
    tools, conn = _tool_with_table(table)
    labels, refs = tools._resolve_edge_specs(
        None, ["L12.5000|Pad.1;2|Sketch.1;4"], table=table)
    assert refs == ["ref-2"]
    assert "Edge.2" in labels[0]
    assert len(conn.spa.asked) <= 2, conn.spa.asked


def test_the_filler_is_lazy_and_idempotent() -> None:
    table = [_row(1, ["Pad.1;2"])]
    tools, conn = _tool_with_table(table)
    fill = tools._length_filler(table)
    assert conn.spa.asked == [], "creating the filler must not open the workbench"
    fill([])
    assert conn.spa.asked == []
    fill(table)
    assert table[0]["length"] == pytest.approx(12.5)
    assert table[0]["fingerprint"] == "L12.5000|Pad.1;2"
    fill(table)                      # asked twice, measured once
    assert len(conn.spa.asked) == 1


# ── 6 the schema that contradicted the changelog ──────────────────────────
def test_the_pad_schema_says_flip_not_swap() -> None:
    """§3.4: the description said the extents swap; the code flips the pair.

    Measured on B30 2026-11-19: limit1=5, limit2=3 with direction=reverse gives
    z -3..+5, i.e. limit2 is the OTHER side - the pair changes side, nothing
    swaps. A caller who believed "swap" would get the mirror of what they asked
    for and no way to tell from the numbers.
    """
    tools = PartDesignTools(connection=None)
    definition = [tool for tool in tools.get_tool_definitions()
                  if tool["name"] == "catia_pad"][0]
    limit2 = definition["inputSchema"]["properties"]["limit2"]["description"]
    assert "FLIPS THE PAIR" in limit2
    assert "swap" not in limit2.replace("swaps", ""), limit2
    assert "-4..+1" in limit2, "the worked example is the evidence"


# ── 7 the screenshot ──────────────────────────────────────────────────────
class _Window:
    def __init__(self, width: int = 772, height: int = 744, state: int = 0):
        self.Width = width
        self.Height = height
        self.WindowState = state
        self.ActiveViewer = _Viewer()

    def Activate(self) -> None:
        pass


class _Viewer:
    def __init__(self) -> None:
        self.updates = 0

    def Update(self) -> None:
        self.updates += 1

    def Reframe(self) -> None:
        pass


def _jpeg(path: str, width: int, height: int) -> None:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">H", height) + struct.pack(">H", width) + b"\x03"
            + b"\x00" * 9)
    with open(path, "wb") as handle:
        handle.write(b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")


def test_a_maximized_window_ignores_the_size_and_the_reply_says_so() -> None:
    tools = ExportTools(connection=None)
    window = _Window(state=1)
    note, restore = tools._apply_capture_size(window, 1200, 800, window.ActiveViewer)
    assert "maximized" in note
    assert restore is None
    assert window.Width == 772


def test_a_normal_window_is_resized_and_the_original_is_returned() -> None:
    tools = ExportTools(connection=None)
    window = _Window()
    note, restore = tools._apply_capture_size(window, 1200, 800, window.ActiveViewer)
    assert note == ""
    assert (window.Width, window.Height) == (1200, 800)
    assert restore == (772, 744)
    tools._restore_window_size(window, restore)
    assert (window.Width, window.Height) == (772, 744)


def test_a_window_that_ignores_the_setter_is_reported_not_hidden() -> None:
    """A clamp and a silent no-op look identical from the file, so both are said.

    Measured on B30 2026-11-19: right after `catia_new_part` the window accepts
    1200x800 and reads it back, but the VIEW keeps its old frame until CATIA's
    own message loop runs - which is why the capture is verified and retried.
    A window that never takes the size at all has the same visible outcome.
    """

    class _Stuck(_Window):
        def __init__(self) -> None:
            object.__setattr__(self, "Width", 772)
            object.__setattr__(self, "Height", 744)
            object.__setattr__(self, "WindowState", 0)
            object.__setattr__(self, "ActiveViewer", _Viewer())

        def __setattr__(self, name, value):
            if name in ("Width", "Height"):
                return
            object.__setattr__(self, name, value)

    tools = ExportTools(connection=None)
    note, restore = tools._apply_capture_size(_Stuck(), 1200, 800, _Viewer())
    assert "asked for 1200x800" in note
    assert "772x744" in note
    assert restore == (772, 744)


def test_the_crop_box_is_validated_before_anything_is_captured() -> None:
    tools = ExportTools(connection=None)
    assert tools._crop_box(None) is None
    assert tools._crop_box([10, 20, 300, 200]) == (10, 20, 300, 200)
    for bad in ([1, 2, 3], "nonsense", [0, 0, 0, 10], [-1, 0, 10, 10]):
        with pytest.raises(ArgumentRejected):
            tools._crop_box(bad)


def test_the_crop_cuts_the_file_it_was_given(tmp_path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    tools = ExportTools(connection=None)
    path = tmp_path / "shot.jpg"
    with Image.new("RGB", (900, 700), (30, 40, 50)) as image:
        image.save(path, quality=90)
    size = tools._crop_image(str(path), (10, 20, 300, 200))
    assert size == "300x200"
    with Image.open(path) as cropped:
        assert cropped.size == (300, 200)


def test_a_crop_that_does_not_fit_refuses_and_says_the_file_is_whole(tmp_path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    tools = ExportTools(connection=None)
    path = tmp_path / "shot.jpg"
    with Image.new("RGB", (120, 100), (0, 0, 0)) as image:
        image.save(path)
    with pytest.raises(ArgumentRejected) as caught:
        tools._crop_image(str(path), (100, 50, 300, 200))
    assert "uncropped" in str(caught.value)
    with Image.open(path) as still:
        assert still.size == (120, 100)


def test_a_missing_pillow_is_a_sentence_not_a_traceback(tmp_path, monkeypatch) -> None:
    """Pillow is not a runtime dependency, so this branch is reachable."""
    import builtins
    real_import = builtins.__import__

    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("No module named 'PIL'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    tools = ExportTools(connection=None)
    path = tmp_path / "shot.jpg"
    _jpeg(str(path), 100, 80)
    with pytest.raises(CodedError) as caught:
        tools._crop_image(str(path), (0, 0, 10, 10))
    assert caught.value.code == "OPTIONAL_DEPENDENCY_MISSING"
    assert "NOT cropped" in str(caught.value)
    assert json.dumps(caught.value.to_dict()) if hasattr(caught.value, "to_dict") else True
