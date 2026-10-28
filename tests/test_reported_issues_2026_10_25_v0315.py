# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-25 batch (reports against v0.3.14).

Every test here comes from a report that was reproduced on B30 before the code
was touched. The three that matter most:

* a vertex search returns the *sketch wire's* corners as well as the solid's,
  and ``is_construction`` had no vertex prefix at all - so every wire corner
  passed the filter and a 40 x 20 x 10 pad measured 60 x 80 x 10 in an assembly;
* ``_require_active_product`` asked ``get_active_product()`` first, which
  succeeds on a Part document because every PartDocument carries a stub
  ``Product`` - so the "activate the only open assembly for you" branch was
  dead code and the caller got a Part Design diagnostic;
* a failure envelope said ``rollback_supported: false`` / "recovery is manual"
  even when the tool had just removed the feature CATIA rejected.
"""

from __future__ import annotations

import inspect
import json

import pytest


# --------------------------------------------------------------------------
# 1. Construction geometry: the word CATIA puts in front of the type name.
# --------------------------------------------------------------------------

def test_sketch_wire_vertex_is_construction():
    from catia_mcp.topo_search import is_construction

    assert is_construction(
        "Selection_WireFVertex:(Vertex:(Neighbours:(Face:(Brp:(Sketch.2;1))))")
    assert is_construction("Selection_BorderRVertex:(Vertex:(Neighbours:(Face:")


def test_solid_vertex_is_not_construction():
    from catia_mcp.topo_search import is_construction

    # The head is the only place a marker may be read: the rest of the path
    # names sketches and pads everywhere.
    assert not is_construction(
        "Selection_FVertex:(Vertex:(Neighbours:(Face:(Brp:(Pad.1;2);"
        "WireFrame:(Sketch.1;1))))"
    )
    assert not is_construction("Selection_REdge:(Edge:(Face:(Brp:(Pad.1;2))))")
    assert not is_construction("Selection_RSur:(Face:(Brp:(Pad.1;2)))")


def test_the_prefixes_that_were_already_there_still_work():
    from catia_mcp.topo_search import is_construction

    assert is_construction("Selection_WireREdge:(Edge:")
    assert is_construction("Selection_BorderREdge:(Edge:")
    assert is_construction("Selection_BorderRSur:(Face:")
    # Datum-plane geometry named without a Border prefix.
    assert is_construction("Selection_RSur:(Face:(Brp:(GSMPlane.1;0)))")


def test_empty_nls_is_not_construction():
    from catia_mcp.topo_search import is_construction

    assert not is_construction("")


def test_unknown_variants_are_caught_by_the_marker():
    """Nobody has met Selection_WireRVertex yet; the rule still has to hold."""
    from catia_mcp.topo_search import is_construction

    assert is_construction("Selection_WireRVertex:(Vertex:")
    assert is_construction("Selection_BorderRFVertex:(Vertex:")


# --------------------------------------------------------------------------
# 2. Which document is active - decided by type, not by a property that always
#    resolves.
# --------------------------------------------------------------------------

class _Doc:
    def __init__(self, name: str, kind: str) -> None:
        self.Name = name
        self.Type = kind
        # Every PartDocument answers .Product with a stub. That is the trap.
        self.Product = object()


def test_a_part_document_is_not_a_product_document():
    from catia_mcp.tools.assembly import AssemblyTools

    assert not AssemblyTools._is_product_document(_Doc("P.CATPart", "CATPart"))
    assert AssemblyTools._is_product_document(
        _Doc("A.CATProduct", "CATProduct"))


def test_type_is_preferred_over_the_extension():
    from catia_mcp.tools.assembly import AssemblyTools

    class _Unsaved:
        Name = "Product1"

        @property
        def Type(self):
            return "CATProduct"

        @property
        def FullName(self):
            raise RuntimeError("never saved")

    assert AssemblyTools._is_product_document(_Unsaved())


def test_untyped_unsaved_document_is_not_treated_as_an_assembly():
    from catia_mcp.tools.assembly import AssemblyTools

    class _Opaque:
        Name = "?"

        def __getattr__(self, item):
            raise RuntimeError("no such property")

    assert not AssemblyTools._is_product_document(_Opaque())


def test_require_active_product_checks_the_type_before_trusting_the_handle():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._require_active_product)
    assert "_is_product_document" in source
    # get_active_product() succeeds on a Part document, so it must not decide.
    assert "self.conn.get_active_product()" not in source


def test_add_new_part_uses_the_checked_product_handle():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._add_new_part)
    assert "_require_active_product" in source
    assert "self.conn.get_active_product()" not in source


def test_automatic_activation_is_reported():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._require_active_product)
    assert "_activated_for_insert" in source
    add = inspect.getsource(AssemblyTools._add_component)
    assert "activated" in add


# --------------------------------------------------------------------------
# 3. The failure envelope has to agree with the message.
# --------------------------------------------------------------------------

def test_a_rolled_back_failure_says_nothing_is_left_to_repair():
    from catia_mcp.results import operation_result

    result = operation_result(
        tool="catia_fillet", ok=False, code="COM_ERROR",
        message="CATIA rejected the feature ... (rolled_back: true)",
        data={"failure_stage": "execute"}, modified=None, rolled_back=True,
    )
    assert result["effects"]["modified"] is False
    assert result["recovery"]["rolled_back"] is True
    assert "Nothing to repair" in result["recovery"]["next_actions"][0]
    assert "recovery is manual" not in " ".join(result["recovery"]["next_actions"])


def test_a_failed_rollback_points_at_the_dead_feature():
    from catia_mcp.results import operation_result

    result = operation_result(
        tool="catia_fillet", ok=False, code="COM_ERROR", message="...",
        data={}, modified=None, rolled_back=False,
    )
    assert result["recovery"]["rolled_back"] is False
    assert "catia_list_features" in result["recovery"]["next_actions"][0]


def test_an_ordinary_failure_keeps_the_old_advice():
    from catia_mcp.results import operation_result

    result = operation_result(
        tool="catia_pad", ok=False, code="COM_ERROR", message="...",
        data={}, modified=None,
    )
    assert result["recovery"]["rolled_back"] is None
    assert "not verified" in result["recovery"]["next_actions"][0]


def test_rolled_back_is_always_present_even_when_unknown():
    """A client can branch on the field without checking for a missing key."""
    from catia_mcp.results import operation_result

    for value in (None, True, False):
        result = operation_result(tool="t", ok=False, code="c", message="m",
                                  data={}, modified=None, rolled_back=value)
        assert result["recovery"]["rolled_back"] is value


def test_feature_rejected_carries_the_flag_and_stays_a_runtime_error():
    from catia_mcp.results import FeatureRejected

    error = FeatureRejected("CATIA rejected ... (rolled_back: true)", rolled_back=True)
    assert isinstance(error, RuntimeError)
    assert error.rolled_back is True
    assert "rolled_back: true" in str(error)


def test_the_server_forwards_the_rollback_state():
    from catia_mcp import server

    source = inspect.getsource(server)
    assert 'getattr(error, "rolled_back", None)' in source


def test_safe_update_raises_the_typed_error():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._safe_update)
    assert "FeatureRejected" in source
    assert "rolled_back=True" in source
    assert "rolled_back=False" in source


# --------------------------------------------------------------------------
# 4. A pattern that changed nothing is not a success.
# --------------------------------------------------------------------------

def test_rect_pattern_warns_when_the_model_did_not_change():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert "added no material" in source
    assert "reverse_dir1" in source


def test_rect_pattern_reports_the_signed_direction():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._rect_pattern)
    # 'axis x+ -> dir1' instead of 'axis x (reversed) -> dir1'
    assert "reverse1 else '+'" in source.replace("-", "-")  or "+' -> dir1" in source


# --------------------------------------------------------------------------
# 5. Geometrical sets are part of the tree, so the listing has to show them.
# --------------------------------------------------------------------------

def test_list_features_reports_geometrical_sets():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._list_features)
    assert "geometrical_sets" in source
    assert "_pattern_geoset_inventory" in source


class _Collection:
    def __init__(self, items):
        self._items = list(items)
        self.Count = len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key - 1]
        raise KeyError(key)


class _Item:
    def __init__(self, name, count=0):
        self.Name = name
        self.HybridShapes = _Collection([_Item(f"e{i}") for i in range(count)])


def test_pattern_geoset_inventory_counts_the_elements():
    from catia_mcp.tools.part_design import PartDesignTools

    part = type("P", (), {"HybridBodies": _Collection(
        [_Item("PatternAxis", 3), _Item("Construction", 0)])})()
    inventory = PartDesignTools._pattern_geoset_inventory(None, part)
    assert inventory == [{"name": "PatternAxis", "elements": 3},
                         {"name": "Construction", "elements": 0}]


def test_pattern_geoset_inventory_survives_a_part_without_geosets():
    from catia_mcp.tools.part_design import PartDesignTools

    class _Broken:
        @property
        def HybridBodies(self):
            raise RuntimeError("no hybrid bodies on a fresh part")

    assert PartDesignTools._pattern_geoset_inventory(None, _Broken()) == []


# --------------------------------------------------------------------------
# 6. Three bounding faces means two of them are coplanar.
# --------------------------------------------------------------------------

def test_bounding_faces_counts_the_fingerprint():
    from catia_mcp.tools.part_design import PartDesignTools

    assert PartDesignTools._bounding_faces(
        "Edge.7 (L43.1800|Sketch.9;2|Sketch.9;1)") == 2
    assert PartDesignTools._bounding_faces(
        "Edge.9 (L43.1800|Sketch.9;2|Sketch.10;1|Sketch.9;1)") == 3


def test_coplanar_hint_explains_a_three_face_refusal():
    from catia_mcp.tools.part_design import PartDesignTools

    hint = PartDesignTools._coplanar_hint(
        ["Edge.9 (L43.1800|Sketch.9;2|Sketch.10;1|Sketch.9;1)"])
    assert "coplanar" in hint
    assert "no material to remove" in hint


def test_coplanar_hint_is_silent_for_ordinary_edges():
    from catia_mcp.tools.part_design import PartDesignTools

    assert PartDesignTools._coplanar_hint(["Edge.7 (L43.18|Sketch.9;2|Sketch.9;1)"]) == ""


def test_the_batch_refusal_path_calls_the_hint():
    from catia_mcp.tools.part_design import PartDesignTools

    assert "_coplanar_hint" in inspect.getsource(PartDesignTools._apply_fillets)
    assert "_coplanar_hint" in inspect.getsource(PartDesignTools._fillet_one)


# --------------------------------------------------------------------------
# 7. Features are appended; say where, and that it cannot be moved.
# --------------------------------------------------------------------------

def test_fillet_reports_the_tree_position_and_the_limitation():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._tree_position)
    assert "no reorder" in source
    assert "Insert / Reorder / Move" in source
    reply = inspect.getsource(PartDesignTools._fillet_one)
    assert "_tree_position" in reply


def test_tree_position_names_index_and_total():
    from catia_mcp.tools.part_design import PartDesignTools

    shapes = _Collection([_Item("Pad.1"), _Item("倒圆角.1")])
    body = type("B", (), {"Name": "PartBody", "Shapes": shapes})()
    feature = shapes.Item(2)
    note = PartDesignTools._tree_position(None, type("P", (), {})(), body, feature)
    assert "feature 2 of 2" in note
    assert "no reorder" in note


# --------------------------------------------------------------------------
# 8. Loft sections have to be one continuous curve.
# --------------------------------------------------------------------------

def test_loft_description_names_the_one_curve_rule():
    """Corrected 2026-10-25: a closed polyline is NOT one curve, and neither is
    an Extract of it. The first version of this text claimed a sketch rectangle
    would work; measured, it fails like four separate lines do."""
    from catia_mcp.tools.gsd import GSDTools

    doc = [d for d in GSDTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_gsd_multi_section_surface"][0]
    text = doc["description"]
    assert "ONE CURVE PER SECTION" in text
    assert "sketch rectangle" in text
    assert "SKETCH CIRCLE" in text
    # the two claims that were wrong must not come back
    assert "IS one wire and works" not in text
    assert "Extracting the profile does not help" in text


def test_loft_failure_checks_the_curve_rule_first():
    from catia_mcp.tools.gsd import GSDTools

    source = inspect.getsource(GSDTools._multi_section_surface)
    assert "not ONE curve" in source
    assert "closed catia_gsd_spline" in source


# --------------------------------------------------------------------------
# 9. The counting rule is part of the answer.
# --------------------------------------------------------------------------

def test_measure_topology_publishes_the_counting_rule():
    from catia_mcp.tools.planner import PlannerTools

    source = inspect.getsource(PlannerTools._count_topology)
    assert "counting_rule" in source
    assert "re-baselined" in source


# --------------------------------------------------------------------------
# 10. An unsigned direction is what made a zero-effect pattern unreadable.
# --------------------------------------------------------------------------

def test_save_as_description_is_honest_about_both_builds():
    from catia_mcp.tools.document import DocumentTools

    doc = [d for d in DocumentTools(connection=None).get_tool_definitions()
           if d["name"] == "catia_save_document"][0]
    text = doc["description"]
    assert "B30" in text and "B32" in text
    assert "right after catia_new_part" in text


# `rollback_supported` was here until 0.3.35: it was a constant false in every
# reply (no tool takes a rollback argument) sitting next to `rolled_back: true`,
# so it read as "this failure could not be rolled back" - the opposite of what
# had just happened. Both 2026-10-28 reviewers flagged it; the key is now
# `can_request_rollback`, which says what the constant actually meant.
@pytest.mark.parametrize("field", ["rolled_back", "can_request_rollback",
                                   "automatic_retry_allowed", "next_actions"])
def test_recovery_block_shape_is_stable(field):
    from catia_mcp.results import operation_result

    result = operation_result(tool="t", ok=True, code="OK", message="m",
                              data={}, modified=False)
    assert field in result["recovery"]
    assert json.dumps(result["recovery"])  # serialisable for MCP


def test_a_constant_that_read_as_the_opposite_is_gone():
    from catia_mcp.results import operation_result

    result = operation_result(tool="catia_pad", ok=False, code="NO_MATERIAL",
                             message="rolled back", data={}, modified=None,
                             rolled_back=True)
    block = result["recovery"]
    assert "rollback_supported" not in block
    assert block["can_request_rollback"] is False
    assert block["rolled_back"] is True
    # The sentence the constant was standing in for.
    assert "no tool in this server takes a rollback argument" in block["rollback_note"]
