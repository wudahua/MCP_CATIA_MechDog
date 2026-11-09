"""0.3.89 probe: can an off-surface circle cutter be diagnosed AFTER the failure?

The 2026-11-09 report split a loft by a circle and got nothing but
"UpdateObject 失败". Facts say a failed update can leave the whole document in an
error state, so the measurement may have to happen BEFORE the split - or it may
still work afterwards. This probe answers that with two cases on one patch:

  1  circle lying ON the patch      -> split must succeed (control)
  2  circle 3 mm above the patch    -> split must fail, and the probe then tries
                                       to place a point on that circle and
                                       measure it against the patch

Everything lives in this probe's own folder; only its own documents are closed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402

ROOT = r"F:\catia_probe_v0389"
PATCH = "SP89"
CIRCLE_ON = "C89_ON"
CIRCLE_OFF = "C89_OFF"


def say(line: str = "") -> None:
    print(line, flush=True)


def distance(conn, part, first, second) -> float | None:
    try:
        spa = conn.active_document.GetWorkbench("SPAWorkbench")
        return float(spa.GetMeasurable(first).GetMinimumDistance(second))
    except Exception as error:  # noqa: BLE001
        say(f"    distance failed: {str(error).splitlines()[0][:90]}")
        return None


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    own = os.path.normcase(os.path.abspath(ROOT))
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own):
            say(f"closing leftover {doc.Name}")
            doc.Close()

    path = os.path.join(ROOT, "split89.CATPart")
    if os.path.isfile(path):
        os.remove(path)
    document.execute("catia_new_part", {"name": "SPLIT89", "file_path": path})
    part = conn.get_active_part()

    # A flat 40 x 40 patch on z = 0.
    corners = [(-20, -20, 0), (20, -20, 0), (20, 20, 0), (-20, 20, 0)]
    for index in range(4):
        gsd.execute("catia_gsd_line",
                    {"point1": list(corners[index]),
                     "point2": list(corners[(index + 1) % 4]),
                     "name": f"P89_{index}"})
    gsd.execute("catia_gsd_fill",
                {"boundaries": [f"P89_{i}" for i in range(4)], "name": PATCH})
    part.Update()
    say(f"patch built, area {gsd.execute('catia_gsd_boundary', {'surface': PATCH, 'name': 'PB89'}).splitlines()[0][:80]}")

    # ── 1 the control: a circle that LIES on the patch ──────────────────
    say("")
    say("--- 1. circle on the patch ---")
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 0], "support_plane": "xy", "radius": 6,
                 "name": CIRCLE_ON})
    try:
        reply = gsd.execute("catia_gsd_split",
                            {"element": PATCH, "cutter": CIRCLE_ON,
                             "name": "S89_OK"})
        say(f"  split OK: {reply.splitlines()[0][:120]}")
    except Exception as error:  # noqa: BLE001
        say(f"  split FAILED: {type(error).__name__} {str(error).splitlines()[0][:120]}")

    # ── 2 a circle 3 mm above the patch: the reported case ──────────────
    say("")
    say("--- 2. circle 3 mm above the patch ---")
    gsd.execute("catia_gsd_circle",
                {"center": [0, 0, 3], "support_plane": "xy", "radius": 6,
                 "name": CIRCLE_OFF})
    failed = False
    try:
        reply = gsd.execute("catia_gsd_split",
                            {"element": PATCH, "cutter": CIRCLE_OFF,
                             "name": "S89_BAD"})
        say(f"  split OK (unexpected): {reply.splitlines()[0][:120]}")
    except Exception as error:  # noqa: BLE001
        failed = True
        say(f"  split FAILED: {type(error).__name__} {str(error).splitlines()[0][:160]}")

    if not failed:
        return

    # ── 3 after that failure, can the gap still be measured? ────────────
    say("")
    say("--- 3. measuring after the failed update ---")
    circle = part.FindObjectByName(CIRCLE_OFF)
    patch = part.FindObjectByName(PATCH)
    if circle is None or patch is None:
        say("  could not resolve the elements")
        return
    circle_ref = part.CreateReferenceFromObject(circle)
    patch_ref = part.CreateReferenceFromObject(patch)
    say(f"  whole-cutter min distance: "
        f"{distance(conn, part, circle_ref, patch_ref)}")

    hsf = gsd._factory()
    geoset = gsd._get_geoset()
    probes = []
    for ratio in (0.0, 0.25, 0.5, 0.75):
        try:
            point = hsf.AddNewPointOnCurveFromPercent(circle_ref, ratio, True)
            geoset.AppendHybridShape(point)
            part.UpdateObject(point)
            probes.append((ratio, point))
            say(f"  point at {ratio:.2f}: created")
        except Exception as error:  # noqa: BLE001
            say(f"  point at {ratio:.2f}: FAILED {str(error).splitlines()[0][:90]}")

    for ratio, point in probes:
        point_ref = part.CreateReferenceFromObject(point)
        say(f"  point at {ratio:.2f} -> distance to the patch: "
            f"{distance(conn, part, point_ref, patch_ref)}")

    for _ratio, point in probes:
        try:
            sel = conn.active_document.Selection
            sel.Clear()
            sel.Add(point)
            sel.Delete()
        except Exception:  # noqa: BLE001
            pass
    say(f"  probes deleted, {len(probes)} were placed")

    conn.active_document.Save()
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = os.path.normcase(os.path.abspath(str(doc.FullName)))
        except Exception:  # noqa: BLE001
            continue
        if full.startswith(own):
            doc.Close()


if __name__ == "__main__":
    main()
