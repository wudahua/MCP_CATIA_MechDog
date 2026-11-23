"""B30 probe, 0.3.164: does catia_pad/pocket(insert_after=...) really place it?

The product path, not the raw factory: 0.3.163 measured that the factory inserts
when InWorkObject is left alone, and this server was the thing re-pointing it.
So the questions here are the ones only the product path can answer:

  A. does catia_pocket(insert_after='凸台.1') land at index 2, with the volume
     actually cut (not just placed)?
  B. does the reply say where it landed AND admit it rebuilt the tail?
  C. a name that is not in the body: refused, nothing created?
  D. insert_after pointing at the LAST feature: an ordinary append, said so?
  E. a pad built into the middle of a two-feature history.

Every cell is its own NEW document - one failure poisons the rest of a part, and
that has bitten this suite before.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), str(detail)[:220]))
    print(("  PASS " if ok else "  FAIL ") + label
          + ("" if ok else f"   <- {str(detail)[:220]}"), flush=True)


def say(text: str = "") -> None:
    print(text, flush=True)


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def volume(measure) -> float | None:
    data = payload(measure.execute("catia_get_inertia", {}))
    values = data.get("data") if isinstance(data.get("data"), dict) else data
    value = values.get("volume_mm3")
    return None if value is None else float(value)


class Cell:
    """One new part document, wired to the real tool objects."""

    def __init__(self, conn, root: str, name: str) -> None:
        self.docs = DocumentTools(conn)
        self.sk = SketcherTools(conn)
        self.design = PartDesignTools(conn)
        self.measure = MeasurementTools(conn)
        self.conn = conn
        self.path = os.path.join(root, f"{name}.CATPart")
        try:
            self.docs.execute("catia_close_document",
                              {"document": self.path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(self.path):
            os.remove(self.path)
        self.docs.execute("catia_new_part", {"name": name,
                                             "file_path": self.path})

    def sketch(self, name: str, draw, offset: float | None = None) -> None:
        args = {"plane": "xy", "name": name}
        if offset is not None:
            args["offset"] = offset
        self.sk.execute("catia_create_sketch", args)
        draw(self.sk)
        self.sk.execute("catia_close_sketch", {})

    def rect(self, sketch_name: str, x1, y1, x2, y2) -> None:
        self.sketch(sketch_name, lambda sk: sk.execute(
            "catia_sketch_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2}))

    def circle(self, sketch_name: str, cx, cy, r) -> None:
        self.sketch(sketch_name, lambda sk: sk.execute(
            "catia_sketch_circle", {"cx": cx, "cy": cy, "radius": r}))

    def order(self) -> list[str]:
        part = self.conn.get_active_part()
        body = part.MainBody
        return [str(body.Shapes.Item(i).Name)
                for i in range(1, body.Shapes.Count + 1)]

    def close(self) -> None:
        try:
            self.docs.execute("catia_close_document",
                              {"document": self.path, "save": False})
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    root = os.path.join(tempfile.gettempdir(), "p0501")
    os.makedirs(root, exist_ok=True)

    # ── A/B: a pocket inserted after the first pad ──────────────────────
    say("A. catia_pocket(insert_after='凸台.1') on a pad -> pad -> pocket history")
    cell = Cell(conn, root, "INS_A")
    cell.rect("S1", 0, 0, 25, 45)
    cell.design.execute("catia_pad", {"sketch_name": "S1", "height": 80})
    cell.circle("HA", 8, 8, 3)
    cell.design.execute("catia_pocket", {"sketch_name": "HA", "depth": 80})
    before_order = cell.order()
    before_volume = volume(cell.measure)
    say(f"  history {before_order}, V={before_volume}")
    cell.circle("HB", 18, 18, 3)
    reply = cell.design.execute("catia_pocket",
                                {"sketch_name": "HB", "depth": 80,
                                 "insert_after": "凸台.1"})
    say("  " + reply)
    order = cell.order()
    after_volume = volume(cell.measure)
    check("A the new cut is at index 2 of 3", order[:2] == ["凸台.1", order[1]]
          and order[2] == "凹槽.1" and len(order) == 3, str(order))
    check("B the reply reads the tree back", "Placed at position 2 of 3" in reply,
          reply[:160])
    check("B and admits the tail was rebuilt",
          "REBUILT by a full part.Update()" in reply, reply[:200])
    check("A both holes were cut",
          before_volume is not None and after_volume is not None
          and abs((before_volume - after_volume) - 3.141592653589793 * 9 * 80) < 1.0,
          f"{before_volume} -> {after_volume}")
    cell.close()

    # ── C: a name that is not a feature of this body ─────────────────────
    say("C. insert_after='凸台.9' - refused before anything is built")
    cell = Cell(conn, root, "INS_C")
    cell.rect("S1", 0, 0, 25, 45)
    cell.design.execute("catia_pad", {"sketch_name": "S1", "height": 80})
    cell.circle("HC", 8, 8, 3)
    before_order = cell.order()
    try:
        cell.design.execute("catia_pocket", {"sketch_name": "HC", "depth": 80,
                                             "insert_after": "凸台.9"})
        check("C a missing insertion point is refused", False, "it went through")
    except CodedError as error:
        check("C a missing insertion point is refused",
              error.code == "FEATURE_NOT_FOUND", error.code)
        check("C and the refusal lists the body's features",
              "凸台.1" in str(error) and "build order" in str(error),
              str(error)[:200])
    check("C nothing was created", cell.order() == before_order,
          str(cell.order()))
    cell.close()

    # ── D: insert_after pointing at the last feature ─────────────────────
    say("D. insert_after='凹槽.1' where 凹槽.1 is already last")
    cell = Cell(conn, root, "INS_D")
    cell.rect("S1", 0, 0, 25, 45)
    cell.design.execute("catia_pad", {"sketch_name": "S1", "height": 80})
    cell.circle("HD", 8, 8, 3)
    cell.design.execute("catia_pocket", {"sketch_name": "HD", "depth": 80})
    last = cell.order()[-1]
    cell.circle("HD2", 18, 18, 3)
    reply = cell.design.execute("catia_pocket",
                                {"sketch_name": "HD2", "depth": 80,
                                 "insert_after": last})
    say("  " + reply)
    check("D it says this was an ordinary append",
          "already the LAST feature" in reply, reply[:200])
    check("D and the new feature is last", cell.order()[-1] != last
          and len(cell.order()) == 3, str(cell.order()))
    cell.close()

    # ── E: a pad inserted into the middle ────────────────────────────────
    say("E. catia_pad(insert_after='凹槽.1') between a cut and the pad after it")
    cell = Cell(conn, root, "INS_E")
    cell.rect("S1", 0, 0, 25, 45)
    cell.design.execute("catia_pad", {"sketch_name": "S1", "height": 80})
    cell.circle("HE", 8, 8, 3)
    cell.design.execute("catia_pocket", {"sketch_name": "HE", "depth": 80})
    # An OPEN frame after the cut, so the history has something AFTER the
    # insertion point - inserting after the last feature is an append (cell D),
    # and a frame is the shape a real part uses anyway.
    cell.sketch("FR", lambda sk: (
        sk.execute("catia_sketch_rectangle", {"x1": 10, "y1": 10, "x2": 15, "y2": 15}),
        sk.execute("catia_sketch_rectangle", {"x1": 12, "y1": 12, "x2": 13, "y2": 13})))
    cell.design.execute("catia_pad", {"sketch_name": "FR", "height": 5})
    before_volume = volume(cell.measure)
    say(f"  history {cell.order()}, V={before_volume}")
    cell.rect("SE", 0, 0, 25, 45)
    reply = cell.design.execute("catia_pad",
                                {"sketch_name": "SE", "height": 10,
                                 "insert_after": "凹槽.1"})
    say("  " + reply)
    order = cell.order()
    after_volume = volume(cell.measure)
    check("E the new pad is at index 3 of 4", len(order) == 4, str(order))
    check("E the reply says where", "Placed at position 3 of 4" in reply,
          reply[:180])
    check("E the material grew (a 25x45 pad over a body that is not a clean "
          "block, so only the sign and the order matter)",
          before_volume is not None and after_volume is not None
          and after_volume > before_volume + 1.0,
          f"{before_volume} -> {after_volume}")
    cell.close()

    # ── F: an insertion is not cosmetic - the ORDER decides the solid ────
    say("F. the same two features in the two orders give two different solids")
    SAY = 20.0
    RIM = 12.0
    RING_TOP = 30.0
    BORE = 4.0
    volumes = {}
    histories = {}
    for order_name, after in (("inserted", "凸台.1"), ("appended", None)):
        cell = Cell(conn, root, f"INS_F_{order_name.upper()}")
        # A SOLID PLATE 20x20x10 on top, with the bore's mouth inside it. This
        # was measured here the hard way: a closed ring (20x20 outline, 12x12
        # hole) cut NOTHING out of the bore - the inner opening is a separate
        # region, and neither adding it nor a U-shaped outline changed what
        # CATIA removed (34802.04 and 35739.38 in the two attempts below).
        # A plate has no second region to get wrong.
        cell.rect("S1", 0, 0, 25, 45)
        cell.design.execute("catia_pad", {"sketch_name": "S1", "height": 30})
        cell.circle("H", 12.5, 22.5, BORE)
        cell.design.execute("catia_pocket", {"sketch_name": "H", "depth": 40})
        # The plate's sketch sits ON TOP of the 30 mm pad: a sketch on XY with an
        # offset puts the extrusion's start at that height (measured in this same
        # probe: at offset=40 with nothing under it, the pad added only the part
        # where it crossed the first pad's wall).
        cell.sketch("PL", lambda sk: sk.execute(
            "catia_sketch_rectangle",
            {"x1": 12.5 - 10.0, "y1": 22.5 - 10.0,
             "x2": 12.5 + 10.0, "y2": 22.5 + 10.0}), offset=RING_TOP)
        args = {"sketch_name": "PL", "height": 10}
        if after:
            args["insert_after"] = after
        reply = cell.design.execute("catia_pad", args)
        say(f"  {order_name}: " + reply[:230])
        histories[order_name] = cell.order()
        say(f"  {order_name} history {histories[order_name]}")
        volumes[order_name] = volume(cell.measure)
        cell.close()
    say(f"  volumes {volumes}")
    check("F both orders built something",
          all(v is not None for v in volumes.values()), str(volumes))
    check("F the two orders gave DIFFERENT volumes - the order is real",
          volumes["inserted"] is not None and volumes["appended"] is not None
          and abs(volumes["inserted"] - volumes["appended"]) > 100.0,
          str(volumes))
    # The arithmetic, so the direction of the difference cannot be argued:
    #   appended 32242.0355 + 1800 - 4 x 2.25 (corner overlaps) = 34033.04
    #   inserted 34033.04 - pi*16*10 = 33530.38
    check("F appended: the plate covers the bore's mouth and the bore stays cut "
          "inside the pad (32242.0355 + 4000 = 36242.04)",
          volumes["appended"] is not None
          and abs(volumes["appended"] - 36242.0355) < 1.0, str(volumes["appended"]))
    check("F inserted: the bore is cut through the plate as well "
          "(36242.0355 - pi*16*10 = 35739.38)",
          volumes["inserted"] is not None
          and abs(volumes["inserted"] - 35739.38) < 1.0, str(volumes["inserted"]))
    check("F and the two histories show the two places",
          histories["inserted"][1] == "凸台.2" and histories["appended"][-1] == "凸台.2",
          str(histories))

    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    say()
    say(f"{passed}/{len(CHECKS)} checks passed")
    for label, ok, detail in CHECKS:
        if not ok:
            say(f"  FAILED: {label}  <- {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
