"""Does the 17-instance hexapod from the 2026-11-20 report still cost 200 s?

That report measured `get_bounding_box` on `hexapod_assembly.CATProduct` at
200 498 ms, with 7 parts paying a cold topology read each: P05_legrod 68 516 +
68 185 (5+1 instances), P07_link 31 655, P02_crankdisk 14 351, P03 11 187.

0.3.153 changed the rule for parts with more than 200 vertices - they are
bounded from their MESH instead of from a 200-vertex sample - and that was
verified on a 2-instance assembly. The report's own scene was never re-run.
This does, twice: once with the default (auto = mesh on an assembly, which the
report never measured - P6), once with method='vertices' (what the report used).

Run: ./.venv/Scripts/python.exe -u scripts/b30_v0491_hexapod_box_cost.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402

ASSEMBLY = os.path.join("F:/WokrBuddy Catia Space", "catia文建-六足行走机构",
                        "parts", "hexapod_assembly.CATProduct")

#: What the report measured, per part, on 0.3.152 (ms).
REPORTED = {"P05_legrod": [68516, 68185], "P07_link": [31655],
            "P02_crankdisk": [14351], "P03": [11187]}
REPORTED_TOTAL = 200498


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, dict) else json.loads(reply)


def run(measurement, method: str) -> dict:
    started = time.perf_counter()
    reply = payload(measurement.execute("catia_get_bounding_box",
                                        {"method": method}))
    took = (time.perf_counter() - started) * 1000
    data = reply.get("data", reply)
    say(f"\n── method={method!r}: {took:.0f} ms "
        f"(report: {REPORTED_TOTAL} ms) ──")
    say(f"  summary: {' '.join(str(data.get('summary', '')).split())[:180]}")
    say(f"  regime={data.get('regime')} session_first_parts="
        f"{data.get('session_first_parts')} exact_envelope={data.get('exact_envelope')}")

    entries = data.get("parts") or data.get("components") or []
    rows = []
    for entry in entries:
        rows.append({
            "part": entry.get("part") or entry.get("component"),
            "source": entry.get("source"),
            "instances": entry.get("instances"),
            "ms": entry.get("elapsed_ms"),
            "cells": entry.get("cells"),
            "points": entry.get("points"),
            "reason": entry.get("mesh_reason"),
        })
    for row in rows:
        say("  " + json.dumps(row, ensure_ascii=False))
    say(f"  {len(rows)} part(s) in the reply, total part time "
        f"{sum(r['ms'] or 0 for r in rows):.0f} ms")
    data["_took_ms"] = round(took, 1)
    data["_rows"] = rows
    return data


def main() -> int:
    if not os.path.exists(ASSEMBLY):
        say(f"missing: {ASSEMBLY}")
        return 1

    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    measurement = MeasurementTools(conn)

    open_reply = docs.execute("catia_open_document", {"file_path": ASSEMBLY})
    say(f"opened: {' '.join(str(open_reply).split())[:120]}")

    first = run(measurement, "auto")
    second = run(measurement, "vertices")

    say("\n── what this says about the report's P2 ──")
    for label, data in (("auto", first), ("vertices", second)):
        took = data["_took_ms"]
        say(f"  {label:9} {took:8.0f} ms  vs 200 498 ms reported: "
            f"{took / REPORTED_TOTAL * 100:5.1f} %")
    swept = [r for r in second["_rows"] if r["source"] == "vertices"]
    meshed = [r for r in second["_rows"] if r["source"] != "vertices"]
    say(f"  vertices route: {len(swept)} part(s) still swept, "
        f"{len(meshed)} moved to their mesh")
    for row in swept:
        if (row["ms"] or 0) > 5000:
            say(f"    still slow: {row['part']} {row['ms']:.0f} ms, "
                f"{row['cells']} cells / {row['points']} points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
