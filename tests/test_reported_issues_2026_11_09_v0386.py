"""0.3.86: the three 2026-11-09 reports on 0.3.85.

Two of them are about things this package said it could do and could not, one is
about a reply that named a cause it had not measured, and one is the P0 that has
now been reported four times:

  * ``catia_create_sketch`` rejected a datum plane NAME in its schema while the
    description said names were accepted - for two releases
  * a 25 mm straight side of a flat sector came back as "Loop #1 ... THIS loop
    only"; the border is 91.1450 mm in four sides
  * every ``edge=N`` failure was attributed to "a JOIN result", including a
    failure on a single loft
  * a datum plane's border carries the same Selection_BorderREdge label as a
    surface's and measures 2e7 mm, so summing "the border edges" gave
    80000091.1450
  * ``SAVE_FAILED`` printed three assertions about a document it had not looked
    at ("This document already has a file") next to "no part document"
"""
from __future__ import annotations

import types

import pytest

from catia_mcp.results import CodedError


# ── the schema that contradicted its own description ────────────────────


def test_create_sketch_plane_accepts_any_name_and_says_which_tools_make_one():
    from catia_mcp.tools.sketcher import SketcherTools

    schema = next(entry for entry in SketcherTools(connection=None)
                  .get_tool_definitions()
                  if entry["name"] == "catia_create_sketch")
    plane = schema["inputSchema"]["properties"]["plane"]
    branches = plane["anyOf"]
    strings = [b for b in branches if b["type"] == "string"]
    assert len(strings) == 1
    assert "enum" not in strings[0], (
        "an enum here rejects 'Plane3_36' before the server can look it up - "
        "that was reported twice")
    description = plane["description"]
    assert "_plane_angle" in description
    assert "any name, not just the three main planes" in description


def test_the_three_main_planes_still_validate_as_plain_strings():
    """Removing the enum must not break plane='xy' - the commonest call there is."""
    import jsonschema

    from catia_mcp.tools.sketcher import SketcherTools

    schema = next(entry for entry in SketcherTools(connection=None)
                  .get_tool_definitions()
                  if entry["name"] == "catia_create_sketch")
    plane = schema["inputSchema"]["properties"]["plane"]
    wrapped = {"type": "object", "properties": {"plane": plane},
               "required": ["plane"]}
    for value in ("xy", "yz", "zx", "Plane3_36", "Plane4_72"):
        jsonschema.validate({"plane": value}, wrapped)
    jsonschema.validate({"plane": {"type": "offset", "base": "xy",
                                   "distance": 40}}, wrapped)


# ── the reply that called a straight side a loop ────────────────────────


class _Shape:
    def __init__(self, name: str = "") -> None:
        self.Name = name


class _Sel:
    def __init__(self, cells=()) -> None:
        self._cells = list(cells)
        self.Count = len(self._cells)

    def Clear(self):
        pass

    def Add(self, _thing):
        pass

    def Search(self, _query):
        pass

    def Item(self, index):
        return self._cells[index - 1]


class _Cell:
    def __init__(self, index: int, nls: str, length: float | None) -> None:
        self.Value = types.SimpleNamespace(Name=nls)
        # Unique per cell: the first 20 characters of every NLS are identical,
        # so keying on them collapsed the whole table onto one length.
        self.Reference = f"ref{index}"
        self._length = length

    def length(self):
        return self._length


def _gsd(plane_length: float = 2e7):
    from catia_mcp.tools.gsd import GSDTools

    tools = GSDTools(connection=None)
    cells = [
        _Cell(1, "Selection_BorderREdge:(BEdge:(Brp:(GSMFill.1;(Brp:(GSMLine.5)));"
              "None:(Limits1:();Limits2:();+1);Cf12:());GSMFill.1;Z0", 5.8788),
        _Cell(2, "Selection_BorderREdge:(BEdge:(Brp:(GSMFill.1;(Brp:(GSMLine.4)));"
              "None:(Limits1:();Limits2:();+1);Cf12:());GSMFill.1;Z0", 24.9637),
        _Cell(3, "Selection_BorderREdge:(BEdge:(Brp:(GSMPlane.1;4);"
              "None:(Limits1:();Limits2:();+1);Cf12:());GSMPlane.1;Z0",
              plane_length),
        _Cell(4, "Selection_WireREdge:(Wire:(Brp:(GSMLine.2);None:(Limits1:();"
              "Limits2:());Cf12:());GSMLine.2;Z0", 25.0),
    ]
    selection = _Sel(cells)
    lengths = {cell.Reference: cell.length() for cell in cells}

    class _Spa:
        def GetMeasurable(self, reference):
            return types.SimpleNamespace(Length=lengths.get(reference, 0.0))

    tools.conn = types.SimpleNamespace(
        hso=selection,
        active_document=types.SimpleNamespace(
            GetWorkbench=lambda name: _Spa()),
    )
    tools._get_geoset = lambda: types.SimpleNamespace(Name="GSD_Set")  # type: ignore[method-assign]
    return tools


