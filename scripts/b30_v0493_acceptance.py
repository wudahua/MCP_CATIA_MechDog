"""0.3.156 acceptance on B30, 2026-11-21: the four items taken from the report.

  A  P0-1 - catia_chamfer(length=25, length2=20) on a 100 mm cube edge removes
     exactly 25 000 mm3, and the length+angle control does NOT (11 374 mm3 at
     20 deg). Volume is the judge: a two-distance bevel and a 20-degree one
     differ by 14x on the same edge.
  B  P1-14/P1-16 - strict_arc accepts a 1.4e-9 mm gap on R6 and R40, refuses
     4e-4 mm on R40, and the refusal prints the gap and the tolerance.
  C  P1-18 - catia_get_inertia takes scope='main_body' and answers the same
     numbers as without it, while another scope is refused with a reason.
  D  P1-17 - a feature tool called while an assembly is active names the remedy.

One new document per cell.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import ArgumentRejected, PreconditionRejected  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="acc0493_")
CHECKS: list[tuple[str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail else ""), flush=True)


def payload(reply):
    return reply if isinstance(reply, dict) else json.loads(reply)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)

    def volume():
        # catia_get_inertia answers at the TOP level (no 'data' wrapper), and the
        # key is volume_mm3 - both learned by printing a real reply, after the
        # first version of this script read None twice.
        data = payload(measurement.execute("catia_get_inertia", {}))
        if isinstance(data.get("data"), dict):
            data = data["data"]
        for key in ("volume_mm3", "volume"):
            if data.get(key) is not None:
                return float(data[key])
        return None

    # ══ A: two distances vs length+angle, same edge, same cube ══════════
    say("A: a 100 mm cube, one 100 mm edge, two bevels")

    def cube(name: str) -> None:
        docs.execute("catia_new_part",
                     {"name": name, "file_path": os.path.join(ROOT, name + ".CATPart")})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 100, "y2": 100})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"sketch_name": "SQ", "height": 100})

    cube("TwoDist")
    before = volume()
    reply = part_design.execute("catia_chamfer",
                                {"length": 25, "length2": 20, "edge_name": "Edge.1"})
    after = volume()
    removed = None if (before is None or after is None) else before - after
    say(f"  reply: {' '.join(str(reply).split())[:140]}")
    say(f"  volume {before} -> {after}, removed {removed}")
    check("A the reply says 'two distances'", "two distances" in str(reply), str(reply)[:90])
    check("A 25/20 removed 25 000 mm3 (25*20/2*100)",
          removed is not None and abs(removed - 25000.0) < 1.0,
          f"{removed}")
    docs.execute("catia_close_document", {"save": False})

    cube("OneAngle")
    before = volume()
    part_design.execute("catia_chamfer",
                        {"length": 25, "angle": 20, "edge_name": "Edge.1"})
    after = volume()
    angle_removed = None if (before is None or after is None) else before - after
    say(f"  control (25 mm at 20 deg): removed {angle_removed}")
    check("A the length+angle control removes a different amount",
          angle_removed is not None and abs(angle_removed - 25000.0) > 5000.0,
          f"{angle_removed}")
    docs.execute("catia_close_document", {"save": False})

    # ══ B: strict_arc, the two residuals ════════════════════════════════
    say("\nB: strict_arc on a nanometre gap and on 0.4 micron")
    docs.execute("catia_new_part",
                 {"name": "ArcTol", "file_path": os.path.join(ROOT, "ArcTol.CATPart")})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "S1"})

    def arc(radius: float, gap: float) -> dict:
        return {"type": "arc", "center": [0.0, 0.0], "radius": radius,
                "p1": [radius + gap, 0.0], "p2": [0.0, radius - gap]}

    try:
        sketcher.execute("catia_sketch_profile",
                         {"segments": [arc(6.0, 1.4e-9)],
                          "strict_arc": True})
        check("B R6 with a 1.4e-9 mm gap is accepted", True, "drawn")
    except Exception as exc:  # noqa: BLE001
        check("B R6 with a 1.4e-9 mm gap is accepted", False,
              " ".join(str(exc).split())[:80])

    try:
        sketcher.execute("catia_sketch_profile",
                         {"segments": [arc(40.0, 4e-4)], "strict_arc": True})
        check("B R40 with a 4e-4 mm gap is refused", False, "it was accepted")
    except ArgumentRejected as exc:  # noqa: BLE001
        text = " ".join(str(exc).split())
        check("B R40 with a 4e-4 mm gap is refused", "ARC_ENDPOINT_OFF_CIRCLE" in text
              or "off its own circle" in text, text[:70])
        check("B the refusal prints the gap in mm", "0.000400" in text, text[:90])
        check("B and prints the tolerance it was compared with",
              "4.000e-08" in text, text[:90])
    except Exception as exc:  # noqa: BLE001
        check("B R40 with a 4e-4 mm gap is refused", False,
              f"{type(exc).__name__}: {' '.join(str(exc).split())[:60]}")
    docs.execute("catia_close_document", {"save": False})

    # ══ C: scope on the inertia tool ════════════════════════════════════
    say("\nC: catia_get_inertia(scope='main_body') against no scope")
    docs.execute("catia_new_part",
                 {"name": "ScopeT", "file_path": os.path.join(ROOT, "ScopeT.CATPart")})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 40, "y2": 30})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"sketch_name": "SQ", "height": 20})

    def flat(reply):
        data = payload(reply)
        return data["data"] if isinstance(data.get("data"), dict) else data

    plain = flat(measurement.execute("catia_get_inertia", {}))
    with_scope = flat(measurement.execute("catia_get_inertia",
                                          {"scope": "main_body"}))
    plain_v = plain.get("volume_mm3")
    scope_v = with_scope.get("volume_mm3")
    say(f"  plain volume {plain_v}, with scope {scope_v}")
    check("C scope='main_body' is accepted", scope_v is not None, str(scope_v))
    check("C and answers the same number", plain_v == scope_v,
          f"{plain_v} vs {scope_v}")
    check("C the reply echoes a scope word", plain.get("scope") in
          ("main_body", "active_body"), str(plain.get("scope")))
    try:
        measurement.execute("catia_get_inertia", {"scope": "face"})
        check("C another scope is refused", False, "it was accepted")
    except Exception as exc:  # noqa: BLE001
        check("C another scope is refused", "main_body" in str(exc),
              " ".join(str(exc).split())[:70])
    docs.execute("catia_close_document", {"save": False})

    # ══ D: a feature tool called on an assembly ═════════════════════════
    say("\nD: delete_feature while the assembly is the active document")
    asm = os.path.join(ROOT, "AsmD.CATProduct")
    docs.execute("catia_new_product", {"name": "AsmD", "file_path": asm})
    try:
        part_design.execute("catia_delete_feature", {"names": ["Pad.1"]})
        check("D a feature tool on an assembly is refused", False, "it was accepted")
    except PreconditionRejected as exc:
        text = " ".join(str(exc).split())
        check("D a feature tool on an assembly is refused", True, text[:60])
        check("D the refusal names catia_activate_document",
              "catia_activate_document" in text, text[:120])
    except Exception as exc:  # noqa: BLE001
        check("D a feature tool on an assembly is refused", False,
              f"{type(exc).__name__}: {' '.join(str(exc).split())[:60]}")
    docs.execute("catia_close_document", {"save": False})

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    say(f"\n{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
