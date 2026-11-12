"""Two claims from the 2026-11-12/13 reports, one document each.

A. `curve_on_surface`'s `support` is "only used to choose the BODY" (our own
   docstring). The reporter's 36 points all moved 0.6000 mm - exactly the depth of
   the recess the text was meant to sit in - and passing `support=Face.24` made
   them move 0.0000. A recess gives two parallel walls 0.6 mm apart, so the
   question is answerable with three calls on one body.

B. A pocket that removes nothing is rolled back, and the reply says "CATIA removed
   the sketch" - which makes the retry loop (change a parameter, try again)
   impossible whenever the sketch held an arc. Whose delete is it? If the sketch
   survives the FEATURE delete, that sentence is accusing CATIA of our bug.

    .venv/Scripts/python.exe scripts/b30_v0415_support_and_sketch.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0415")


def say(line: str = "") -> None:
    print(line, flush=True)


def flatten(reply) -> str:
    return " ".join(str(reply).split())


def travelled(reply: str) -> str:
    """The 'travelled a to b mm' figure from a curve_on_surface reply."""
    text = flatten(reply)
    if "travelled " not in text:
        return "?"
    return text.split("travelled ", 1)[1].split(" mm", 1)[0]


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
        if full and os.path.abspath(full).startswith(os.path.abspath(ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def sketch_names(conn: CATIAConnection) -> list[str]:
    """Every sketch name in every body - read straight from the tree."""
    part = conn.get_active_part()
    found: list[str] = []
    bodies = part.Bodies
    for index in range(1, bodies.Count + 1):
        try:
            sketches = bodies.Item(index).Sketches
        except Exception:  # noqa: BLE001
            continue
        for position in range(1, sketches.Count + 1):
            found.append(str(sketches.Item(position).Name))
    return found


def planar_faces(part_design) -> list[tuple[float, str]]:
    listing = json.loads(part_design.execute("catia_list_faces", {}))
    found = []
    for entry in listing.get("faces", []):
        area = entry.get("area_mm2")
        if area is not None and "planar" in str(entry.get("type", "")).lower():
            found.append((float(area), str(entry.get("name"))))
    return found


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)
    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    gsd = GSDTools(conn)

    # ── A: two parallel walls 0.6 mm apart ──────────────────────────────────
    say("")
    say("── A: does `support` pick the FACE, or only the body? ──")
    recess_path = os.path.join(ROOT, "RECESS.CATPart")
    if os.path.exists(recess_path):
        os.remove(recess_path)
    documents.execute("catia_new_part", {"name": "RECESS",
                                         "file_path": recess_path,
                                         "overwrite": True})
    activate(conn, "RECESS.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -20, "y1": -10, "x2": 20, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 20})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 20})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -10, "y1": -5, "x2": 10, "y2": 5})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pocket", {"depth": 0.6})
    say("body: 40x20 block, a 20x10 x 0.6 recess in its top face (floor z=19.4)")

    faces = planar_faces(part_design)
    say(f"planar faces (area, name): {faces[:8]}")
    floor = min(faces, key=lambda item: abs(item[0] - 200.0))[1] if faces else None
    top = min(faces, key=lambda item: abs(item[0] - 1400.0))[1] if faces else None
    say(f"recess floor = {floor} (200 mm2), outer top face = {top} (1400 mm2)")

    strokes = [[-7.0, -2.0], [-2.0, -2.0], [0.0, 2.0], [2.0, -2.0], [7.0, -2.0]]
    for label, extra in (("no support (Face.1)", {}),
                         (f"support={floor}", {"support": floor}),
                         (f"support={top}", {"support": top})):
        arguments = {"plane": "xy", "offset": 19.4, "strokes": [strokes],
                     "name": "Snap"}
        arguments.update(extra)
        try:
            reply = gsd.execute("catia_curve_on_surface", arguments)
        except Exception as error:  # noqa: BLE001
            say(f"  {label:24} FAILED {str(error).splitlines()[0][:110]}")
            continue
        say(f"  {label:24} travelled {travelled(reply)} mm   "
            f"| {flatten(reply)[:110]}")
        # Remove the spline so the next call starts from a clean set.
        try:
            for name in ("Snap",):
                part_design.execute("catia_delete_feature", {"name": name})
        except Exception:  # noqa: BLE001
            pass

    # ── A2: one plain wall, outline 0.6 mm in front of it ────────────────────
    #
    # A2 is the decisive cell. With the outline already sitting ON the body the
    # three supports above all answered 0.0000, which cannot tell "support is
    # ignored" from "support happened to be the same wall". Here the nearest wall
    # is unambiguous (0.6 mm straight ahead), so a support that names a DIFFERENT
    # face either moves the points to that face (support selects the face) or
    # changes nothing at all (support only selects the body).
    say("")
    say("── A2: plain wall, outline 0.6 mm in front of it ──")
    flat_path = os.path.join(ROOT, "FLAT.CATPart")
    if os.path.exists(flat_path):
        os.remove(flat_path)
    documents.execute("catia_new_part", {"name": "FLAT", "file_path": flat_path,
                                         "overwrite": True})
    activate(conn, "FLAT.CATPart")
    # 100 x 20 x 10, so every face has its own area: z=0/z=10 are 2000, y=0/y=20
    # are 1000, x=0/x=100 are 200. A support on an x face is then unmistakable -
    # if it is honoured the points travel ~90 mm, if it is ignored they land 0.6
    # ahead on the y=0 wall.
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0, "y1": 0, "x2": 100, "y2": 20})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    faces = planar_faces(part_design)
    say(f"planar faces (area, name): {faces}")
    end_face = next((name for area, name in faces if abs(area - 200.0) < 0.5), None)
    say(f"an end face (200 mm2, x=0 or x=100) = {end_face}")

    # The zx frame is u = Z, v = X, so the 2D pairs below are (z, x) and the
    # offset is y. 0.6 mm in front of the y = 0 wall, well inside x 0..100.
    strokes = [[3.0, 10.0], [5.0, 25.0], [3.0, 40.0]]
    for label, extra in (("no support", {}),
                         (f"support={end_face}", {"support": end_face})):
        arguments = {"plane": "zx", "offset": -0.6, "strokes": [strokes],
                     "name": "Snap2"}
        arguments.update(extra)
        try:
            reply = gsd.execute("catia_curve_on_surface", arguments)
        except Exception as error:  # noqa: BLE001
            say(f"  {label:22} FAILED {str(error).splitlines()[0][:110]}")
            continue
        text = flatten(reply)
        origin = text.split("onto ", 1)[-1].split(",", 1)[0] if "onto " in text else "?"
        say(f"  {label:22} travelled {travelled(reply)} mm  (landed {origin})")
        try:
            part_design.execute("catia_delete_feature", {"name": "Snap2"})
        except Exception:  # noqa: BLE001
            pass

    # ── B: a pocket that cuts nothing - does the sketch survive? ────────────
    say("")
    say("── B: a NO_MATERIAL pocket - is the sketch still there afterwards? ──")
    roll_path = os.path.join(ROOT, "ROLL.CATPart")
    if os.path.exists(roll_path):
        os.remove(roll_path)
    documents.execute("catia_new_part", {"name": "ROLL", "file_path": roll_path,
                                         "overwrite": True})
    activate(conn, "ROLL.CATPart")
    sketcher.execute("catia_create_sketch", {"plane": "xy"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": -20, "y1": -10, "x2": 20, "y2": 10})
    sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_pad", {"height": 10})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": 100})
    sketcher.execute("catia_sketch_circle", {"cx": 0, "cy": 0, "radius": 5})
    sketcher.execute("catia_close_sketch", {})
    say(f"  sketches before the pocket: {sketch_names(conn)}")

    try:
        reply = flatten(part_design.execute("catia_pocket", {"depth": 3}))
        say("  pocket answered: " + reply[:200])
    except Exception as error:  # noqa: BLE001 - the refusal is the datum
        say("  pocket refused: " + " ".join(str(error).split())[:420])
    say(f"  sketches after: {sketch_names(conn)}")
    say(f"  VERDICT B: {sketch_names(conn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
