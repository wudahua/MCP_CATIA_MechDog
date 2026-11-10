"""Which elements of a part fail to update on their own.

A failed update is the most expensive error this package produces: CATIA answers
"方法 Update 失败" and nothing else, so the caller is left to bisect the tree by
hand. ``catia_update_part`` has walked the body's features since 0.3.24 to name a
culprit - but only the body's, and that is where it stopped.

The 2026-11-10 report is the cost: deleting two dead Part Design features left the
part still failing, because the real culprit was ``图案_投影`` - a GSD element in a
geometrical set - and the probe never entered HybridBodies. The reply said
"features that depended on it are now in error" and named none of them; the
reporter found it by deleting elements one at a time.

So the walk covers both trees, and returns every failing element rather than the
first: a part can carry two independent casualties, and naming one of them sends
the caller round the loop again.
"""
from __future__ import annotations

from typing import Any


def _hybrid_sets(container: Any) -> list[Any]:
    try:
        return [container.Item(i)
                for i in range(1, int(container.Count) + 1)]
    except Exception:  # noqa: BLE001 - no sets, or unreadable
        return []


def iter_tree(part: Any):
    """Yield ``(kind, owner_name, element)`` for every updatable element.

    Bodies first (their ``Shapes`` are the Part Design features), then every
    geometrical set depth-first - sets nest, and the culprit of the 2026-11-10
    report sat two levels down.
    """
    try:
        bodies = _hybrid_sets(part.Bodies)
    except Exception:  # noqa: BLE001 - not a part
        bodies = []
    for body in bodies:
        try:
            shapes = body.Shapes
        except Exception:  # noqa: BLE001
            continue
        for i in range(1, int(shapes.Count) + 1):
            try:
                element = shapes.Item(i)
            except Exception:  # noqa: BLE001
                continue
            yield "feature", str(getattr(body, "Name", "?")), element

    try:
        sets = _hybrid_sets(part.HybridBodies)
    except Exception:  # noqa: BLE001 - no sets in this document
        sets = []
    stack = [(s, 0) for s in sets]
    while stack:
        hybrid, depth = stack.pop()
        if depth > 6:                      # same cap as the visibility walk
            continue
        for element in _hybrid_sets(getattr(hybrid, "HybridShapes", None)):
            yield "element", str(getattr(hybrid, "Name", "?")), element
        stack.extend((nested, depth + 1)
                     for nested in _hybrid_sets(getattr(hybrid, "HybridBodies",
                                                        None)))


def failing_elements(part: Any, limit: int = 8) -> list[str]:
    """Names of the elements that fail ``UpdateObject`` on their own.

    Only call this after a whole-part update has already failed: it is a full
    walk plus one COM call per element (measured 2026-11-03: the equivalent
    per-name walk over 91 elements took 49.9 s, which is why the caller does not
    get to run this on the happy path).

    Empty list means "no single element reproduces it" - the failure is a
    relation between features, which this probe cannot name.
    """
    culprits: list[str] = []
    for _kind, owner, element in iter_tree(part):
        try:
            part.UpdateObject(element)
        except Exception:  # noqa: BLE001 - this is the culprit
            name = str(getattr(element, "Name", "") or "?")
            culprits.append(name if name != "?" else f"an unnamed element in {owner!r}")
            if len(culprits) >= limit:
                break
        # An element that updates alone may still have been *left* in error by
        # the failed whole-part update; only the failure is evidence, so nothing
        # else is recorded here.
    return culprits


def describe(culprits: list[str]) -> str:
    """One clause naming the culprits, or '' when there are none."""
    if not culprits:
        return ""
    if len(culprits) == 1:
        return (f"'{culprits[0]}' is the element that fails on its own")
    shown = ", ".join(f"'{name}'" for name in culprits[:6])
    more = "" if len(culprits) <= 6 else f" and {len(culprits) - 6} more"
    return f"{len(culprits)} elements fail on their own: {shown}{more}"
