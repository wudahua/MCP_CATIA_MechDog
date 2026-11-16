"""A warm note that changes shape when the clock cannot resolve the call.

Found in CI while releasing 0.3.142 (commit 732a0c6): the Python 3.10 runner went
red while 3.12 passed, on `test_a_repeat_warm_call_quotes_this_sessions_difference`
- the same class of single-sided failure this repository has met before, and the
same root cause: a value that is legitimately ZERO treated as absent.

`SessionBooks.describe` has two warm branches. When the previous warm reading was
resolvable it says "… for the previous warm call on this document with the same
scope (…)"; when it was 0 ms - below the timer's resolution, which is what a fast
runner produces - the sentence became "The previous warm call on this document
… came back as 0 ms", i.e. the phrase a caller (and the test) looks for was
dropped, and only the machine's speed decided which sentence appeared.

The zero case still refuses to quote a percentage (a ratio against a zero
reading is a division by zero dressed as a measurement), but it now names the
previous call in the SAME WORDS.
"""
from __future__ import annotations

from catia_mcp.regime import SessionBooks


def _note(books: SessionBooks, elapsed: float, first: int = 0) -> str:
    return books.describe(book="assembly_box", route="box.assembly", measured=True,
                          parts_total=1, session_first=first, doc_key="ASM",
                          elapsed_ms=elapsed, scope=("vertices",))["elapsed_note"]


def test_a_zero_reading_names_the_previous_call_in_the_same_words():
    books = SessionBooks()
    _note(books, 12.0, first=1)      # cold
    _note(books, 8.0)                # first warm: nothing to compare with
    _note(books, 0.0)                # warm, below the clock - this is the 0 ms
    note = _note(books, 5.0)         # warm, previous reading was 0
    assert "for the previous warm call on this document" in note, note
    assert "equal to the clock" in note, note
    assert "%" not in note, "a percentage against a 0 ms reading is not a ratio"


def test_a_resolved_reading_still_quotes_the_difference():
    books = SessionBooks()
    _note(books, 12.0, first=1)
    _note(books, 8.0)
    note = _note(books, 10.0)
    assert "for the previous warm call on this document" in note
    assert "+2.0 ms" in note and "+25.0 %" in note
    assert "equal to the clock" not in note


def test_no_previous_call_is_not_a_zero_reading():
    books = SessionBooks()
    _note(books, 12.0, first=1)
    note = _note(books, 8.0)
    assert "no earlier warm call on this document" in note
    assert "equal to the clock" not in note
