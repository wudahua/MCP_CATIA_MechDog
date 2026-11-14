"""0.3.126 acceptance on B30: elliptic sweep sections (the 2026-11-14 report's P0-4).

Every case is one FRESH document. The numbers were worked out before the run, so a
match is a measurement and not a fit:

  case B  two circles r8 -> r16 over 40 mm ......      18765.75 mm3 (pi*h/3*(R^2+Rr+r^2))
  case C  ellipses 5.4x4.8 -> 2.7x2.4 over 40 mm       1899.87 mm3 (pi*h/6*(f0+4fmid+f1))

    .venv/Scripts/python.exe scripts/b30_v0428_audit.py
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
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0428")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, title, bool(passed), detail))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}"
        + (f"  ({detail})" if detail else ""))


def fresh(conn: CATIAConnection, documents: DocumentTools, name: str):
    path = os.path.join(ROOT, name + ".CATPart")
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
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": name, "file_path": path, "overwrite": True})
    for index in range(1, int(conn.documents.Count) + 1):
        if conn.documents.Item(index).Name == name + ".CATPart":
            conn.documents.Item(index).Activate()
    return conn.get_active_part()


def volume(measurement) -> float:
    payload = json.loads(measurement.execute("catia_get_inertia", {}))
    return float(payload.get("volume_mm3") or 0.0)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    documents = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)

    # ── A: the circle path is exactly what it was ────────────────────────
    say("")
    say("── A: two circles, r8 -> r16 over 40 mm (the 0.3.124 baseline) ──")
    fresh(conn, documents, "AuditCirc")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 0, 40],
                                   "name": "Spine"})
    reply = " ".join(str(gsd.execute("catia_solid_sweep_variable",
                                     {"spine": "Spine",
                                      "sections": [[0, 8], [1, 16]],
                                      "name": "Circ"})).split())
    say("  " + reply[:340])
    got, want = volume(measurement), math.pi * 40.0 / 3.0 * (64 + 128 + 256)
    check(1, "circle stations still make the truncated cone",
          abs(got - want) / want < 0.02 and abs(got - 18765.8) / 18765.8 < 0.001,
          f"{got:.2f} vs {want:.2f}")
    check(2, "the reply does not call a round sweep elliptic",
          "elliptic" not in reply.lower())

    # ── B: the report's own shape - a flattened stem ─────────────────────
    say("")
    say("── B: ellipses 5.4 x 4.8 -> 2.7 x 2.4 over 40 mm ──")
    fresh(conn, documents, "AuditEll")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 0, 40],
                                   "name": "Spine"})
    reply = " ".join(str(gsd.execute("catia_solid_sweep_variable",
                                     {"spine": "Spine",
                                      "sections": [[0, 5.4, 4.8], [1, 2.7, 2.4]],
                                      "name": "Stem"})).split())
    say("  " + reply[:420])
    f0, fmid, f1 = 5.4 * 4.8, 4.05 * 3.6, 2.7 * 2.4
    want = math.pi * 40.0 / 6.0 * (f0 + 4 * fmid + f1)
    got = volume(measurement)
    check(3, "elliptic stations make the elliptic frustum",
          abs(got - want) / want < 0.02, f"{got:.2f} vs {want:.2f}")
    check(4, "the reply says the stations are elliptic, not round",
          "elliptic" in reply.lower() and "rx=" in reply)

    # The section's own length is what a volume can hide: a round station of the
    # same volume would be a circle, and a circle and this ellipse do not share a
    # perimeter. Measured on the SPLINE, not on a bounding box - a part whose
    # solid sits in a body the caller created has no bbox through
    # catia_get_bounding_box({}) ("that part has no solid shape yet", 2026-11-14).
    spa = conn.active_document.GetWorkbench("SPAWorkbench")
    part = conn.get_active_part()
    ring = part.HybridBodies.Item(1).HybridShapes.Item("Stem_01_S")
    measured = float(spa.GetMeasurable(
        part.CreateReferenceFromObject(ring)).Length)
    exact = 0.0
    steps = 20000
    for index in range(steps):
        angle = 2.0 * math.pi * (index + 0.5) / steps
        dx = -5.4 * math.sin(angle)
        dy = 4.8 * math.cos(angle)
        exact += math.hypot(dx, dy) * (2.0 * math.pi / steps)
    check(5, "the station curve is the 5.4 x 4.8 ellipse, not a circle",
          abs(measured - exact) / exact < 0.001,
          f"{measured:.4f} mm against {exact:.4f} (a circle of r=5.1 would be 32.0)")

    # ── C: a circle that becomes an ellipse ──────────────────────────────
    say("")
    say("── C: [[0, 4], [0.5, 3, 6], [1, 2]] - round, flat, round ──")
    fresh(conn, documents, "AuditMix")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 0, 30],
                                   "name": "Spine"})
    reply = " ".join(str(gsd.execute("catia_solid_sweep_variable",
                                     {"spine": "Spine",
                                      "sections": [[0, 4], [0.5, 3, 6], [1, 2]],
                                      "name": "Mix"})).split())
    say("  " + reply[:400])
    # ONE of the three is elliptic, not two: [0, 4] and [1, 2] are circles (a
    # two-number station IS a circle) and only [0.5, 3, 6] is an ellipse. This
    # assertion said "2 of them elliptic" when it was written, and the tool was
    # right - the count is of stations whose rx != ry, not of pairs with a third
    # number in them.
    check(6, "the three mixed forms build one solid",
          "Swept 3 stations" in reply and "1 of them elliptic" in reply,
          "1 of the 3 stations is elliptic")

    # ── D: refusals, and the tree stays as it was ────────────────────────
    say("")
    say("── D: what must be refused ──")
    fresh(conn, documents, "AuditRefuse")
    gsd.execute("catia_gsd_line", {"point1": [0, 0, 0], "point2": [0, 0, 40],
                                   "name": "Spine"})
    for label, payload, code in (
        ("major_axis along the spine", {"major_axis": [0, 0, 1]},
         "MAJOR_AXIS_ALONG_SPINE"),
        ("negative semi-axis", {"sections": [[0, 5, -4], [1, 2, 2]]},
         "RADIUS_OUT_OF_RANGE"),
        ("four numbers in a station", {"sections": [[0, 5, 4, 1], [1, 2, 2, 1]]},
         "BAD_SECTIONS"),
    ):
        args = {"spine": "Spine", "name": "Bad",
                "sections": [[0, 5.4, 4.8], [1, 2.7, 2.4]]}
        args.update(payload)
        try:
            gsd.execute("catia_solid_sweep_variable", args)
            check(7, f"refuses {label}", False, "it was accepted")
        except Exception as error:  # noqa: BLE001 - the code is the answer
            got_code = getattr(error, "code", "")
            check(7, f"refuses {label}", got_code == code,
                  f"{got_code}: {str(error).splitlines()[0][:90]}")
    elements = json.loads(gsd.execute("catia_gsd_list_elements", {}))
    names = [item.get("name") for item in elements.get("elements", [])]
    check(8, "the refusals left nothing behind", not any(
        str(name or "").startswith("Bad") for name in names), str(names[:6]))

    # ── E: a bent spine - the axes have to be carried ────────────────────
    say("")
    say("── E: elliptic stations on a BENT spine (the carry does the work) ──")
    fresh(conn, documents, "AuditBent")
    gsd.execute("catia_gsd_spline",
                {"points": [[0, 0, 0], [10, 0, 20], [10, 0, 40], [0, 0, 55]],
                 "name": "Arc"})
    try:
        reply = " ".join(str(gsd.execute("catia_solid_sweep_variable",
                                         {"spine": "Arc",
                                          "sections": [[0, 6, 3], [0.5, 5, 2.5],
                                                       [1, 3, 1.5]],
                                          "name": "Bent"})).split())
        say("  " + reply[:420])
        got = volume(measurement)
        check(9, "a bent spine with elliptic stations still builds a solid",
              got > 0, f"{got:.1f} mm3")
    except Exception as error:  # noqa: BLE001 - a refusal is an answer too
        say(f"  refused: {getattr(error, 'code', '')} {str(error)[:260]}")
        check(9, "a bent spine with elliptic stations still builds a solid",
              False, "refused")

    # ── F: the router still advertises it ────────────────────────────────
    definitions = {item["name"]: item for item in gsd.get_tool_definitions()}
    schema = definitions["catia_solid_sweep_variable"]["inputSchema"]
    check(10, "major_axis is in the schema and rx/ry are documented as semi-axes",
          "major_axis" in schema["properties"]
          and "SEMI" in definitions["catia_solid_sweep_variable"]["description"],
          "schema + description")

    say("")
    passed = sum(1 for _n, _t, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} checks passed")
    for number, title, ok, detail in RESULTS:
        if not ok:
            say(f"  FAILED {number}. {title}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
