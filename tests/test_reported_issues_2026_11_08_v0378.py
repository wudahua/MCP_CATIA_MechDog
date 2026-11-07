"""0.3.78: the 2026-11-08 acceptance of 0.3.77, both reports.

Six new items on B30 plus one documentation item on the B32 side.

N1 is the one that matters most about method: 0.3.77 fixed the AXIS and audited
it by comparing `dimensions`, which cannot see a sign - a pattern that mirrors by
200 mm grows the same length. The acceptance compared the box ENDPOINTS and found
every label inverted. Measured on B30 2026-11-08 (`scripts/b30_v0378_sign.py`):

    slot 1 takes the plane's H axis, NEGATED   (PlaneXY -> -X)
    slot 2 takes the plane's V axis, NEGATED   (PlaneXY -> -Y)

so the two slots need different tables, and both need the flags flipped.

N2 (duplicate sketch names, the report's own "most serious") is fixed at the
creation point: a new sketch whose name the part already uses is renamed, and the
conflict is reported by catia_list_features for trees that already have one.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.planner import PlannerTools
from catia_mcp.tools.sketcher import SketcherTools


# ── N1: the two slots, and the signs ──────────────────────────────────────

def test_the_two_slots_have_different_tables():
    """Slot 2 does not follow slot 1's rule - measured, not assumed."""
    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert 'by_h = {"x": "xy", "y": "yz", "z": "zx"}' in source
    assert 'by_v = {"x": "zx", "y": "xy", "z": "yz"}' in source
    assert "planes[by_h[first]]" in source
    assert "planes[by_v[second]]" in source
    # And both are negated, because the reference runs backwards on this build.
    assert "reverse1, reverse2 = not reverse1, not reverse2" in source


def test_the_axis_reply_is_what_the_geometry_does():
    """`axis x+` must move the copies to +X - reported inverted in 2026-11-08."""
    source = inspect.getsource(PartDesignTools._rect_pattern)
    # The label is built from the caller's sign and the flip happens AFTER it,
    # so the reply keeps saying what was asked and the geometry now matches.
    label = source.index('directions = (f"axis {first}')
    flip = source.index("reverse1, reverse2 = not reverse1, not reverse2")
    assert label < flip


def test_the_default_pair_states_the_measured_directions():
    source = inspect.getsource(PartDesignTools._rect_pattern)
    assert "PlaneYZ -> dir1 (-Y), PlaneZX -> dir2 (-X)" in source


def test_the_schema_agrees_with_the_runtime_reply():
    """N6: the schema still promised PlaneYZ -> X, the reply said Y."""
    schema = [tool for tool in PartDesignTools(None).get_tool_definitions()
              if tool["name"] == "catia_rect_pattern"][0]
    description = schema["description"]
    assert "PlaneYZ -> X" not in description
    assert "-Y" in description and "-X" in description
    assert "ENDPOINTS" in description  # says how it was measured


# ── N2: a duplicate name is fixed at creation, and reported afterwards ─────

def _collection(items: list):
    return SimpleNamespace(Count=len(items), Item=lambda index: items[index - 1])


def _sketches(names: list[str]):
    return _collection([SimpleNamespace(Name=name) for name in names])


def _part_with(names: list[str]):
    body = SimpleNamespace(Sketches=_sketches(names), HybridSketches=_sketches([]),
                           HybridBodies=_sketches([]))
    return SimpleNamespace(Bodies=SimpleNamespace(Count=1, Item=lambda i: body),
                           HybridBodies=SimpleNamespace(Count=0, Item=lambda i: None))


def test_a_new_sketch_gets_a_free_name():
    part = _part_with(["草图.1", "草图.1"])
    sketch = SimpleNamespace(Name="草图.1")

    note = SketcherTools(connection=None)._make_sketch_name_unique(part, sketch)

    assert sketch.Name == "草图.1#2"
    assert "renamed to '草图.1#2'" in note
    assert "already held a '草图.1'" in note


def test_a_unique_name_is_left_alone():
    part = _part_with(["草图.1"])
    sketch = SimpleNamespace(Name="草图.2")
    note = SketcherTools(connection=None)._make_sketch_name_unique(part, sketch)
    assert sketch.Name == "草图.2"
    assert note == ""