def test_a_datum_planes_border_is_not_counted_as_a_surface_border():
    """Its four borders measure 2e7 mm; counting them gave 80000091.1450."""
    tools = _gsd()
    _selection, rows = tools._geoset_border_edges()
    lengths = [length for _i, _r, length in rows]
    assert lengths == [5.8788, 24.9637], (
        f"a datum plane border or a construction-line edge leaked in: {lengths}")
    assert sum(lengths) == pytest.approx(30.8425)


def test_a_construction_line_edge_is_not_counted_either():
    """Selection_WireREdge is a line's own edge, same length as a real border."""
    tools = _gsd()
    _selection, rows = tools._geoset_border_edges()
    assert all(length < 1e3 for _i, _r, length in rows)


# ── Save failures: conditions, not assertions ──────────────────────────


def _save_refusal():
    from catia_mcp import dialogs

    class _Doc:
        Name = "never_saved.CATPart"

        def Save(self):
            raise RuntimeError("方法 Save 失败")

    import catia_mcp.dialogs as module

    original_dismiss = module.dismiss
    original_sleep = module.time.sleep
    module.dismiss = lambda *a, **k: 0
    module.time.sleep = lambda *_a: None
    try:
        with pytest.raises(CodedError) as caught:
            dialogs.save(_Doc(), tries=1)
    finally:
        module.dismiss = original_dismiss
        module.time.sleep = original_sleep
    return caught.value


def test_a_save_failure_does_not_assert_the_document_has_a_file():
    error = _save_refusal()
    assert error.code == "SAVE_FAILED"
    text = " ".join(error.next_actions)
    assert "IF this document already had a file" in text
    assert "IF it has never been saved" in text
    assert "already has a file, so the path is not the problem" not in text


def test_a_save_failure_is_not_reported_as_a_modified_model():
    """effects.modified was null; a Save that wrote nothing modified nothing."""
    error = _save_refusal()
    assert error.modified is False


# ── the two descriptions that sent callers the wrong way ───────────────


def test_the_thickening_description_compares_one_side_not_the_total():
    from catia_mcp.tools.gsd import GSDTools

    schema = next(entry for entry in GSDTools(connection=None)
                  .get_tool_definitions()
                  if entry["name"] == "catia_gsd_thick_surface")
    text = schema["description"]
    assert "EACH SIDE'S OFFSET" in text
    assert "split the same total across both sides" in text
    # The old sentence that sent the reporter away from 0.5 mm:
    assert "TOTAL OFFSET MUST STAY BELOW TWICE" not in text


def test_cap_curve_says_a_loft_border_has_to_be_one_of_the_guides():
    from catia_mcp.tools.gsd import GSDTools

    schema = next(entry for entry in GSDTools(connection=None)
                  .get_tool_definitions()
                  if entry["name"] == "catia_gsd_circ_pattern")
    text = schema["inputSchema"]["properties"]["cap_curve"]["description"]
    assert "ON A LOFT THIS CURVE HAS TO BE ONE OF THE GUIDES" in text


def test_new_part_no_longer_promises_that_the_path_is_enough():
    from catia_mcp.tools.document import DocumentTools

    schema = next(entry for entry in DocumentTools(connection=None)
                  .get_tool_definitions()
                  if entry["name"] == "catia_new_part")
    text = schema["description"]
    assert "nothing else is needed when the path is given" not in text
    # 0.3.88 reworded both sentences with the measurements behind them: the
    # folder is environment-dependent (nine successes here, eight failures on
    # the reporter's machine), and the first save names the COM call it makes.
    assert "A NON-ASCII FOLDER is environment-dependent" in text
    assert "THE FIRST SAVE IS doc.SaveAs(path)" in text
    assert "eight failures and no success" in text
