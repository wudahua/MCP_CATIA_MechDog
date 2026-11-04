"""Does the surfacing factory expose a way to MOVE a surface?

The five-petal bottle base (2026-11-04) needed the same 50-degree sector five
times around an axis. The MCP has catia_gsd_symmetry (mirror) and nothing else,
so the run was built by rotating the PROFILE five times and revolving each one -
five extra revolutions, and a sector that could only be placed by rebuilding it.

So ask the typelib: list every AddNew* on the HybridShapeFactory whose name
mentions a transform, plus the full AddNew* list so a missing one is visible as
absent rather than unsearched. Read only - nothing is created.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

KEYS = ("Translat", "Rotat", "AxisToAxis", "Scaling", "Affinity", "Symmetr",
        "Mirror", "Transform")


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    gsd = GSDTools(conn)
    docs.execute("catia_new_part", {})
    part = conn.active_document.Part

    factory = part.HybridShapeFactory
    print("factory:", factory, flush=True)

    info = factory._oleobj_.GetTypeInfo()
    attributes = info.GetTypeAttr()
    added: list[tuple[str, list[str]]] = []
    for index in range(attributes.cFuncs):
        try:
            desc = info.GetFuncDesc(index)
            names = info.GetNames(desc.memid)
        except Exception:  # noqa: BLE001 - not all entries carry a name
            continue
        if not names:
            continue
        name = names[0]
        added.append((name, list(names[1:])))

    add_new = [(n, a) for n, a in added if n.startswith("AddNew")]
    print(f"\nAddNew* methods: {len(add_new)}", flush=True)
    for name, args in add_new:
        print(f"  {name}({', '.join(args)})", flush=True)

    print("\n=== transform-looking ===", flush=True)
    hits = [(n, a) for n, a in added if any(k.lower() in n.lower() for k in KEYS)]
    if not hits:
        print("  none", flush=True)
    for name, args in hits:
        print(f"  {name}({', '.join(args)})", flush=True)

    # A transform result is only useful if it is a HybridShape we can insert.
    for probe in ("HybridShapeAxisToAxis", "HybridShapeTranslate"):
        try:
            made = getattr(factory, "AddNew" + probe[len("HybridShape"):])()
            print(f"\n{probe}: constructed -> {made}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"\n{probe}: {str(error).splitlines()[0][:120]}", flush=True)

    docs.close_document()
    print("\ndone", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
