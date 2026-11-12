"""0.3.101 acceptance: the guard that only worked in its own session.

The 2026-11-11 report measured two things this version had to answer:

  1  a sketch that came back with a REOPENED file reported a projection length
     with no share and no warning - the guard depended on the session record;
  2  a refused engrave left the point-on-curve element in the tree and then said
     "Nothing this call created was left behind", while catia_update_part named
     that element as the blocker (2/2).

Everything runs in this script's own folder. The reopen is a real Save/Close/
Open: the session record is emptied by hand because this one process both draws
and reopens, which a user never does.
"""
from __future__ import annotations

import math
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0401")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []
RADIUS = 40.0


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for i in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(i)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass
    if os.path.isdir(ROOT):
        for name in os.listdir(ROOT):
            if name.lower().endswith(".catpart"):
                try:
                    os.remove(os.path.join(ROOT, name))
                except OSError:
                    pass


def tree_elements(part) -> list[str]:
    names: list[str] = []

    def walk(container, depth: int) -> None:
        if depth > 4:
            return
        try:
            shapes = container.HybridShapes
            for i in range(1, int(shapes.Count) + 1):
                names.append(str(shapes.Item(i).Name))
        except Exception:  # noqa: BLE001
            pass
        try:
            sets = container.HybridBodies
            for i in range(1, int(sets.Count) + 1):
                walk(sets.Item(i), depth + 1)
        except Exception:  # noqa: BLE001
            pass

    try:
        for i in range(1, int(part.HybridBodies.Count) + 1):
            walk(part.HybridBodies.Item(i), 1)
    except Exception:  # noqa: BLE001
        pass
    return names


