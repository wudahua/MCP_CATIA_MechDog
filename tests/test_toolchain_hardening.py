# -*- coding: utf-8 -*-
"""Regression tests for the hardening pass of 2026-10-22.

These cover the four failures that cost the most time on the P03 propeller:
measurements that were not measurements, a batch of edges that could not be
applied in one call, no way to undo an experiment, and a "method failed" with
no state attached to it.
"""
import json

import pytest

from catia_mcp.server import CATIAMCPServer
from catia_mcp.tools.measurement import _pick
from catia_mcp.tools.part_design import PartDesignTools


# ─────────────────────────────────────────── measurement tells the truth
class _Measurable:
    def __init__(self, length=0.0, area=0.0):
        self.Length = length
        self.Area = area


class _Spa:
    def __init__(self, measurable):
        self._m = measurable

    def GetMeasurable(self, _ref):  # noqa: N802 - CATIA spelling
        return self._m


class _Cell:
    Reference = object()


def test_placeholder_length_is_reported_as_unknown_not_as_a_number():
    """SPA answers 2e7 instead of raising. That must never reach the caller."""
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(_Spa(_Measurable(length=2e7)), _Cell(), "Length") is None
    assert tools._cell_measure(_Spa(_Measurable(length=8e7)), _Cell(), "Length") is None


def test_real_measurements_pass_through_in_millimetres():
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(_Spa(_Measurable(length=30.0)), _Cell(), "Length") == 30.0
    # SPA hands areas back in m2; the tool publishes mm2.
    assert tools._cell_measure(_Spa(_Measurable(area=0.001)), _Cell(), "Area") == pytest.approx(1000.0)


def test_zero_area_is_unknown_because_a_face_cannot_have_no_area():
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(_Spa(_Measurable(area=0.0)), _Cell(), "Area") is None


def test_measurement_without_a_workbench_degrades_instead_of_crashing():
    tools = PartDesignTools(connection=None)
    assert tools._cell_measure(None, _Cell(), "Length") is None


# ─────────────────────────────────────────── batch fillet
def test_fillet_batch_applies_every_edge_and_says_so():
    tools = PartDesignTools(connection=None)
    applied = []

    report = tools._apply_fillets(1.27, ["Edge.4", "Edge.7"], [object(), object()],
                                 add=lambda ref: applied.append(ref))

    assert len(applied) == 2
    assert "2/2" in report


def test_fillet_batch_survives_a_rejected_edge_and_names_it():
    tools = PartDesignTools(connection=None)

    def flaky(ref):
        if ref == "bad":
            raise RuntimeError("(-2147352567, '发生意外。', (0, 'CATIAShapeFactory', '方法失败', ...)")

    report = tools._apply_fillets(3.81, ["Edge.4", "Edge.9"], ["good", "bad"], add=flaky)

    assert "1/2" in report
    assert "Edge.9" in report
    # The message has to point at the real cause, not just record the failure.
    assert "topology" in report


def test_single_fillet_still_takes_the_plain_edge_name():
    tools = PartDesignTools(connection=None)
    seen = []
    tools._fillet_one = lambda radius, name, propagation=1: (
        seen.append((name, propagation)) or "ok")

    tools._fillet({"radius": 2.0, "edge_name": "Edge.1"})
    tools._fillet({"radius": 2.0, "edge_name": "Edge.1", "propagation": "tangency"})

    # 0.3.7 flipped the default to 'none': tangency silently fillets the whole
    # tangent chain, which is how a 10-edge batch reached a non-target edge.
    assert seen == [("Edge.1", 0), ("Edge.1", 1)]


# ─────────────────────────────────────────── measurement argument aliases
def test_element_aliases_resolve_and_the_error_names_them_all():
    assert _pick({"elem1_name": "Edge.3"}, "element1", "elem1_name") == "Edge.3"
    assert _pick({"element1_name": "Edge.4"}, "element1", "element1_name") == "Edge.4"
    with pytest.raises(ValueError) as caught:
        _pick({"nope": 1}, "element1", "elem1_name", "element1_name")
    assert "element1" in str(caught.value)
    assert "elem1_name" in str(caught.value)


# ─────────────────────────────────────────── failure messages carry state
class _BrokenConnection:
    def __getattr__(self, name):
        raise RuntimeError("No active document")

    def ensure_connected(self):
        return None


def test_session_state_never_raises_even_with_nothing_connected():
    server = CATIAMCPServer()
    server.connection = _BrokenConnection()
    text = server._session_state()
    assert "document=" in text
    assert "Session state" not in text            # that prefix belongs to the caller


