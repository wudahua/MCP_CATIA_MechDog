"""catia_gsd_splines on B30 - one fresh document per case.

A  three closed 18-point rings in ONE call, then a solid loft from them
B  a mid-chain refusal: entry 2 is broken, entries 1 and 3 are legal
C  two entries with the same name: refused before anything is built
D  a closed ring whose last point repeats the first: 21 sent, 20 on the curve

    .venv/Scripts/python.exe scripts/b30_v0423_probe_splines.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0423")
ROOT = os.path.join(_PROBE_ROOT, "splines")


def say(line: str = "") -> None:
    print(line, flush=True)


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        document = conn.documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def fresh(conn, documents, name):
    path = os.path.join(ROOT, f"{name}.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, conn.documents.Count + 1):
        if conn.documents.Item(index).Name == f"{name}.CATPart":
            conn.documents.Item(index).Activate()


def ring(z: float, radius: float, count: int = 18):
    return [[round(radius * math.cos(2 * math.pi * i / count), 4),
             round(radius * math.sin(2 * math.pi * i / count), 4), z]
            for i in range(count)]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    part_design = PartDesignTools(conn)

    # ── A: three rings, one call, then a loft ────────────────────────────
    say("")
    say("── A: three 18-point rings in one call, then a solid loft ──")
    fresh(conn, documents, "SplinesA")
    entries = [{"name": "S01", "points": ring(0, 10), "closed": True},
               {"name": "S02", "points": ring(30, 14), "closed": True},
               {"name": "S03", "points": ring(60, 6), "closed": True}]
    try:
        say("  " + gsd.execute("catia_gsd_splines", {"entries": entries}))
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:400])
    part = conn.get_active_part()
    for name in ("S01", "S02", "S03"):
        say(f"  {name} in tree: {part.FindObjectByName(name) is not None}")
    reply = part_design.execute("catia_solid_loft",
                                {"sections": ["S01", "S02", "S03"], "name": "Cone"})
    say("  " + " ".join(str(reply).split())[:200])
    volume = part_design._body_volume_mm3(part, part.MainBody)
    say(f"  volume {volume:.1f} mm3" if volume is not None else "  volume unreadable")

    # ── B: entry 2 is broken ─────────────────────────────────────────────
    say("")
    say("── B: entry 2 has one point - entries 1 and 3 must survive ──")
    fresh(conn, documents, "SplinesB")
    entries = [{"name": "T01", "points": ring(0, 10), "closed": True},
               {"name": "T02", "points": [[0, 0, 10]], "closed": True},
               {"name": "T03", "points": ring(30, 12), "closed": True}]
    try:
        say("  " + gsd.execute("catia_gsd_splines", {"entries": entries})[:300])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED code=" + str(getattr(error, "code", "-"))
            + " " + " ".join(str(error).split())[:300])
        say("  data=" + str(getattr(error, "data", None)))
    part = conn.get_active_part()
    say("  tree has T01: " + str(part.FindObjectByName("T01") is not None)
        + ", T03: " + str(part.FindObjectByName("T03") is not None))

    # ── C: the same name twice ───────────────────────────────────────────
    say("")
    say("── C: two entries named the same ──")
    fresh(conn, documents, "SplinesC")
    entries = [{"name": "X1", "points": ring(0, 10), "closed": True},
               {"name": "X1", "points": ring(30, 12), "closed": True}]
    try:
        say("  " + gsd.execute("catia_gsd_splines", {"entries": entries})[:200])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED code=" + str(getattr(error, "code", "-"))
            + " " + " ".join(str(error).split())[:200])
    part = conn.get_active_part()
    say("  X1 in tree: " + str(part.FindObjectByName("X1") is not None))

    # ── E: the same coordinates twice - does CATIA refuse one mid-chain? ─
    say("")
    say("── E: entry 2 repeats a point, entry 1 is fine ──")
    fresh(conn, documents, "SplinesE")
    twice = [[0, 0, 0], [0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]
    entries = [{"name": "U01", "points": ring(0, 10), "closed": True},
               {"name": "U02", "points": twice, "closed": False}]
    try:
        say("  " + gsd.execute("catia_gsd_splines", {"entries": entries})[:300])
    except Exception as error:  # noqa: BLE001
        say("  REFUSED code=" + str(getattr(error, "code", "-"))
            + " " + " ".join(str(error).split())[:260])
        say("  data=" + str(getattr(error, "data", None)))
    part = conn.get_active_part()
    say("  tree has U01: " + str(part.FindObjectByName("U01") is not None))

    # ── D: a closed ring that repeats its first point ────────────────────
    say("")
    say("── D: 21 points, last repeats the first, closed ──")
    fresh(conn, documents, "SplinesD")
    points = ring(0, 10) + [ring(0, 10)[0]]
    say(f"  sent {len(points)} point(s)")
    try:
        say("  " + gsd.execute("catia_gsd_splines",
                               {"entries": [{"name": "R01", "points": points,
                                             "closed": True}]}))
    except Exception as error:  # noqa: BLE001
        say("  REFUSED " + " ".join(str(error).split())[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
