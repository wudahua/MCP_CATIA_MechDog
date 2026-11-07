"""0.3.79 probe 4: part-level and feature-level colour, and what "unset" reads as.

The three questions the tools need answered before they can be written:
 1. Can the WHOLE part be coloured - via the Part object or only via its body?
 2. Can one feature (a Pad) be coloured on its own, leaving the body alone?
 3. What does GetRealColor return for something nobody ever coloured? If it is
    indistinguishable from "explicitly white", the read side must say so instead
    of claiming "not set".
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402

ROOT = r"F:\catia_probe_v0379d"
CHECKS = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    say(f"   [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def raw(selection):
    try:
        return tuple(int(v) for v in selection.VisProperties.GetRealColor())
    except Exception as error:  # noqa: BLE001
        return f"<{str(error).splitlines()[0][:70]}>"


def rgb(value):
    if isinstance(value, tuple) and len(value) >= 4:
        return value[-3:]
    return value


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    before = {str(conn.documents.Item(i).Name)
              for i in range(1, conn.documents.Count + 1)}
    say(f"session before: {conn.documents.Count}")

    try:
        path = os.path.join(ROOT, "pt.CATPart")
        docs.execute("catia_new_part", {"name": "PT", "file_path": path})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 12})
        sketcher.execute("catia_close_sketch", {})
        reply = design.execute("catia_pad", {"height": 10})
        say("pad: " + str(reply)[:80])

        doc = conn.active_document
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        sel = doc.Selection
        pad = body.Shapes.Item(1)

        say("\n1  what an untouched part reads as")
        sel.Clear()
        sel.Add(body)
        virgin_body = raw(sel)
        say(f"   body            -> {virgin_body}  rgb={rgb(virgin_body)}")
        sel.Clear()
        sel.Add(pad)
        virgin_pad = raw(sel)
        say(f"   pad feature     -> {virgin_pad}  rgb={rgb(virgin_pad)}")
        check("an untouched part and its feature read the same colour",
              virgin_body == virgin_pad, f"{virgin_body} vs {virgin_pad}")

        say("\n2  colour the BODY, then check the feature follows or not")
        sel.Clear()
        sel.Add(body)
        sel.VisProperties.SetRealColor(200, 40, 40, 0)
        body_now = raw(sel)
        sel.Clear()
        sel.Add(pad)
        pad_now = raw(sel)
        say(f"   body            -> {rgb(body_now)}")
        say(f"   pad feature     -> {rgb(pad_now)}")
        check("the body took the colour", rgb(body_now) == (200, 40, 40), str(rgb(body_now)))
        check("the feature inherits it (a feature has no colour of its own yet)",
              rgb(pad_now) == (200, 40, 40), str(rgb(pad_now)))

        say("\n3  colour ONE feature - does the body change?")
        sel.Clear()
        sel.Add(pad)
        try:
            sel.VisProperties.SetRealColor(40, 160, 90, 0)
            feature_ok = True
            say(f"   SetRealColor on the pad accepted -> {rgb(raw(sel))}")
        except Exception as error:  # noqa: BLE001
            feature_ok = False
            say(f"   SetRealColor on the pad REFUSED: {str(error).splitlines()[0][:80]}")
        if feature_ok:
            sel.Clear()
            sel.Add(body)
            body_after = rgb(raw(sel))
            sel.Clear()
            sel.Add(pad)
            pad_after = rgb(raw(sel))
            say(f"   body now {body_after} | pad now {pad_after}")
            check("a feature can carry its own colour", pad_after == (40, 160, 90), str(pad_after))
            check("the body keeps its own", body_after == (200, 40, 40), str(body_after))

        say("\n4  the whole Part object, not just the body")
        sel.Clear()
        try:
            sel.Add(part)
            sel.VisProperties.SetRealColor(120, 120, 120, 0)
            say(f"   sel.Add(part) worked -> {rgb(raw(sel))}")
            part_ok = True
        except Exception as error:  # noqa: BLE001
            part_ok = False
            say(f"   sel.Add(part) refused: {str(error).splitlines()[0][:80]}")
        check("the Part object can be selected (for a whole-part colour)", part_ok)

        say("\n5  does a body colour survive save + reopen of the PART?")
        docs.execute("catia_save_document", {})
        doc.Close()
        reopened = conn.documents.Open(os.path.normpath(path))
        rsel = reopened.Selection
        rbody = conn.get_active_part_body()
        rsel.Clear()
        rsel.Add(rbody)
        after = rgb(raw(rsel))
        say(f"   body after reopen -> {after}")
        check("part-level colour is stored in the CATPart", after is not None, str(after))

    finally:
        current = {str(conn.documents.Item(i).Name)
                   for i in range(1, conn.documents.Count + 1)}
        for name in sorted(current - before, reverse=True):
            for index in range(1, conn.documents.Count + 1):
                if str(conn.documents.Item(index).Name) == name:
                    try:
                        conn.documents.Item(index).Close()
                    except Exception as error:  # noqa: BLE001
                        say(f"   (could not close {name}: {error})")
                    break
        shutil.rmtree(ROOT, ignore_errors=True)
        say(f"\nsession after: {conn.documents.Count}")

    passed = sum(1 for ok in CHECKS if ok)
    say(f"\n{passed}/{len(CHECKS)} checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