def test_failure_message_points_at_session_state():
    server = CATIAMCPServer()
    server.connection = _BrokenConnection()
    document_tools = server.document_tools
    document_tools.execute = lambda name, arguments: (_ for _ in ()).throw(
        RuntimeError("(-2147352567, '发生意外。', (0, 'CATIAShapeFactory', '方法失败', ...)")
    )
    result = server._execute_tool("catia_new_part", {})
    message = result.structuredContent["message"]
    assert message.startswith("(-2147352567")
    assert "Session state:" in message
    assert "document=" in message


# ─────────────────────────────────────────── new tool surfaces
def test_save_as_copy_exists_and_explains_the_saveas_limit():
    from catia_mcp.tools.document import DocumentTools

    tool = [d for d in DocumentTools(connection=None).get_tool_definitions()
            if d["name"] == "catia_save_as_copy"][0]
    text = tool["description"]
    assert "geometry" in text
    assert "Save As is refused" in text or "refused after" in text


def test_delete_feature_can_target_the_last_one_without_a_name():
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_delete_feature"][0]
    # feature_name must be optional, otherwise undoing your own last feature
    # needs CATIA's localized naming.
    assert "required" not in tool["inputSchema"]
    assert "index" in tool["inputSchema"]["properties"]


def test_circ_pattern_takes_its_axis_from_the_part_itself():
    """The axis Reference used to be minted in a donor document that was closed
    before the pattern was built, so every AddNewCircPattern failed at update.

    Measured on V5-6R2020 B30, 2026-10-24: with the axis line living in the
    part's own 'PatternAxis' geometrical set the pattern builds, and the volume
    lands on the analytic value (delta 1131.0 mm3 for 5 added Ø6 holes).
    """
    import inspect

    from catia_mcp.tools.part_design import PartDesignTools as PD

    source = inspect.getsource(PD._pattern_axis_reference)
    assert "HybridShapeFactory" in source
    assert "AppendHybridShape" in source
    assert "part.Update()" in source          # not just UpdateObject(line)

    listing = inspect.getsource(PD._circ_pattern)
    assert "_pattern_axis_reference" in listing
    assert "_mint_axis_reference" not in listing
    # The seed must be a feature; patterning the body always failed.
    assert "_pattern_seed" in listing


def test_face_and_edge_listings_expose_the_element_type():
    for func in (PartDesignTools._list_edges, PartDesignTools._list_faces):
        import inspect

        assert '["type"]' in inspect.getsource(func)


# ─────────────────────────────────────────── visibility
def test_set_visibility_is_registered_with_the_measured_enum_note():
    """Hiding is not cosmetic: leftover GSD surfaces show up in STL exports."""
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_set_visibility"][0]
    assert "show" in tool["inputSchema"]["properties"]
    text = tool["description"]
    assert "STL" in text
    assert "envelope" in text

    import inspect

    source = inspect.getsource(PD._set_visibility)
    # 0 shows, 1 hides - 2 is accepted and does nothing, which is why this was
    # silently broken for a while.
    assert "SetShow(0 if show else 1)" in source
    assert "HybridShapes" in source          # descend into the set


# ─────────────────────────────────────────── multi-body round trip
def test_set_active_body_exists_because_new_body_only_moves_forward():
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_set_active_body"][0]
    assert tool["inputSchema"]["required"] == ["name"]
    text = tool["description"]
    assert "9" in text            # names the drone body as the motivating case
    assert "catia_list_bodies" in text


def test_symmetric_pad_halves_the_dimension_because_catia_doubles_it():
    """CATIA's IsSymmetric is a mirrored extent, not a total.

    Measured: r=10 pad, height=10 -> y -10..+10 (6283.19 mm3). The tool's
    contract says total = height, so it must hand CATIA height/2.
    """
    import inspect

    source = inspect.getsource(PartDesignTools._pad)
    assert "height / 2.0 if symmetric else height" in source
    assert "IsSymmetric = True" in source

    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_pad"][0]
    text = tool["inputSchema"]["properties"]["symmetric"]["description"]
    assert "TOTAL" in text
    assert "mirrored extent" in text


def test_multi_edge_fillet_resolves_all_references_before_cutting():
    """Each fillet renumbers the body, so re-resolving Edge.N mid-batch lands
    on the tangent edge the previous fillet just made - which CATIA reports as
    'some selected edges are not sharp' instead of failing."""
    import inspect

    source = inspect.getsource(PartDesignTools._fillet_many)
    assert "_resolve_edge_specs" in source
    # the resolution has to happen before any AddNew call
    assert source.index("_resolve_edge_specs") < source.index("AddNewSolidEdgeFillet")


