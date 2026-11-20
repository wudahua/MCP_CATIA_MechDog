"""0.3.154: withdrawing the one piece of evidence that could not carry it.

0.3.152 concluded "the UI command is not registered - 'Fix' answers unknown
command while 'Update' is accepted", and read that as pointing at the Assembly
Design constraint package (a licence or configuration problem). 0.3.154 measured
the negative control and the conclusion fell apart:

    'Update', 'Fit All In', 'Hide/Show', 'Fix', 'CATAssembly.CreateFix', and
    'ZzzNoSuchCommand' ALL return None, and ALL open the same 未知命令 box.

``Application.StartCommand`` does not validate its argument on this build, so the
box says nothing about whether a command exists. The claim had to go - and the
project had already written the opposite down in ``export.py`` in 0.3.151, which
is what makes this a consistency defect and not just a wrong guess.

These tests pin the withdrawal rather than the prose: the claim may not come
back, the probe that used to make it must ship the control that disproves it,
and the two files that talk about StartCommand must say the same thing.

No CATIA here.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Sentences that were measured false on 2026-11-20 and may not return. The
#: withdrawal itself quotes the claim, so these are the ASSERTING forms only -
#: "read it as X; that is withdrawn" is fine, "X, therefore Y" is not.
WITHDRAWN = (
    # assembly.py, _CONSTRAINT_EXPLAIN
    "are not registered in this session's command set",
    "The Fix UI command is not registered",
    # document.py, _CONSTRAINT_CEILING
    "the Fix command itself is not registered",
    # the inference that followed from it
    "missing the Assembly Design constraint commands",
    "points at the Assembly Design constraint package",
)


def _source(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_withdrawn_claim_is_gone_from_every_file_that_made_it() -> None:
    for name in ("assembly.py", "diagnostics.py", "document.py"):
        text = _source("catia_mcp", "tools", name)
        for phrase in WITHDRAWN:
            assert phrase not in text, (name, phrase)


def test_each_file_says_what_is_left_instead() -> None:
    """A withdrawal without a replacement leaves the reader with nothing."""
    for name in ("assembly.py", "diagnostics.py", "document.py"):
        text = _source("catia_mcp", "tools", name).lower()
        assert ("not established" in text) or ("withdrawn" in text), name


def test_the_probe_ships_the_negative_control_that_disproved_the_claim() -> None:
    text = _source("catia_mcp", "tools", "diagnostics.py")
    # Both names in one loop, so 'Fix' can never be run alone again.
    assert '("Fix", "ZzzNoSuchCommand")' in text
    assert "negative_control" in text


def test_the_probe_publishes_whether_names_are_validated_at_all() -> None:
    text = _source("catia_mcp", "tools", "diagnostics.py")
    for key in ("StartCommand_validates_names", "informative"):
        assert key in text, key


def test_the_ui_command_step_is_not_allowed_to_pass_or_fail() -> None:
    """It is a control, not a reading: None, never True/False.

    A False here would be read as "the command is missing", which is exactly the
    mistake 0.3.152 made; a True would be read as "the command is there".
    """
    text = _source("catia_mcp", "tools", "diagnostics.py")
    assert '"UI command \'Fix\', with a nonsense name as the control"' in text
    assert "True if created else None" in text


def test_the_two_files_that_describe_startcommand_agree() -> None:
    """The 0.3.152 defect was a fact stated correctly in one file and wrongly in
    another. This is the test that would have caught it.

    It matches the CLAIM, not the wording: either file may emphasise it however
    it likes, but both must say that StartCommand does not validate the name.
    """
    import re

    export = _source("catia_mcp", "tools", "export.py")
    diagnostics = _source("catia_mcp", "tools", "diagnostics.py")
    pattern = re.compile(r"does NOT validate", re.IGNORECASE)
    assert pattern.search(export), "export.py lost its note"
    assert pattern.search(diagnostics), "diagnostics.py lost its note"


def test_the_licence_route_is_named_as_closed_rather_than_guessed() -> None:
    """SystemConfiguration has no licence list and there is no nodelock file, so
    'it is a licence problem' cannot be checked from here - and the reply says
    which three routes were tried."""
    text = _source("catia_mcp", "tools", "diagnostics.py")
    for said in ("SystemConfiguration", "nodelock"):
        assert said in text, said
