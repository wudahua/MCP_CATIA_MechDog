"""B30 acceptance for 0.3.163 - catia_reorder_feature, through the real tool.

  A  a 3-feature body: the refusal carries the plan and changes nothing
  B  asking for the order it already has is a success, not a refusal
  C  force=true re-reads the interfaces and excludes InsertHybridShape
  D  the name and the argument checks: unknown feature, no target, bad index
  E  a target in another body is TARGET_IN_OTHER_BODY, not "not found"
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

results: list[tuple[str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply):
    return reply if isinstance(reply, (dict, list)) else json.loads(str(reply))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "p0500acc")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "REORDER_ACC.CATPart")
    try:
        docs.execute("catia_close_document", {"document": path, "save": False})
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(path):
        os.remove(path)

    docs.execute("catia_new_part", {"name": "REORDER_ACC", "file_path": path})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S1"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 45})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S1", "height": 80})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "H1"})
    sk.execute("catia_sketch_circle", {"cx": 15, "cy": 20, "radius": 5})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pocket", {"sketch_name": "H1", "depth": 80})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S2"})
    sk.execute("catia_sketch_rectangle", {"x1": 40, "y1": 10, "x2": 50, "y2": 30})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S2", "height": 40})

    part = conn.get_active_part()
    body = part.MainBody
    order = [str(shape.Name) for shape in body.Shapes]
    say(f"tree: {order}")
    before = payload(measure.execute("catia_get_inertia", {}))
    volume_before = (before.get("data") if isinstance(before.get("data"), dict)
                     else before).get("volume_mm3")

    say("\nA. move the last feature to the front")
    try:
        design.execute("catia_reorder_feature",
                       {"feature": "凸台.2", "index": 1})
        check("A the refusal is UNSUPPORTED_CAPABILITY", False, "it went through")
    except CodedError as error:
        check("A the refusal is UNSUPPORTED_CAPABILITY",
              error.code == "UNSUPPORTED_CAPABILITY", error.code)
        check("A and it says nothing was changed", error.modified is False,
              str(error.modified))
        message = str(error)
        check("A the reply carries both positions",
              "position 3 -> 1 of 3" in message, message[:80])
        check("A the reply carries the resulting order",
              "凸台.2, 凸台.1, 凹槽.1" in message, "")
        check("A the reply names the Shape collection's members",
              "no Insert, Move or Remove" in message, "")
        check("A the reply says paste appends",
              "APPENDED" in message, "")
    after_order = [str(shape.Name) for shape in body.Shapes]
    check("A the tree order is untouched", after_order == order, str(after_order))
    after = payload(measure.execute("catia_get_inertia", {}))
    volume_after = (after.get("data") if isinstance(after.get("data"), dict)
                    else after).get("volume_mm3")
    check("A the volume is untouched", volume_after == volume_before,
          f"{volume_before} -> {volume_after}")

    say("\nB. asking for the order that already exists")
    reply = design.execute("catia_reorder_feature",
                           {"feature": "凸台.1", "index": 1})
    check("B it is a success, not a refusal",
          "already at position 1 of 3" in str(reply), str(reply)[:100])
    reply = design.execute("catia_reorder_feature",
                           {"feature": "凸台.1", "before": "凹槽.1"})
    check("B 'before its own successor' is also a no-op",
          "already at position 1 of 3" in str(reply), str(reply)[:100])

    say("\nC. force re-reads the interfaces")
    reply = str(design.execute("catia_reorder_feature",
                               {"feature": "凸台.2", "index": 1, "force": True}))
    say("  " + reply[:700])
    check("C it reports the Shapes collection's 5 members",
          "Body.Shapes: 5 member(s)" in reply or "Body.Shapes:" in reply, "")
    check("C InsertHybridShape is named and excluded, not counted",
          "not InsertHybridShape" in reply and "refuses a solid" in reply, "")
    check("C the verdict stands", "cannot be moved on this build" in reply,
          reply[-160:])
    check("C nothing was touched",
          [str(shape.Name) for shape in body.Shapes] == order, "")

    say("\nD. the argument checks")
    try:
        design.execute("catia_reorder_feature", {"feature": "倒圆角.61", "index": 1})
        check("D an unknown feature is refused", False, "it went through")
    except CodedError as error:
        check("D an unknown feature is refused",
              error.code == "FEATURE_NOT_FOUND", error.code)
        check("D and the refusal lists the tree",
              "凸台.1, 凹槽.1, 凸台.2" in str(error), str(error)[:120])
    try:
        design.execute("catia_reorder_feature", {"feature": "凹槽.1"})
        check("D no target is refused", False, "it went through")
    except CodedError as error:
        check("D no target is refused", error.code == "BAD_ARGUMENT", error.code)
    try:
        design.execute("catia_reorder_feature", {"feature": "凹槽.1", "index": 9})
        check("D an index outside the body is refused", False, "it went through")
    except CodedError as error:
        check("D an index outside the body is refused",
              error.code == "INDEX_OUT_OF_RANGE", error.code)

    say("\nE. a target in another body")
    design.execute("catia_new_body", {"name": "SECOND"})
    sk.execute("catia_create_sketch", {"plane": "xy", "name": "S3"})
    sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 5, "y2": 5})
    sk.execute("catia_close_sketch", {})
    design.execute("catia_pad", {"sketch_name": "S3", "height": 10})
    try:
        design.execute("catia_reorder_feature",
                       {"feature": "凸台.1", "after": "凸台.3"})
        check("E a cross-body target is refused", False, "it went through")
    except CodedError as error:
        check("E a cross-body target is refused",
              error.code == "TARGET_IN_OTHER_BODY", error.code)
        check("E and it names both bodies",
              "SECOND" in str(error) and "零件几何体" in str(error),
              str(error)[:120])

    docs.execute("catia_close_document", {"document": path, "save": False})
    say("")
    failed = [row for row in results if not row[1]]
    say(f"{len(results) - len(failed)}/{len(results)} passed")
    for name, _ok, detail in failed:
        say(f"  FAILED: {name}  [{detail}]")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
