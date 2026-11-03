"""After-the-change check for 0.3.49, on B30.

What it has to prove, in the order the release note claims it:

    1  points still work: a circle built from CENTRE COORDINATES is right
       (length 62.83 for r=10) even though _point_ref no longer updates the
       point itself - the feature update has to cascade;
    2  90-point spline: the time, and the same 188.496 mm curve;
    3  a point section is refused BEFORE the tree is touched;
    4  the failing trio still fails (geometry unchanged) and the message now
       carries the call's own numbers;
    5  split says which side it kept, and the STL agrees;
    6  the mesh box names its scope and warns about geometrical sets;
    7  catia_diagnose reports a pywin32 build number.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import DiagnosticsTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "b30_v0349")
os.makedirs(OUT, exist_ok=True)


def say(line: str = "") -> None:
    print(line, flush=True)


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if str(candidate.Name) == name:
            candidate.Activate()
            if str(conn.active_document.Name) != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def close_all(document, conn):
    names = []
    for index in range(1, conn.documents.Count + 1):
        try:
            names.append(str(conn.documents.Item(index).Name))
        except Exception:  # noqa: BLE001
            pass
    for name in names:
        activate(conn, name)
        document.execute("catia_close_document", {"save": False})


def fresh(name: str, document: DocumentTools, conn):
    path = os.path.join(OUT, f"{name}.CATPart")
    document.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, f"{name}.CATPart")


def ring(radius: float, z: float, count: int = 90) -> list[list[float]]:
    return [[radius * math.cos(2 * math.pi * i / count),
             radius * math.sin(2 * math.pi * i / count), z] for i in range(count)]


def stl_z(path: str) -> tuple[float, float]:
    low, high = 1e9, -1e9
    with open(path, "r", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("vertex"):
                z = float(line.split()[3])
                low, high = min(low, z), max(high, z)
    return round(low, 3), round(high, 3)


def main() -> int:
    conn = CATIAConnection()
    say(conn.connect())
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    measurement = MeasurementTools(conn)
    part_design = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    diagnostics = DiagnosticsTools(conn)

    close_all(document, conn)

    # ── 1. points still resolve: a circle from centre coordinates ────────
    say("\n── 1. centre coordinates still make a correct circle ──")
    fresh("V0349_SPLINE", document, conn)
    spa = conn.app.ActiveDocument.GetWorkbench("SPAWorkbench")
    reply = gsd.execute("catia_gsd_circle",
                        {"center": [0, 0, 0], "support_plane": "xy",
                         "radius": 10.0, "name": "K_C"})
    part = conn.get_active_part()
    measurable = spa.GetMeasurable(part.CreateReferenceFromObject(
        part.FindObjectByName("K_C")))
    say(f"1  circle r10 length {float(measurable.Length):.3f} mm (analytic 62.832)")
    say(f"   reply: {reply[-60:]}")

    # ── 2. 90-point spline: time and geometry ────────────────────────────
    say("\n── 2. catia_gsd_spline, 90 points ──")
    start = time.perf_counter()
    reply = gsd.execute("catia_gsd_spline",
                        {"points": ring(30.0, 0.0), "closed": True, "name": "K_S"})
    elapsed = time.perf_counter() - start
    spline = part.FindObjectByName("K_S")
    measurable = spa.GetMeasurable(part.CreateReferenceFromObject(spline))
    say(f"2  built in {elapsed:.2f} s, length {float(measurable.Length):.3f} mm "
        f"(analytic 188.496)")
    say(f"   reply: {reply}")

    # ── 3. a point section is refused before anything is built ───────────
    say("\n── 3. point section refused up front ──")
    gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": 40, "name": "K_P"})
    before = gsd._get_geoset().HybridShapes.Count
    try:
        gsd.execute("catia_gsd_multi_section_surface",
                    {"sections": ["K_P", "K_C"], "name": "K_BAD"})
        say("3  NOT refused - the guard did not fire")
    except Exception as error:  # noqa: BLE001
        first = str(error).splitlines()[0]
        after = gsd._get_geoset().HybridShapes.Count
        say(f"3  refused: {first[:110]}")
        say(f"   elements before {before}, after {after} (must be equal)")

    # ── 4. the failing trio still fails, with numbers in the message ─────
    say("\n── 4. fold-back trio: still fails, message now carries the call ──")
    for index, (radius, z) in enumerate([(2.9, -19.0), (3.0, -17.584), (8.0, -25.0)],
                                        start=1):
        gsd.execute("catia_gsd_circle",
                    {"center": [0, 0, z], "support_plane": "xy",
                     "radius": radius, "name": f"K_F{index}"})
    try:
        gsd.execute("catia_gsd_multi_section_surface",
                    {"sections": ["K_F1", "K_F2", "K_F3"], "name": "K_LOFT"})
        say("4  BUILT - geometry changed, which it must not have")
    except Exception as error:  # noqa: BLE001
        text = str(error)
        say(f"4  failed as before; message {len(text)} chars")
        for marker in ("THIS CALL: sections: 3", "y has to self-intersect"
                       " and CATIA refuses" if False else "ARRANGEMENT THAT MAKES"):
            say(f"   contains {marker!r}: {marker in text}")
        start = text.find("THIS CALL")
        say(f"   {text[start:start + 130]}")

    # ── 5. split names the side, and the STL agrees ──────────────────────
    say("\n── 5. split: reply names the side ──")
    fresh("V0349_SPLIT", document, conn)
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 0], "support_plane": "xy",
                                     "radius": 10.0, "name": "S_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "S_C", "direction": [0, 0, 1],
                                      "limit1": 40.0, "name": "S_CYL"})
    gsd.execute("catia_gsd_plane_offset", {"base_plane": "xy", "offset": 10.0,
                                           "name": "S_MID"})
    for reverse in (False, True):
        tag = "R" if reverse else "N"
        reply = gsd.execute("catia_gsd_split",
                            {"element": "S_CYL", "cutter": "S_MID",
                             "reverse": reverse, "name": f"S_CUT{tag}"})
        part_design.execute("catia_set_visibility", {"name": "S_CYL", "show": False})
        path = os.path.join(OUT, f"v0349_{tag}.stl")
        if os.path.exists(path):
            os.remove(path)
        conn.active_document.ExportData(path, "stl")
        part_design.execute("catia_set_visibility", {"name": "S_CYL", "show": True})
        part_design.execute("catia_set_visibility", {"name": f"S_CUT{tag}", "show": False})
        say(f"5  reverse={reverse!s:<5}: kept z {stl_z(path)}  reply: {reply[-95:]}")

    # ── 6. mesh box names its scope ──────────────────────────────────────
    say("\n── 6. mesh bounding box, visible surface above the solid ──")
    fresh("V0349_BOX", document, conn)
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle", {"x1": -5, "y1": -5, "x2": 5, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10.0})
    gsd.execute("catia_gsd_circle", {"center": [0, 0, 30], "support_plane": "xy",
                                     "radius": 4.0, "name": "B_C"})
    gsd.execute("catia_gsd_extrude", {"profile": "B_C", "direction": [0, 0, 1],
                                      "limit1": 5.0, "name": "B_SURF"})
    payload = json.loads(measurement.execute("catia_get_bounding_box",
                                             {"method": "mesh"}))
    say(f"6  z max {payload['max']['z']}  scope: {payload.get('box_scope')}")
    for warning in payload.get("warnings") or []:
        say(f"   warning: {warning[:150]}")

    # ── 7. pywin32 build in the diagnose output ──────────────────────────
    say("\n── 7. catia_diagnose ──")
    info = json.loads(diagnostics.execute("catia_diagnose", {}))
    say(f"7  pywin32_build {info.get('python', {}).get('pywin32_build')}  "
        f"gen_py_entries {info.get('python', {}).get('gen_py_entries')}")

    # ── 8. what the point guard costs on a real 10-section loft ──────────
    say("\n── 8. cost of the point guard (10 sections) ──")
    for index in range(1, 11):
        gsd.execute("catia_gsd_circle",
                    {"center": [0, 0, index * 2], "support_plane": "xy",
                     "radius": 5.0 + index, "name": f"G_R{index}"})
    names = [f"G_R{index}" for index in range(1, 11)]
    start = time.perf_counter()
    resolved = gsd._resolve_sections(names)
    resolved_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    gsd._refuse_point_sections(names, resolved)
    say(f"8  resolve 10 sections: {resolved_ms:.0f} ms (was already done by the "
        f"loft before 0.3.49)")
    say(f"   guard on top of it: {(time.perf_counter() - start) * 1000:.1f} ms")
    start = time.perf_counter()
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": names, "ruled": True, "name": "G_RULED"})
    say(f"   ruled 10-section loft: {time.perf_counter() - start:.2f} s")

    say("\nclosing probe documents")
    close_all(document, conn)
    say(f"documents open at the end: {conn.documents.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
