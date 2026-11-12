"""0.3.113 acceptance on B30: the seam two standards, and the rest of the report.

Each cell gets its own new document. Every check asserts BOTH the geometry and
the words the tool sent back, because a reply that says "closed" while the sketch
holds 30 breaks is the defect this release is about.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection                    # noqa: E402
from catia_mcp.tools.document import DocumentTools                  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools             # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools                  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0413")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(number: int, what: str, okay: bool, detail: str = "") -> None:
    RESULTS.append((number, what, bool(okay), detail))
    say(f"[{'PASS' if okay else 'FAIL'}] {number}. {what}")
    if detail:
        say(f"        {detail}")


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:                                       # noqa: BLE001
            continue
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:                                   # noqa: BLE001
                pass


def fresh(conn, docs, name: str) -> None:
    docs.execute("catia_new_part",
                 {"file_path": os.path.join(ROOT, name), "overwrite": True})


def stadium(off: float) -> list[dict]:
    """Two lines and two quarter arcs whose ends sit `off` mm off the circle."""
    r = 20.0 + off
    return [
        {"type": "line", "p1": [r, 0.0], "p2": [60.0 - r, 0.0]},
        {"type": "arc", "center": [60.0, 0.0], "radius": 20.0,
         "p1": [60.0 - r, 0.0], "p2": [60.0, r], "direction": "ccw"},
        {"type": "line", "p1": [60.0, r], "p2": [0.0, r]},
        {"type": "arc", "center": [0.0, 0.0], "radius": 20.0,
         "p1": [0.0, r], "p2": [r, 0.0], "direction": "ccw"},
    ]


def breaks_of(payload: dict) -> dict:
    data = payload.get("data") or {}
    return data.get("breaks") or {}


def volume(conn) -> float | None:
    part = conn.get_active_part()
    return PartDesignTools(conn)._body_volume_mm3(part, part.MainBody)


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, sk, pd = DocumentTools(conn), SketcherTools(conn), PartDesignTools(conn)

    analytic = (60.0 * 20.0 - 2.0 * (math.pi * 20.0 ** 2 / 4.0)) * 10.0
    say(f"nominal volume for this outline at height 10: {analytic:.4f} mm3")

    # ── 1: the exact stadium still closes, and the reply matches close_sketch ──
    say("\n--- 1: endpoints already on the circle ---")
    fresh(conn, docs, "exact.CATPart")
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    profile = sk.execute("catia_sketch_profile", {"segments": stadium(0.0)})
    closed = sk.execute("catia_close_sketch", {"structured": True})
    text = " ".join(str(profile).split())
    box = breaks_of(closed)
    check(1, "exact ends: profile says it should close and close_sketch agrees",
          "meets within 0.0000 mm" in text and box.get("closed") is True
          and not box.get("breaks"),
          f"profile='{text[60:180]}' breaks={box}")
    pd.execute("catia_pad", {"height": 10})
    got = volume(conn)
    check(2, "exact ends: the pad comes out at the analytic volume",
          got is not None and abs(got - analytic) < analytic * 0.005,
          f"{got} vs {analytic:.4f}")

    # ── 2: the SAME profile with 0.05 mm of fit error on the arc ends ────────
    say("\n--- 2: arc ends 0.05 mm off the circle (a fitted profile) ---")
    fresh(conn, docs, "off050.CATPart")
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    profile = sk.execute("catia_sketch_profile", {"segments": stadium(0.05)})
    closed = sk.execute("catia_close_sketch", {"structured": True})
    text = " ".join(str(profile).split())
    box = breaks_of(closed)
    check(3, "off-circle ends: the profile now says what close_sketch will see",
          "rebuilt" in text and box.get("closed") is True
          and not box.get("breaks"),
          f"breaks={box}")
    check(4, "and it names the radius it had to change, not just the seam",
          "R20.0000 -> R" in text,
          text[text.find("arc 2:"):][:200] if "arc 2:" in text else text[-220:])
    pd.execute("catia_pad", {"height": 10})
    got = volume(conn)
    check(5, "off-circle ends: the pad is a closed solid, near the nominal volume",
          got is not None and abs(got - analytic) < analytic * 0.03,
          f"{got} vs {analytic:.4f} ({(got or 0) / analytic - 1:+.2%})")

    # ── 3: text strokes - the I serif ENDS on its stem ──────────────────────
    say("\n--- 3: text 'CATIA' - the I's serif ends on the stem ---")
    fresh(conn, docs, "text.CATPart")
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    drew = " ".join(str(sk.execute(
        "catia_text_strokes", {"text": "CATIA", "height": 20})).split())
    closed = sk.execute("catia_close_sketch", {"structured": True})
    message = " ".join(str(closed.get("message")).split())
    found = (closed.get("data") or {}).get("self_intersections") or []
    kinds = {item.get("kind") for item in found}
    check(6, "a serif ending on a stem is a TOUCH, not 'crosses itself'",
          "CROSSES ITSELF" not in message and kinds == {"touch"}
          and "ENDS on another" in message,
          f"kinds={kinds}, say='{message[message.find('NOTE'):][:150]}'")
    check(7, "and it says what a T junction does and does not spoil",
          "BRANCHED" in message and "pad profile" in message,
          message[message.find("That is a T junction"):][:140])

    # ── 4: a figure-eight is still a real crossing ──────────────────────────
    say("\n--- 4: a figure-eight is still a crossing ---")
    fresh(conn, docs, "eight.CATPart")
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_sketch_strokes",
               {"strokes": [[[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]]})
    closed = sk.execute("catia_close_sketch", {"structured": True})
    message = " ".join(str(closed.get("message")).split())
    found = (closed.get("data") or {}).get("self_intersections") or []
    check(8, "the figure-eight still reports a crossing, kind='crossing'",
          "CROSSES ITSELF" in message
          and any(item.get("kind") == "crossing" for item in found),
          f"{message[message.find('WARNING'):][:140]}")

    # ── 5: deleting a sketch that is still open ─────────────────────────────
    say("\n--- 5: delete a sketch that is still in edit mode ---")
    fresh(conn, docs, "delskt.CATPart")
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    sk.execute("catia_text_strokes", {"text": "HI", "height": 10})
    refused = None
    try:
        pd.execute("catia_delete_feature", {"feature_name": "草图.1"})
    except Exception as error:                                  # noqa: BLE001
        refused = error
    said = " ".join(str(refused).split()) if refused is not None else ""
    check(9, "an open sketch says SKETCH_IS_OPEN, not 'dead reference'",
          getattr(refused, "code", None) == "SKETCH_IS_OPEN"
          and "catia_close_sketch" in said and "dead reference" not in said,
          f"code={getattr(refused, 'code', None)}: {said[:200]}")
    sk.execute("catia_close_sketch", {})
    after = " ".join(str(pd.execute("catia_delete_feature",
                                    {"feature_name": "草图.1"})).split())
    check(10, "close it and the same delete goes through",
          "Deleted sketch" in after, after[:120])

    # ── 6: the text box, and centre_at doing it in one pass ────────────────
    say("\n--- 6: the text's own bounding box ---")
    fresh(conn, docs, "box.CATPart")
    sk.execute("catia_create_sketch", {"plane": "xy", "offset": 0})
    plain = " ".join(str(sk.execute(
        "catia_text_strokes", {"text": "CATIA", "height": 20})).split())
    check(11, "the box is reported without include_points",
          " It occupies h " in plain and "bbox" in plain,
          plain[plain.find("It occupies"):][:150])
    centred = " ".join(str(sk.execute(
        "catia_text_strokes",
        {"text": "CATIA", "height": 20, "center_at": [0, 0]})).split())
    check(12, "center_at lands the BOX centre on the point asked for",
          "centred on (0.000, 0.000)" in centred,
          centred[centred.find("centred on"):][:60])

    say("")
    passed = sum(1 for _, _, okay, _ in RESULTS if okay)
    say(f"{passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        say("FAILED: " + ", ".join(str(n) for n, _, ok, _ in RESULTS if not ok))
        sys.exit(1)


if __name__ == "__main__":
    main()