def test_list_faces_honours_the_body_name_it_was_given():
    import inspect

    source = inspect.getsource(PartDesignTools._list_faces)
    assert 'part.Bodies.Item(body_name)' in source
    assert '_topology_cells("Face", body)' in source


def test_symmetric_pocket_is_two_one_sided_cuts_because_IsSymmetric_is_ignored():
    """Pad and Pocket behave differently, and only measurement shows it.

    Pad + IsSymmetric is a mirrored extent (each side gets the full dimension).
    Pocket + IsSymmetric is accepted and then ignored: a "mid-plane cut 38.1"
    passed as 19.05 removed 5 609.6 mm3 on the drone body - exactly the
    single-sided 19.05 figure - instead of the 11 150.9 a 38.1 cut gives. So the
    tool builds the second side itself.
    """
    import inspect

    source = inspect.getsource(PartDesignTools._pocket)
    assert "pocket.IsSymmetric" not in source          # measured no-op, never used
    assert "depth / 2.0 if symmetric else depth" in source
    assert 'explicit_direction = "direction" in args or symmetric' in source
    assert "mirror = sf.AddNewPocket(sketch, 0 if through_all else half)" in source

    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_pocket"][0]
    props = tool["inputSchema"]["properties"]
    assert "symmetric" in props
    assert "both" in props["direction"]["enum"]
    assert PD._pocket_extent(False, 38.1, True) == "38.100 mm total, 19.050 each side"
    assert PD._pocket_extent(False, 12.7, False) == "12.7 mm deep"   # unchanged for a one-sided cut
    assert PD._pocket_extent(True, 0, False) == "through all"
    # through-all reaches one way only, so "both" needs a second up-to-last cut
    # (measured 2026-10-23: exactly half the expected volume).
    assert PD._pocket_extent(True, 0, True) == "through all, both directions"
    assert "mirror.FirstLimit.LimitMode = 2" in source


def test_construction_geometry_does_not_occupy_edge_indices():
    """A topology search returns datum planes and sketch wires as well as the
    solid's own cells, and they come first.

    Measured on the drone body (16 real faces): the search returned 30 "faces",
    of which 14 were the three datum planes and sketch wires, so Edge.1 ... Edge.8
    were plane borders with no length. Every length-based edge pick was reading
    construction geometry.

    The strings below are verbatim from that body.
    """
    from catia_mcp.topo_search import is_construction, nls_features

    real_edge = (
        "Selection_REdge:(Edge:(Face:(Brp:(Pad.6;0:(Brp:(Sketch.24;3)));None:();"
        "Cf12:());Face:(Brp:(Pad.6;2);None:();Cf12:());None:(Limits1:();"
        "Limits2:());Cf12:());Pad.6_ResultOUT;Z0;G8782)"
    )
    plane_border = (
        "Selection_BorderREdge:(BEdge:(Brp:(GSMPlane.17;4);None:(Limits1:();"
        "Limits2:();+1);Cf12:());GSMPlane.17;Z0;G8782)"
    )
    sketch_wire = (
        "Selection_WireREdge:(Wire:(Brp:(Sketch.24;4);None:(Limits1:();"
        "Limits2:());Cf12:());Sketch.24;Z0;G8782)"
    )
    plane_face = "Selection_RSur:(Face:(Brp:(GSMPlane.2);None:();Cf12:());GSMPlane.2;Z0;G8782)"

    assert not is_construction(real_edge)
    assert is_construction(plane_border)
    assert is_construction(sketch_wire)
    assert is_construction(plane_face)

    # The feature chain is what tells two equal-length edges apart.
    assert nls_features(real_edge) == ["Pad.6", "Sketch.24"]   # deduplicated, in order
    assert "Sketch.23" in nls_features(
        "Selection_REdge:(Edge:(Face:(Brp:((Brp:(Pad.6;0:(Brp:(Sketch.24;3)));"
        "Brp:(Pad.5;0:(Brp:(Sketch.23;3)))));None:();Cf12:())"
    )


def test_edge_listing_reports_the_feature_that_made_each_edge():
    import inspect

    from catia_mcp.tools.part_design import PartDesignTools as PD

    for func in (PD._edge_table, PD._list_faces):
        source = inspect.getsource(func)
        assert "nls_of(cell)" in source
    for func in (PD._list_edges, PD._list_faces):
        assert '"created_by"' in inspect.getsource(func)


