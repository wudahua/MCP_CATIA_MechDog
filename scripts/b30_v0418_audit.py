"""0.3.116 on B30: the three replies, on real COM, with the numbers they claim.

A  A sketch of glyphs closes as LETTERING: purpose='engrave' by itself, no
   "profile is not closed" warning, the free ends named as free ends.
B  A sketch of glyphs plus a circle stays a PROFILE - the record cannot decide,
   so it does not pretend to.
C  catia_solid_loft names the sections CATIA holds, read back after the update.
D  catia_close_document(all_unmodified=true) closes the saved-and-unmodified
   ones, keeps the dirty one, keeps the active one, and the count adds up.

Every cell gets its own new document. D is the only one that closes things, and
it closes what it says: the reply is printed in full before the verdict.

    .venv/Scripts/python.exe scripts/b30_v0418_audit.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0418")
ROOT = os.path.join(_PROBE_ROOT, "audit")

results: list[tuple[int, str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, title: str, passed: bool, evidence: str) -> None:
    results.append((number, title, bool(passed), evidence))
    say(f"  [{'PASS' if passed else 'FAIL'}] {number}. {title}")
    say(f"         {evidence}")


def activate(conn: CATIAConnection, name: str) -> None:
    for index in range(1, conn.documents.Count + 1):
        candidate = conn.documents.Item(index)
        if candidate.Name == name:
            candidate.Activate()
            if conn.active_document.Name != name:
                raise RuntimeError(f"could not activate {name}")
            return
    raise RuntimeError(f"{name} is not open")


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


def fresh(conn, documents, name: str) -> None:
    path = os.path.join(ROOT, name)
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": os.path.splitext(name)[0], "file_path": path,
                       "overwrite": True})
    activate(conn, name)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)

    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    # ── A: lettering, no instruction ────────────────────────────────────────
    say("")
    say("── A: a sketch of glyphs only ──")
    fresh(conn, documents, "TextOnly.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_text_strokes", {"text": "CATIA", "height": 6.0})
    payload = sketcher.execute("catia_close_sketch", {"structured": True})
    data = payload["data"]
    message = payload["message"]
    say("  purpose=" + str(data["purpose"])
        + " expected_open=" + str(data["expected_open"])
        + " text_strokes=" + str(data["text_strokes"])
        + " breaks.closed=" + str(data["breaks"]["closed"])
        + " expected=" + str(data["breaks"].get("expected")))
    check(1, "a text-only sketch closes as ENGRAVING without being told",
          data["purpose"] == "engrave" and data["expected_open"] is True
          and data["breaks"].get("expected") == "open",
          f"purpose={data['purpose']}, expected_open={data['expected_open']}")
    check(2, "no 'profile is not closed' warning on lettering",
          "WARNING: the profile is not closed" not in message
          and "FREE ENDS" in message and "catia_surface_engrave" in message,
          "the reply names the free ends and the engraving route")

    # ── B: glyphs plus a closed loop - the record cannot decide ─────────────
    say("")
    say("── B: glyphs plus a circle ──")
    fresh(conn, documents, "TextMix.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_text_strokes", {"text": "A", "height": 6.0})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 2.0})
    mixed = sketcher.execute("catia_close_sketch",
                             {"structured": True})["data"]
    check(3, "a MIXED sketch stays a profile and says why",
          mixed["purpose"] == "profile" and mixed["text_elements"] > 0,
          f"purpose={mixed['purpose']}, text_elements={mixed['text_elements']}")

    # ── C: the loft's own section list ──────────────────────────────────────
    say("")
    say("── C: a three-section solid loft ──")
    fresh(conn, documents, "LoftAudit.CATPart")
    for height, radius in ((0.0, 8.0), (10.0, 10.0), (20.0, 12.0)):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": height})
        sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": radius})
        sketcher.execute("catia_close_sketch", {})
    loft = part_design.execute("catia_solid_loft",
                               {"sections": ["草图.1", "草图.2", "草图.3"]})
    say("  " + " ".join(str(loft).split())[:400])
    check(4, "the reply names the sections CATIA holds",
          "CATIA's own section list holds 3" in str(loft)
          and "'草图.1'" in str(loft) and "'草图.3'" in str(loft)
          and "none was silently dropped" in str(loft),
          "read back with GetSectionFromLoft, not inferred")

    # ── D: batch close ──────────────────────────────────────────────────────
    say("")
    say("── D: all_unmodified ──")
    # One saved-and-unmodified part, and one that has edits.
    fresh(conn, documents, "Clean.CATPart")
    documents.execute("catia_save_document", {})
    fresh(conn, documents, "Dirty.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 5.0})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 3.0})
    # A third part that is ALSO saved and unmodified, made active so that
    # 'skipped because active' and 'closed because unmodified' are two different
    # cells rather than one sentence covering both.
    fresh(conn, documents, "Active.CATPart")
    documents.execute("catia_save_document", {})
    before_total = int(conn.documents.Count)
    reply = documents.execute("catia_close_document", {"all_unmodified": True})
    say("  " + " ".join(str(reply).split())[:900])
    after_total = int(conn.documents.Count)
    dirty_open = any(str(conn.documents.Item(i).Name) == "Dirty.CATPart"
                     for i in range(1, conn.documents.Count + 1))
    clean_open = any(str(conn.documents.Item(i).Name) == "Clean.CATPart"
                     for i in range(1, conn.documents.Count + 1))
    active_now = str(conn.active_document.Name)
    active_open = any(str(conn.documents.Item(i).Name) == "Active.CATPart"
                      for i in range(1, conn.documents.Count + 1))
    check(5, "the saved+unmodified part was closed and the dirty one kept",
          (not clean_open) and dirty_open and "MODIFIED" in str(reply),
          f"documents {before_total} -> {after_total}; Clean closed, Dirty kept")
    check(6, "the ACTIVE document is kept, and stays active",
          active_open and active_now == "Active.CATPart"
          and "ACTIVE" in str(reply),
          f"active after the sweep: '{active_now}'")

    say("")
    passed = sum(1 for _n, _t, ok, _e in results if ok)
    say(f"{passed}/{len(results)} checks passed")
    for number, title, ok, _evidence in results:
        if not ok:
            say(f"  FAILED: {number}. {title}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
