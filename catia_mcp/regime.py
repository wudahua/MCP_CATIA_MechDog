"""The cold/warm vocabulary: one book, one sentence, every route that publishes it.

WHY THIS MODULE EXISTS
----------------------
Four fields - ``after_cold``, the count of parts read for the first time, the
reason each one cost what it did, and the sentence explaining both - were
implemented THREE times (box assembly, inertia assembly, per-part box) and
MISSING from four other branches (both empty-product branches, and both
mass-property part routes). Each acceptance round found one or two of the empty
cells: 0.3.37 found the inertia pair, 0.3.38 found the two empty ones, in the
same release that filled the inertia pair by hand.

That is arithmetic, not bad luck: three fields across seven routes is twenty-one
cells, and every one of them was filled in by a person. So there is one function
now. A route calls :meth:`SessionBooks.describe` with FACTS - how many parts it
read, how many for the first time, why - and gets the same key set back, on the
empty branches too. ``tests/test_route_matrix.py`` walks all seven routes and
asserts the key set and the cost closure, which is the part a human was doing.

TWO NAMES THAT WERE WRONG
-------------------------
``cold_parts`` counted parts whose first reading happened in this call, and the
note beside it said "CATIA builds a part's internal representation on the first
topology read". The 2026-10-29 acceptance run falsified the second half: 26 of
28 parts measured 0.97x-1.13x on their second call, i.e. no slower at all, and
the two that were 21x-26x were STL exports, not builds. A field name that
asserts a cause it does not measure is worse than a missing field, because it
reads as evidence. So:

* the count is ``session_first_parts`` - what this SERVER's book did;
* each part carries ``cold_reason`` - which JOB its first reading was
  (``topology``, ``mesh_export``, ``mass_properties``);
* the note states the bookkeeping and stops claiming a cause.

CROSS-SCOPE DELTAS
------------------
A warm reply used to quote "this call took X ms against Y ms for the previous
warm call on this document". When the previous call was ``component='P05.1'``
(one instance) and this one is the whole assembly (70), the difference is the
scope, not the machine - the 2026-10-29 run printed -67 % for a call that had
simply grown 70x in size. A delta is published only between two calls of the
same scope; a different scope gets a sentence saying so instead of a number.
"""

from __future__ import annotations

from typing import Any

#: The keys every measuring route publishes, empty ones included. The matrix
#: test asserts exactly this set, so a new route cannot ship half of it.
REGIME_KEYS = (
    "regime",
    "after_cold",
    "session_first_parts",
    "parts_total",
    "cold_reasons",
    "elapsed_note",
)

#: ``regime`` values. Three, because "cold or warm" has a third honest answer:
#: an empty product read nothing, so it is neither and the flag must not move.
COLD = "cold"
WARM = "warm"
NOT_MEASURED = "not_measured"

#: ``cold_reason`` values - WHICH JOB the first reading paid for.
#:
#: ``topology``: the vertex/topology sweep. ``mesh_export``: the part's box came
#: from an exported STL, and it is the EXPORT that is charged once, which is why
#: those parts read 21x-26x while a vertex-swept part reads 1.0x. ``mass_properties``:
#: volume / area / centre of gravity, a different internal structure again.
TOPOLOGY = "topology"
MESH_EXPORT = "mesh_export"
MASS_PROPERTIES = "mass_properties"

#: Book names. One per JOB, not one per route: the same part's topology is the
#: same work whether it is read alone or inside an assembly, and its mass
#: properties are the same work either way - but those two jobs do NOT warm up
#: together, so they must not share a book.
BOOK_TOPOLOGY = TOPOLOGY
BOOK_MASS = MASS_PROPERTIES

_REASON_WORDS = {
    TOPOLOGY: "a vertex/topology sweep",
    MESH_EXPORT: "an STL export (a one-off cost, and the reason a mesh-bounded "
                 "part can read 20x on its first box while a vertex-swept one "
                 "reads 1.0x)",
    MASS_PROPERTIES: "a mass-properties read",
}


def _reason_sentence(cold_reasons: dict[str, int]) -> str:
    """``"2 part(s) paid for X, 26 for Y"`` - counted, not asserted."""
    parts = [f"{count} part(s) paid for {_REASON_WORDS.get(reason, reason)}"
             for reason, count in sorted(cold_reasons.items()) if count]
    return "; ".join(parts)


