"""0.3.76 on B30: data.breaks[] survives the trip through the real tools.

The 2026-11-06 report asked for the full loose-endpoint list as a structured
field because the sentence was truncated by the client (1242 chars on a six-arc
outline). This measures the three claims the release makes on a real part:

  * an open profile returns an envelope whose data.breaks[] names the gap;
  * a closed profile answers closed=true with no false alarms;
  * the default reply is still the plain sentence, and a pad still builds.

Everything is created under F:\\catia_probe_v0376 and closed again. Documents
that were open before the run are left alone, including the one in front.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0376"
RESULTS: list[tuple[str, bool, str]] = []


def say(line: str) -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""))


def rectangle(sketcher: SketcherTools, gap: float = 0.0) -> None:
    """Four lines; the closing one stops `gap` mm short of the origin."""
    for x1, y1, x2, y2 in ((0.0, 0.0, 40.0, 0.0),
                           (40.0, 0.0, 40.0, 20.0),
                           (40.0, 20.0, 0.0, 20.0),
                           (0.0, 20.0, 0.0, gap)):
        sketcher.execute("catia_sketch_line",
                         {"x1": x1, "y1": y1, "x2": x2, "y2": y2})


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs_tool = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    try:
        before_active = str(conn.app.ActiveDocument.Name)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing = {str(conn.documents.Item(i).Name)
                for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count} document(s), active '{before_active}'")
    os.makedirs(ROOT, exist_ok=True)
    for stale in os.listdir(ROOT):
        try:
            os.remove(os.path.join(ROOT, stale))
        except OSError as error:  # noqa: BLE001
            say(f"   (could not remove stale {stale}: {error})")

    part_path = os.path.join(ROOT, "probe_breaks.CATPart")
    part_name = ""
    try:
        docs_tool.execute("catia_new_part",
                          {"name": "ProbeBreaks", "file_path": part_path})
        part_name = str(conn.active_document.Name)
        say(f"created {part_name} at {part_path}")

        # ── 1) an open profile, as a structured reply ──────────────────────
        say("\n1  open profile -> data.breaks[]")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        rectangle(sketcher, gap=0.0002)
        payload = sketcher.execute("catia_close_sketch", {"structured": True})
        check("the reply is a dict (the server forwards it as an envelope)",
              isinstance(payload, dict), type(payload).__name__)
        if isinstance(payload, dict):
            breaks = payload["data"]["breaks"]
            say(f"      breaks: {breaks['breaks']}")
            check("code / tool are the close-sketch ones",
                  payload["code"] == "SKETCH_CLOSED"
                  and payload["tool"] == "catia_close_sketch", payload["code"])
            check("closed is false", breaks["closed"] is False)
            check("exactly one break is listed", len(breaks["breaks"]) == 1)
            check("the gap is the 0.0002 mm we drew",
                  abs(breaks["smallest_gap_mm"] - 0.0002) < 1e-5,
                  str(breaks["smallest_gap_mm"]))
            check("the message still contains the sentence",
                  "WARNING: the profile is not closed" in payload["message"])
            check("nothing was hidden by a truncation: the list is intact",
                  breaks["breaks"][0]["gap_mm"] == breaks["smallest_gap_mm"])

        # ── 2) a closed profile, no false alarm, and the pad still works ────
        say("\n2  closed profile -> closed=true, pad builds")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        rectangle(sketcher)
        closed = sketcher.execute("catia_close_sketch", {"structured": True})
        if isinstance(closed, dict):
            check("closed is true", closed["data"]["breaks"]["closed"] is True)
            check("breaks[] is empty", closed["data"]["breaks"]["breaks"] == [])
            check("no WARNING in the message",
                  "WARNING" not in closed["message"])
        else:
            check("closed profile returned an envelope", False, type(closed).__name__)
        padded = part_design.execute("catia_pad", {"height": 5})
        say(f"      pad: {padded[:150]}")
        check("40 x 20 x 5 still gives 4000 mm3", "4000" in padded, padded[:120])

        # ── 3) the default is still the same sentence ──────────────────────
        say("\n3  default call -> unchanged text reply")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        rectangle(sketcher)
        text = sketcher.execute("catia_close_sketch", {})
        check("a plain string comes back", isinstance(text, str), type(text).__name__)
        check("the sentence is the one callers already parse",
              isinstance(text, str) and "Sketch '" in text
              and "closed." in text, str(text)[:120])
        check("no warning for a closed profile",
              isinstance(text, str) and "WARNING" not in text)

    except Exception as error:  # noqa: BLE001 - report, then clean up
        check("no COM error during the run", False, f"{type(error).__name__}: {error}")

    finally:
        say("\ncleanup")
        # Close BY NAME from the end: indices shift under a Close.
        for name in sorted({str(conn.documents.Item(i).Name)
                            for i in range(1, conn.documents.Count + 1)}
                           - existing, reverse=True):
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == name:
                    try:
                        conn.documents.Item(i).Close()
                        say(f"   closed {name}")
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        for stale in os.listdir(ROOT):
            try:
                os.remove(os.path.join(ROOT, stale))
            except OSError:
                pass
        try:
            os.rmdir(ROOT)
        except OSError:
            pass
        if before_active:
            for i in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(i).Name) == before_active:
                    try:
                        conn.documents.Item(i).Activate()
                    except Exception:  # noqa: BLE001
                        pass
                    break
        say(f"session after: {conn.documents.Count} document(s)")

    failed = [label for label, ok, _ in RESULTS if not ok]
    say(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        say("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
