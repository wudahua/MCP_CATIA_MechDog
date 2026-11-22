"""0.3.160: the four items the 2026-11-22 reports left open.

  * P0-2  no seed route for a feature mirror - and the three measurements that
          close it (AddNewMirror ignores the reference's kind, Object and
          MirroringObject are read-only, AddObjectToMirror only appends);
  * P2-9  a sketch element carries the coordinates it was DRAWN with, matched to
          the LIVE index, instead of the coordinates living in a separate block;
  * P1-17 feature tools can be pointed at an open document - reading one must not
          move the active document, deleting in one must move it and CHECK that
          it moved.
"""

from __future__ import annotations

import json
import types

import pytest

from catia_mcp.results import CodedError


# ── P2-9: element -> the draw that produced it ────────────────────────────
class _Geom:
    def __init__(self, entries):
        self.Count = len(entries)
        self._entries = entries

    def Item(self, index):
        return self._entries[index - 1]


def _elem(name: str, kind: str = "1"):
    return types.SimpleNamespace(Name=name, GeometricType=kind, Construction=False)


def _geometry_tools(drawn):
    from catia_mcp.tools.sketcher import SketcherTools

    tools = SketcherTools(connection=None)
    sketch = types.SimpleNamespace(
        Name="Sketch.1",
        GeometricElements=_Geom([_elem("原点"), _elem("直线.1"), _elem("直线.2"),
                                 _elem("圆.1")]),
        Constraints=types.SimpleNamespace(Count=0),
    )
    tools._locate_sketch = lambda name=None: (sketch, "by name")
    tools._is_absolute_axis = lambda elem, index: index == 1
    tools._sketch_key = lambda name: "Doc::" + str(name)
    tools._drawn = {"Doc::Sketch.1": list(drawn)}
    tools.conn = types.SimpleNamespace(
        active_document=types.SimpleNamespace(Name="Doc.CATPart"),
        get_active_part=lambda: types.SimpleNamespace(),
        sketch_outlines={},
    )
    tools._all_sketch_names = lambda part: ["Sketch.1"]
    return tools


def test_each_element_carries_the_draw_that_made_it():
    tools = _geometry_tools([
        {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 80, "y2": 50,
         "_span": [2, 3]},
        {"kind": "circle", "cx": 60, "cy": 25, "radius": 8, "_span": [4, 4]},
    ])
    payload = json.loads(tools._get_geometry())
    by_index = {entry["index"]: entry for entry in payload["elements"]}
    assert by_index[2]["drawn"]["kind"] == "rectangle"
    assert by_index[3]["drawn"]["x2"] == 80      # the same draw, second element
    assert by_index[4]["drawn"] == {"kind": "circle", "cx": 60, "cy": 25,
                                    "radius": 8}
    # The frame itself was not drawn by this session: no coordinates invented.
    assert "drawn" not in by_index[1]
    # The record is still published as a whole, and its spans do not leak into
    # the per-element copy.
    assert payload["drawn_this_session"][0]["_span"] == [2, 3]
    assert "_span" not in by_index[2]["drawn"]


def test_the_note_says_where_the_coordinates_come_from_and_what_they_are_not():
    tools = _geometry_tools([])
    payload = json.loads(tools._get_geometry())
    note = payload["note"]
    assert "come from this session's record" in note
    assert "NO 'drawn'" in note               # says what a missing entry means
    assert "not what CATIA solved to" in note
    # The old sentence read as "a sketch cannot be checked" and cost a report a
    # whole verification loop (2026-11-14); it must not come back.
    assert "known by its index and its neighbours, not by its" not in note


def test_an_element_from_another_session_simply_has_no_record():
    tools = _geometry_tools([])
    payload = json.loads(tools._get_geometry())
    assert all("drawn" not in entry for entry in payload["elements"])
    assert "drawn_this_session" not in payload


def test_the_description_stops_promising_that_a_line_has_no_coordinates():
    from catia_mcp.tools.sketcher import SketcherTools

    definitions = {d["name"]: d for d in
                   SketcherTools(connection=None).get_tool_definitions()}
    text = definitions["catia_sketch_get_geometry"]["description"]
    assert "'drawn'" in text
    assert "no readable X/Y" in text          # the CATIA limit is still stated
    assert "What is NOT returned is a LINE'S ENDPOINTS" not in text


# ── P1-17: point a feature tool at a document ─────────────────────────────
def _design_tools(*, active="A.CATPart", documents=None, part_ok=True,
                  activate_to=None):
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    docs = documents if documents is not None else {"A.CATPart": "A.CATPart",
                                                   "B.CATPart": "B.CATPart"}
    state = {"active": active, "activated": []}

    class _Doc:
        def __init__(self, name):
            self.Name = name

        def Activate(self):
            state["activated"].append(self.Name)
            if activate_to:
                state["active"] = activate_to

    def resolve(name):
        if name in docs:
            return _Doc(name), [{"name": name}]
        return None, []

    conn = types.SimpleNamespace(
        resolve_document=resolve,
        part_of=lambda doc: (types.SimpleNamespace(Name=doc.Name) if part_ok
                             else (_ for _ in ()).throw(
                                 RuntimeError("exposes no 'Part'"))),
        describe_matches=lambda matches: matches,
        format_matches=lambda rows: ", ".join(row["name"] for row in rows),
        app=types.SimpleNamespace(
            ActiveDocument=_Doc(active)),
        component_usage=lambda deep=False: {"B.CATPart": ["ASM.CATProduct"]},
        ensure_connected=lambda: None,
    )
    conn.app.ActiveDocument = _Doc(state["active"])

    class _Active:
        pass

    # `ActiveDocument` has to follow the fake Activate(), so it is a property.
    class _App:
        @property
        def ActiveDocument(self):
            return _Doc(state["active"])

    conn.app = _App()
    tools.conn = conn
    return tools, state


