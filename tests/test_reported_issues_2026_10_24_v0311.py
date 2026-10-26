# -*- coding: utf-8 -*-
"""Three reports against v0.3.10, 2026-10-24.

Every case below was reproduced on V5-6R2020 B30 before it was fixed; the numbers
in the docstrings are the B30 measurements. Two of the reporters were on B32/SP6,
so where a behaviour could be build-dependent the test only asserts the claim the
code now makes (and the honest wording when it cannot verify it).
"""

from __future__ import annotations

import inspect
import json

import pytest


# --------------------------------------------------------------- inertia tensor

def test_inertia_rounds_by_significant_digits_not_decimals():
    """round(x, 4) made every real part's tensor zero.

    A 72 cm3 steel-plate-shaped part has Ixx 2.1e-5 and Izz 5.6e-5 kg.m2; at four
    decimals those are 0.0 and 0.0001 (reported 2026-10-24).
    """
    from catia_mcp.tools.measurement import _sig

    assert _sig(2.1e-5) == 2.1e-05
    assert _sig(5.6e-5) == 5.6e-05
    assert _sig(1.23456789e-7) == 1.23457e-07
    # large values keep their size instead of gaining noise decimals
    assert _sig(12345.6789) == 12345.7
    assert _sig(0.0) == 0.0


def test_the_inertia_payload_uses_the_significant_digit_helper():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    # The fields are built in one place so both routes (active document, and the
    # temporary activation used by document=) format the matrix the same way.
    source = inspect.getsource(MT._matrix_fields)
    assert "_sig(inertia[" in source
    assert "round(inertia[" not in source
    # the axes and unit have to be stated, or the matrix cannot be compared
    assert "inertia_matrix_about" in source
    assert "kg*m^2" in source
    assert "_matrix_fields" in inspect.getsource(MT._get_inertia)


def test_inertia_products_below_the_noise_floor_are_zero():
    """-4.28e-50 was reported as an "inertia product" a caller had to judge."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    fields = MT._matrix_fields([0.01, 1.0e-4, -4.28e-50, 0.0,
                                0.0, 2.0e-4, -4.8e-35,
                                0.0, 0.0, 3.0e-4])
    matrix = fields["inertia_matrix_kg_m2"]
    assert matrix[0][1] == 0.0 and matrix[1][2] == 0.0
    assert matrix[0][0] == 1e-4


# ------------------------------------------------------- activate_document checks

def test_activate_document_reads_the_state_back():
    """Activate() returning is not the document being active.

    On B32 a part that exists only as a component of an open product accepted the
    call and left ActiveDocument on the product, so the next get_inertia returned
    the ASSEMBLY volume (16000 for an 8000 mm3 part) with no error (2026-10-24).
    """
    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._activate_document)
    assert "ActiveDocument" in source
    assert "did not switch" in source
    assert "get_inertia(document=" in source


# ---------------------------------------------- document= resolves a component

def test_document_resolution_falls_back_to_the_component_part():
    """A part that lives only inside an open product is not in Documents.

    Measured on B30: opening a product that references two parts leaves the
    Documents collection holding just the product, so document='<part>.CATPart'
    answered "No open document matches" in exactly the case the argument exists
    for (reported 2026-10-24).
    """
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._resolve_open_document)
    assert "_open_component_part" in source

    opener = inspect.getsource(MT._open_component_part)
    # match on every handle a caller may hold
    assert "child.Name" in opener and "PartNumber" in opener
    assert "documents.Open" in opener


def test_a_part_opened_for_a_measurement_is_reported():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    box = inspect.getsource(MT._get_bounding_box)
    assert "opened_for_measurement" in box
    assert "it stays open" in box
    inertia = inspect.getsource(MT._get_inertia)
    assert "opened_for_measurement" in inertia


# ------------------------------------------------------------- mesh export path

def test_mesh_export_checks_the_file_and_falls_back_to_a_copy():
    """ExportData writes nothing while the part is edited in context.

    Then the empty target surfaced as '[WinError 2] ...part.stl' - an error that
    names no cause (reported twice on 2026-10-24, both from an assembly).
    """
    from catia_mcp.tools.measurement import MeasurementTools as MT

    # The export lives in _mesh_export now: _mesh_corners is the thin wrapper
    # that reduces the cloud to two corners (0.3.24).
    mesh = inspect.getsource(MT._mesh_export)
    assert "getsize" in mesh
    assert "_mesh_from_copy" in mesh
    assert "editing in context" in mesh

    copy = inspect.getsource(MT._mesh_from_copy)
    assert "FullName" in copy and "copyfile" in copy
    assert "unsaved edits are not included" in mesh


def test_the_mesh_note_admits_reading_the_saved_file():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._part_bbox) + inspect.getsource(MT._get_bounding_box)
    assert "temporary copy" in source or "copy_note" in source


# ------------------------------------------------------------------ assembly box

def test_assembly_box_returns_a_partial_result_instead_of_raising():
    """An assembly of only curved parts failed the whole call.

    "Which component could not be measured, and why" is the useful answer
    (reported 2026-10-24).
    """
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._assembly_box)
    assert '"dimensions": None' in source
    assert "skipped" in source
    assert "Could not measure any component" not in source


def test_assembly_box_says_curved_not_revolved():
    """A camera bracket's arch was under-reported 19.6 %, and it is not revolved."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._assembly_box)
    assert "curved face" in source
    assert "not limited to" in source
    assert "revolved parts" in source
    assert "catia_open_document" in source


