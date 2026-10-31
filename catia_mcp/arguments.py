"""Spellings a tool accepts, in ONE place, so the schema cannot disagree with the code.

The defect this module closes (reported 2026-10-31, P2-A): `_pick(arguments,
"element1", "elem1_name", "element1_name", "elem1")` accepted four spellings while
the tool's inputSchema declared one or two of them. That was harmless while the
server trusted every argument, and became a refusal the moment 0.3.43 started
rejecting keys a tool does not declare - so `catia_measure_distance` answered
`UNKNOWN_ARGUMENT` for `elem1`, a spelling its own description lists as accepted:

    "Aliases accepted: elem1_name, element1_name, elem1."

The regression is the one this project keeps fixing in a new place: the code knew
something the declaration did not. Listing the names twice would fix today's four
tools and re-open the same gap on the fifth, so the list lives here and both sides
read it - `_pick(arguments, *ELEMENT1_NAMES)` and
`**alias_properties(ELEMENT1_NAMES, role="the first element", ...)`.
"""

from __future__ import annotations

from typing import Any

# The first name is the one the schema's `required` list and every document use;
# the rest are spellings callers have used since before the schemas agreed.
ELEMENT1_NAMES = ("element1", "elem1_name", "element1_name", "elem1")
ELEMENT2_NAMES = ("element2", "elem2_name", "element2_name", "elem2")


def alias_properties(names: tuple[str, ...], *,
                     role: str,
                     description: str | None = None) -> dict[str, Any]:
    """Schema properties for every spelling in ``names``.

    The primary spelling carries the real description; each alias says it is an
    alias and why it is declared at all, because a property named ``elem1`` with
    no explanation reads like a second, different argument.
    """
    if not names:
        raise ValueError("alias_properties needs at least one name")
    primary, *rest = names
    properties: dict[str, Any] = {
        primary: {"type": "string", "description": description or role},
    }
    for alias in rest:
        properties[alias] = {
            "type": "string",
            "description": (
                f"Alias of '{primary}' ({role}). Declared rather than only read: "
                "the server refuses an argument a tool does not declare, so a "
                "spelling the tool accepts has to be in here."),
        }
    return properties
