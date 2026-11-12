"""Read the REAL signatures of the factory methods the 2026-11-03 report names.

The report says AddNewFilletBiTangent / AddNewFilletTriTangent / AddNewSpine /
AddNewBoundaryOfSurface "exist in the typeinfo, only not exposed". Existence is
not usability: AddNewHealing is declared the same way and does nothing (0.3.51),
and guessing argument counts produces "invalid number of arguments" for every
wrong guess. So ask the typelib instead of guessing - ITypeInfo::GetFuncDesc
carries the parameter names and order, which is the actual contract.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

WANTED = ("AddNewFilletBiTangent", "AddNewFilletTriTangent", "AddNewSpine",
          "AddNewBoundaryOfSurface", "AddNewExtract", "AddNewHealing",
          "AddNewConnect", "AddNewBlend", "AddNewLoft", "AddNewAssemble")


def dump(obj: object, label: str) -> None:
    """Print name + parameter list for every wanted method on ``obj``."""
    print(f"\n=== {label} ===", flush=True)
    try:
        info = obj._oleobj_.GetTypeInfo()
    except Exception as error:  # noqa: BLE001
        print(f"  no type info: {str(error).splitlines()[0][:80]}")
        return
    try:
        attributes = info.GetTypeAttr()
        count = attributes.cFuncs
    except Exception as error:  # noqa: BLE001
        print(f"  GetTypeAttr failed: {str(error).splitlines()[0][:80]}")
        return
    found = 0
    for index in range(count):
        try:
            desc = info.GetFuncDesc(index)
        except Exception:  # noqa: BLE001 - some entries are not callable
            continue
        try:
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001
            continue
        if not names:
            continue
        name = names[0]
        if name not in WANTED:
            continue
        found += 1
        args = list(names[1:])
        print(f"  {name}(")
        for position, argument in enumerate(args, start=1):
            print(f"      {position}: {argument}")
        print("  )")
    if not found:
        print("  none of the wanted methods are declared here")


def main() -> None:
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    document.execute("catia_new_part",
                     {"name": "SIG", "file_path":
                      r"C:\Users\Administrator\AppData\Local\Temp\b30_v0353\SIG.CATPart"})
    print("active:", conn.active_document.Name)
    part = conn.get_active_part()
    dump(part.HybridShapeFactory, "HybridShapeFactory (Part)")
    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith("SIG"):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    print(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
