"""P0-2: is ShapeFactory.AddNewMirror really a one-argument method?

The refusal shipped in 0.3.x says AddNewMirror "takes a plane and nothing else"
and that `.Object` (the seed) is read-only. Both halves deserve a direct reading
of the type library, because the whole item turns on them:

  * if `AddNewMirror` declares TWO parameters, the project has been passing the
    plane into the wrong slot, and a feature mirror may be one argument away;
  * the Mirror object's own member table says which properties exist and, from
    the invoke kind, which of them can be written.

Nothing is created that is not deleted again: the part is temporary.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402


def say(text: str) -> None:
    print(text, flush=True)


def short(value, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit]


def func_table(obj) -> list[tuple[str, list[str], int]]:
    """(name, parameter names, invoke kind) for every member of the type info."""
    try:
        info = obj._oleobj_.GetTypeInfo()
        attrs = info.GetTypeAttr()
    except Exception as exc:  # noqa: BLE001
        return [("<unreadable: " + short(exc, 60) + ">", [], -1)]
    rows: list[tuple[str, list[str], int]] = []
    for index in range(attrs.cFuncs):
        desc = info.GetFuncDesc(index)
        try:
            names = info.GetNames(desc.memid)
            kind = desc.invkind
            params = list(names[1:]) if len(names) > 1 else []
            rows.append((names[0], params, kind))
        except Exception:  # noqa: BLE001
            pass
    return rows


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    design = PartDesignTools(conn)

    root = os.path.join(tempfile.gettempdir(), "probe0497")
    os.makedirs(root, exist_ok=True)
    part_path = os.path.join(root, "Mir.CATPart")

    try:
        try:
            docs.execute("catia_close_document", {"document": part_path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        if os.path.isfile(part_path):
            os.remove(part_path)

        docs.execute("catia_new_part", {"name": "Mir", "file_path": part_path})
        sketcher = __import__("catia_mcp.tools.sketcher", fromlist=["SketcherTools"])
        sk = sketcher.SketcherTools(conn)
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 60, "y2": 40})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "SQ", "height": 50})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "PK"})
        sk.execute("catia_sketch_rectangle", {"x1": 10, "y1": 10, "x2": 30, "y2": 30})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pocket", {"sketch_name": "PK", "depth": 20,
                                        "direction": "up"})
        docs.execute("catia_save_document", {})

        part = conn.get_active_part()
        body = part.MainBody
        sf = part.ShapeFactory

        say("== ShapeFactory.AddNewMirror, from the type library")
        rows = func_table(sf)
        for name, params, kind in rows:
            if "mirror" in name.casefold() or "symmetr" in name.casefold():
                say(f"  {name}({', '.join(params)})  invkind={kind}")
        if not any("mirror" in n.casefold() for n, _, _ in rows):
            say("  no member with 'mirror' in its name")
        say(f"  total members on ShapeFactory: {len(rows)}")

        say("\n== the Mirror feature the current call produces")
        plane = conn.get_origin_elements()["xy"]
        plane_ref = part.CreateReferenceFromObject(plane)
        mirror = None
        try:
            mirror = sf.AddNewMirror(plane_ref)
            say(f"  AddNewMirror(plane) -> {short(getattr(mirror, 'Name', '?'), 40)}")
        except Exception as exc:  # noqa: BLE001
            say(f"  AddNewMirror(plane) failed: {short(exc, 160)}")
        if mirror is not None:
            for name, params, kind in func_table(mirror):
                if kind < 0:
                    say(f"    .{name}  invkind={kind}")
                    continue
                try:
                    value = getattr(mirror, name)
                    say(f"    .{name} = {short(value, 80)}")
                except Exception as exc:  # noqa: BLE001
                    say(f"    .{name} -> {short(exc, 80)}")

        say("\n== can a SEED be given to AddNewMirror itself?")
        # The signature read above decides how many arguments it accepts; try the
        # two-argument form on a fresh feature, then the one-argument form on a
        # seed reference (in case the parameter is the seed and the plane has a
        # default).
        seed = None
        try:
            seed = body.Shapes.Item(int(body.Shapes.Count))
            seed_ref = part.CreateReferenceFromObject(seed)
            say(f"  seed = {short(getattr(seed, 'Name', '?'), 40)}")
        except Exception as exc:  # noqa: BLE001
            seed_ref = None
            say(f"  seed unreadable: {short(exc, 100)}")
        if seed_ref is not None:
            for label, call in (
                ("AddNewMirror(seed, plane)", lambda: sf.AddNewMirror(seed_ref, plane_ref)),
                ("AddNewMirror(plane, seed)", lambda: sf.AddNewMirror(plane_ref, seed_ref)),
            ):
                try:
                    made = call()
                    say(f"  {label} -> OK {short(getattr(made, 'Name', '?'), 30)}")
                    try:
                        say(f"    its .Object = {short(getattr(made, 'Object', '?'), 70)}")
                    except Exception as exc:  # noqa: BLE001
                        say(f"    its .Object -> {short(exc, 70)}")
                except Exception as exc:  # noqa: BLE001
                    say(f"  {label} -> {short(exc, 140)}")

        say("\n== the seed once the mirror exists")
        if mirror is not None and seed_ref is not None:
            try:
                say(f"  .Object reads {short(getattr(mirror, 'Object'), 80)}")
            except Exception as exc:  # noqa: BLE001
                say(f"  .Object -> {short(exc, 90)}")
            for label, value in (("feature ref", seed_ref),
                                 ("the body", part.CreateReferenceFromObject(body))):
                try:
                    mirror.Object = value
                    say(f"  mirror.Object = {label}  ACCEPTED; reads "
                        f"{short(getattr(mirror, 'Object'), 70)}")
                except Exception as exc:  # noqa: BLE001
                    say(f"  mirror.Object = {label} -> {short(exc, 110)}")

        say("\n== anything else on Part / Body that names a mirror or a seed")
        for label, obj in (("Part", part), ("Body", body)):
            hits = [f"{n}({', '.join(p)})" for n, p, _ in func_table(obj)
                    if "mirror" in n.casefold() or "symmetr" in n.casefold()]
            say(f"  {label}: {hits or 'none'}")

        return 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    finally:
        try:
            docs.execute("catia_close_document", {"document": part_path, "save": False})
            say("  closed the probe part")
        except Exception as exc:  # noqa: BLE001
            say(f"  could not close the probe part: {short(exc, 100)}")


if __name__ == "__main__":
    sys.exit(main())
