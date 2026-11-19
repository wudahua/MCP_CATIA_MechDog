"""Is the assembly-constraint failure a LICENCE / WORKBENCH ceiling?

Probe v0465 showed ten argument forms of AddMonoEltCst failing with E_FAIL and
``GenerateConstraints`` not existing at all. Before this becomes "cannot be done
here", one class of cause is left: the Assembly Design workbench, which is the
only thing that owns that command. If starting it fails for want of a licence,
that IS the ceiling and it can be stated with evidence.

.venv/Scripts/python.exe -u scripts/b30_v0465b_probe_asm_workbench.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = os.path.join(os.environ.get("CATIA_PROBE_ROOT")
                    or tempfile.gettempdir(), "catia_probe_v0465")


def say(line: str = "") -> None:
    print(line, flush=True)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    parts = PartDesignTools(conn)
    sketcher = SketcherTools(conn)
    assembly = AssemblyTools(conn)
    say(f"probe v0465b on {conn.app.Name}")
    for index in range(int(conn.documents.Count), 0, -1):
        try:
            doc = conn.documents.Item(index)
            full = str(doc.FullName or "")
        except Exception:  # noqa: BLE001
            continue
        if full and os.path.abspath(full).lower().startswith(
                os.path.abspath(ROOT).lower()):
            try:
                doc.Close()
            except Exception:  # noqa: BLE001
                pass

    try:
        say(f"app.StartWorkbench: {getattr(conn.app, 'StartWorkbench')!r}")
    except Exception as error:  # noqa: BLE001
        say(f"app.StartWorkbench absent: {str(error).splitlines()[0][:70]}")
    for attribute in ("Workbench", "ActiveWorkbench"):
        try:
            say(f"app.{attribute}: {getattr(conn.app, attribute)!r}")
        except Exception as error:  # noqa: BLE001
            say(f"app.{attribute}: <{str(error).splitlines()[0][:60]}>")

    # a part and an assembly, the smallest thing a constraint could live in
    path = os.path.join(ROOT, "ProbeV0465b_P.CATPart")
    if os.path.exists(path):
        os.remove(path)
    docs.execute("catia_new_part", {"name": "ProbeV0465b_P", "file_path": path})
    sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "R"})
    sketcher.execute("catia_sketch_rectangle",
                     {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0})
    sketcher.execute("catia_close_sketch", {})
    parts.execute("catia_pad", {"sketch_name": "R", "height": 5.0})
    docs.execute("catia_save_document", {})

    asm_path = os.path.join(ROOT, "ProbeV0465b_ASM.CATProduct")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    docs.execute("catia_new_product", {"name": "ProbeV0465b_ASM",
                                       "file_path": asm_path})
    assembly.execute("catia_add_component", {"file_path": path})
    product = conn.get_active_product()
    constraints = product.Connections("CATIAConstraints")
    component = product.Products.Item(1)

    for name in ("Assembly", "Assembly Design", "ASD", "AssemblyDesign"):
        try:
            conn.app.StartWorkbench(name)
            say(f"StartWorkbench({name!r}): OK")
        except Exception as error:  # noqa: BLE001
            say(f"StartWorkbench({name!r}): {str(error).splitlines()[0][:110]}")
            continue
        try:
            created = constraints.AddMonoEltCst(0, component)
            say(f"  after {name!r}: constraint {created.Name!r} created "
                f"(count {constraints.Count})")
        except Exception as error:  # noqa: BLE001
            say(f"  after {name!r}: still fails - "
                f"{str(error).splitlines()[0][:100]}")
    say(f"final constraint count: {constraints.Count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
