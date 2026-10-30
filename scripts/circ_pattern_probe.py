"""Is a circular pattern really translating its instances instead of rotating?

Report 2026-10-30 (v0.3.41): an 8-instance pattern around X of a 400 mm bar
gives Y +-500 and Z +-305, which is exactly `p + (R*c - c)` - the instances are
placed at the right angles and keep their orientation. Volume is 8x the seed,
so the volume check both the tool and its docs relied on cannot see it.

This builds that bar on a fresh part, applies the shipped call, and then three
variants of the same CATIA call, measuring the bounding box of each. The
variants exist because the argument order of AddNewCircPattern is easy to get
wrong and the symptom (correct positions, unchanged orientation) is what an
unused rotation slot looks like:

    shipped     what catia_circ_pattern sends today
    center_pt   rotation centre as a real point instead of an empty reference
    angle_360   iRotationAngle = 360 instead of 0
    flags_00    iRadialPos/iAngularPos = 0,0 instead of 1,1

A correct pattern of 8 copies at 45 degrees of a bar along +Y must give
|Y|max == |Z|max. The probe says which variant achieves that.

Every document is created here, saved to a temp dir (so the mesh route can read
it) and closed at the end. Each mutation asserts the active document first -
one run without that check put three fillets into a user's finished part.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def activate(conn, name: str):
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return candidate
    raise RuntimeError(f"{name} is not open")


def numbers(reply: str) -> dict:
    return json.loads(reply) if reply.strip().startswith("{") else {}


def build_bar(conn, documents, sketcher, part_design, name: str, workdir: str):
    path = os.path.join(workdir, f"{name}.CATPart")
    documents.execute("catia_new_part", {"name": name, "file_path": path})
    activate(conn, os.path.basename(path))
    sketcher.execute("catia_create_sketch", {"plane": "yz"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 100.0, "y1": -5.0, "x2": 500.0, "y2": 5.0})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10.0, "symmetric": True})
    return path


def bbox(conn, measurement, name: str) -> dict:
    activate(conn, name)
    conn.active_document.Save()
    payload = numbers(measurement.execute("catia_get_bounding_box", {"method": "mesh"}))
    low, high = payload.get("min") or {}, payload.get("max") or {}
    measured = str(payload.get("method", ""))
    if name.split(".")[0] not in measured:
        raise RuntimeError(f"asked for {name}, but the box measured {measured[:60]}")
    return {axis: (round(low[axis], 2), round(high[axis], 2))
            for axis in ("x", "y", "z") if axis in low and axis in high}


def main() -> int:
    conn = CATIAConnection()
    print(conn.connect())
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)

    workdir = tempfile.mkdtemp(prefix="circ_pattern_")
    created = []

    # ── the shipped call, through the tool ──────────────────────────────
    name = "CircShipped.CATPart"
    build_bar(conn, documents, sketcher, part_design, "CircShipped", workdir)
    created.append(name)
    reply = part_design.execute("catia_circ_pattern",
                                {"axis": "x", "center": [0, 0, 0], "count": 8})
    print("shipped reply:", str(reply).splitlines()[0][:110])
    print("shipped bbox :", bbox(conn, measurement, name))

    # ── the same call through the tool, both flags, so the difference is
    #    measured on the argument callers actually pass
    for label, extra in (("radius_aligned=false", {"radius_aligned": False}),
                         ("radius_aligned=true ", {"radius_aligned": True})):
        doc_name = "Circ" + label.split("=")[1].strip().capitalize()
        build_bar(conn, documents, sketcher, part_design, doc_name, workdir)
        created.append(f"{doc_name}.CATPart")
        activate(conn, f"{doc_name}.CATPart")
        part_design.execute("catia_circ_pattern",
                            {"axis": "x", "center": [0, 0, 0], "count": 8, **extra})
        print(f"{label} bbox :", bbox(conn, measurement, f"{doc_name}.CATPart"))

    for name in reversed(created):
        try:
            document = activate(conn, name)
            document.Save()
            document.Close()
        except Exception as error:  # noqa: BLE001
            print(f"could not close {name}: {str(error)[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
