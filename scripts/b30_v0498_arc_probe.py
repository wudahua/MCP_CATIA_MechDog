"""B30 probe for 0.3.161 - the 180-degree cap and the arc+line profile.

Physical, not arithmetic: a capsule slot padded to 10 mm must have the volume of
a capsule, and a profile drawn with catia_sketch_arc once and catia_sketch_profile
once must be accepted by catia_pad without allow_open_curves.

  A  the report's own arc (R6.142572, ends 180.0000023 deg apart, ccw) bulges
     OUTWARD, and the capsule built from it has the analytic volume
  B  the same capsule with direction='cw' is the OTHER half - the arc apex flips
  C  an arc plus a profile whose ends are 2e-06 mm apart is a closed profile
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

# The report's cap, to the digit.
CX, CY, R = 211.171943, 18.704630, 6.142572
P1 = (207.648709, 23.736330)
P2 = (214.695178, 13.672929)
LENGTH = 125.0

results: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def capsule():
    """(C, O, p1, p2, q1, q2, direction at O) for the report's cap."""
    # p2 - p1 is a diameter, so it is PERPENDICULAR to the slot's axis.
    dx, dy = P2[0] - P1[0], P2[1] - P1[1]
    chord = math.hypot(dx, dy)
    ux, uy = dy / chord, -dx / chord          # rotate the chord by -90 degrees
    # The cap at C bulges along -u (the report's apex is at -R*u from C).
    if (CX + R * math.cos(math.atan2(P1[1] - CY, P1[0] - CX) + math.pi / 2)
            - CX) * ux + (CY + R * math.sin(
                math.atan2(P1[1] - CY, P1[0] - CX) + math.pi / 2) - CY) * uy > 0:
        ux, uy = -ux, -uy
    other = (CX + LENGTH * ux, CY + LENGTH * uy)
    q1 = (P1[0] + LENGTH * ux, P1[1] + LENGTH * uy)
    q2 = (P2[0] + LENGTH * ux, P2[1] + LENGTH * uy)
    # Which way round the far cap bulges along +u.
    a1 = math.atan2(q2[1] - other[1], q2[0] - other[0])
    a2 = math.atan2(q1[1] - other[1], q1[0] - other[0])
    best = None
    for direction, sweep in (("ccw", (a2 - a1) % (2 * math.pi)),
                             ("cw", 2 * math.pi - ((a2 - a1) % (2 * math.pi)))):
        start = a1 if direction == "ccw" else a2
        apex = (other[0] + R * math.cos(start + sweep / 2),
                other[1] + R * math.sin(start + sweep / 2))
        along = ((apex[0] - other[0]) * ux + (apex[1] - other[1]) * uy) / R
        if best is None or along > best[0]:
            best = (along, direction)
    return other, (q1, q2), best[1]


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0498")
    os.makedirs(root, exist_ok=True)

    def fresh(name: str) -> str:
        path = os.path.join(root, name)
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        return path

    def volume() -> float | None:
        try:
            data = payload(measure.execute("catia_get_inertia", {}))
        except Exception as error:  # noqa: BLE001
            say(f"    (volume unreadable: {error})")
            return None
        values = data.get("data", data) if isinstance(data, dict) else {}
        return values.get("volume_mm3")

    other, (q1, q2), far_direction = capsule()
    say(f"  slot: C=({CX}, {CY}) R{R} other=({other[0]:.6f}, {other[1]:.6f}) "
        f"far cap bulges {far_direction}")
    analytic_area = 2 * R * LENGTH + math.pi * R * R
    analytic = analytic_area * 10
    say(f"  analytic capsule: area {analytic_area:.4f} mm2, volume {analytic:.4f} mm3")

    # ── A. the report's arc, ccw, inside a real capsule ──────────────────
    say("\n== A the report's 180-degree cap, direction=ccw")
    path = fresh("CAP0498.CATPart")
    docs.execute("catia_new_part", {"name": "CAP0498", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "CAP"})
    reply = str(sk.execute("catia_sketch_arc", {
        "cx": CX, "cy": CY, "radius": R,
        "p1": list(P1), "p2": list(P2), "direction": "ccw"}))
    say("  " + reply[:400])
    check("A the near cap bulges outward (the report's expected apex)",
          "bulge apex (206.1" in reply, reply.split("bulge apex")[-1][:40])
    # The far cap, the two flats.
    sk.execute("catia_sketch_line", {"x1": P2[0], "y1": P2[1],
                                     "x2": q2[0], "y2": q2[1]})
    sk.execute("catia_sketch_line", {"x1": q1[0], "y1": q1[1],
                                     "x2": P1[0], "y2": P1[1]})
    far = str(sk.execute("catia_sketch_arc", {
        "cx": other[0], "cy": other[1], "radius": R,
        "p1": list(q2), "p2": list(q1), "direction": far_direction}))
    say("  " + far[:300])
    closed = str(sk.execute("catia_close_sketch", {}))
    say("  " + closed[:300])
    pad = str(design.execute("catia_pad", {"sketch_name": "CAP", "height": 10}))
    say("  " + pad[:300])
    got = volume()
    say(f"  volume {got} vs analytic {analytic:.4f}")
    check("A the capsule pads to the analytic volume",
          got is not None and abs(got - analytic) < 0.5,
          f"{got} vs {analytic:.4f}")

    # ── B. the same call with cw is the OTHER half ───────────────────────
    say("\n== B the same arc with direction='cw' is the other semicircle")
    path_b = fresh("CAP0498B.CATPart")
    docs.execute("catia_new_part", {"name": "CAP0498B", "file_path": path_b})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "CAPB"})
    cw_reply = str(sk.execute("catia_sketch_arc", {
        "cx": CX, "cy": CY, "radius": R,
        "p1": list(P1), "p2": list(P2), "direction": "cw"}))
    say("  " + cw_reply[:400])
    ccw_apex = reply.split("bulge apex ")[1].split(" ")[0].strip("(),")
    cw_apex = cw_reply.split("bulge apex ")[1].split(" ")[0].strip("(),")
    check("B the two directions give DIFFERENT semicircles", ccw_apex != cw_apex,
          f"ccw {ccw_apex} cw {cw_apex}")
    check("B the cw apex is the report's inward side (216.2, 22.2)",
          cw_apex.startswith("216.2"), cw_apex)
    sk.execute("catia_close_sketch", {})

    # ── C. arc + profile with a 2e-06 mm seam ────────────────────────────
    say("\n== C a profile mixing catia_sketch_arc with catia_sketch_profile")
    path_c = fresh("MIX0498.CATPart")
    docs.execute("catia_new_part", {"name": "MIX0498", "file_path": path_c})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "MIX"})
    # A 60x40 stadium: the two half circles from catia_sketch_arc, six decimals,
    # then the two flats from catia_sketch_profile. The ends differ by the
    # projection error, which is the case that used to need allow_open_curves.
    radius = 20.0
    # FIVE decimals on purpose: the arc's ends are then up to 5e-06 mm off the
    # lines below, which is past the 1e-06 the old pre-check used and inside the
    # 1e-03 it uses now. Six decimals would leave a gap so small that the test
    # would pass whichever threshold was in force.
    # Both caps are named CLOCKWISE from the point given first, and both bulge
    # AWAY from the body: from (0,0) to (0,40) clockwise passes 180 degrees
    # (out to -X), and from (60,40) to (60,0) clockwise passes 0 degrees (out to
    # +X). Two calls of the same verb, two different-looking halves - exactly the
    # thing the report could not see without an apex in the reply.
    for cx, start, way in ((0.0, -90.0, "cw"), (60.0, 90.0, "cw")):
        p_start = [cx + radius * math.cos(math.radians(start)),
                   20.0 + radius * math.sin(math.radians(start))]
        p_end = [cx + radius * math.cos(math.radians(start + 180.0)),
                 20.0 + radius * math.sin(math.radians(start + 180.0))]
        sk.execute("catia_sketch_arc", {
            "cx": cx, "cy": 20.0, "radius": radius,
            "p1": [round(p_start[0], 5), round(p_start[1], 5)],
            "p2": [round(p_end[0], 5), round(p_end[1], 5)],
            "direction": way})
    sk.execute("catia_sketch_profile", {"segments": [
        {"type": "line", "p1": [0.0, 0.0], "p2": [60.0, 0.0]},
        {"type": "line", "p1": [60.0, 40.0], "p2": [0.0, 40.0]},
    ]})
    closed_c = str(sk.execute("catia_close_sketch", {}))
    say("  " + closed_c[:400])
    try:
        mixed = str(design.execute("catia_pad", {"sketch_name": "MIX",
                                                 "height": 10}))
        say("  " + mixed[:300])
        check("C catia_pad accepts it WITHOUT allow_open_curves", True)
    except Exception as error:  # noqa: BLE001
        say(f"  refused: {error}")
        check("C catia_pad accepts it WITHOUT allow_open_curves", False,
              str(error)[:120])
    got_c = volume()
    expected_c = (60 * 40 + math.pi * radius * radius) * 10
    check("C and the stadium has its analytic volume",
          got_c is not None and abs(got_c - expected_c) < 1.0,
          f"{got_c} vs {expected_c:.3f}")

    say("")
    passed = sum(1 for _n, ok, _d in results if ok)
    say(f"{passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            say(f"  FAILED: {name}  [{detail}]")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
