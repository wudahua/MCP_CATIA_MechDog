"""2026-11-15 report, second pass: the three items 0.3.137 wrote off.

0.3.137 answered C5, C8 and N7 in one sentence each - "needs a colour path that
does not go through the tree", "hiding the GSD set takes the solid with it",
"needs an independent fit + constraint design". Two of those were wrong, and
each wrong sentence cost a report. What is pinned here is the measurement that
replaced them, so the next reader cannot re-derive the wrong answer:

  * C5  a FACE takes a colour, and it stays its own while its neighbours change.
        The reason this works is the reference comes off the live selection
        (topology_reference), not from CreateReferenceFromObject - which is
        refused for HSO-resolved cells and is what made the first probe fail.
  * C8  the leftover is a BODY datum ('Datum_xy_30'), not Part.OriginElements:
        hiding the absolute planes leaves the capture byte-identical on B30,
        while hiding the datum changes it and leaves the solid's centre-light
        share at 0.591. The rule that keeps the solid is a NAME comparison
        against body.Shapes.
  * N7  a spline has no dimension of its own (measured: AddMonoEltCst(LENGTH)
        fails while the same call on a LINE succeeds), but a distance between two
        of its CONTROL POINTS drives the geometry: 165856.7 baseline ->
        199723.2 at 140 mm, 64547.8 at 60 mm. So the capability exists; what it
        needs is a design, not an impossibility.
"""
from __future__ import annotations

import inspect

import pytest


# ── C5: Face.N is a target ────────────────────────────────────────────────

def test_face_names_in_the_spellings_a_caller_might_use():
    from catia_mcp.tools.appearance import AppearanceTools

    assert AppearanceTools._face_index("Face.1") == 1
    assert AppearanceTools._face_index("face.1") == 1
    assert AppearanceTools._face_index("FACE_12") == 12
    assert AppearanceTools._face_index("face 3") == 3
    assert AppearanceTools._face_index(" Face.7 ") == 7


def test_a_name_that_only_starts_with_face_is_not_a_face():
    """The tree is searched for everything else; guessing here would colour a
    random node while the reply said 'Face'."""
    from catia_mcp.tools.appearance import AppearanceTools

    for name in ("Face", "Faces", "Face.N", "Face.", "Face 1a", "MyFace.1",
                 "Face.1.foo"):
        assert AppearanceTools._face_index(name) is None, name


def test_a_colour_is_flushed_to_the_display_after_it_is_written():
    """Measured on B30 2026-11-16: set the body red, capture straight away -> 0 red
    pixels; refresh_display() -> still 0; part.Update() -> 122516. Every reply
    before 0.3.138 read the value back while the picture stayed grey, and the
    2026-11-15 report's "整件上色可用" came from a session that had built features
    after colouring."""
    from catia_mcp.tools.appearance import AppearanceTools

    source = inspect.getsource(AppearanceTools._set_color)
    assert source.count("_flush_display") == 2, (
        "both the product and the part path have to flush, or an instance colour "
        "is the only one that appears")
    flush = inspect.getsource(AppearanceTools._flush_display)
    assert "part.Update()" in flush
    assert "refresh_display" in flush, "an assembly has no part to update"


def test_the_flush_says_so_when_it_could_not():
    """A colour that will not show is a fact the caller needs, not a silent one."""
    from catia_mcp.tools.appearance import AppearanceTools

    class _Part:
        def Update(self):
            raise RuntimeError("UpdateObject failed")

    class _Conn:
        @staticmethod
        def part_of(doc):
            return _Part()

    note = AppearanceTools(_Conn())._flush_display(object())
    assert "would not update" in note
    assert "stored" in note


def test_the_face_refusal_happens_before_anything_else():
    """Whether a face can carry its own colour is a fact about this BUILD, not
    about this document, so the caller hears it without a session being opened
    (the ordering rule 0.3.127 learned from _open_document), and before the tree
    is searched - walking the tree first is the refusal 0.3.136 gave by accident
    ('No tree node named Face.1')."""
    from catia_mcp.tools.appearance import AppearanceTools

    for method in ("_set_color", "_get_color"):
        source = inspect.getsource(getattr(AppearanceTools, method))
        check = source.index("_face_index")
        connect = source.index("ensure_connected")
        assert check < connect, f"{method} must refuse before connecting"


