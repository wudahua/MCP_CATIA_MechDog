"""0.3.82 probe: the two claims in the 0.3.81 report that contradict this package.

(a) "catia_new_part cannot save into a folder whose path is not ASCII". 0.3.74
    measured this and concluded the opposite - that only the FILE NAME matters,
    and the reply was changed to say so. That measurement used save_as on a
    document that already had a path. The report's control is sharper: an EMPTY
    part, first SaveAs, non-ASCII FOLDER, ASCII name - which is exactly the call
    catia_new_part makes. Four combinations, plus the follow-up the report says
    works (save an already-saved document into that folder).
(b) "close_sketch calls a closed spline+line outline open".  The report's profile
    is one 6-point spline plus four lines whose ends coincide to the digit.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0382"
CJK = os.path.join(ROOT, "饮料瓶瓶底曲面建模")
ASCII_DIR = os.path.join(ROOT, "ascii")


def say(line: str = "") -> None:
    print(line, flush=True)


def attempt(label, call):
    try:
        reply = call()
    except Exception as error:  # noqa: BLE001 - the failure IS the measurement
        say(f"   {label}: FAILED {type(error).__name__} "
            f"{getattr(error, 'code', '')}")
        say("     " + str(error).replace("\n", "\n     ")[:340])
        return None
    say(f"   {label}: OK -> {str(reply)[:150]}")
    return reply


def main() -> int:
    for folder in (ROOT, CJK, ASCII_DIR):
        os.makedirs(folder, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    mine = os.path.normcase(os.path.abspath(ROOT))
    say(f"session before: {conn.documents.Count} document(s)")

    try:
        say("\n(a) catia_new_part, four combinations")
        attempt("CJK folder / ascii name",
                lambda: docs.execute("catia_new_part", {
                    "name": "PB_CJK", "overwrite": True,
                    "file_path": os.path.join(CJK, "PB_CJK.CATPart")}))
        attempt("CJK folder / ascii name (again)",
                lambda: docs.execute("catia_new_part", {
                    "name": "PB_CJK2", "overwrite": True,
                    "file_path": os.path.join(CJK, "PB_CJK2.CATPart")}))
        attempt("ascii folder / ascii name",
                lambda: docs.execute("catia_new_part", {
                    "name": "PB_ASCII", "overwrite": True,
                    "file_path": os.path.join(ASCII_DIR, "PB_ASCII.CATPart")}))
        attempt("ascii folder / CJK name",
                lambda: docs.execute("catia_new_part", {
                    "name": "PB_CJKNAME", "overwrite": True,
                    "file_path": os.path.join(ASCII_DIR, "探针.CATPart")}))

        say("\n   and the follow-up the report says works: a document that")
        say("   ALREADY has a path, saved into the CJK folder")
        attempt("save_document -> CJK folder",
                lambda: docs.execute("catia_save_document", {
                    "file_path": os.path.join(CJK, "PB_MOVED.CATPart")}))

        say("\n(b) close_sketch on one 6-point spline plus four lines")
        docs.execute("catia_new_part", {
            "name": "PB_SPLINE", "overwrite": True,
            "file_path": os.path.join(ASCII_DIR, "PB_SPLINE.CATPart")})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        # A closed-ish outline: spline from (0,0) to (40,0), then four lines
        # back to (0,0). Every joint is the same pair of numbers.
        sketcher.execute("catia_sketch_spline", {
            "points": [[0, 0], [10, 0], [20, 0], [30, 0], [40, 0], [40, 10]],
            "closed": False})
        for x1, y1, x2, y2 in ((40, 10, 40, 20), (40, 20, 0, 20),
                               (0, 20, 0, 0)):
            sketcher.execute("catia_sketch_line",
                             {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        payload = sketcher.execute("catia_close_sketch", {"structured": True})
        data = payload.get("data") if isinstance(payload, dict) else {}
        say("   breaks: " + str(data.get("breaks"))[:700])
        ends = data.get("breaks", {}).get("endpoints")
        say(f"   endpoints recorded: {ends} (spline adds 2, three lines add 6 "
            "-> 8 is right; 6 means the spline's ends were never recorded)")
    finally:
        say("\ncleanup: closing only documents under " + ROOT)
        for index in range(int(conn.documents.Count), 0, -1):
            try:
                doc = conn.documents.Item(index)
                full = os.path.normcase(os.path.abspath(str(doc.FullName)))
            except Exception:  # noqa: BLE001
                continue
            if not full.startswith(mine):
                continue
            try:
                doc.Close()
                say(f"   closed {os.path.basename(full)}")
            except Exception as error:  # noqa: BLE001
                say(f"   could not close {os.path.basename(full)}: "
                    f"{str(error).splitlines()[0][:60]}")
        say(f"session after: {conn.documents.Count}")
        should = os.path.join(CJK, "PB_MOVED.CATPart")
        if os.path.isfile(should):
            say(f"   PB_MOVED.CATPart exists in the CJK folder "
                f"({os.path.getsize(should)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