def build_solid(gsd) -> None:
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 0, "name": "B0"})
    gsd.execute("catia_gsd_circle", {"center": "B0", "support_plane": "xy",
                                     "radius": RADIUS, "name": "B_lo"})
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 100, "name": "B1"})
    gsd.execute("catia_gsd_circle", {"center": "B1", "support_plane": "xy",
                                     "radius": RADIUS, "name": "B_hi"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["B_lo", "B_hi"], "name": "Wall"})
    gsd.execute("catia_gsd_close_surface", {"surface": "Wall"})


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, gsd = DocumentTools(conn), SketcherTools(conn), GSDTools(conn)
    pd = PartDesignTools(conn)

    say("0.3.101 acceptance - the guard after a reopen, and the point left behind")
    say("=" * 78)

    # ── 1: a sketch read back from disk ───────────────────────────────
    path = os.path.join(ROOT, "reopen_0401.CATPart")
    docs.execute("catia_new_part", {"file_path": path})
    build_solid(gsd)
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -45})
    # 40 x 10 -> 100.000 mm of outline, four curves
    sk.execute("catia_sketch_strokes",
               {"strokes": [[[0, 0], [40, 0], [40, 10], [0, 10], [0, 0]]]})
    sk.execute("catia_close_sketch", {})
    stroke = str(conn.last_sketch.Name)
    say(f"drew '{stroke}' (40 x 10 rectangle, 100.000 mm by hand)")

    conn.active_document.Save()
    conn.active_document.Close()
    conn.app.Documents.Open(path)
    part = conn.get_active_part()
    if hasattr(conn, "sketch_outlines"):
        conn.sketch_outlines.clear()
    say(f"reopened, session record emptied "
        f"({len(getattr(conn, 'sketch_outlines', {}) or {})} entries)")

    measured = gsd._sketch_outline_length_mm(stroke)
    total, read, unread = measured if measured else (0.0, 0, 0)
    check(1, "a reopened sketch's own curves can be measured",
          measured is not None and abs(total - 100.0) < 0.05,
          f"{total:.4f} mm from {read} curves, {unread} unread (hand 100.000)")

    reply = gsd.execute("catia_gsd_project",
                        {"element": stroke, "support": "Wall", "name": "P_after"})
    flat = " ".join(str(reply).split())
    share = re.search(r"([\d.]+) % of the ([\d.]+) mm", flat)
    check(2, "the projection reports a share even cross-session",
          share is not None, share.group(0) if share else flat[:160])
    check(3, "...and no longer says the length could not be read",
          "could not be read" not in flat,
          "the 2026-11-11 reply said exactly that")
    check(4, "the reply names where the source length came from",
          "sketch's" in flat, flat[:120])

    # ── 2: a refused engrave takes its point back ─────────────────────
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "orphan_0401.CATPart")})
    closed = [[12 * math.cos(2 * math.pi * i / 24),
               12 * math.sin(2 * math.pi * i / 24), 0.0] for i in range(24)]
    gsd.execute("catia_gsd_spline", {"points": closed, "closed": True,
                                     "name": "ClosedCurve"})
    part = conn.get_active_part()
    before = tree_elements(part)
    error = None
    try:
        gsd.execute("catia_surface_engrave",
                    {"curve": "ClosedCurve", "depth": 0.4})
    except Exception as caught:  # noqa: BLE001
        error = caught
    code = getattr(error, "code", None)
    text = " ".join(str(error).split())
    left = [n for n in tree_elements(part) if n not in before]
    check(5, "a closed spline is refused with the documented code",
          code == "ENGRAVE_CURVE_UNPARAMETRISABLE", str(code))
    check(6, "...and the point it had already added is gone",
          not [n for n in left if "_at0.5" in n], f"left: {left or 'nothing'}")
    check(7, "...so the part still updates",
          _updates(part), "part.Update()")
    check(8, "the reply does not claim a leftover it cannot see",
          "could not be removed" not in text.lower()
          and "COULD NOT BE REMOVED" not in text,
          "no stranded-point sentence")

    # ── 3: an OPEN guide is allowed - the old ceiling said otherwise ──
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, "open_0401.CATPart")})
    build_solid(gsd)
    gsd.execute("catia_gsd_point", {"x": 0, "y": -RADIUS, "z": 10, "name": "V1"})
    gsd.execute("catia_gsd_point", {"x": 0, "y": -RADIUS, "z": 60, "name": "V2"})
    gsd.execute("catia_gsd_line", {"point1": "V1", "point2": "V2",
                                   "name": "VertOn"})
    part = conn.get_active_part()
    body = conn.get_active_part_body()
    before_volume = pd._body_volume_mm3(part, body)
    groove = gsd.execute("catia_surface_engrave",
                         {"curve": "VertOn", "depth": 0.5})
    after_volume = pd._body_volume_mm3(part, body)
    removed = (None if before_volume is None or after_volume is None
               else before_volume - after_volume)
    predicted = math.pi * 0.5 ** 2 * 50.0 / 2.0
    check(9, "an OPEN guide engraves (the ceiling used to forbid it)",
          removed is not None and removed > 0.9 * predicted,
          f"removed {removed:.4f} mm3 of a predicted {predicted:.3f}"
          if removed is not None else "volume unreadable")
    check(10, "...and the reply states the groove's own numbers",
          "deep groove over" in " ".join(str(groove).split()),
          " ".join(str(groove).split())[:120])

    # ── 4: the ceilings carry the corrections ─────────────────────────
    from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS

    engrave_ceiling = next(e for e in _CAPABILITY_CEILINGS
                           if "engraving" in e["member"])
    check(11, "the ceiling now allows an open guide",
          "GUIDE MAY BE OPEN" in engrave_ceiling["detail"], "measured 2026-11-11")
    check(12, "the ceiling lists which curves carry the parameter",
          "SKETCH outline projected onto a face YES" in engrave_ceiling["detail"],
          "matrix, not examples")
    check(13, "the reopen failure is published as measured but unexplained",
          any(e["status"] == "MEASURED_UNEXPLAINED" for e in _CAPABILITY_CEILINGS),
          "no cause claimed")

    say("")
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {label}  {detail}")
    say(f"documents left open under {ROOT}")


def _updates(part) -> bool:
    try:
        part.Update()
        return True
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    main()