def test_a_face_colour_is_refused_and_says_why():
    """The first implementation of this honoured Face.N, wrote the colour, read it
    straight back and reported a face that "keeps its own colour". A B30 session on
    2026-11-16 showed all of it was the SOLID's colour: setting Face.1 red turned
    122516 pixels red, setting Face.2 green turned them green, and the body's own
    colour never moved off #D2D2FF. The refusal quotes that, because a refusal
    without a measurement reads as an opinion."""
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.appearance import AppearanceTools

    with pytest.raises(ArgumentRejected) as caught:
        AppearanceTools(None)._set_color({"target": "Face.1", "color": "#C82828"})
    message = str(caught.value)
    assert "Face.1" in message
    assert "WHOLE solid" in message
    assert "122516" in message, "the pixel count is the evidence"
    assert "#D2D2FF" in message, "the body colour that did NOT change"
    assert "own BODY" in message and "own part" in message, (
        "a refusal has to leave the caller somewhere to go")
    assert getattr(caught.value, "code", "") == "FACE_COLOUR_UNAVAILABLE"


def test_reading_a_face_colour_is_refused_too():
    """The read-back is the more dangerous half: every face answers with the last
    value written, so a 'Face.1: #C82828' line would invent a local finish."""
    from catia_mcp.results import ArgumentRejected
    from catia_mcp.tools.appearance import AppearanceTools

    tools = AppearanceTools(None)
    with pytest.raises(ArgumentRejected) as caught:
        tools._get_color({"target": "Face.2"})
    assert "not a colour target" in str(caught.value)
    assert getattr(caught.value, "code", "") == "FACE_COLOUR_UNAVAILABLE"


def test_the_schema_advertises_face_targets():
    from catia_mcp.tools.appearance import AppearanceTools

    tools = {item["name"]: item
             for item in AppearanceTools(None).get_tool_definitions()}
    description = tools["catia_set_color"]["inputSchema"]["properties"]["target"][
        "description"]
    assert "Face.N" in description
    assert "REFUSED" in description, (
        "the schema must not advertise a face target it will refuse silently")
    assert "WHOLE solid" in description


# ── C8: the datum inside body.HybridShapes ────────────────────────────────

class _Node:
    def __init__(self, name):
        self.Name = name


class _Collection:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, index):
        if isinstance(index, str):
            for item in self._items:
                if item.Name == index:
                    return item
            raise KeyError(index)
        return self._items[index - 1]


class _Body:
    def __init__(self, solid_names, hybrid_names):
        self.Name = "Body"
        self.Shapes = _Collection([_Node(name) for name in solid_names])
        self.HybridShapes = _Collection([_Node(name) for name in hybrid_names])
        self.Sketches = _Collection([])


class _Part:
    def __init__(self, bodies):
        self.Bodies = _Collection(bodies)


class _Selection:
    def __init__(self, shown):
        self.shown = dict(shown)
        self.current = None
        self.VisProperties = self
        self._depth = 0
        self.added = []

    def Clear(self):
        pass

    def Add(self, node):
        self.current = node
        self.added.append(getattr(node, "Name", "?"))

    def GetShow(self):
        if self.current is None:
            raise RuntimeError("nothing selected")
        return self.shown.get(getattr(self.current, "Name", "?"), 0)

    def SetShow(self, state):
        if self.current is None:
            raise RuntimeError("nothing selected")
        self.shown[getattr(self.current, "Name", "?")] = int(state)


def test_the_solid_is_never_hidden_even_when_it_lives_in_the_collection():
    """0.3.126 emptied this collection, lost the model, and stopped touching it.
    The 2026-11-15 report then photographed what that left behind."""
    from catia_mcp.tools.measurement import _hide_body_datums

    body = _Body(["多截面实体.1"], ["Datum_xy_30", "多截面实体.1"])
    selection = _Selection({})
    records, info = _hide_body_datums(_Part([body]), selection)

    assert selection.shown["Datum_xy_30"] == 1, "the datum is the leftover"
    assert "多截面实体.1" not in selection.shown, "the solid must not be touched"
    assert info["kept"] == ["多截面实体.1"]
    assert info["datums"] == 1 and info["hidden"] == 1
    assert [getattr(node, "Name", "?") for node, _ in records] == ["Datum_xy_30"]