def test_the_rename_skips_names_that_are_already_taken():
    part = _part_with(["草图.1", "草图.1", "草图.1#2", "草图.1#3"])
    sketch = SimpleNamespace(Name="草图.1")
    SketcherTools(connection=None)._make_sketch_name_unique(part, sketch)
    assert sketch.Name == "草图.1#4"


def test_every_sketch_name_in_the_part_is_seen():
    """Nested geometrical sets count too - a name there collides just as well."""
    inner = SimpleNamespace(Sketches=_sketches([]),
                            HybridSketches=_sketches(["草图.9"]),
                            HybridBodies=_sketches([]))
    outer = SimpleNamespace(Sketches=_sketches(["草图.1"]),
                            HybridSketches=_sketches([]),
                            HybridBodies=_collection([inner]))
    part = SimpleNamespace(Bodies=SimpleNamespace(Count=1, Item=lambda i: outer),
                           HybridBodies=SimpleNamespace(Count=1, Item=lambda i: outer))
    names = SketcherTools._all_sketch_names(part)
    assert "草图.1" in names and "草图.9" in names


def test_list_features_reports_a_name_shared_by_two_sketches():
    source = inspect.getsource(PartDesignTools._list_features)
    assert "name_shared_by" in source
    assert "sketch_name_conflicts" in source
    assert "sketch_name_conflict_note" in source


def test_the_shared_name_count_walks_bodies_and_nested_sets():
    inner = SimpleNamespace(Sketches=_sketches(["草图.2"]),
                            HybridSketches=_sketches([]),
                            HybridBodies=_sketches([]))
    body = SimpleNamespace(Sketches=_sketches(["草图.1", "草图.2"]),
                           HybridSketches=_sketches([]),
                           HybridBodies=_collection([inner]))
    part = SimpleNamespace(Bodies=SimpleNamespace(Count=1, Item=lambda i: body),
                           HybridBodies=SimpleNamespace(Count=0, Item=lambda i: None))
    counts = PartDesignTools._shared_sketch_names(part)
    assert counts["草图.2"] == 2
    assert counts["草图.1"] == 1


def test_geometry_says_when_its_session_record_may_belong_elsewhere():
    source = inspect.getsource(SketcherTools._get_geometry)
    assert "drawn_this_session_note" in source
    assert "may describe a " in source and "different sketch with the same name" in source


# ── N3: the warning survives run_plan, and step_warnings always exists ─────

def test_the_warning_is_extracted_from_the_untrimmed_reply():
    text = "Solid loft created. " + ("x" * 700) + " WARNING: sample both sections the same way."
    found = PlannerTools._warning_text(text)
    assert len(found) == 1
    assert found[0].startswith("WARNING: sample both sections")
    # The advice at the END is what matters, and it is still there.
    assert "same way" in found[0]


def test_a_reply_without_a_warning_yields_nothing():
    assert PlannerTools._warning_text("Pad created. Volume 0.0 -> 4000.0 mm3.") == []
    # A JSON reply with an EMPTY warnings list is not a warning.
    assert PlannerTools._warning_text(json.dumps({"ok": True, "warnings": []})) == []
    assert PlannerTools._warning_text(
        json.dumps({"ok": True, "warnings": ["careful"]})) == ["careful"]


def test_step_warnings_is_always_present_under_summary():
    source = inspect.getsource(PlannerTools._run_plan)
    assert 'data["step_warnings"] = warned' in source
    assert "always present, " in source and "possibly empty" in source
    # The detection runs on the full text, before the trim.
    assert "found = self._warning_text(text_value)" in source


# ── N4: Open that returns a different file is not a success ───────────────

def _open_tools(opened_full_name: str):
    class Docs:
        Count = 0

        @staticmethod
        def Item(index):
            raise AssertionError("no document should be listed")

        @staticmethod
        def Open(path):
            return SimpleNamespace(Name="Frigate_from_TXT.CATPart",
                                   FullName=opened_full_name)

    tools = DocumentTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, documents=Docs(),
        last_sketch=None, active_body=None, last_part_name=None,
    )
    return tools