# --------------------------------------------------------------- draft neutral

def test_draft_tries_more_than_the_largest_face():
    """The largest face is usually the base, whose normal is parallel to the pull.

    Picking by area alone failed every time on the reported plate (2026-10-24);
    the retry found a side wall on the second candidate on B30 (Face.1 rejected,
    Face.4 accepted).
    """
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._draft)
    assert "candidates" in source
    assert "for neutral_name, neutral_note in candidates" in source
    assert "every neutral face tried" in source


# -------------------------------------------------------------- edge count rule

def test_topology_count_uses_the_same_rule_as_the_listings():
    """109 edges from one tool and 101 from the other, three releases running.

    Reproduced on B30: a block with a boss reported 18 edges in
    catia_measure_topology and 14 in catia_list_edges; the four extra were
    datum-plane borders ('Selection_BorderREdge' on 'Brp:(GSMPlane.N;k)').
    """
    from catia_mcp.tools.planner import PlannerTools

    source = inspect.getsource(PlannerTools._count_topology)
    # Since 2026-10-26 the counter calls the very same enumeration the listings
    # use, instead of re-implementing the filter beside it.
    assert "topology_cells(self.conn, body, kind" in source
    assert "datum_plane_borders" in source
    assert "same set" in source


def test_list_edges_reports_what_it_excluded():
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._list_edges)
    assert '"excluded"' in source


# ------------------------------------------------------------------ diagnostics

def test_diagnose_does_not_promise_UNSUPPORTED_CAPABILITY_for_unused_methods():
    """The note said two working tools would fail, for three releases."""
    from catia_mcp.tools import diagnostics

    source = inspect.getsource(diagnostics.DiagnosticsTools._notes)
    assert "does NOT disable any tool" in source
    assert "GetInertia / GetBoundingBox" in source or "GetBoundingBox" in source


# ---------------------------------------------------------------- gsd loft note

def test_the_loft_warning_is_session_sensitive_not_build_determined():
    """One B32 session failed 7/7; two others built it and matched B30 to 0.02 mm3."""
    from catia_mcp.tools.gsd import GSDTools

    definition = next(
        d for d in GSDTools(connection=None).get_tool_definitions()
        if d["name"] == "catia_gsd_multi_section_surface")
    text = definition["description"]
    assert "SESSION-sensitive" in text
    assert "fails on V5-6R2022 (B32)" not in text


# ----------------------------------------------------------------- new product

def test_new_product_tries_the_part_number():
    """Product.Name is read-only on the root; PartNumber is not.

    The parameter was a no-op for three releases and the reply admitted it
    (2026-10-24). B30: PartNumber set to 'ASMV' and reported back.
    """
    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._new_product)
    assert '"PartNumber", "Name"' in source
    assert "catia_set_part_number" in source


# ------------------------------------------------------------ sketch delete delta

def test_sketch_delete_element_reports_the_volume_delta():
    """10681.4 -> 10053.1 was left for the caller to find for themselves."""
    from catia_mcp.tools.sketcher import SketcherTools

    source = inspect.getsource(SketcherTools._delete_element)
    assert "delta" in source
    assert "_solid_volume" in source


# ------------------------------------------------------------------ empty part

def test_an_empty_part_is_not_called_surface_only():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    reason = inspect.getsource(MT._no_solid_reason)
    assert "is empty" in reason
    assert "catia_gsd_close_surface" in reason


# ---------------------------------------------------------- bounding box wording

def test_auto_is_documented_as_mesh():
    """The schema said 'auto = vertices if the shape has any', the opposite of
    what it does - and reading it led straight to the under-reporting route."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    definition = next(
        d for d in MT(connection=None).get_tool_definitions()
        if d["name"] == "catia_get_bounding_box")
    text = json.dumps(definition)
    assert "ALWAYS takes the STL mesh" in text
    assert "cannot be applied to a non-active document" not in text
    assert "'auto' uses vertices when they exist" not in text
