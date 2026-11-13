"""0.3.116: the three things the reports asked for as tools, not as prose.

1  catia_close_sketch answered a sketch of LETTERING with "closed: false, 16
   loose endpoints, 8 breaks" - every number right, every one of them read as a
   defect, because nothing in the reply knew the lines were glyphs (2026-11-12,
   P2-5). The drawing record now carries `source='text'`, so the reply can tell
   a letter from a broken contour. Explicit purpose= wins; with no instruction
   only a sketch that is NOTHING but text is treated as lettering.
2  catia_solid_loft replied "through 45 section(s)" and the caller could not
   tell whether any were dropped; it compared its own integral with the volume
   instead (2026-11-12, P2-7). B30 2026-11-13 measured GetSectionFromLoft: it is
   1-based, returns (reference, orientation, closing point), raises past the end,
   and reference.DisplayName is the section's name. So the count is read back.
3  catia_close_document had no batch form, and catia_diagnose reported 98 stale
   documents (2026-11-12, P2-10). all_unmodified needs THREE conditions, not
   one: Document.Saved is True only for edits - a never-saved part answers True
   as well - so a file on disk is required, and the active document stays.

These pin the logic, which is pure; the COM behaviour lives in
scripts/b30_v0418_probe_loft_sections.py and ..._loft_saved.py.
"""
from __future__ import annotations

import os
import types

import pytest

from catia_mcp.results import ArgumentRejected
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.part_design import PartDesignTools
from catia_mcp.tools.sketcher import SketcherTools


# ── a sketcher that talks to a sketch-shaped stub ───────────────────────────

class _Line:
    Construction = False


class _Factory:
    def __init__(self) -> None:
        self.lines: list[tuple[float, float, float, float]] = []

    def CreateLine(self, x1, y1, x2, y2):
        self.lines.append((x1, y1, x2, y2))
        return _Line()

    def CreateClosedCircle(self, cx, cy, radius):
        self.lines.append((cx, cy, radius, radius))
        return _Line()


class _Sketch:
    def __init__(self, name: str = "草图.3") -> None:
        self.Name = name
        self.closed = False

    def CloseEdition(self) -> None:
        self.closed = True


class _Part:
    def UpdateObject(self, sketch) -> None:
        pass

    def Update(self) -> None:
        pass


class _Conn:
    """Just enough connection for the close path."""

    open_sketch = None

    def __init__(self, name: str = "T.CATPart") -> None:
        self.active_document = types.SimpleNamespace(Name=name)
        self.sketch_outlines: dict = {}

    def refresh_display(self) -> None:
        pass

    def get_active_part(self):
        return _Part()


class _Sketcher(SketcherTools):
    def __init__(self, name: str = "T.CATPart") -> None:
        super().__init__(connection=_Conn(name))
        self._active_sketch = _Sketch()
        self._active_factory = _Factory()


def _text_sketch(tools: _Sketcher, text: str = "CATIA") -> str:
    return tools._draw_text({"text": text, "height": 6.0})


# ── 1: a sketch of glyphs is reported as lettering ──────────────────────────

def test_a_text_sketch_is_not_told_its_profile_is_broken():
    tools = _Sketcher()
    _text_sketch(tools)

    reply = tools._close_sketch(structured=True)

    assert reply["data"]["purpose"] == "engrave"
    assert reply["data"]["expected_open"] is True
    # The counts are still the evidence - they are not hidden, just explained.
    assert reply["data"]["text_strokes"] >= 1
    assert reply["data"]["breaks"]["expected"] == "open"
    message = reply["message"]
    assert "NOTE: this sketch is not a closed profile" in message
    assert "came from catia_text_strokes" in message
    assert "FREE ENDS" in message
    # And the thing the old wording got wrong: there is nothing to fix.
    assert "Nothing here needs fixing" in message
    assert "catia_surface_engrave" in message
    # The opening sentence used to offer pad/pocket, which the rest of the same
    # reply then refuses - two halves of one message contradicting each other.
    assert "will REFUSE this sketch on purpose" in message
    assert "You can now apply Part Design features" not in message


def test_a_text_sketch_is_not_warned_about_loose_ends():
    """The regression itself: 'WARNING: the profile is not closed' must be gone.

    It used to fire whenever the smallest gap was within 0.05 mm, and on the
    report's 26-line sketch it produced 8 gaps and 16 loose endpoints in the
    voice of a defect.
    """
    tools = _Sketcher()
    _text_sketch(tools)

    message = tools._close_sketch()

    assert "WARNING: the profile is not closed" not in message
    # The fact is still stated - as what the sketch was built to be.
    assert "is not a closed profile, and it was not built as one" in message


