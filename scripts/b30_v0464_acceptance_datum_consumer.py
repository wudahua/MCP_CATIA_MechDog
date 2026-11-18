"""B30 acceptance for 0.3.149: what a datum is, who consumes a sketch, and why
a sample count stopped.

Every cell here answers a claim made in the code, and the ones that matter are
the pairs: the criterion must REFUSE for a sketch standing on the datum and
CLEAN UP for one that merely shares its plane - the 2026-11-18 report measured
that 0.3.148 got the second one wrong (P2). The consumer check must refuse
BEFORE the attempt, because CATIA's own behaviour is not reliable in either
direction (probe v0462: it refused for the reporter, and it accepted for us and
left the pad failing every later update).

  A  two frames on z = 0, datum points 50 mm apart: auto cleans the first one's
     scaffolding, names the coplanar sketch as a DIFFERENT datum, update passes
  B  a sketch built ON 'DAT_A_FRAME' by name: auto refuses, and says why
  C  once that sketch is gone, the same delete goes through
  D  sketch -> pad -> delete the sketch: SKETCH_CONSUMED_BY, nothing attempted,
     tree unchanged, update fine
  E  delete the pad: the sketch goes with it (the cascade the refusal promises)
  F  the batch form deletes pad + sketch in one call
  G  structured spline: sampling_limited_by is 'cap' when the 32-per-span budget
     ends the fit, null when the tolerance was met

.venv/Scripts/python.exe -u scripts/b30_v0464_acceptance_datum_consumer.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0464")
CHECKS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(label: str, passed: bool, evidence: str = "") -> None:
    CHECKS.append((label, bool(passed), evidence))
    say(f"  [{'PASS' if passed else 'FAIL'}] {label}"
        + (f"  <- {evidence}" if evidence and not passed else ""))


def close_docs(conn) -> None:
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass


def data_of(reply) -> dict:
    """The structured payload, whichever shape the tool returned it in."""
    if isinstance(reply, dict):
        return reply.get("data", {})
    return json.loads(reply).get("data", {})


def sketches_of(body) -> list[str]:
    return [str(body.Sketches.Item(i).Name)
            for i in range(1, int(body.Sketches.Count) + 1)]


def hybrid_of(body) -> list[str]:
    return [str(body.HybridShapes.Item(i).Name)
            for i in range(1, int(body.HybridShapes.Count) + 1)]


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    measurement = MeasurementTools(conn)
    say(f"acceptance v0464 on {conn.app.Name}")

    close_docs(conn)
    name = "AccV0149"
    path = os.path.join(ROOT, name + ".CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": name, "file_path": path})
    body = conn.get_active_part_body()

    def frame(sketch_name: str, origin, u, v) -> None:
        sketcher.execute("catia_create_sketch",
                         {"plane": {"type": "frame", "origin": list(origin),
                                    "u": list(u), "v": list(v)},
                          "name": sketch_name})

    def rect(x2: float = 20.0, y2: float = 10.0) -> None:
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0.0, "y1": 0.0, "x2": x2, "y2": y2})
        sketcher.execute("catia_close_sketch", {})

    # ══ A: two datums on one plane - the report's P2 ═══════════════════════
    say("A: two frame sketches on z = 0, datum points (0,0,0) and (50,0,0)")
    frame("DAT_A", (0.0, 0.0, 0.0), (1, 0, 0), (0, 1, 0))
    rect()
    frame("DAT_B", (50.0, 0.0, 0.0), (1, 0, 0), (0, 1, 0))
    rect()
    reply = parts.execute("catia_delete_feature", {"feature_name": "DAT_A"})
    say("  reply: " + " ".join(reply.split())[:400])
    check("A auto cleans up although another sketch shares the plane",
          "removed the frame scaffolding" in reply, reply[:160])
    check("A says the coplanar sketch is a different datum",
          "different datum" in reply and "50.0000 mm apart" in reply, reply[:200])
    check("A the four scaffolding elements are gone",
          not any(n.startswith("DAT_A_FRAME") for n in hybrid_of(body)),
          str(hybrid_of(body)))
    check("A the other sketch and its datum survive",
          sketches_of(body) == ["DAT_B"]
          and any(n == "DAT_B_FRAME" for n in hybrid_of(body)),
          f"{sketches_of(body)} / {hybrid_of(body)}")
    try:
        update = " ".join(measurement.execute("catia_update_part", {}).split())
    except Exception as error:  # noqa: BLE001
        update = f"FAILED {str(error).splitlines()[0][:120]}"
    check("A the part still updates", "successfully" in update, update[:160])

    # ══ B: a sketch standing ON the datum - the case that must refuse ══════
    say("B: a sketch built on 'DAT_C_FRAME' by name")
    frame("DAT_C", (0.0, 0.0, 100.0), (1, 0, 0), (0, 1, 0))
    rect()
    sketcher.execute("catia_create_sketch",
                     {"plane": "DAT_C_FRAME", "name": "ON_C"})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 5.0, "cy": 5.0, "radius": 3.0})
    sketcher.execute("catia_close_sketch", {})
    reply = parts.execute("catia_delete_feature", {"feature_name": "DAT_C"})
    say("  reply: " + " ".join(reply.split())[:400])
    check("B auto refuses when a sketch shares the datum",
          "left alone" in reply and "share this datum" in reply, reply[:200])
    check("B the datum is still in the tree",
          any(n == "DAT_C_FRAME" for n in hybrid_of(body)), str(hybrid_of(body)))
    check("B the sketch itself still went - only the support was kept",
          "DAT_C" not in sketches_of(body), str(sketches_of(body)))
    try:
        update = " ".join(measurement.execute("catia_update_part", {}).split())
    except Exception as error:  # noqa: BLE001
        update = f"FAILED {str(error).splitlines()[0][:120]}"
    check("B keeping the support kept the survivor healthy",
          "successfully" in update, update[:140])

    # ══ C: once that sketch is gone, the same delete goes through ═════════
    say("C: a second pair, its dependant deleted first")
    frame("DAT_D", (0.0, 0.0, 200.0), (1, 0, 0), (0, 1, 0))
    rect()
    sketcher.execute("catia_create_sketch",
                     {"plane": "DAT_D_FRAME", "name": "ON_D"})
    sketcher.execute("catia_sketch_circle",
                     {"cx": 4.0, "cy": 4.0, "radius": 2.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_delete_feature", {"feature_name": "ON_D"})
    reply = parts.execute("catia_delete_feature", {"feature_name": "DAT_D"})
    say("  reply: " + " ".join(reply.split())[:300])
    check("C the scaffolding is removed once nothing stands on it",
          "removed the frame scaffolding" in reply, reply[:160])
    check("C DAT_D_FRAME is gone",
          not any(n.startswith("DAT_D_FRAME") for n in hybrid_of(body)),
          str(hybrid_of(body)))

    # ══ D: a sketch a pad consumes ════════════════════════════════════════
    say("D: sketch -> pad -> delete the sketch")
    frame("SK_P", (0.0, 0.0, 0.0), (1, 0, 0), (0, 1, 0))
    rect(40.0, 20.0)
    pad_reply = " ".join(parts.execute(
        "catia_pad", {"sketch_name": "SK_P", "height": 10.0}).split())
    say("  pad: " + pad_reply[:120])
    consumed = None
    shapes_before = [str(body.Shapes.Item(i).Name)
                     for i in range(1, int(body.Shapes.Count) + 1)]
    say(f"  shapes: {shapes_before}")
    try:
        reply = parts.execute("catia_delete_feature", {"feature_name": "SK_P"})
        say("  reply: " + " ".join(reply.split())[:300])
        check("D deleting a consumed sketch is refused", False,
              "the call went through instead")
    except CodedError as error:
        say(f"  raised {error.code}: " + " ".join(str(error).split())[:280])
        check("D the code is SKETCH_CONSUMED_BY",
              error.code == "SKETCH_CONSUMED_BY", error.code)
        check("D the consuming feature is named",
              bool(error.data.get("consumed_by")) and "delete" in str(error),
              json.dumps(getattr(error, "data", {}), ensure_ascii=False))
        check("D nothing was attempted", error.modified is False, str(error.modified))
        consumed = (error.data.get("consumed_by") or [None])[0]
    check("D the sketch is still in the tree", "SK_P" in sketches_of(body),
          str(sketches_of(body)))
    try:
        update = " ".join(measurement.execute("catia_update_part", {}).split())
    except Exception as error:  # noqa: BLE001
        update = f"FAILED {str(error).splitlines()[0][:120]}"
    check("D the part was not left in error", "successfully" in update, update[:140])

    # ══ E: the cascade the refusal promises ═══════════════════════════════
    say("E: delete the pad - the sketch goes with it")
    check("D the feature name was captured", bool(consumed), str(consumed))
    reply = parts.execute("catia_delete_feature",
                          {"feature_name": consumed or shapes_before[0]})
    say("  reply: " + " ".join(reply.split())[:300])
    check("E the sketch went with the feature", "SK_P" not in sketches_of(body),
          str(sketches_of(body)))

    # ══ F: the batch form ═════════════════════════════════════════════════
    say("F: one call with the pad and its sketch")
    frame("SK_Q", (0.0, 0.0, 0.0), (1, 0, 0), (0, 1, 0))
    rect(30.0, 30.0)
    pad_reply = " ".join(parts.execute(
        "catia_pad", {"sketch_name": "SK_Q", "height": 8.0}).split())
    pad_name = ""
    for piece in pad_reply.split("'"):
        if piece.startswith(("凸台", "Pad")):
            pad_name = piece
    say(f"  pad: {pad_name}")
    # THE SKETCH FIRST, ON PURPOSE: the batch has to put the pad before it.
    reply = parts.execute("catia_delete_feature",
                          {"names": ["SK_Q", pad_name]})
    say("  reply: " + " ".join(reply.split())[:300])
    check("F the batch does not refuse", "SKETCH_CONSUMED_BY" not in reply,
          reply[:160])
    check("F nothing is reported as failed - the sketch went with the pad",
          "Failed" not in reply, reply[:200])
    check("F both names are counted as removed",
          "Deleted 2 element(s) for 2 name(s) given." in reply, reply[:160])
    check("F both names are gone",
          "SK_Q" not in sketches_of(body)
          and pad_name not in [str(body.Shapes.Item(i).Name)
                               for i in range(1, int(body.Shapes.Count) + 1)],
          f"{sketches_of(body)} shapes={body.Shapes.Count}")

    # ══ G: what limited the sampling ══════════════════════════════════════
    say("G: structured spline, the 32-per-span budget and a met tolerance")
    frame("SK_SP", (0.0, 0.0, 0.0), (1, 0, 0), (0, 1, 0))
    reply = sketcher.execute("catia_sketch_spline",
                             {"control_points": [[100, 0], [100, 100], [0, 100]],
                              "degree": 2,
                              "weights": [1, 0.7071067811865476, 1],
                              "structured": True})
    data = data_of(reply)
    say(f"  R=100 quarter circle: error {data['sampling_error_mm']} "
        f"met={data['sampling_tolerance_met']} limited_by="
        f"{data['sampling_limited_by']}")
    check("G a capped miss says 'cap'",
          data["sampling_limited_by"] == "cap",
          str(data["sampling_limited_by"]))
    check("G the met/unmet fact is still published",
          isinstance(data["sampling_tolerance_met"], bool),
          str(data["sampling_tolerance_met"]))
    sketcher.execute("catia_sketch_line",
                     {"x1": -10.0, "y1": -10.0, "x2": -6.0, "y2": -10.0})
    flat = sketcher.execute("catia_sketch_spline",
                            {"control_points": [[0, 40], [40, 40], [80, 40]],
                             "degree": 2, "structured": True})
    flat_data = data_of(flat)
    say(f"  straight line: error {flat_data['sampling_error_mm']} "
        f"met={flat_data['sampling_tolerance_met']} limited_by="
        f"{flat_data['sampling_limited_by']}")
    check("G a met tolerance claims no limit",
          flat_data["sampling_limited_by"] is None,
          str(flat_data["sampling_limited_by"]))
    sketcher.execute("catia_close_sketch", {})
    try:
        update = " ".join(measurement.execute("catia_update_part", {}).split())
    except Exception as error:  # noqa: BLE001
        update = f"FAILED {str(error).splitlines()[0][:120]}"
    check("G the part still updates", "successfully" in update, update[:140])

    say("")
    passed = sum(1 for _label, ok, _why in CHECKS if ok)
    say(f"RESULT {passed}/{len(CHECKS)} passed")
    for label, ok, why in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  <- {why}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
