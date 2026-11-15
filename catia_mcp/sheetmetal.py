"""Generative Sheetmetal Design: the part of it this automation CAN reach.

THE MEASUREMENT THIS MODULE IS BUILT ON
    B30, 2026-11-15, ``scripts/b30_v0434_probe_sheetmetal.py``:

    ``Part.SheetMetalFactory``            exists - 23 members
      of which real                       1: ``CreateSheetMetalParameters()``
    what that call returns                23 members, one of them ``GetThickness()``
    ``part.SheetMetalParameters``         the SAME object, readable only AFTER the
                                          call above has created it - before that
                                          it answers "方法 SheetMetalParameters 失败"
    ``part.Parameters``                   <-- THE ROUTE THAT WORKS. The whole
                                          sheet-metal setup is in here as ordinary
                                          parameters, and ``Parameter.Value`` IS
                                          writable.

    Measured on one fresh part, after ``CreateSheetMetalParameters()`` (defaults
    on the left, what happened on the right):

        钣金参数.1\\线宽                2.0        written 1.5  -> GetThickness() reads 1.5
        钣金参数.1\\弯曲半径            4.0        written 1.905, read back
        钣金参数.1\\K 因子              0.40051    REFUSED ("方法 Value 失败") while the
                                                  DIN rule is on; with
                                                  ...\\DIN 一般公式\\Activity = False it
                                                  takes 0.5
        钣金参数.1\\拐角止裂槽半径长度  1.0        written 0.8
        钣金参数.1\\拐角止裂槽轴长度    2.0        written 1.5
        钣金参数.1\\Minimum Bend Radius  0.0        written 1.0

    So the 2026-11-03 report's item 1 ("read/write thickness, K factor, default
    bend radius, relief") and item 2 ("a new part that IS a sheet-metal part")
    are both reachable, and an earlier note in this project saying the parameters
    "can be read and nothing can be built from them" was half wrong: the reader
    of that note was ``SheetMetalParameters`` (which is indeed read-only), and it
    never looked at ``part.Parameters``.

    What does NOT exist, and this is why there is no wall / flange / bend /
    unfold / flat-pattern tool: the factory has no ``AddNew*`` of any kind, and
    the parameter object adds no setter. That is an interface fact, not a licence
    question - loading the workbench cannot add methods to an interface.

WHY A NAME MATCH AND NOT AN INDEX
    The leaves are LOCALISED on a Chinese build ('线宽', '弯曲半径', 'K 因子') but
    not consistently - 'Minimum Bend Radius' and 'CornerReliefRadius' stay
    English on the same document. An index would break on the next language, so
    each property carries the spellings it is known by, and a property that
    matches nothing is REPORTED missing (with its siblings' names) rather than
    skipped or guessed. Guessing here means writing 1.5 into a parameter that
    happens to be called something similar, on a part somebody is about to cut
    metal for.
"""

from __future__ import annotations

from typing import Any

#: property key -> the leaf spellings it is known by, normalized by ``_normalise``.
#: The Chinese spellings are the ones measured on B30; the English ones are what
#: the same build uses for two of the neighbours, so an English install is
#: covered for the leaves that keep their English name.
PROPERTIES: dict[str, tuple[str, ...]] = {
    "thickness": ("线宽", "Thickness", "Sheet Thickness", "Part Thickness"),
    "bend_radius": ("弯曲半径", "Bend Radius", "Default Bend Radius", "BendRadius"),
    "k_factor": ("K 因子", "K Factor", "KFactor", "K-Factor"),
    "relief_radius": ("拐角止裂槽半径长度", "Corner Relief Radius Length"),
    "relief_axis": ("拐角止裂槽轴长度", "Corner Relief Axis Length"),
    "minimum_bend_radius": ("Minimum Bend Radius",),
}

#: What has to be switched off before a parameter will accept a value. On B30 the
#: K factor is computed by the standard's own formula, and ``Value`` raises
#: "方法 Value 失败" until that rule's ``Activity`` is False.
ACTIVITY = "Activity"


def _segments(name: Any) -> list[str]:
    """``'Doc\\钣金参数.1\\线宽'`` -> ``['Doc', '钣金参数.1', '线宽']``."""
    return [part for part in str(name).split("\\") if part]


def _normalise(leaf: Any) -> str:
    """Case-, space- and hyphen-insensitive: 'K 因子' == 'KFactor' == 'k-factor'."""
    text = str(leaf).replace("\u3000", " ").strip().casefold()
    return "".join(character for character in text
                   if character not in " _-\t")


#: normalized spelling -> property key, built once.
_BY_SPELLING: dict[str, str] = {
    _normalise(spelling): key
    for key, spellings in PROPERTIES.items()
    for spelling in spellings
}


def property_of(leaf: Any) -> str | None:
    """Which sheet-metal property this leaf name is, or None."""
    return _BY_SPELLING.get(_normalise(leaf))


def locate(names: list[str]) -> dict[str, Any]:
    """Find the sheet-metal parameter container and each property inside it.

    Returns::

        {"container":     'Doc\\钣金参数.1'  or None,
         "container_leaf": '钣金参数.1',
         "found":         {"thickness": 'Doc\\钣金参数.1\\线宽', ...},
         "missing":       ["k_factor", ...]}

    The container is the one that holds the MOST recognised properties, so a
    stray parameter elsewhere called 'Thickness' cannot pull the lookup out of
    the sheet-metal set. When nothing matches at all, ``container`` is None and
    every property is missing - which the caller turns into "create the
    parameters first" rather than into an empty success.
    """
    holders: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for name in names:
        segments = _segments(name)
        if len(segments) < 2:
            continue
        container, leaf = "\\".join(segments[:-1]), segments[-1]
        key = property_of(leaf)
        if key is None:
            continue
        if container not in holders:
            holders[container] = {}
            order.append(container)
        holders[container].setdefault(key, name)
    if not holders:
        return {"container": None, "container_leaf": None, "found": {},
                "missing": list(PROPERTIES)}
    best = max(order, key=lambda item: (len(holders[item]), -order.index(item)))
    found = holders[best]
    return {
        "container": best,
        "container_leaf": _segments(best)[-1],
        "found": found,
        "missing": [key for key in PROPERTIES if key not in found],
    }


def activity_switches(names: list[str], container: str) -> list[str]:
    """Every ``<container>\\...\\Activity`` parameter - the rules that lock a value."""
    prefix = container + "\\"
    return [name for name in names
            if str(name).startswith(prefix)
            and _normalise(_segments(name)[-1]) == _normalise(ACTIVITY)]


def same_value(asked: Any, read: Any) -> bool:
    """Did the write land? Compared as READ BACK, never as "no exception"."""
    try:
        wanted, got = float(asked), float(read)
    except (TypeError, ValueError):
        return str(asked) == str(read)
    return abs(got - wanted) <= max(1e-9, abs(wanted) * 1e-6)


def reading(value: Any) -> Any:
    """A parameter Value as something JSON can carry and a person can read."""
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return str(value)