def test_multi_edge_fillet_builds_the_whole_batch_then_rebuilds_once():
    """Updating each fillet as it is created invalidates the queue.

    Measured on the drone body: 12 edges at R5.08 reported "1/12 done" and then
    every remaining AddNew call failed with a bare "method failed", because the
    first update had rebuilt the topology the other 11 references pointed into.
    Create all, rebuild once - which is also what the UI does.
    """
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tools = PD(connection=None)
    seen = {"update": 0}
    features = []

    class FakePart:
        def Update(self):
            seen["update"] += 1

    def add(ref):
        feature = object()
        features.append(feature)
        return feature

    report = tools._apply_fillets(5.08, ["Edge.1", "Edge.2", "Edge.3"],
                                  [1, 2, 3], add=add, part=FakePart())

    assert "3/3" in report
    assert seen["update"] == 1          # one rebuild for the batch, not three
    assert len(features) == 3


def test_a_failed_batch_rebuild_rolls_every_feature_back():
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tools = PD(connection=None)
    removed = []
    tools._remove_created = lambda feats: removed.extend(feats)

    class FakePart:
        def Update(self):
            raise RuntimeError("(0, 'CATIPrtPart', '更新失败', None, 0, ...)")

    report = tools._apply_fillets(5.08, ["Edge.1", "Edge.2"], [1, 2],
                                  add=lambda ref: object(), part=FakePart())

    assert "0/2" in report
    assert "one-at-a-time retry" in report   # the batch is only an optimisation
    # 2 batch features rolled back + the 2 the retry built and rejected
    assert len(removed) == 4            # nothing left half-built in the tree


def test_a_failed_batch_rebuild_is_retried_one_edge_at_a_time():
    """Three edges of one face failed as a batch and worked one by one.

    Reported 2026-10-23 with the numbers: catia_fillet(radius=2.54,
    edge_names=['Edge.5','Edge.10','Edge.13'], propagation='none') answered
    "0/3 edge(s) done ... would not rebuild", while the same three edges
    filleted one at a time all took. Separate updates are what CATIA wants for
    adjacent edges, so a failed batch now degrades to exactly that.
    """
    from catia_mcp.tools.part_design import PartDesignTools as PD

    tools = PD(connection=None)
    removed = []
    tools._remove_created = lambda feats: removed.extend(feats)
    updates = {"batch": 0, "single": 0}

    class FakePart:
        def Update(self):
            updates["batch"] += 1
            raise RuntimeError("(0, 'CATIPrtPart', '更新失败', None, 0, ...)")

        def UpdateObject(self, feature):
            updates["single"] += 1

    # resolve() hands back fresh references, as the real one does after the
    # topology was rebuilt by the rollback.
    report = tools._apply_fillets(
        2.54, ["Edge.5", "Edge.10", "Edge.13"], [1, 2, 3],
        add=lambda ref: object(), part=FakePart(),
        resolve=lambda: (["Edge.5", "Edge.10", "Edge.13"], [4, 5, 6]))

    assert "3/3" in report
    assert "one-at-a-time retry" in report
    assert updates == {"batch": 1, "single": 3}
    assert len(removed) == 3            # the batch really was rolled back first


def test_the_package_version_matches_pyproject():
    """__version__ sat at 0.2.2 through two releases while pyproject said 0.3.1.

    Anything that asks the package its version - a client, a bug report, a
    release check - got an answer two versions wrong, and nothing failed.
    """
    import re
    from pathlib import Path

    import catia_mcp

    root = Path(catia_mcp.__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)

    assert catia_mcp.__version__ == declared


def test_visibility_can_hide_a_whole_class_of_bookkeeping():
    """Hiding sketches one name at a time is not usable: a part has a dozen, and
    the HSO cannot resolve them by name. The kind= form walks the tree instead."""
    import inspect

    from catia_mcp.tools.part_design import PartDesignTools as PD

    tool = [d for d in PD(connection=None).get_tool_definitions()
            if d["name"] == "catia_set_visibility"][0]
    props = tool["inputSchema"]["properties"]
    assert props["kind"]["enum"] == ["sketch", "datum_plane", "construction"]
    assert "required" not in tool["inputSchema"]      # name is optional now

    source = inspect.getsource(PD._set_visibility_kind)
    assert "Sketches" in source and "HybridSketches" in source
    assert "PlaneXY" in source
    assert "SetShow(0 if show else 1)" in source      # the measured enum
