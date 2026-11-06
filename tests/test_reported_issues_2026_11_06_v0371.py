"""0.3.71: the four reports of 2026-11-06, as tests.

Two envelope holes (P2-A: a lookup translated inside an `except`; P2-B: seven
`CodedError`s with no stage), and three "the reply says something the code did not
measure":

  * `catia_hole(threaded=true)` failed the update on B32 and left a zombie
    feature while answering COM_ERROR / modified:null;
  * `document=` paid 790 ms for a tensor it cannot return, and then explained the
    absence as a focus refusal the caller could fix - it cannot;
  * `catia_close_sketch` called a plate with a hole self-intersecting, and told
    the caller CATIA "still refuses" a sketch whose pad had just succeeded;
  * `seam_hint` was popped on a surface patch and then added straight back.

Each test names the report line it closes.
"""

from __future__ import annotations

import json
import pathlib
import re
from types import SimpleNamespace

import pytest

from catia_mcp.results import CodedError, PreconditionRejected

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── P2-A: no document open, the first call of a cold session ───────────────


def test_no_active_document_is_a_precondition_not_a_com_failure():
    """Reported as COM_ERROR / execute / modified:null for every tool call."""
    from catia_mcp.connection import CATIAConnection

    class _App:
        @property
        def ActiveDocument(self):
            raise RuntimeError("no document")

    conn = CATIAConnection()
    conn.ensure_connected = lambda: None
    conn.app = _App()
    with pytest.raises(PreconditionRejected) as caught:
        conn.active_document
    error = caught.value
    assert error.code == "PRECONDITION_FAILED"
    assert error.stage == "validate"
    assert error.modified is False
    assert "catia_new_part" in json.dumps(error.next_actions)


# ── P2-B: a validate code must not travel at the execute stage ─────────────
# The class-level guard (test_argument_envelopes) walks every CodedError whose
# code is in the validate family and fails on a missing `stage`. This file keeps
# the behaviour that guard protects, in one spot: a name that is not in the list.



# ── the note must describe the right subject ──────────────────────────────


@pytest.mark.parametrize("code,expected", [
    ("ARGUMENT_REJECTED", "ARGUMENT"),
    ("PRECONDITION_FAILED", "STATE"),
    ("ARGUMENT_OUT_OF_RANGE", "index"),
    ("DOCUMENT_NOT_FOUND", "NAME"),
    ("INTERNAL_ERROR", "defect"),
])
def test_next_actions_note_matches_the_reason(code, expected):
    """`PreconditionRejected` shipped "this is a state, not a typo" beside a note
    that called it "the ARGUMENT this call used" (reported 2026-11-06, P3)."""
    from catia_mcp.results import _topic

    assert expected in _topic(code)


# ── threading: no zombie, and an honest `modified` ────────────────────────


class _Part:
    def __init__(self, fail_update: bool) -> None:
        self.MainBody = SimpleNamespace(Name="PartBody")
        self.Selection = SimpleNamespace(Clear=lambda: None, Add=lambda x: None,
                                         Delete=lambda: setattr(self, "deleted", True))
        self.deleted = False
        self.fail_update = fail_update

    def Update(self):
        if self.fail_update:
            raise RuntimeError("方法 Update 失败")


class _Hole:
    Name = "孔.1"

    def __init__(self, part: _Part) -> None:
        self._mode = 1
        self._part = part

    @property
    def ThreadingMode(self):
        return self._mode

    @ThreadingMode.setter
    def ThreadingMode(self, value):
        self._mode = value


def test_a_threaded_hole_whose_update_fails_is_removed_again():
    """B32 2026-11-06: 5/5 failures left 孔.1 in the tree with no geometry."""
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(fail_update=True)
    part.Selection = SimpleNamespace(
        Clear=lambda: None, Add=lambda x: None,
        Delete=lambda: (_ for _ in ()).throw(RuntimeError("cannot delete")))
    hole = _Hole(part)
    with pytest.raises(CodedError) as caught:
        PartDesignTools(None)._apply_threading(part, hole, True)
    error = caught.value
    assert error.code == "FEATURE_INCOMPLETE"
    assert error.stage == "execute"
    assert error.modified is True, "it was created and the delete failed"
    assert error.rolled_back is False
    assert "STILL IN THE TREE" in str(error)
    assert "catia_delete_feature" in json.dumps(error.next_actions)


def test_a_threaded_hole_removed_successfully_reports_modified_false():
    from catia_mcp.tools.part_design import PartDesignTools

    part = _Part(fail_update=True)
    part.Selection = SimpleNamespace(Clear=lambda: None, Add=lambda x: None,
                                     Delete=lambda: None)
    hole = _Hole(part)
    with pytest.raises(CodedError) as caught:
        PartDesignTools(None)._apply_threading(part, hole, True)
    error = caught.value
    assert error.modified is False
    assert error.rolled_back is True
    assert "tree is as it was" in str(error)


# ── the sheet furniture the reports measured ───────────────────────────────


def test_seam_hint_is_not_added_back_on_a_surface_patch():
    """The pop was undone two lines later: `skin_note` and `seam_hint` coexisted."""
    from catia_mcp.tools import measurement

    source = pathlib.Path(measurement.__file__).read_text(encoding="utf-8")
    assert "if seam_hint and not boundary:" in source
    assert not re.search(r"^\s+if seam_hint:\n", source, re.MULTILINE)


def test_the_curvature_report_publishes_the_chord_radius_it_asks_you_to_read():
    """"they disagree by the ratio shown" pointed at a number that was not there."""
    from catia_mcp.tools import measurement

    source = pathlib.Path(measurement.__file__).read_text(encoding="utf-8")
    assert '"radius_chord_mm"' in source
    assert "L/(2 sin(f/2))" in source


def test_showing_an_element_inside_a_hidden_set_says_so():
    from catia_mcp.tools import part_design

    source = pathlib.Path(part_design.__file__).read_text(encoding="utf-8")
    assert "hides its contents" in source


def test_the_document_route_does_not_attempt_a_tensor_it_cannot_return():
    from catia_mcp.tools import measurement

    source = pathlib.Path(measurement.__file__).read_text(encoding="utf-8")
    part_inertia = source.split("def _part_inertia", 1)[1].split("\n    def ", 1)[0]
    assert "_tensor_for" not in part_inertia
    assert "not_on_this_route" in part_inertia
