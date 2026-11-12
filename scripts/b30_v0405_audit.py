"""0.3.105 acceptance: answer the box, and stop claiming what five cells deny.

  1  a REAL CATIA message box (the SaveAs "replace it?" prompt, class #32770) is
     answered while the blocked call waits, and the count is reported
  2  a call with no box says nothing extra - the note is not wallpaper
  3  engraving still removes exactly 376.9911 mm3 with the watcher armed
  4  a pad with a line on it: 376.9911 mm3 before and after save/close/reopen
  5  a lofted bottle with a projected glyph: 7.9958 mm3 before and after
  6  the ceiling is downgraded and the advice withdrawn
  7  the README carries the measured projection grid

Everything happens in this script's own folder.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp import dialogs  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.diagnostics import _CAPABILITY_CEILINGS  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0405")
ROOT = os.path.join(_PROBE_ROOT, "audit")
RESULTS: list[tuple[int, str, bool, str]] = []
GLYPH = [[-9, 0], [-4, 20], [0, 20], [5, 0], [2.5, 0], [1, 5], [-1, 5],
         [-2.5, 0], [-9, 0]]


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, label: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((number, label, passed, detail))
    say(f"[{'OK ' if passed else 'FAIL'}] {number}. {label}"
        + (f"  |  {detail}" if detail else ""))


def close_ours(conn: CATIAConnection) -> None:
    for index in range(int(conn.app.Documents.Count), 0, -1):
        document = conn.app.Documents.Item(index)
        try:
            full = str(document.FullName or "")
        except Exception:  # noqa: BLE001
            full = ""
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    os.makedirs(ROOT, exist_ok=True)
    for name in os.listdir(ROOT):
        if name.lower().endswith(".catpart"):
            try:
                os.remove(os.path.join(ROOT, name))
            except OSError:
                pass
    conn = CATIAConnection()
    conn.connect()
    close_ours(conn)
    docs, gsd = DocumentTools(conn), GSDTools(conn)
    sk, pd = SketcherTools(conn), PartDesignTools(conn)

    say("0.3.105 acceptance - answer the box, withdraw the advice")
    say("=" * 78)

    def block(tag: str) -> None:
        docs.execute("catia_new_part", {"file_path": os.path.join(ROOT, f"{tag}.CATPart"),
                                        "overwrite": True})
        sk.execute("catia_create_sketch", {"plane": "xy"})
        sk.execute("catia_sketch_rectangle", {"x1": -50, "y1": -50,
                                              "x2": 50, "y2": 50})
        sk.execute("catia_close_sketch", {})
        pd.execute("catia_pad", {"height": 20.0})

    def line_on_top(tag: str) -> str:
        sk.execute("catia_create_sketch", {"plane": "xy", "offset": 20.0})
        sk.execute("catia_sketch_line", {"x1": -30, "y1": 0, "x2": 30, "y2": 0})
        sk.execute("catia_close_sketch", {})
        part = conn.get_active_part()
        return str(part.MainBody.Sketches.Item(
            int(part.MainBody.Sketches.Count)).Name)

    def volume() -> float | None:
        part = conn.get_active_part()
        return pd._body_volume_mm3(part, conn.get_active_part_body())

    # ── 1: a live box, answered and counted ───────────────────────────
    target = os.path.join(ROOT, "replace.CATPart")
    docs.execute("catia_new_part", {"file_path": target, "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_rectangle", {"x1": -20, "y1": -20, "x2": 20, "y2": 20})
    sk.execute("catia_close_sketch", {})
    doc = conn.active_document
    dialogs.save_as(doc, target)
    show_alerts = conn.app.DisplayFileAlerts
    conn.app.DisplayFileAlerts = True
    state: dict[str, object] = {}

    def save_again() -> None:
        # Its own proxy: the main thread's gives RPC_E_WRONG_THREAD (measured).
        import pythoncom  # noqa: PLC0415
        import win32com.client  # noqa: PLC0415

        pythoncom.CoInitialize()
        try:
            other = win32com.client.GetActiveObject("CATIA.Application")
            for index in range(1, int(other.Documents.Count) + 1):
                candidate = other.Documents.Item(index)
                if str(candidate.Name) == "replace.CATPart":
                    state["ok"] = str(candidate.SaveAs(target))
                    return
            state["error"] = "document not found from the second proxy"
        except Exception as error:  # noqa: BLE001
            state["error"] = str(error).splitlines()[0][:110]
        finally:
            pythoncom.CoUninitialize()

    worker = threading.Thread(target=save_again, name="audit-saveas", daemon=True)
    worker.start()

    def box_classes_now() -> list[str]:
        try:
            pid = dialogs.catia_pid()
            return [cls for _hwnd, cls, _t, wpid in dialogs._visible_windows()  # noqa: SLF001
                    if wpid == pid and cls == "#32770"]
        except Exception:  # noqa: BLE001
            return []

    # Wait for the box rather than guessing how long a save takes: the first
    # attempt scanned once at 1.5 s and saw nothing, while watch() answered one.
    seen: list[str] = []
    deadline = time.time() + 8.0
    while time.time() < deadline and worker.is_alive():
        seen = box_classes_now()
        if seen:
            break
        time.sleep(0.2)
    with dialogs.watch(interval=0.1) as answered:
        worker.join(timeout=12.0)
    released = not worker.is_alive()
    if not released:
        dialogs.dismiss(rounds=8, wait=0.4, prefer=dialogs.YES_WORDS)
        worker.join(timeout=5.0)
        released = not worker.is_alive()
    conn.app.DisplayFileAlerts = show_alerts
    check(1, "a live #32770 message box is answered and counted",
          answered.clicks >= 1 and released and bool(seen),
          f"clicks={answered.clicks} box_seen={bool(seen)} released={released}")

    # ── 3: engraving is unchanged with the watcher armed ──────────────
    block("plain")
    curve = line_on_top("plain")
    before = volume()
    reply = " ".join(str(gsd.execute("catia_surface_engrave",
                                     {"curve": curve, "depth": 2.0})).split())
    after = volume()
    removed = None if before is None or after is None else before - after
    check(3, "engraving still removes 376.9911 mm3 with the watcher armed",
          removed is not None and abs(removed - 376.9911) < 0.01,
          f"removed {removed:.4f}" if removed is not None else "unreadable")
    check(2, "...and that call, which raised no box, carries no note",
          "NOTE: CATIA raised" not in reply, reply[:80])

    # ── 4: the same numbers across a reopen, pad ──────────────────────
    path_b = os.path.join(ROOT, "reopen.CATPart")
    docs.execute("catia_new_part", {"file_path": path_b, "overwrite": True})
    sk.execute("catia_create_sketch", {"plane": "xy"})
    sk.execute("catia_sketch_rectangle", {"x1": -50, "y1": -50, "x2": 50, "y2": 50})
    sk.execute("catia_close_sketch", {})
    pd.execute("catia_pad", {"height": 20.0})
    curve_b = line_on_top("reopen")
    docs.execute("catia_save_document", {})
    docs.execute("catia_close_document", {})
    docs.execute("catia_open_document", {"file_path": path_b})
    before_b = volume()
    gsd.execute("catia_surface_engrave", {"curve": curve_b, "depth": 2.0})
    after_b = volume()
    removed_b = None if before_b is None or after_b is None else before_b - after_b
    check(4, "a pad cut after a reopen removes the same 376.9911 mm3",
          removed_b is not None and abs(removed_b - 376.9911) < 0.01,
          f"removed {removed_b:.4f}" if removed_b is not None else "unreadable")

    # ── 5: the same numbers across a reopen, lofted bottle ────────────
    path_c = os.path.join(ROOT, "bottle.CATPart")
    docs.execute("catia_new_part", {"file_path": path_c, "overwrite": True})
    for index, (radius, z) in enumerate(((30, 0), (34, 25), (22, 60))):
        gsd.execute("catia_gsd_point", {"x": 0, "y": 0, "z": z, "name": f"L{index}"})
        gsd.execute("catia_gsd_circle", {"center": f"L{index}", "support_plane": "xy",
                                         "radius": radius, "name": f"Sec{index}"})
    gsd.execute("catia_gsd_multi_section_surface",
                {"sections": ["Sec0", "Sec1", "Sec2"], "name": "Bottle"})
    sk.execute("catia_create_sketch", {"plane": "zx", "offset": -40.0})
    sk.execute("catia_sketch_strokes", {"strokes": [GLYPH]})
    sk.execute("catia_close_sketch", {})
    gsd.execute("catia_gsd_project", {"element": str(conn.last_sketch.Name),
                                      "support": "Bottle", "name": "GlyphR"})
    gsd.execute("catia_gsd_close_surface", {"surface": "Bottle"})
    docs.execute("catia_save_document", {})
    docs.execute("catia_close_document", {})
    docs.execute("catia_open_document", {"file_path": path_c})
    before_c = volume()
    reply_c = " ".join(str(gsd.execute("catia_surface_engrave",
                                       {"curve": "GlyphR", "depth": 0.4})).split())
    after_c = volume()
    removed_c = None if before_c is None or after_c is None else before_c - after_c
    check(5, "a projected glyph cut after a reopen removes the same 7.9958 mm3",
          removed_c is not None and abs(removed_c - 7.9958) < 0.01,
          f"removed {removed_c:.4f}" if removed_c is not None else "unreadable")

    # ── 6: the ceiling ────────────────────────────────────────────────
    entry = next(e for e in _CAPABILITY_CEILINGS
                 if "saved, closed and reopened" in e["member"])
    detail = entry["detail"]
    check(6, "the reopen row is downgraded and the advice withdrawn",
          entry["status"] == "SEEN_ONCE_NOT_REPRODUCED"
          and "WITHDRAWN" in detail and "376.9911" in detail
          and "7.9958" in detail,
          entry["status"])

    # ── 7: the README grid ────────────────────────────────────────────
    readme = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "README.md"), encoding="utf-8").read()
    check(7, "the README carries the measured projection grid",
          "68.284" in readme and "13.6 %" in readme and "16.0 %" in readme
          and "Do the projection and the engrave in ONE session" not in readme,
          "grid present, old advice gone")

    say()
    say("=" * 78)
    passed = sum(1 for _n, _l, ok, _d in RESULTS if ok)
    say(f"{passed}/{len(RESULTS)} passed")
    close_ours(conn)
    if passed != len(RESULTS):
        for number, label, ok, detail in RESULTS:
            if not ok:
                say(f"  FAILED {number}: {label} | {detail}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
