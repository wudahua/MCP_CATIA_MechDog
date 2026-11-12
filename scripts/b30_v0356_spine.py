"""What can a HybridShapeSpine actually do? Full typeinfo, not a method-name guess.

0.3.53 established two facts and wrote them into README: AddNewSpine() takes no
arguments, and UpdateObject fails on the fresh object. What it did NOT establish
is whether the object has any way to receive sections - the probe then only
listed dir(spine) filtered for "Add"/"ection", which for a late-bound COM object
proves nothing (pywin32's dir shows almost nothing useful).

The reliable route is the type library itself:
    obj._oleobj_.GetTypeInfo() -> GetTypeAttr().cFuncs
    -> GetFuncDesc(i) -> GetNames(memid)   # real parameter names and order

So this dumps every function and property of the spine object, then tries the
plausible names through IDispatch by DISPID. A method that does not exist here
does not exist at all - the interface is fixed at build time.

For contrast it dumps HybridShapeSweep too: the sweep's spine in the UI is a
curve, and if Sweep has a Spine property that takes a curve, then "spine" as a
standalone object may be a dead end while the real capability is elsewhere.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT = os.path.join(tempfile.gettempdir(), "b30_v0356", "SPINE.CATPart")
TAG = "SPINE"


def say(text: str = "") -> None:
    print(text, flush=True)


def describe(obj, label: str) -> None:
    """Every function and property in the object's type library."""
    say(f"\n──── {label} ────")
    try:
        type_info = obj._oleobj_.GetTypeInfo()
        attr = type_info.GetTypeAttr()
    except Exception as error:  # noqa: BLE001
        say(f"  no type info: {str(error).splitlines()[0][:90]}")
        return
    say(f"  cFuncs={attr.cFuncs}  cVars={attr.cVars}  "
        f"iid={getattr(attr, 'iid', getattr(attr, 'guid', '?'))}")
    for index in range(attr.cFuncs):
        try:
            desc = type_info.GetFuncDesc(index)
            names = type_info.GetNames(desc.memid)
        except Exception as error:  # noqa: BLE001
            say(f"  func[{index}]: unreadable ({str(error).splitlines()[0][:50]})")
            continue
        params = ", ".join(names[1:])
        flag = "PROP" if desc.invkind in (2, 4) else "METHOD"
        count = getattr(desc, "cParams", len(names) - 1)
        say(f"  func[{index}] {flag:6s} {names[0]}({params})  "
            f"args={count}  memid={desc.memid}  invkind={desc.invkind}")
    for index in range(attr.cVars):
        try:
            var = type_info.GetVarDesc(index)
            names = type_info.GetNames(var.memid)
        except Exception as error:  # noqa: BLE001
            say(f"  var[{index}]: unreadable ({str(error).splitlines()[0][:50]})")
            continue
        say(f"  var[{index}] {names[0]}  memid={var.memid}")


def dispid_of(obj, name: str) -> int | None:
    try:
        return obj._oleobj_.GetIDsOfNames(name)
    except Exception:  # noqa: BLE001
        return None


def probe_methods(obj, label: str, candidates: list[str]) -> None:
    say(f"\n  ── IDispatch lookup on {label} ──")
    for name in candidates:
        dispid = dispid_of(obj, name)
        say(f"    {name:22s} -> {'DISPID ' + str(dispid) if dispid is not None else 'does not exist'}")


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    document.execute("catia_new_part", {"name": TAG, "file_path": OUT})
    assert str(conn.active_document.Name).startswith(TAG), conn.active_document.Name
    part = conn.get_active_part()
    factory = gsd._factory()
    geoset = gsd._get_geoset()

    # Three sections to give a spine something to pass through, plus a guide.
    for z, radius, name in ((0.0, 10.0, f"{TAG}_C1"),
                            (20.0, 7.0, f"{TAG}_C2"),
                            (40.0, 4.0, f"{TAG}_C3")):
        gsd.execute("catia_gsd_circle", {"center": [0, 0, z], "support_plane": "xy",
                                         "radius": radius, "name": name})
    say("built sections: " + ", ".join(f"{TAG}_C{i}" for i in (1, 2, 3)))

    spine = factory.AddNewSpine()
    geoset.AppendHybridShape(spine)
    spine.Name = f"{TAG}_SP"
    describe(spine, "HybridShapeSpine (fresh from AddNewSpine)")
    probe_methods(spine, "spine", [
        "AddSection", "Add", "AddGuide", "AddSectionToSpine", "SetSection",
        "Sections", "Section1", "Section2", "Guide", "Guides", "AddCurve",
        "SetCurve", "SetSectionCurve", "RemoveSection", "SetSupport",
    ])

    say("\n── does UpdateObject fail on the empty spine? ──")
    try:
        part.UpdateObject(spine)
        say("  UpdateObject SUCCEEDED on an empty spine")
    except Exception as error:  # noqa: BLE001
        say(f"  UpdateObject failed: {str(error).splitlines()[0][:80]}")

    say("\n──── the sweep, for contrast ────")
    sweep = factory.AddNewSweepExplicit()
    geoset.AppendHybridShape(sweep)
    sweep.Name = f"{TAG}_SW"
    describe(sweep, "HybridShapeSweepExplicit")
    probe_methods(sweep, "sweep", [
        "Spine", "SetSpine", "AddSectionToSweep", "SetGuide", "AddGuide",
        "SweepMode", "Section", "SetMode",
    ])

    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(TAG):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