def test_purpose_profile_keeps_the_profile_voice_on_lettering():
    """The caller can still ask for the strict reading - it just has to ask."""
    tools = _Sketcher()
    _text_sketch(tools)

    reply = tools._close_sketch(structured=True, purpose="profile")

    assert reply["data"]["purpose"] == "profile"
    assert reply["data"]["expected_open"] is False
    assert "NOTE: this sketch is not a closed profile" not in reply["message"]
    # A mixed sketch still says which of its elements came from text.
    assert "came from catia_text_strokes" in reply["message"]


def test_a_mixed_sketch_stays_a_profile_unless_told_otherwise():
    """Glyphs plus a real closed loop is a case the record cannot decide."""
    tools = _Sketcher()
    _text_sketch(tools, "A")
    tools._draw_circle(0.0, 0.0, 3.0)

    reply = tools._close_sketch(structured=True)

    assert reply["data"]["purpose"] == "profile"
    assert "Pass purpose='engrave'" in reply["message"]


def test_an_unknown_purpose_closes_nothing():
    tools = _Sketcher()

    with pytest.raises(ArgumentRejected) as caught:
        tools._close_sketch(purpose="lettering")

    assert caught.value.code == "BAD_ARGUMENT"
    assert "Nothing was closed" in str(caught.value)
    assert tools._active_sketch.closed is False


def test_a_crossing_in_lettering_is_a_note_not_a_verdict():
    """An engraved 'X' has two grooves that meet; that is the point of an X."""
    from catia_mcp.geometry2d import describe

    found = [{"kind": "crossing", "label_a": "line #1", "label_b": "line #2",
              "point": (5.0, 5.0)}]

    profile = describe(found, 4)
    engraving = describe(found, 4, engraving=True)

    assert "WARNING: THE OUTLINE CROSSES ITSELF" in profile
    assert "fix the crossing before the feature" in profile
    assert "WARNING" not in engraving
    assert "NOTE: the strokes cross in 1 place(s)" in engraving
    assert "not a defect" in engraving
    # Both still carry the coordinates, so the claim can be checked.
    assert "(5.0000, 5.0000)" in profile and "(5.0000, 5.0000)" in engraving


# ── 2: the loft names the sections CATIA kept ──────────────────────────────

class _Loft:
    """GetSectionFromLoft: 1-based, (reference, orientation, closing point)."""

    def __init__(self, names: list[str], refuse_at: int = 0) -> None:
        self.names = names
        self.refuse_at = refuse_at

    def GetSectionFromLoft(self, index: int):
        if self.refuse_at and index >= self.refuse_at:
            raise RuntimeError("方法 GetSectionFromLoft 失败")
        if not 1 <= index <= len(self.names):
            raise RuntimeError("方法 GetSectionFromLoft 失败")
        reference = types.SimpleNamespace(DisplayName=self.names[index - 1])
        return (reference, 1, None)


def test_the_loft_reports_the_sections_catia_holds():
    kept, note = PartDesignTools._loft_sections_used(
        _Loft(["草图.1", "草图.2", "草图.3"]), ["草图.1", "草图.2", "草图.3"])

    assert kept == ["草图.1", "草图.2", "草图.3"]
    assert "CATIA's own section list holds 3" in note
    assert "none was silently dropped" in note


def test_a_section_catia_dropped_is_named_not_inferred():
    kept, note = PartDesignTools._loft_sections_used(
        _Loft(["草图.1", "草图.3"]), ["草图.1", "草图.2", "草图.3"])

    assert kept == ["草图.1", "草图.3"]
    assert note.startswith(" WARNING")
    assert "holds only 2" in note
    assert "Not in the loft: '草图.2'" in note


def test_an_unreadable_section_list_says_the_count_was_sent_not_measured():
    kept, note = PartDesignTools._loft_sections_used(
        _Loft([], refuse_at=1), ["草图.1", "草图.2"])

    assert kept == []
    assert "could NOT be read back" in note
    assert "SENT, not a count" in note


# ── 3: all_unmodified closes only what can be closed safely ────────────────

class _Doc:
    def __init__(self, name: str, path: str, saved, collection) -> None:
        self.Name = name
        self.FullName = path
        self.Saved = saved
        self._collection = collection
        self.closed = False
        self.defer = False

    def Close(self) -> None:
        if self.defer:
            return
        self.closed = True
        self._collection.remove(self)


