"""Third pass: can ANY handle on an existing solid loft report how many sections
it holds?

Pass 1 and 2 both failed for the same reason, and it is worth naming: a shape
read out of ``body.Shapes.Item(i)`` is a bare base ``Item`` (21 members, no
``HybridShape``), so the loft's own interface was never in hand. Two ways to get
one:

  1. ``Part.FindObject(name)`` - CATIA's own name lookup;
  2. build it here, so the ``Loft`` that ``AddNewLoft()`` returned is still held,
     and enumerate THAT object.

If neither yields a count, 0.3.116 will report what it can actually count
(accepted AddSectionToLoft calls) and say plainly that CATIA's own section list
is not readable - the honest version of the 2026-11-12 report's problem.

    .venv/Scripts/python.exe scripts/b30_v0418_probe_loft_sections.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

_PROBE_ROOT = os.environ.get("CATIA_PROBE_ROOT") or os.path.join(
    tempfile.gettempdir(), "catia_probe_v0418")
ROOT = os.path.join(_PROBE_ROOT, "loft_sections")


def say(line: str = "") -> None:
    print(line, flush=True)


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
        if full and os.path.abspath(full).startswith(os.path.abspath(_PROBE_ROOT)):
            try:
                document.Close()
            except Exception:  # noqa: BLE001
                pass


def members(obj: object) -> list[str] | str:
    try:
        info = obj._oleobj_.GetTypeInfo()  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001
        return f"no typeinfo: {str(error).splitlines()[0][:70]}"
    try:
        attr = info.GetTypeAttr()
    except Exception as error:  # noqa: BLE001
        return f"no typeattr: {str(error).splitlines()[0][:70]}"
    names: list[str] = []
    for index in range(attr.cFuncs):
        try:
            described = info.GetFuncDesc(index)
            names.append(info.GetNames(described.memid)[0])
            info.ReleaseFuncDesc(described)
        except Exception:  # noqa: BLE001
            continue
    return sorted(set(names))


def show(label: str, obj: object, conn: CATIAConnection | None = None) -> None:
    dynamic = conn._dynamic(obj) if conn is not None else obj
    names = members(dynamic)
    if isinstance(names, str):
        say(f"  {label}: {names}")
        return
    say(f"  {label}: {len(names)} members")
    if len(names) <= 40:
        say("    " + ", ".join(names))
    interesting = [name for name in names
                   if "ection" in name or "Count" in name or "Get" == name[:3]]
    for name in interesting:
        try:
            value = getattr(dynamic, name)
        except Exception as error:  # noqa: BLE001
            say(f"    {name:34} -> RAISED {str(error).splitlines()[0][:50]}")
            continue
        if callable(value):
            say(f"    {name:34} -> method")
        else:
            say(f"    {name:34} -> {repr(value)[:60]}")


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    say(conn.connect())
    close_ours(conn)

    documents = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)

    path = os.path.join(ROOT, "Loft3.CATPart")
    if os.path.exists(path):
        os.remove(path)
    documents.execute("catia_new_part",
                      {"name": "Loft3", "file_path": path, "overwrite": True})
    activate(conn, "Loft3.CATPart")
    for height, radius in ((0.0, 8.0), (10.0, 10.0), (20.0, 12.0)):
        sketcher.execute("catia_create_sketch", {"plane": "xy", "offset": height})
        sketcher.execute("catia_sketch_circle",
                         {"cx": 0, "cy": 0, "radius": radius})
        sketcher.execute("catia_close_sketch", {})
    part_design.execute("catia_solid_loft",
                        {"sections": ["草图.1", "草图.2", "草图.3"]})

    part = conn.get_active_part()
    say("")
    say("── 1: Part.FindObject('多截面实体.1') ──")
    found = None
    try:
        found = part.FindObject("多截面实体.1")
        say(f"  found: {type(found)}")
        show("FindObject result", found, conn)
        try:
            inner = found.HybridShape
            say(f"  HybridShape -> {type(inner)}")
            show("FindObject().HybridShape", inner, conn)
        except Exception as error:  # noqa: BLE001
            say(f"  HybridShape -> RAISED {str(error).splitlines()[0][:70]}")
    except Exception as error:  # noqa: BLE001
        say(f"  FindObject RAISED {str(error).splitlines()[0][:90]}")

    say("")
    say("── 2: build one here, so AddNewLoft's own object stays in hand ──")
    body = part.MainBody
    part.InWorkObject = body
    factory = part.ShapeFactory
    feature = factory.AddNewLoft()
    inner = feature.HybridShape
    show("AddNewLoft() feature", feature, conn)
    show("its HybridShape", inner, conn)
    sketches = body.Sketches
    say(f"  body.Sketches: {sketches.Count} sketch(es)")
    refs = [part.CreateReferenceFromObject(sketches.Item(i))
            for i in range(1, sketches.Count + 1)]
    accepted = 0
    for ref in refs:
        try:
            inner.AddSectionToLoft(ref, 1, None)
        except Exception:
            inner.AddSectionToLoft(ref, 1)
        accepted += 1
    say(f"  AddSectionToLoft accepted: {accepted} of {len(refs)}")
    part.Update()
    say(f"  volume after update: {part.MainBody.Shapes.Count} shape(s)")

    # GetSectionFromLoft(index): the one member that looks like a READBACK of
    # the section list. Index base and out-of-range behaviour are both unknown,
    # so ask 0..5 and print every answer - a clamped answer would be a number
    # that is not the count.
    say("")
    say("── 3: GetSectionFromLoft(0..5) after 3 sections were added ──")
    dynamic = conn._dynamic(inner)
    for index in range(0, 6):
        try:
            got = dynamic.GetSectionFromLoft(index)
        except Exception as error:  # noqa: BLE001
            say(f"  GetSectionFromLoft({index}) -> RAISED "
                f"{str(error).splitlines()[0][:70]}")
            continue
        say(f"  GetSectionFromLoft({index}) -> {type(got).__name__} "
            f"{repr(got)[:120]}")
        if isinstance(got, tuple):
            for slot, item in enumerate(got):
                detail = type(item).__name__
                for attribute in ("Name", "DisplayName"):
                    try:
                        detail += f" {attribute}={getattr(item, attribute)!r}"
                    except Exception:  # noqa: BLE001
                        detail += f" {attribute}=?"
                say(f"      [{slot}] {detail[:120]}")

    # Every getter that mentions a section, asked with a plausible index.
    for name in ("GetSectionToLoft", "SectionToLoft", "GetNbSections",
                 "NbSections", "Sections", "GetSectionsCount"):
        try:
            value = getattr(conn._dynamic(inner), name)
        except Exception as error:  # noqa: BLE001
            say(f"  inner.{name:18} -> RAISED {str(error).splitlines()[0][:60]}")
            continue
        if callable(value):
            for argument in (1, 0):
                try:
                    say(f"  inner.{name}({argument}) -> {repr(value(argument))[:70]}")
                except Exception as error:  # noqa: BLE001
                    say(f"  inner.{name}({argument}) -> RAISED "
                        f"{str(error).splitlines()[0][:60]}")
        else:
            say(f"  inner.{name:18} -> {repr(value)[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
