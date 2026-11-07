"""0.3.77 probe 2: the default pattern direction, and joining a sketch.

A  With no axis arguments the tool passes PlaneYZ / PlaneZX and claims
   "dir1 (X), dir2 (Y)". Probe 1 showed a plane reference drives the plane's OWN
   horizontal axis, so that claim can be checked directly.
B  Can a multi-element sketch be turned into ONE curve that a loft accepts?
   Four reference forms are tried, one per fresh sketch.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0377b"


def say(line: str) -> None:
    print(line, flush=True)


def dims(measure, part) -> str:
    box = json.loads(measure._part_bbox(part, "mesh"))
    return str(box["dimensions"])


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measure = MeasurementTools(conn)

    try:
        before_active = str(conn.app.ActiveDocument.Name)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing = {str(conn.documents.Item(i).Name)
                for i in range(1, conn.documents.Count + 1)}
    os.makedirs(ROOT, exist_ok=True)

    path = os.path.join(ROOT, "probe377b.CATPart")
    try:
        # ── A: the default pair ────────────────────────────────────────────
        say("A  rect_pattern with no axis arguments")
        docs.execute("catia_new_part", {"name": "Probe377b", "file_path": path})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"height": 5})
        say(f"   base: {dims(measure, part)}")
        for label, args in (
            ("no args", {"dir1_count": 3, "dir1_spacing": 100}),
            ("dir1_count only, dir2 2x200", {"dir1_count": 3, "dir1_spacing": 100,
                                             "dir2_count": 2, "dir2_spacing": 200}),
        ):
            try:
                reply = part_design.execute("catia_rect_pattern", args)
                say(f"   {label}: {dims(measure, part)}")
                say(f"      {str(reply)[:150]}")
            except Exception as error:  # noqa: BLE001
                say(f"   {label}: REFUSED {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:110]}")

        # ── B: four ways to make one curve out of a polygon sketch ─────────
        say("\nB  can a 4-line sketch become a single loft section?")
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        first = str(conn.last_sketch.Name)
        second = "unset"

        def twin(offset: int) -> str:
            sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": offset})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
            sketcher.execute("catia_close_sketch", {})
            return str(conn.last_sketch.Name)

        second = twin(30)
        sketch_obj = None
        for i in range(1, body.Sketches.Count + 1):
            if str(body.Sketches.Item(i).Name) == first:
                sketch_obj = body.Sketches.Item(i)
        gsf = part.HybridShapeFactory

        variants: list[tuple[str, object]] = []
        # 1) a Join whose parents are the sketch's own 2D elements
        try:
            elements = [sketch_obj.GeometricElements.Item(i)
                        for i in range(1, sketch_obj.GeometricElements.Count + 1)]
            say(f"   sketch exposes {len(elements)} 2D element(s)")
            refs = [part.CreateReferenceFromObject(e) for e in elements]
            join = gsf.AddNewJoin(refs[0], refs[1])
            for extra in refs[2:]:
                join.AddElement(extra)
            join.SetConnexion(0.1, False)
            join.Name = "ProbeJoin"
            part.UpdateObject(join)
            variants.append(("join of 2D elements",
                             part.CreateReferenceFromObject(join)))
        except Exception as error:  # noqa: BLE001
            say(f"   join of 2D elements: FAILED {type(error).__name__}: "
                f"{str(error).splitlines()[0][:120]}")
        # 2) the Sketch OBJECT rather than a reference to it
        variants.append(("sketch object", sketch_obj))
        # 3) an Extract of the whole sketch
        try:
            extract = gsf.AddNewExtract(part.CreateReferenceFromObject(sketch_obj))
            part.UpdateObject(extract)
            variants.append(("extract of the sketch",
                             part.CreateReferenceFromObject(extract)))
        except Exception as error:  # noqa: BLE001
            say(f"   extract: FAILED {type(error).__name__}: "
                f"{str(error).splitlines()[0][:110]}")

        for label, ref in variants:
            try:
                feature = part.ShapeFactory.AddNewLoft()
                inner = feature.HybridShape
                inner.AddSectionToLoft(ref, 1, None)
                inner.AddSectionToLoft(part.CreateReferenceFromObject(
                    [body.Sketches.Item(i) for i in range(1, body.Sketches.Count + 1)
                     if str(body.Sketches.Item(i).Name) == second][0]), 1, None)
                part.Update()
                say(f"   {label}: LOFT BUILT")
                body.Remove(feature)
                part.Update()
            except Exception as error:  # noqa: BLE001
                say(f"   {label}: FAILED {type(error).__name__}: "
                    f"{str(error).splitlines()[0][:110]}")
                for shape in [body.Shapes.Item(i)
                              for i in range(1, body.Shapes.Count + 1)]:
                    try:
                        body.Remove(shape)
                        part.Update()
                    except Exception:  # noqa: BLE001
                        pass

    except Exception as error:  # noqa: BLE001
        say(f"ABORTED: {type(error).__name__}: {error}")
    finally:
        say("\ncleanup")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
