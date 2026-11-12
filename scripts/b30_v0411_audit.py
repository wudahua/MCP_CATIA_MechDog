"""0.3.111 acceptance - the real 381-edge part from the 2026-11-11 report.

The eighth report's own model, `SiphonBottle.CATPart`, is used here as a COPY
(renamed, because CATIA refuses to load a second document with the same
identifier). The original file is never opened for writing and its hash is
checked before and after.

What this checks:

  1  the copy is byte-identical to the original, and the original is untouched
  2  the model is what the report says: 2 features, 129 faces, 67278.8 mm3 -
     and 381 EDGES, which the report never counted
  3  R1.5 with the DEFAULT max_edges says "166 of 381 are shorter than 2*R"
     BEFORE it mentions max_edges - the count is arithmetic and used to arrive
     only after 186 s of work
  4  the same reply carries the measured cost of the batch attempt (186 s, 0
     fillets), because "one batch attempt" reads as cheap and is not
  5  the part is left as it was: 2 shapes, volume unchanged, still updates
  6  the ceilings record what R0.25 did on this part (101 of 381, 458 s)

Run: .venv/Scripts/python.exe -u scripts/b30_v0411_audit.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection          # noqa: E402
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS  # noqa: E402
from catia_mcp.tools.document import DocumentTools       # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

ORIGINAL = r"F:\WorkBuddy Space\2026-10-20-13-31-14\他人反馈的问题\SiphonBottle.CATPart"
PROBE_ROOT = r"F:\catia_probe_v0411"
ROOT = os.path.join(PROBE_ROOT, "audit")
COPY = os.path.join(ROOT, "SiphonBottle_probe.CATPart")
EXPECTED_VOLUME = 67278.8
RESULTS: list[tuple[int, str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def digest(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:                                     # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(PROBE_ROOT)):
            try:
                document.Close()
            except Exception:                                 # noqa: BLE001
                pass


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    original_hash = digest(ORIGINAL)
    shutil.copy2(ORIGINAL, COPY)

    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs = DocumentTools(conn)
    pd = PartDesignTools(conn)

    say("0.3.111 acceptance - the 381-edge part from the eighth report")
    say("=" * 78)

    check(1, "the copy is byte-identical to the original",
          digest(COPY) == original_hash,
          f"sha256 {original_hash[:16]} ... on both")

    docs.execute("catia_open_document", {"file_path": COPY})
    part = conn.get_active_part()
    bodies = json.loads(pd.execute("catia_list_bodies", {}))["bodies"]
    volume = bodies[0].get("volume_mm3")
    faces = json.loads(pd.execute("catia_list_faces", {}))["faces"]
    edges = json.loads(pd.execute("catia_list_edges", {}))["edges"]
    features = json.loads(pd.execute("catia_list_features", {}))["features"]
    lengths = sorted(float(e.get("length_mm") or 0) for e in edges)
    check(2, "the model matches the report: 2 features, 129 faces, 67278.8 mm3",
          len(features) == 2 and len(faces) == 129
          and volume is not None and abs(volume - EXPECTED_VOLUME) < 0.1,
          f"{len(features)} feature(s), {len(faces)} face(s), volume {volume}")
    check(3, "...but it has 381 EDGES, which the report never counted",
          len(edges) == 381,
          f"{len(edges)} edge(s); shortest {lengths[0]:.4f} mm, longest "
          f"{lengths[-1]:.4f} mm, {sum(1 for x in lengths if x < 3.0)} shorter "
          "than 2*R at R1.5")

    shapes_before = part.MainBody.Shapes.Count
    started = time.perf_counter()
    reply = str(pd.execute("catia_fillet", {"radius": 1.5}))
    elapsed = time.perf_counter() - started
    text = " ".join(reply.split())
    say(f"   (the call took {elapsed:.1f}s)")

    check(4, "R1.5 rounds 0 of 381 edges and says how many are too short",
          "0/381 edge(s) done" in text and "166 of 381" in text,
          text[:200])
    # The ORDER is the fix: the arithmetic has to be readable before the caller
    # is told about max_edges and the time it spent.
    short_at = text.find("shorter than")
    limit_at = text.find("max_edges")
    check(5, "the short-edge count comes BEFORE the max_edges paragraph",
          short_at != -1 and limit_at != -1 and short_at < limit_at,
          f"shorter-than at {short_at}, max_edges at {limit_at}")
    check(6, "the reply carries the measured cost of the batch attempt",
          "186 s" in text and "0 fillets" in text, text[-320:])

    shapes_after = part.MainBody.Shapes.Count
    volume_after = pd._body_volume_mm3(part, part.MainBody)
    try:
        part.Update()
        updates = True
    except Exception as caught:                            # noqa: BLE001
        updates = False
        say(f"   part.Update() failed: {caught}")
    check(7, "the refused batch left the body untouched and it still updates",
          shapes_after == shapes_before
          and volume_after is not None
          and abs(volume_after - EXPECTED_VOLUME) < 0.1
          and updates,
          f"shapes {shapes_before} -> {shapes_after}, volume {volume_after}, "
          f"Update()={updates}")

    entry = next((item for item in _CAPABILITY_CEILINGS
                  if item["member"] == "rounding every edge of one solid at one radius"),
                 None)
    detail = entry["detail"] if entry else ""
    check(8, "the ceilings record what R0.25 did on this same part",
          "101 of 381" in detail and "458 s" in detail and "381 EDGES" in detail,
          (entry["status"] if entry else "entry missing"))

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} passed")
    for number, label, okay, _ in RESULTS:
        if not okay:
            say(f"  failed: {number}. {label}")

    close_ours(conn)
    check(9, "the ORIGINAL file was never written to",
          digest(ORIGINAL) == original_hash,
          f"sha256 {digest(ORIGINAL)[:16]} ... unchanged")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} passed")


if __name__ == "__main__":
    main()
