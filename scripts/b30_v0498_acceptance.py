"""B30 probe for 0.3.161 - the two capabilities the report asked for.

  A  catia_set_visibility on a COMPONENT INSTANCE, with the read-back
  B  catia_edit_pad changing a depth that already exists, volume and all
  C  catia_list_components says honestly that it cannot read `fixed`
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.results import CodedError  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

results: list[tuple[str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def _volume(measure) -> float | None:
    data = payload(measure.execute("catia_get_inertia", {}))
    values = data.get("data") if isinstance(data.get("data"), dict) else data
    value = values.get("volume_mm3")
    return None if value is None else float(value)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0498cap")
    os.makedirs(root, exist_ok=True)

    def fresh(name):
        path = os.path.join(root, name)
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(path):
            os.remove(path)
        return path

    part_path = fresh("EDIT0498.CATPart")
    docs.execute("catia_new_part", {"name": "EDIT0498", "file_path": part_path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 25, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S", "height": 80})
    docs.execute("catia_save_document", {})

    asm_path = fresh("EDIT0498.CATProduct")
    docs.execute("catia_new_product", {"name": "EDIT0498A", "file_path": asm_path})
    for _ in range(2):
        assembly.execute("catia_add_component", {"file_path": part_path})

    # ── A. instance visibility ───────────────────────────────────────────
    say("\n== A catia_set_visibility on an instance")
    try:
        hidden = str(design.execute("catia_set_visibility",
                                    {"name": "EDIT0498.1"}))
        say("  " + hidden)
        check("A the instance is hidden", "is now hidden" in hidden, hidden[:80])
        check("A the reply carries the read-back",
              "GetShow() -> (0, 1)" in hidden, hidden[:120])
        check("A and it says the other instances are untouched",
              "other 1 component(s)" in hidden, "")
    except CodedError as error:
        say(f"  refused: {error}")
        check("A the instance is hidden", False, str(error)[:120])

    shown = str(design.execute("catia_set_visibility",
                               {"name": "EDIT0498.1", "show": True}))
    say("  " + shown)
    check("A and it comes back", "is now shown" in shown
          and "GetShow() -> (0, 0)" in shown, shown[:100])

    second = str(design.execute("catia_set_visibility", {"name": "EDIT0498.2"}))
    check("A the second instance is reachable on its own",
          "EDIT0498.2" in second and "is now hidden" in second, second[:80])

    try:
        design.execute("catia_set_visibility", {"name": "NoSuchThing.9"})
        check("A control: a name that is neither a node nor an instance is refused",
              False, "accepted")
    except CodedError as error:
        check("A control: a name that is neither a node nor an instance is refused",
              error.code in ("ARGUMENT_REJECTED", "STEP_EXCEPTION"),
              f"{error.code}: {str(error)[:80]}")

    # ── C. the fixed field ───────────────────────────────────────────────
    say("\n== C catia_list_components and the fixed state")
    listing = payload(assembly.execute("catia_list_components", {}))
    say("  fixed_state: " + str(listing.get("fixed_state"))[:220])
    check("C every component carries fixed: null",
          all(entry.get("fixed", "missing") is None
              for entry in listing["components"]), "")
    check("C and the payload says why it cannot be read",
          "cannot read it" in (listing.get("fixed_state") or ""), "")
    check("C and names the members that were probed",
          "Product.Move.IsFixed" in (listing.get("fixed_state") or ""), "")

    docs.execute("catia_close_document", {"document": asm_path, "save": False})

    # ── B. edit_pad ──────────────────────────────────────────────────────
    say("\n== B catia_edit_pad on the 25x45x80 pad")
    docs.execute("catia_activate_document", {"name": "EDIT0498.CATPart"})
    try:
        edited = str(design.execute("catia_edit_pad",
                                    {"feature_name": "凸台.1", "height": 20}))
        say("  " + edited)
        check("B the reply names both values", "80 -> 20 mm" in edited, edited[:100])
        check("B and the implied area is the sketch's 1125 mm2",
              "1125.0000 mm2 of area" in edited, edited[:220])
        volume = _volume(measure)
        check("B the volume is 25x45x20",
              volume is not None and abs(volume - 22500.0) < 0.01, str(volume))
        check("B and the reply says the direction is NOT editable",
              "DIRECTION" in edited, "")
    except CodedError as error:
        say(f"  refused: {error}")
        check("B the reply names both values", False, str(error)[:150])

    say("  -- second_height on the same pad (observation, not a gate)")
    try:
        two_sided = str(design.execute("catia_edit_pad",
                                       {"feature_name": "凸台.1",
                                        "second_height": 5}))
        say("  " + two_sided[:300])
        volume = _volume(measure)
        say(f"  volume with a 5 mm second limit: {volume} "
            f"(25x45x25 = {25 * 45 * 25})")
    except CodedError as error:
        say(f"  refused: {str(error)[:200]}")

    say("\n  -- and the control: a direction that cannot be changed")
    try:
        design.execute("catia_edit_pad", {"feature_name": "凸台.1",
                                          "direction": "reversed"})
        check("B control: 'direction' is not an argument this tool pretends to have",
              False, "accepted")
    except CodedError as error:
        check("B control: 'direction' is refused", True, error.code)

    docs.execute("catia_close_document", {"document": part_path, "save": False})
    say("")
    passed = sum(1 for _n, ok, _d in results if ok)
    say(f"{passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            say(f"  FAILED: {name}  [{detail}]")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