def test_open_refuses_when_catia_returns_a_different_file(monkeypatch, tmp_path):
    from catia_mcp import dialogs

    dismissed = []
    monkeypatch.setattr(dialogs, "dismiss", lambda **kw: dismissed.append(kw))
    requested = tmp_path / "工作区" / "Frigate_from_TXT.CATPart"
    requested.parent.mkdir()
    tools = _open_tools("F:/WB_CATIA/Frigate_from_TXT.CATPart")

    with pytest.raises(Exception) as caught:
        tools._open_document(str(requested))

    error = caught.value
    assert getattr(error, "code", None) == "DOCUMENT_IDENTITY_CONFLICT"
    assert error.modified is False
    assert "was NOT opened" in str(error)
    assert "F:/WB_CATIA/Frigate_from_TXT.CATPart" in str(error)
    # The modal CATIA raises on the automation thread is cleared, not left in
    # front of the next call.
    assert dismissed


def test_open_of_the_requested_file_is_a_success(monkeypatch, tmp_path):
    from catia_mcp import dialogs

    monkeypatch.setattr(dialogs, "dismiss", lambda **kw: None)
    target = tmp_path / "part.CATPart"
    tools = _open_tools(str(target).replace("\\", "/"))

    reply = tools._open_document(str(target))

    assert reply.startswith("Opened document:")


# ── N5: nested geometrical sets ───────────────────────────────────────────

def _gsd_tools(parent_raises: bool = False):
    from catia_mcp.tools.gsd import GSDTools

    created = []

    class Sets:
        def __init__(self, items=None):
            self.items = items or []

        @property
        def Count(self):
            return len(self.items)

        def Item(self, index):
            return self.items[index - 1]

        def Add(self):
            if parent_raises:
                raise RuntimeError("方法 Add 失败")
            node = SimpleNamespace(Name="x", HybridBodies=Sets([]),
                                   HybridShapes=Sets([]))
            created.append(node)
            self.items.append(node)
            return node

    root_set = SimpleNamespace(Name="OUTER", HybridBodies=Sets([]),
                               HybridShapes=Sets([]))
    part = SimpleNamespace(HybridBodies=Sets([root_set]))
    tools = GSDTools(connection=None)
    tools.conn = SimpleNamespace(
        ensure_connected=lambda: None, get_active_part=lambda: part,
        active_document=SimpleNamespace(Name="probe.CATPart"))
    return tools, created, root_set


def test_a_geometrical_set_can_be_created_inside_another():
    tools, created, root = _gsd_tools()
    reply = tools._create_geoset({"name": "INNER", "parent": "OUTER"})

    assert root.HybridBodies.Count == 1
    assert created and created[0].Name == "INNER"
    assert "inside 'OUTER'" in reply
    # The visibility caveat the report asked for: a curve does not triangulate.
    assert "cannot be triangulated" in reply


def test_a_missing_parent_is_refused_by_name():
    from catia_mcp.results import ArgumentRejected

    tools, _created, _root = _gsd_tools()
    with pytest.raises(ArgumentRejected) as caught:
        tools._create_geoset({"name": "INNER", "parent": "NOPE"})
    assert "No geometrical set named 'NOPE'" in str(caught.value)
    assert "Available:" in str(caught.value) and "OUTER" in str(caught.value)


def test_a_refused_nesting_says_nothing_was_created():
    from catia_mcp.results import PreconditionRejected

    tools, created, _root = _gsd_tools(parent_raises=True)
    with pytest.raises(PreconditionRejected) as caught:
        tools._create_geoset({"name": "INNER", "parent": "OUTER"})
    assert "was not created" in str(caught.value)
    assert created == []


# ── the loft's code stops depending on its wording ────────────────────────

def test_the_loft_failure_has_a_name_not_a_guess():
    from catia_mcp.results import FeatureRejected

    assert "code" in inspect.signature(FeatureRejected.__init__).parameters
    source = inspect.getsource(PartDesignTools._solid_loft)
    assert 'code="LOFT_REJECTED"' in source


# ── the B32 documentation item ────────────────────────────────────────────

def test_the_readme_lists_the_threaded_hole_as_a_build_difference():
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8")
    assert "Two** known build differences" in readme
    assert "threaded hole cannot be cut on B32" in readme
    # And it does not pretend the CHANGELOG note is a fix.
    assert "Registering the limitation is not" in readme


def test_the_threaded_argument_warns_at_the_call_site():
    """0.3.59's rule: a build limit belongs where the caller decides."""
    schema = [tool for tool in PartDesignTools(None).get_tool_definitions()
              if tool["name"] == "catia_hole"][0]
    description = schema["inputSchema"]["properties"]["threaded"]["description"]
    assert "BUILD LIMIT" in description
    assert "V5-6R2022 (B32)" in description
    assert "FEATURE_INCOMPLETE" in description
