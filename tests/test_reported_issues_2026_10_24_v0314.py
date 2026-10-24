# -*- coding: utf-8 -*-
"""Five reports against v0.3.11, 2026-10-24.

Every claim below was checked on V5-6R2020 B30 before anything changed. Two of
them did not reproduce there and are recorded as such rather than "fixed":

* ``circ_pattern(count=2, angular_spacing=90)`` builds normally on B30 with both a
  Pad and a Shaft seed (2 instances, 1 added, volume doubled);
* ``mirror`` about YZ on a 4x90 deg pattern builds normally too.

The reporter was on B32, so both stay open as one-session observations - what is
tested here is that this release does not change behaviour it could not verify.
"""

from __future__ import annotations

import inspect

import pytest


# --------------------------------------------------------- a dead feature kills a part

def test_every_single_feature_path_rolls_back():
    """One rejected fillet made the WHOLE part un-updatable.

    Reproduced on B30: fillet R40 on a 40x20x10 block -> shape_count 1 -> 2,
    ``update_part`` fails, and the next pad fails too, until the dead
    '倒圆角.1' is deleted. The batch paths rolled back; the single ones called
    ``part.UpdateObject`` raw.
    """
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._safe_update)
    assert "rolled_back: true" in source
    assert "could NOT be removed" in source
    assert "catia_delete_feature" in source

    for method in ("_fillet_one", "_chamfer_one", "_mirror", "_shell", "_thickness"):
        body = inspect.getsource(getattr(PartDesignTools, method))
        rolled_back = ("_safe_update" in body
                       or ("_try_update" in body and "_remove_feature" in body))
        assert rolled_back, method


def test_update_part_names_the_feature_that_blocks_it():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._update_part)
    assert "_first_failing_feature" in source
    assert "catia_delete_feature" in source
    assert "no single feature reproduces it" in source


def test_list_features_publishes_the_count():
    """The failure state line showed shape_count 23 for a 22-feature part; the
    successful listing gave no count to compare against."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._list_features)
    assert '"count"' in source and '"body"' in source


# ------------------------------------------------------------ metric semantics

def test_list_bodies_uses_the_same_face_definition():
    """191 vs 186 on the same body: list_bodies counted raw search results."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._list_bodies)
    assert "topology_cells" in source
    assert "faces_excluded" in source


# ------------------------------------------------------------------ assembly

def test_add_component_needs_an_active_assembly_and_says_so():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._require_active_product)
    assert "needs the ASSEMBLY to be the active document" in source
    assert "catia_activate_document" in source
    # exactly one product open -> activate it instead of failing
    assert "len(open_products) == 1" in source


def test_insert_blocked_by_an_activated_part_explains_the_recovery():
    """Measured: the same insert works while the part is open but never
    activated, and fails right after it was activated."""
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._insert_blocked_hint)
    # the sentence is split across literals in the source
    assert "refuses to " in source
    assert "insert a file whose document has been activated" in source
    assert "catia_close_document" in source


def test_list_documents_says_which_assembly_uses_a_part():
    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._list_documents)
    assert "component_of" in source
    assert "_component_usage" in source


def test_mesh_never_exports_a_part_in_context():
    """In context the export raises a modal dialog that nobody can answer, and on
    one B32 session it still produced a valid STL - so the size check could not
    tell, and every measurement left a dialog behind."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._mesh_corners)
    assert "_edited_in_context" in source
    assert "if not in_context:" in source
    detector = inspect.getsource(MT._edited_in_context)
    assert "ActiveDocument" in detector and "Activate()" in detector


def test_a_box_measured_from_the_copy_carries_a_warning():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._part_bbox)
    assert '"warnings"' in source
    assert "edits made since the last save are NOT in " in source


def test_assembly_inertia_returns_a_partial_result_like_the_box():
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._assembly_inertia)
    assert '"volume_mm3": None' in source
    assert "Could not measure any component" not in source


def test_get_inertia_by_document_also_gives_the_tensor():
    """CATIA only computes the tensor for the active document, so getting nine
    parts' took 18 calls. It borrows the activation and restores it."""
    from catia_mcp.tools.measurement import MeasurementTools as MT

    source = inspect.getsource(MT._part_inertia)
    assert "_tensor_for" in source
    borrow = inspect.getsource(MT._tensor_for)
    assert "doc.Activate()" in borrow
    assert "previous" in borrow


# ------------------------------------------------------------------- patterns

def test_rect_pattern_seed_is_a_feature_not_the_body():
    """Patterning the body fails at UpdateObject - the trap circ_pattern hit."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert "self._pattern_seed(body" in source
    # the old call returned the body; it must not come back
    assert "= self._get_last_shape(args.get" not in source


def test_rect_pattern_takes_axes_and_a_reversal():
    from catia_mcp.tools.part_design import PartDesignTools

    definition = next(d for d in PartDesignTools(connection=None).get_tool_definitions()
                      if d["name"] == "catia_rect_pattern")
    properties = definition["inputSchema"]["properties"]
    for key in ("dir1_axis", "dir2_axis", "reverse_dir1", "reverse_dir2"):
        assert key in properties


def test_a_left_handed_axis_pair_is_swapped_not_refused():
    """CATIA answers a left-handed direction pair with a bare UpdateObject
    failure: (x,y), (y,z), (z,x) build; (x,z), (z,y), (y,x) do not."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert "right_handed" in source
    assert "axes swapped internally" in source
    assert '("x", "z"), ("z", "y"), ("y", "x")' not in source or "in (" in source


def test_draft_blames_the_direction_before_the_neutral_face():
    """A face cannot be drafted along its own normal, and then no neutral face
    helps - the reporter tried all five and concluded the tool was broken."""
    from catia_mcp.tools.part_design import PartDesignTools

    source = inspect.getsource(PartDesignTools._draft)
    assert "Check the DIRECTION first" in source
    assert "own normal" in source


# ------------------------------------------------------------------- wording

def test_new_product_no_longer_contradicts_itself():
    from catia_mcp.tools.document import DocumentTools

    source = inspect.getsource(DocumentTools._new_product)
    # Only the reply matters - the docstring/comment above it quotes the old text
    # on purpose, to say what stopped being true.
    reply = source[source.index("return ("):]
    assert "always 'Product1.CATProduct'" not in reply
    assert "In this session the document is called " in reply
    assert "decides the FILE name" in reply


def test_run_plan_says_topology_is_not_applicable_on_a_product():
    from catia_mcp.tools.planner import PlannerTools

    source = inspect.getsource(PlannerTools._measure_part)
    assert "n/a (Product document" in source


def test_add_new_part_reports_when_the_name_did_not_take():
    from catia_mcp.tools.assembly import AssemblyTools

    source = inspect.getsource(AssemblyTools._add_new_part)
    assert "was not applied by CATIA" in source
    assert "catia_rename_component" in source


# -------------------------------------------------- not reproduced on this build

def _circ_pattern_and_mirror_sources():
    from catia_mcp.tools.part_design import PartDesignTools

    return (inspect.getsource(PartDesignTools._circ_pattern),
            inspect.getsource(PartDesignTools._mirror))


def test_circ_pattern_count_two_is_untouched():
    """Reported as a 100 % failure on B32; builds normally on B30 with a Pad AND
    a Shaft seed, so no workaround was added for a build we could not reproduce."""
    circ, _ = _circ_pattern_and_mirror_sources()
    assert "count == 2" not in circ
    assert "angular_spacing < 360" not in circ


def test_mirror_still_mirrors_the_body():
    _, mirror = _circ_pattern_and_mirror_sources()
    assert "AddNewMirror" in mirror
    assert "already covers 360" not in mirror