class SessionBooks:
    """What this server has read in this session, kept per JOB.

    Session state, not tool state: two tool objects talking to one CATIA share
    it, because CATIA's internal caches are shared too and a second book would
    report "first time" about a part this server had already read twice.
    """

    def __init__(self) -> None:
        # book -> part key -> why that part's first reading was what it was
        self._seen: dict[str, dict[Any, str]] = {}
        # book -> part key -> {"cold_ms": first reading, "warm_ms": latest}
        self._times: dict[str, dict[Any, dict[str, float]]] = {}
        # book -> was the last measured call of this book a cold one
        self._last_cold: dict[str, bool] = {}
        # (book, document, scope) -> the elapsed_ms of the last warm call
        self._warm: dict[tuple[str, str, tuple], float] = {}

    # ── per-part bookkeeping ────────────────────────────────────────────

    def reset(self) -> None:
        """Forget everything. Tests call this; a server never does."""
        self._seen.clear()
        self._times.clear()
        self._last_cold.clear()
        self._warm.clear()

    def is_new(self, book: str, key: Any) -> bool:
        """True when this part has not been read by this server yet."""
        return key not in self._seen.setdefault(book, {})

    def note(self, book: str, key: Any, reason: str) -> bool:
        """Record a first reading. Returns True when it WAS the first one."""
        seen = self._seen.setdefault(book, {})
        if key in seen:
            return False
        seen[key] = reason
        return True

    def reason_of(self, book: str, key: Any) -> str | None:
        return self._seen.get(book, {}).get(key)

    def time_of(self, book: str, key: Any) -> dict[str, Any]:
        """``cold_ms`` / ``cold_warm_ratio`` for one part, once both are known.

        Empty until the part has been read twice: a ratio of one reading is not a
        ratio, and publishing ``cold_warm_ratio: 1.0`` for a part that has only
        been seen once would read as "no cold cost" instead of "not known yet".
        """
        history = self._times.get(book, {}).get(key) or {}
        if history.get("cold_ms") is None or not history.get("warm_ms"):
            return {}
        return {"cold_ms": history["cold_ms"],
                "cold_warm_ratio": round(history["cold_ms"] / history["warm_ms"], 2)}

    def record_time(self, book: str, key: Any, elapsed_ms: float,
                    first: bool) -> dict[str, Any]:
        """This part's own two regimes, as fields (empty until both are seen).

        The ratio is the part's own ``cold_ms / warm_ms`` and nothing else. It is
        not divided by, multiplied by, or averaged into any other part's: the
        assembly-level figure is a weighted sum over these and moves when the
        composition moves, which is why quoting it as CATIA's factor was wrong
        four releases running.
        """
        history = self._times.setdefault(book, {}).setdefault(key, {})
        if first:
            history["cold_ms"] = elapsed_ms
        else:
            history["warm_ms"] = elapsed_ms
        return self.time_of(book, key)

    # ── the one implementation ─────────────────────────────────────────

    def describe(self, *,
                 book: str,
                 route: str,
                 measured: bool,
                 parts_total: int,
                 session_first: int,
                 cold_reasons: dict[str, int] | None = None,
                 doc_key: str = "",
                 elapsed_ms: float = 0.0,
                 scope: tuple = (),
                 empty_reason: str = "",
                 tail: str = "") -> dict[str, Any]:
        """The six regime fields, for one call, on any route.

        ``measured=False`` is a call that read nothing (an empty product): it is
        neither cold nor warm, ``after_cold`` is null meaning "not applicable",
        and the book is NOT touched - an empty product cannot make the next real
        call look warm.

        ``scope`` identifies what was measured, so a delta is only quoted between
        two calls that measured the same thing. It is opaque here: the routes pass
        the tuple that matters to them (component filter, instance count).

        ``tail`` is a sentence only one route can write - `warm_up=true` exists on
        the bounding box alone - appended inside the same paragraph so a caller
        reading `elapsed_note` still gets one answer, not one answer plus a
        footnote in a field of its own.
        """
        cold_reasons = {name: count for name, count in (cold_reasons or {}).items()
                        if count}
        if not measured:
            return {
                "regime": NOT_MEASURED,
                "after_cold": None,
                "session_first_parts": 0,
                "parts_total": parts_total,
                "cold_reasons": cold_reasons,
                "elapsed_note": (
                    "nothing was read, so this call is neither cold nor warm: "
                    "'regime: not_measured' and 'after_cold: null' both mean 'not "
                    "applicable', and this call does not change what the next one "
                    "on this document reports"
                    + (f". {empty_reason}" if empty_reason else "")),
            }

        is_cold = session_first > 0
        after_cold: bool | None = None
        if not is_cold:
            after_cold = self._last_cold.get(book)
        self._last_cold[book] = is_cold

        warm_key = (book, doc_key, tuple(scope))
        previous = self._warm.get(warm_key) if not is_cold else None
        if not is_cold:
            self._warm[warm_key] = elapsed_ms

        if is_cold:
            noun = "part" if parts_total == 1 else "parts"
            verb = "was" if parts_total == 1 else "were"
            note = (
                f"{session_first} of {parts_total} {noun} here {verb} read by this "
                "SERVER for the first time in this session, which is all "
                "'session_first_parts' counts - it is a fact about this server's "
                "book, NOT a claim that CATIA built anything. What a first reading "
                "costs depends on the job: "
                f"{_reason_sentence(cold_reasons) or 'no reason was recorded'}. "
                "Per part, 'cold_reason' names the job and "
                "'parts[].cold_warm_ratio' "
                "(cold_ms / warm_ms) is that part's own two readings once both "
                "have been seen; no aggregate multiplier is quoted here, and none "
                "should be inferred from the count - most first readings are not "
                "slower at all. Compare this call with this call: read 'parts_ms' "
                "and 'overhead_ms' out of THIS reply (they add up to 'elapsed_ms' "
                "by construction) instead of quoting a number from another "
                "session."
                + (f" {tail}" if tail else ""))
        else:
            note = (
                "every part here had already been read in this session, so this "
                "is the warm number. Two warm calls of one assembly still differ - "
                "and a single small part can swing far more than a big assembly, so "
                "do not read one part's time to three digits."
                + (f" This call took {elapsed_ms} ms against {previous} ms for the "
                   f"previous warm call on this document with the same scope "
                   f"({round(elapsed_ms - previous, 1):+.1f} ms, "
                   f"{(elapsed_ms / previous - 1) * 100:+.1f} %)."
                   if previous else "")
                + ((self._scope_refusal(book, doc_key, scope)
                    or " There is no earlier warm call on this document in this "
                       "session yet, so this is one reading, not a trend.")
                   if not previous and doc_key else "")
                + (" It is also the FIRST call after a cold pass, and across "
                   "versions that has measured both ways, so the direction is not a "
                   "property of this code: read the number as 'not yet the steady "
                   "state', NOT as an upper bound."
                   if after_cold else "")
                + (f" {tail}" if tail else ""))
        return {
            "regime": COLD if is_cold else WARM,
            "after_cold": after_cold,
            "session_first_parts": session_first,
            "parts_total": parts_total,
            "cold_reasons": cold_reasons,
            "elapsed_note": note,
        }

    def _scope_refusal(self, book: str, doc_key: str,
                       scope: tuple) -> str:
        """Say the scopes differ instead of printing a delta between them.

        A -67 % that is really "one instance instead of seventy" reads as a
        performance claim and is not one (reported 2026-10-29, §5.3).
        """
        others = [key[2] for key in self._warm
                  if key[0] == book and key[1] == doc_key and key[2] != tuple(scope)]
        if not others:
            return ""
        return (
            f" The previous warm call on this document measured a different scope "
            f"({_scope_words(others[-1])}, {_count_of(others[-1])} instance(s), "
            f"against {_scope_words(tuple(scope))}, {_count_of(tuple(scope))} "
            "here), so no delta is quoted: a difference between two scopes is the "
            "scope, not the machine.")


def _scope_words(scope: tuple) -> str:
    """``"the whole assembly"`` or ``"component 'P05.1'"``."""
    if not scope:
        return "an unnamed scope"
    if len(scope) >= 1 and scope[0]:
        return f"component '{scope[0]}'"
    return "the whole assembly"


def _count_of(scope: tuple) -> str:
    return str(scope[1]) if len(scope) > 1 else "?"