def test_a_reader_does_not_activate_the_named_document():
    tools, state = _design_tools()
    part, name = tools._target_part("B.CATPart", activate=False)
    assert name == "B.CATPart" and part.Name == "B.CATPart"
    assert state["activated"] == []
    assert state["active"] == "A.CATPart"


def test_a_writer_activates_and_verifies():
    tools, state = _design_tools(activate_to="B.CATPart")
    part, name = tools._target_part("B.CATPart", activate=True)
    assert name == "B.CATPart" and part.Name == "B.CATPart"
    assert state["activated"] == ["B.CATPart"]


def test_a_write_against_a_document_that_will_not_come_forward_is_refused():
    """A part held by an open assembly accepts Activate() and stays behind."""
    tools, state = _design_tools(activate_to=None)
    with pytest.raises(CodedError) as error:
        tools._target_part("B.CATPart", activate=True)
    text = str(error.value)
    assert "Nothing was changed" in text
    assert "ASM.CATProduct" in text           # the holder is named
    assert error.value.code == "ACTIVATION_FAILED"


def test_a_product_is_refused_with_the_way_out():
    tools, _ = _design_tools(part_ok=False)
    with pytest.raises(CodedError) as error:
        tools._target_part("B.CATPart", activate=False)
    assert error.value.code == "NOT_A_PART_DOCUMENT"
    assert "not a Part document" in str(error.value)
    # The way out is a next_action, not prose: a caller that parses the field
    # gets it (0.3.156's rule for "this is not a Part").
    assert any("catia_open_document" in action
               for action in error.value.next_actions)


def test_an_unknown_document_lists_what_is_open():
    tools, _ = _design_tools()
    with pytest.raises(CodedError) as error:
        tools._target_part("Nope.CATPart", activate=False)
    assert error.value.code == "DOCUMENT_NOT_FOUND"
    assert "nothing was measured or changed" in str(error.value)


def test_list_features_reports_the_document_it_read():
    tools, state = _design_tools()
    body = types.SimpleNamespace(
        Name="PartBody", Shapes=types.SimpleNamespace(Count=0),
        Sketches=types.SimpleNamespace(Count=0))
    tools.conn.part_of = lambda doc: types.SimpleNamespace(
        Name=doc.Name, MainBody=body, Bodies=types.SimpleNamespace(Count=1))
    payload = json.loads(tools._list_features("B.CATPart"))
    assert payload["document"] == "B.CATPart"
    assert payload["body"] == "PartBody"
    assert "read from 'B.CATPart' by name" in (payload.get("note") or "")
    assert state["active"] == "A.CATPart"


def test_the_two_schemas_carry_document_and_say_what_it_costs():
    from catia_mcp.tools.part_design import PartDesignTools

    definitions = {d["name"]: d for d in
                   PartDesignTools(connection=None).get_tool_definitions()}
    reading = definitions["catia_list_features"]["inputSchema"]["properties"]
    writing = definitions["catia_delete_feature"]["inputSchema"]["properties"]
    assert "document" in reading and "document" in writing
    assert "does NOT activate" in reading["document"]["description"]
    assert "ACTIVATES" in writing["document"]["description"]


# ── P0-2: the refusal carries the measurements that close it ──────────────
def test_the_feature_mirror_refusal_reports_the_three_routes_it_ruled_out():
    from catia_mcp.tools.part_design import PartDesignTools

    tools = PartDesignTools(connection=None)
    body = types.SimpleNamespace(
        Name="PartBody",
        Shapes=types.SimpleNamespace(
            Count=2,
            Item=lambda index: types.SimpleNamespace(
                Name=["凸台.1", "凹槽.1"][index - 1])))
    tools.conn = types.SimpleNamespace(ensure_connected=lambda: None)
    tools._prepare_body = lambda part: body
    tools._feature_kind_resolved = lambda part, shape: "pocket"
    tools.conn.get_active_part = lambda: types.SimpleNamespace(
        ShapeFactory=object())
    tools.conn.get_origin_elements = lambda: {"yz": object()}
    with pytest.raises(CodedError) as error:
        tools._mirror({"plane": "yz", "feature": "凹槽.1"})
    text = str(error.value)
    assert error.value.code == "MIRROR_SEED_UNSUPPORTED"
    # The three readings the 2026-11-22 report asked for, not one property.
    assert "AddObjectToMirror" in text
    assert "242 000" in text
    assert "FILLS" in text.upper()
    assert "Nothing was created" in text


def test_the_description_does_not_say_addnewmirror_takes_only_a_plane():
    from catia_mcp.tools.part_design import PartDesignTools

    definitions = {d["name"]: d for d in
                   PartDesignTools(connection=None).get_tool_definitions()}
    text = definitions["catia_mirror"]["description"]
    assert "takes only a plane" not in text
    assert "MirroringObject" in text and "AddObjectToMirror" in text
    assert "242 000" in text