def test_a_datum_that_was_already_hidden_is_recorded_but_not_counted():
    """The restore has to put it back hidden; the count is what THIS call did."""
    from catia_mcp.tools.measurement import _hide_body_datums

    body = _Body(["Solid"], ["Datum_xy_30", "Solid"])
    selection = _Selection({"Datum_xy_30": 1})
    records, info = _hide_body_datums(_Part([body]), selection)

    assert len(records) == 1, "restored to hidden, not to shown"
    assert info["datums"] == 1
    assert info["hidden"] == 0


def test_a_body_with_nothing_extra_reports_nothing():
    from catia_mcp.tools.measurement import _hide_body_datums

    body = _Body(["凸台.1"], [])
    records, info = _hide_body_datums(_Part([body]), _Selection({}))
    assert records == [] and info["datums"] == 0 and info["hidden"] == 0


def test_the_clean_view_calls_it_in_both_modes():
    """'solid_only' is the mode the report used, but a datum is never the solid,
    so the plain set-only mode must hide it too."""
    from catia_mcp.tools.export import ExportTools

    source = inspect.getsource(ExportTools._clean_view)
    assert "_hide_body_datums(part, selection)" in source
    block = source[source.index("_hide_body_datums(part, selection)"):]
    assert "if mode == \"solid_only\"" not in block.split("\n")[0]
    # and the call is not inside the solid_only branch
    solid_branch = source.index('if mode == "solid_only"')
    assert solid_branch < source.index("_hide_body_datums(part, selection)")


def test_the_clean_reply_no_longer_claims_the_body_collection_is_untouched():
    """The old sentence said the elements inside the bodies are NOT hidden on
    purpose, which is what the report read as "so it will never be fixed"."""
    from catia_mcp.tools.export import ExportTools

    source = inspect.getsource(ExportTools._clean_view)
    assert "hiding the body's own HybridShapes switches the SOLID" not in source
    assert "datum(s) inside" in source


def test_the_capture_repaints_after_hiding():
    """0.3.131 measured two byte-identical frames at x1 and x1.6 because the
    repaint ran BEFORE the change. The same ordering bug applied to visibility."""
    from catia_mcp.tools.export import ExportTools

    source = inspect.getsource(ExportTools._screenshot)
    hide = source.index("self._clean_view(clean)")
    capture = source.index("viewer.CaptureToFile")
    between = source[hide:capture]
    assert "viewer.Update()" in between, (
        "a visibility change followed by a capture with no repaint photographs "
        "the previous frame")


def test_the_absolute_planes_are_named_as_measured_not_as_guessed():
    """0.3.138's first probe read 0.303 -> 0.926 and would have shipped
    'hiding the absolute planes works'. It was comparing a 400x300 capture with
    a 768x744 one; the byte-identical pair says otherwise."""
    from catia_mcp.tools.export import ExportTools

    source = inspect.getsource(ExportTools._clean_view)
    assert "byte-identical" in source
    assert "does not draw" in source or "not drawn" in source


# ── N7: what a spline can and cannot carry ────────────────────────────────

def test_a_spline_gets_no_dimension_of_its_own():
    """Measured on B30 2026-11-15 in one sketch: AddMonoEltCst(LENGTH) succeeded
    on a LINE ('长度.1') and failed on the closed spline, for LENGTH and RADIUS,
    with the argument as the raw object, as a reference and as geom item 2.

    This test pins the DOCUMENTED conclusion, not the call: if a future build
    makes it work, this test should be edited deliberately, with the new number
    in it."""
    from catia_mcp.tools import diagnostics

    entries = {item["member"]: item for item in diagnostics._CAPABILITY_CEILINGS}
    spline = {key: value for key, value in entries.items()
              if "spline" in key.lower()}
    assert spline, "the spline ceiling disappeared without a measurement"
    text = " ".join(str(value.get("detail") or "")
                    for value in spline.values()).lower()
    assert "dimension" in text and "control point" in text


def test_the_control_point_route_is_the_one_that_works():
    """The evidence behind N7 being possible at all: two control points of the
    same spline, constrained to a distance, moved the padded volume from
    165856.7071 to 199723.1725 (140 mm) and 64547.8081 (60 mm)."""
    from catia_mcp.tools import diagnostics

    text = " ".join(str(item.get("detail") or "")
                    for item in diagnostics._CAPABILITY_CEILINGS)
    assert "199723" in text, (
        "the measured control-point route must be discoverable from the tool "
        "surface, or the next caller will conclude N7 is impossible")
