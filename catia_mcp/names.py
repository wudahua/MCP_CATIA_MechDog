"""Names a caller typed into a JSON string, and the one mistake that outlives it.

CATIA takes a name as given: a view called ``\\u4e3b\\u89c6\\u56fe`` is created
with the backslashes in it, appears in the tree that way, and the tool echoes the
same six characters back - so nothing in the exchange looks wrong except the
picture. Only the caller can tell an escape that was never decoded from a name
somebody wanted.
"""

from __future__ import annotations

import re

# A literal backslash followed by u and four hex digits: what a JSON string
# carries when the escape is not decoded, e.g. "\u4e3b" arriving as six
# characters. Windows paths contain single backslashes too ("F:\users\..."), so
# the match requires the full escape shape.
_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def unescaped_name_note(name: str) -> str:
    """A sentence when ``name`` still holds undecoded ``\\uXXXX`` escapes.

    Reported 2026-11-07: a caller passed the view name as the literal six
    characters ``\\u4e3b\\u89c6\\u56fe`` (the escape of 主视图), the server created
    a view with a backslash in its name, and every later error echoed the
    backslashes back - so the mistake cost a whole drawing round trip. The tool
    cannot refuse such a name (somebody may really want a backslash), so it says
    what it thinks and lets the call through.

    Empty string when the name is clean, so callers can append it unconditionally.
    """
    if not name or "\\" not in name:
        return ""
    codes = _ESCAPE.findall(name)
    if not codes:
        return ""
    try:
        decoded = "".join(chr(int(code, 16)) for code in codes)
    except (ValueError, OverflowError):  # pragma: no cover - hex is range-checked
        return ""
    return (f" ⚠ NOTE: the name holds {len(codes)} literal '\\uXXXX' sequence(s), "
            f"which is an escape that was never decoded - those codes spell "
            f"'{decoded}'. CATIA created it with the backslashes in the name; if "
            f"you meant '{decoded}', pass those characters themselves.")