class _Docs:
    def __init__(self, docs: list[_Doc]) -> None:
        self.docs = docs

    @property
    def Count(self) -> int:
        return len(self.docs)

    def Item(self, index: int) -> _Doc:
        return self.docs[index - 1]


class _DocsConn:
    def __init__(self, docs: list[_Doc], active: _Doc) -> None:
        self.documents = _Docs(docs)
        self.active_document = active

    def ensure_connected(self) -> None:
        pass


class _Documents(DocumentTools):
    """DocumentTools whose document list is a list of stubs."""

    def __init__(self, docs: list[_Doc], active: _Doc) -> None:
        super().__init__(connection=_DocsConn(docs, active))
        self._active = active

    def _document_names(self) -> list[str]:
        return [doc.Name for doc in self.conn.documents.docs]


def _fixture(tmp_path):
    work = tmp_path / "Work.CATPart"
    work.write_text("x", encoding="utf-8")
    idle = tmp_path / "Idle.CATPart"
    idle.write_text("x", encoding="utf-8")

    docs: list[_Doc] = []
    active = _Doc("Active.CATPart", str(work), True, docs)
    unmodified = _Doc("Idle.CATPart", str(idle), True, docs)
    modified = _Doc("Dirty.CATPart", str(work), False, docs)
    never_saved = _Doc("Fresh.CATPart", "Fresh.CATPart", True, docs)
    deferred = _Doc("Held.CATPart", str(idle), True, docs)
    deferred.defer = True
    docs.extend([active, unmodified, modified, never_saved, deferred])
    return _Documents(docs, active), {
        "active": active, "unmodified": unmodified, "modified": modified,
        "never_saved": never_saved, "deferred": deferred}


def test_all_unmodified_closes_the_one_document_that_is_safe(tmp_path):
    tools, docs = _fixture(tmp_path)

    reply = tools._close_document(all_unmodified=True)

    assert docs["unmodified"].closed is True
    # Every refusal is named with its reason, not silently skipped.
    assert docs["modified"].closed is False and "MODIFIED" in reply
    assert docs["never_saved"].closed is False and "no file on disk" in reply
    assert docs["active"].closed is False and "ACTIVE" in reply
    assert docs["deferred"].closed is False and "STILL OPEN" in reply
    assert "Closed 1 of 5" in reply


def test_a_never_saved_document_is_not_unmodified_just_because_catia_says_so(tmp_path):
    """Measured: a new part and a new product both answer Saved=true.

    Their FullName is 'Fresh.CATPart' - no directory - so Saved alone would have
    closed a document whose content exists nowhere.
    """
    work = tmp_path / "Only.CATPart"
    work.write_text("x", encoding="utf-8")
    docs: list[_Doc] = []
    fresh = _Doc("Fresh.CATPart", "Fresh.CATPart", True, docs)
    active = _Doc("A.CATPart", str(work), True, docs)
    docs.extend([fresh, active])
    tools = _Documents(docs, active)

    reply = tools._close_document(all_unmodified=True)

    assert fresh.closed is False
    assert "there were none" in reply
    assert "destroy the only copy" in reply


def test_all_unmodified_refuses_to_be_a_typo_away_from_closing_one_named_file(tmp_path):
    tools, _docs = _fixture(tmp_path)

    with pytest.raises(ArgumentRejected):
        tools._close_document(document="Idle.CATPart", all_unmodified=True)
    with pytest.raises(ArgumentRejected):
        tools._close_document(save=True, all_unmodified=True)


def test_a_document_whose_saved_flag_cannot_be_read_is_left_alone(tmp_path):
    work = tmp_path / "X.CATPart"
    work.write_text("x", encoding="utf-8")

    class _Mute(_Doc):
        @property
        def Saved(self):  # noqa: D401 - raises on purpose
            raise RuntimeError("no such member")

        @Saved.setter
        def Saved(self, value):
            pass

    docs: list[_Doc] = []
    mute = _Mute("Mute.CATPart", str(work), True, docs)
    active = _Doc("A.CATPart", str(work), True, docs)
    docs.extend([mute, active])
    tools = _Documents(docs, active)

    reply = tools._close_document(all_unmodified=True)

    assert mute.closed is False
    assert "'Saved' not readable" in reply
    assert "not the same as unmodified" in reply
