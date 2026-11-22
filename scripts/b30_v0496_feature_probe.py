"""P1-21 and P2-22: what the Chamfer feature exposes, and whether the inertia
tensor can be read without activating the document.

B. After a two-distance bevel, is `Mode` / `Length` / `SecondLength` readable on
   the feature? A reply that quotes a READ-BACK is worth more than one that
   repeats its own arguments - and if the members are absent, the reply must say
   so instead of printing the arguments as if they had been confirmed.

C. The tensor route uses `CATIA.ActiveDocument.GetWorkbench("SPAWorkbench")`
   inside VBScript. Handing the DOCUMENT in as a parameter would remove the
   activation entirely, which is better than borrowing focus (`borrow_focus`
   in the report's suggestion) - one fewer way to move what the caller sees.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402


def say(text: str) -> None:
    print(text, flush=True)


def short(value, limit: int = 220) -> str:
    return " ".join(str(value).split())[:limit]


def members(obj) -> list[str]:
    try:
        info = obj._oleobj_.GetTypeInfo()
        attrs = info.GetTypeAttr()
        names: list[str] = []
        for index in range(attrs.cFuncs):
            desc = info.GetFuncDesc(index)
            try:
                names.append(info.GetNames(desc.memid)[0])
            except Exception:  # noqa: BLE001
                pass
            info.ReleaseFuncDesc(desc)
        for index in range(attrs.cVars):
            desc = info.GetVarDesc(index)
            try:
                names.append(info.GetNames(desc.memid)[0])
            except Exception:  # noqa: BLE001
                pass
            info.ReleaseVarDesc(desc)
        info.ReleaseTypeAttr(attrs)
        return sorted(set(names))
    except Exception as exc:  # noqa: BLE001
        return ["<unreadable: " + short(exc, 80) + ">"]


VBS_WITH_DOC = (
    "Public Function catia_mcp_inertia_doc(oDoc, oBody)\n"
    "    Dim oSPA, oInertia, i\n"
    "    Dim m(8)\n"
    "    Dim a(9)\n"
    "    Set oSPA = oDoc.GetWorkbench(\"SPAWorkbench\")\n"
    "    Set oInertia = oSPA.Inertias.Add(oBody)\n"
    "    oInertia.GetInertiaMatrix m\n"
    "    a(0) = oInertia.Mass\n"
    "    For i = 0 To 8\n"
    "        a(i + 1) = m(i)\n"
    "    Next\n"
    "    catia_mcp_inertia_doc = a\n"
    "End Function\n"
)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    root = os.path.join(tempfile.gettempdir(), "probe0496c")
    os.makedirs(root, exist_ok=True)
    first_path = os.path.join(root, "Chamfer21.CATPart")
    second_path = os.path.join(root, "Tensor22.CATPart")

    try:
        say("== B. chamfer feature members (P1-21)")
        for stale in (first_path, second_path):
            if os.path.isfile(stale):
                os.remove(stale)
        docs.execute("catia_new_part", {"name": "Chamfer21", "file_path": first_path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 100})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "SQ", "height": 100})
        # The reporter's case: 25 x 20 on the top edge of a 100 mm block.
        reply = design.execute("catia_chamfer",
                               {"length": 25, "length2": 20, "edge_name": "Edge.1"})
        say("  reply: " + short(reply, 300))
        part = conn.get_active_part()
        body = part.MainBody
        # The feature is named in the UI LANGUAGE ('倒角.1' here), so a prefix
        # test on 'Chamfer' finds nothing - the LAST shape is the one just made.
        feature = None
        try:
            feature = body.Shapes.Item(int(body.Shapes.Count))
        except Exception as exc:  # noqa: BLE001
            say(f"  last shape unreadable: {short(exc, 90)}")
        if feature is None:
            say("  no Chamfer feature in the body")
        else:
            say(f"  feature {feature.Name!r}")
            say(f"  members: {members(feature)}")
            for name in ("Mode", "Length", "SecondLength", "Angle", "Orientation",
                         "Direction", "Propagation", "ElementsToChamfer",
                         "LeftSideDistance", "RightSideDistance", "Body"):
                try:
                    value = getattr(feature, name)
                    extra = ""
                    for attr in ("Value", "Name", "Count"):
                        try:
                            extra += f" {attr}={getattr(value, attr)!r}"
                        except Exception:  # noqa: BLE001
                            pass
                    say(f"    {name} = {type(value).__name__}{extra}"
                        if extra else f"    {name} = {short(value, 80)}")
                except Exception as exc:  # noqa: BLE001
                    say(f"    {name} -> {short(exc, 80)}")
            say("    -- the same members through a DYNAMIC dispatcher")
            try:
                import win32com.client

                dynamic = win32com.client.dynamic.Dispatch(feature._oleobj_)
            except Exception as exc:  # noqa: BLE001
                dynamic = None
                say(f"      dynamic wrap failed: {short(exc, 90)}")
            if dynamic is not None:
                for name in ("Mode", "Length", "SecondLength", "Angle"):
                    try:
                        value = getattr(dynamic, name)
                        extra = ""
                        try:
                            extra = f" Value={value.Value}"
                        except Exception as exc:  # noqa: BLE001
                            extra = f" ({type(value).__name__}, Value -> {short(exc, 50)})"
                        say(f"      {name} = {type(value).__name__}{extra}")
                    except Exception as exc:  # noqa: BLE001
                        say(f"      {name} -> {short(exc, 90)}")
            # Do the values SURVIVE an update? A member that reads back the
            # argument is only evidence if it is still there after a rebuild.
            try:
                part.Update()
                say("    after part.Update():")
                for name in ("Mode", "Length", "SecondLength"):
                    try:
                        value = getattr(feature, name)
                        extra = ""
                        try:
                            extra = f" Value={value.Value}"
                        except Exception:  # noqa: BLE001
                            extra = f" {short(value, 40)}"
                        say(f"      {name} = {type(value).__name__}{extra}")
                    except Exception as exc:  # noqa: BLE001
                        say(f"      {name} -> {short(exc, 80)}")
            except Exception as exc:  # noqa: BLE001
                say(f"    part.Update() failed: {short(exc, 120)}")
            # Volume, so the geometry is on record next to the readings.
            spa = part.Parent.GetWorkbench("SPAWorkbench")
            measurable = spa.GetMeasurable(part.CreateReferenceFromObject(body))
            say(f"    volume = {measurable.Volume:.4f} mm3")

        say("\n== C. inertia tensor from a document that is NOT active (P2-22)")
        docs.execute("catia_new_part", {"name": "Tensor22", "file_path": second_path})
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sketcher.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "SQ", "height": 40})
        docs.execute("catia_save_document", {})
        target = conn.active_document
        body = target.Part.MainBody
        try:
            values = measure._evaluate_floats(VBS_WITH_DOC, "catia_mcp_inertia_doc",
                                              [target, body])
            say(f"  same doc, active: {[round(v, 6) for v in values]}")
        except Exception as exc:  # noqa: BLE001
            say(f"  same doc, active FAILED: {short(exc, 200)}")
        # Put the OTHER document in front, then read the first one's tensor.
        docs.execute("catia_activate_document", {"name": "Chamfer21.CATPart"})
        say(f"  active is now {short(conn.active_document.Name, 60)}")
        try:
            values = measure._evaluate_floats(VBS_WITH_DOC, "catia_mcp_inertia_doc",
                                              [target, body])
            say(f"  NOT active: {[round(v, 6) for v in values]}")
        except Exception as exc:  # noqa: BLE001
            say(f"  NOT active FAILED: {short(exc, 200)}")
        say(f"  active after = {short(conn.active_document.Name, 60)}")
        # And the existing helper, which borrows the activation.
        try:
            fields = measure._tensor_for(target.Part)
            say(f"  _tensor_for (borrows activation) = "
                f"{short(fields, 200)} why={measure._tensor_why}")
        except Exception as exc:  # noqa: BLE001
            say(f"  _tensor_for FAILED: {short(exc, 160)}")
        say(f"  active after _tensor_for = {short(conn.active_document.Name, 60)}")
        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    finally:
        for path in (second_path, first_path):
            try:
                say("  " + short(docs.execute("catia_close_document",
                                              {"document": path, "save": False}), 100))
            except Exception as exc:  # noqa: BLE001
                say(f"  close({os.path.basename(path)}) refused: {short(exc, 100)}")


if __name__ == "__main__":
    sys.exit(main())
