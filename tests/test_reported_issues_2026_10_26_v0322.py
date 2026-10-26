# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-26 (later) batch: three reports on 0.3.21.

Three of them are the same shape as before - a tool that answered without
measuring:

* ``catia_rect_pattern`` treated a single reversed direction as a left-handed
  axis pair, swapped the slots, and the reversed axis landed in dir2 where
  ``AddNewRectPattern`` refuses it. Measured on B30 2026-10-26: ``reverse_dir1``
  with no axis builds (-1005.31 mm3), the same call with ``dir1_axis: "x"`` was
  rolled back, and reversing both sides built again.
* ``catia_measure_topology`` re-counted with its own copy of the filter and could
  answer ``edges 1`` and then ``edges 12`` for the same untouched part.
* ``catia_get_bounding_box`` refused ``document=`` for an assembly, i.e. the only
  unambiguous route in a session where a second client can steal ActiveDocument.
"""

from __future__ import annotations

import inspect
import json

import pytest


# --------------------------------------------------------------------------
# 1. A reversed direction is not a left-handed axis pair.
# --------------------------------------------------------------------------

def test_a_single_reversed_direction_does_not_swap_the_axes():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert 'right_handed = (first, second) in (("x", "y"), ("y", "z"), ("z", "x"))' in source
    # The 0.3.21 form ANDed the reversal parity in, which made every single-sided
    # reversal look left-handed.
    assert "and reverse1 == reverse2" not in source
    # The swap itself stays, for genuinely left-handed pairs.
    assert "first, second = second, first" in source


def test_the_reversal_still_travels_with_its_axis_when_the_pair_is_swapped():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert "reverse1, reverse2 = reverse2, reverse1" in source
    assert "d1_count, d2_count = d2_count, d1_count" in source
    assert "d1_spacing, d2_spacing = d2_spacing, d1_spacing" in source


# --------------------------------------------------------------------------
# 2. The construction filter says WHY, so a count can be explained.
# --------------------------------------------------------------------------

def test_each_construction_reason_is_named():
    from catia_mcp.topo_search import construction_reason, is_construction

    assert construction_reason("") is None
    assert construction_reason(
        "Selection_REdge:(Edge:(Face:(Brp:(Pad.1;2);None:();Cf12:());"
        "None:(Limits1:();Limits2:());Cf12:());Pad.1_ResultOUT;Z0;G8782)"
    ) is None
    assert construction_reason(
        "Selection_WireREdge:(Wire:(Brp:(Sketch.1;4);None:(Limits1:();Limits2:());"
        "Cf12:());Sketch.1;Z0;G8782)"
    ) == "sketch_wire_edge"
    assert construction_reason(
        "Selection_BorderREdge:(Edge:(Brp:(GSMPlane.1;1)))"
    ) == "datum_plane_border_edge"
    assert construction_reason(
        "Selection_FVertex:(Vertex:(Brp:(Pad.1;0);None:();Cf12:());Pad.1_ResultOUT)"
    ) is None
    assert is_construction("Selection_WireFVertex:(Vertex:(Wire:...))") is True
    assert is_construction("Selection_REdge:(Edge:(...))") is False


# --------------------------------------------------------------------------
# 3. A transient CATIA state cannot renumber every Edge.N.
# --------------------------------------------------------------------------

class _Cell:
    def __init__(self, nls):
        self.Value = type("V", (), {"Name": nls})()


_SOLID = ("Selection_REdge:(Edge:(Face:(Brp:(Pad.1;2);None:();Cf12:());"
          "None:(Limits1:();Limits2:());Cf12:());Pad.1_ResultOUT;Z0;G8782)")
_WIRE = ("Selection_WireREdge:(Wire:(Brp:(Sketch.1;4);None:(Limits1:();Limits2:());"
         "Cf12:());Sketch.1;Z0;G8782)")


class _Sel:
    """A selection whose answer changes between passes, like the reported one."""

    def __init__(self, passes, fail_item=False):
        self.passes = 0
        self.planned = passes
        self.fail_item = fail_item
        self.cells = []

    def Clear(self):
        self.cells = []

    def Add(self, _item):
        pass

    def Search(self, _query):
        self.passes += 1
        layout = self.planned[min(self.passes, len(self.planned)) - 1]
        self.cells = [_Cell(_SOLID if kind == "solid" else _WIRE) for kind in layout]

    @property
    def Count(self):
        return len(self.cells)

    def Item(self, index):
        if self.fail_item and self.passes == 1:
            raise RuntimeError("方法 Item 失败")
        return self.cells[index - 1]


def _conn(selection):
    return type("C", (), {"hso": selection})()


def test_a_suspiciously_small_count_is_enumerated_again():
    from catia_mcp.topo_search import topology_cells

    sel = _Sel([["wire"] * 20, ["solid"] * 12 + ["wire"] * 4])
    stats = {}
    cells = topology_cells(_conn(sel), object(), "Edge", stats=stats)
    assert len(cells) == 12
    assert stats["retried"] is True
    assert stats["excluded"] == {"sketch_wire_edge": 4}


def test_a_clean_enumeration_is_not_repeated():
    from catia_mcp.topo_search import topology_cells

    sel = _Sel([["solid"] * 12 + ["wire"] * 4])
    stats = {}
    assert len(topology_cells(_conn(sel), object(), "Edge", stats=stats)) == 12
    assert stats["retried"] is False


def test_a_busy_selection_is_retried_and_then_named():
    from catia_mcp.topo_search import topology_cells

    sel = _Sel([["wire"] * 20, ["solid"] * 12 + ["wire"] * 4], fail_item=True)
    # The first pass never delivers its items; the retry does.
    assert len(topology_cells(_conn(sel), object(), "Edge")) == 12


def test_a_selection_that_never_answers_says_it_is_the_selection():
    from catia_mcp.topo_search import topology_cells

    class _AlwaysBusy(_Sel):
        def Item(self, index):
            raise RuntimeError("方法 Item 失败")

    with pytest.raises(RuntimeError) as error:
        topology_cells(_conn(_AlwaysBusy([["solid"]])), object(), "Edge")
    message = str(error.value)
    assert "SELECTION being unavailable" in message
    assert "not the model having no" in message


# --------------------------------------------------------------------------
# 4. The counter uses the same enumeration as the listings.
# --------------------------------------------------------------------------

def test_the_counter_shares_the_listing_enumeration():
    from catia_mcp.tools.planner import PlannerTools

    source = inspect.getsource(PlannerTools._count_topology)
    assert "topology_cells(self.conn, body, kind, stats=stats)" in source
    assert "by_reason" in source
    assert "_retried" in source


# --------------------------------------------------------------------------
# 5. An assembly can be measured by name, and one box per part is enough.
# --------------------------------------------------------------------------

def _measurement_tools():
    from catia_mcp.tools.measurement import MeasurementTools

    return MeasurementTools(connection=None)


def test_a_product_document_is_recognised_by_its_type():
    tools = _measurement_tools()
    assert tools._is_product_document(type("D", (), {"Type": "Product"})()) is True
    assert tools._is_product_document(type("D", (), {"Type": "ProductDocument"})()) is True
    assert tools._is_product_document(type("D", (), {"Type": "Part"})()) is False


def test_bbox_document_routes_a_product_to_the_world_aggregation():
    from catia_mcp.tools.measurement import MeasurementTools

    source = inspect.getsource(MeasurementTools._get_bounding_box)
    assert "_is_product_document(doc)" in source
    assert "self._assembly_box(doc, method, component," in source
    # The Part-branch refusal must still exist for anything that is neither.
    assert "is neither a Part nor a Product document" in source


def test_the_same_part_is_bounded_once_per_assembly():
    tools = _measurement_tools()
    calls = []

    class _Doc:
        FullName = "C:/parts/PA.CATPart"

    class _Part:
        Parent = _Doc()
        MainBody = type("B", (), {"Name": "PartBody"})()

    part = _Part()
    key_one = tools._part_identity(part)
    key_two = tools._part_identity(_Part())
    assert key_one == key_two

    rows = [{"part": part, "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
             "translation": [i * 100, 0, 0], "name": f"PA.{i + 1}",
             "part_number": "PA", "index": i + 1, "problems": []}
            for i in range(4)]

    def local_box(_part, _body, _stats=None):
        calls.append(1)
        return [[0, 0, 0], [10, 10, 10]], 2

    tools._local_box = local_box
    tools._components_doc = lambda doc: rows
    payload = json.loads(tools._assembly_box(object(), "vertices"))
    assert len(calls) == 1, "one part must be bounded once"
    assert payload["part_boxes"] == 1
    assert payload["instances"] == {"total": 4, "measured": 4, "skipped": 0}
    assert payload["max"]["x"] == 310.0


# --------------------------------------------------------------------------
# 6. Listing components filters before paying for each placement.
# --------------------------------------------------------------------------

def test_list_components_filters_before_reading_placements():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._list_components)
    filter_at = source.index("if wanted is not None:")
    pose_at = source.index("self._position_components(comp.Position)")
    assert filter_at < pose_at, "the placement must not be read for filtered-out rows"
    assert "names_only" in source


def test_list_components_takes_names_only():
    from catia_mcp.tools.assembly import AssemblyTools

    schema = [d for d in AssemblyTools(connection=None).get_tool_definitions()
              if d["name"] == "catia_list_components"][0]["inputSchema"]
    assert schema["properties"]["names_only"]["default"] is False


# --------------------------------------------------------------------------
# 7. Small things the same reports listed.
# --------------------------------------------------------------------------

def test_pad_description_states_the_auto_flip():
    from catia_mcp.tools.part_design import PartDesignTools

    description = [d for d in PartDesignTools(connection=None).get_tool_definitions()
                   if d["name"] == "catia_pad"][0]["description"]
    assert "auto-flipped to reach material" in description
    assert "BOTH orientations" in description


def test_the_pad_failure_text_has_no_double_full_stop():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._pad)
    assert 'if update_error else ".")' not in source
    assert "does.." not in source


def test_set_active_body_reads_the_work_object_back():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._set_active_body)
    assert "_in_work_name(part)" in source
    assert "CATIA still reports" in source


def test_a_rolled_back_pad_puts_the_body_back_in_work():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._pad)
    assert "part.InWorkObject = body" in source
    # The reset has to happen on the failure path, right after the rollback.
    rollback = source.index("gone = self._remove_feature(part, pad)")
    reset = source.index("part.InWorkObject = body", rollback)
    assert reset - rollback < 400, "reset must sit next to the rollback"


def test_closing_a_referenced_part_pluralises_correctly():
    from catia_mcp.tools.document import DocumentTools

    tools = DocumentTools(connection=None)
    tools._document_names = lambda: ["P.CATPart", "A.CATProduct", "B.CATProduct"]
    tools._component_usage = lambda: {"P.CATPart": ["A.CATProduct", "B.CATProduct"]}

    class _Doc:
        Name = "P.CATPart"

        def Close(self):
            return None

    tools.conn = type("C", (), {"active_document": _Doc()})()
    reply = tools._close_document()
    assert "'A.CATProduct', 'B.CATProduct' do." in reply
    assert "does." not in reply
