"""0.3.80 acceptance on B30: the four defects of the 0.3.79 report.

1  P1-1  an uncoloured assembly must say "at the assembly default", never
         "already carried a colour" - measured with instances reading (255,255,255)
         and bodies reading (210,210,255)
2  P1-1  a second run DOES report the colours it overwrites
3  P2-2  structured=true gives data.mapping[] with previous_colour / generated,
         and the part number keeps the caller's capitalisation
4  P1-2  catia_replace_component re-points an instance at another file, keeps the
         pose, carries the colour and removes the old one
5  P2-3  set_visibility(kind="construction") hides the datum elements a frame
         sketch puts inside the body - not only the three origin planes

Everything is built under F:\\catia_probe_v0380e and closed again; the document
that was active on entry is re-activated at the end.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.appearance import AppearanceTools  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0380e"
RESULTS: list[tuple[str, bool, str]] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(ok), detail))
    say(f"   {'OK  ' if ok else 'FAIL'} {label}" + (f"  [{detail[:200]}]" if detail else ""))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    assembly = AssemblyTools(conn)
    colour = AppearanceTools(conn)

    try:
        before_active = str(conn.app.ActiveDocument.FullName)
    except Exception:  # noqa: BLE001
        before_active = ""
    existing = set()
    for index in range(1, conn.documents.Count + 1):
        try:
            existing.add(_key(str(conn.documents.Item(index).FullName)))
        except Exception:  # noqa: BLE001 - an unsaved document
            continue
    say(f"session before: {conn.documents.Count} document(s), active '{before_active}'")
    # ── safety: this run must not be able to touch anything of the caller's ──
    # (An earlier version resolved the document BY NAME and two documents shared
    # one name, so it acted on the caller's open assembly and saved it. Names are
    # unique here and every document is found by full path.)
    planned = [os.path.join(ROOT, "ZASM380.CATProduct"),
               os.path.join(ROOT, "PZ01.CATPart"),
               os.path.join(ROOT, "PZ02.CATPart"),
               os.path.join(ROOT, "ZFR380.CATPart")]
    busy = [path for path in planned if _has_at(conn, path)]
    if busy:
        say(f"ABORT: these are already open, refusing to run: {busy}")
        return 2
    say(f"probe folder: {ROOT}")

    os.makedirs(ROOT, exist_ok=True)
    for stale in os.listdir(ROOT):
        try:
            os.remove(os.path.join(ROOT, stale))
        except OSError:
            pass

    try:
        say("\n0  two parts (P01, P02) and an assembly with 3 instances")
        for name, size in (("PZ01", 20), ("PZ02", 12)):
            docs.execute("catia_new_part",
                         {"name": name,
                          "file_path": os.path.join(ROOT, f"{name}.CATPart")})
            sketcher.execute("catia_create_sketch", {"plane": "xy"})
            sketcher.execute("catia_sketch_rectangle",
                             {"x1": 0, "y1": 0, "x2": size, "y2": size})
            sketcher.execute("catia_close_sketch", {})
            design.execute("catia_pad", {"height": size})
            docs.execute("catia_save_document", {})
        asm_path = os.path.join(ROOT, "ZASM380.CATProduct")
        docs.execute("catia_new_product", {"name": "ZASM380", "file_path": asm_path})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "PZ01.CATPart"), "count": 2})
        assembly.execute("catia_add_component",
                         {"file_path": os.path.join(ROOT, "PZ02.CATPart")})
        docs.execute("catia_save_document", {})

        # ── 1: the P1-1 wording on a never-coloured assembly ─────────────
        say("\n1  P1-1: colorize an assembly that has never been coloured")
        reply = colour.execute("catia_colorize_assembly", {})
        say("      " + reply.replace("\n", "\n      "))
        check("nothing is claimed to have been overwritten",
              "already carried a colour" not in reply, reply.splitlines()[-1][:160])
        check("the reply names the assembly default and how many were at it",
              "at the assembly default" in reply and "#FFFFFF" in reply,
              reply.splitlines()[-1][:160])

        # ── 2: a second run does report the overwrite ────────────────────
        say("\n2  P1-1: a second run reports what it overwrites")
        again = colour.execute("catia_colorize_assembly", {})
        check("the second run says 2 were overwritten",
              "2 already carried a colour" in again, again.splitlines()[-1][:160])

        # ── 3: structured + capitalisation ──────────────────────────────
        say("\n3  P2-2: structured=true and the part number's case")
        payload = colour.execute("catia_colorize_assembly", {"structured": True})
        say("      " + json.dumps(payload.get("data", {}), ensure_ascii=False)[:420])
        data = payload.get("data", {})
        parts = [entry.get("part_number") for entry in data.get("mapping", [])]
        check("the mapping keeps PZ01/PZ02, not pz01/pz02",
              parts == ["PZ01", "PZ02"], str(parts))
        check("previous_colour is null for a fresh run and set for a repeat",
              all(entry.get("previous_was_default") is False for entry in data.get("mapping", [])),
              "second run, so every entry had a colour")
        check("the counts are there",
              data.get("parts") == 2 and data.get("instances") == 3
              and data.get("generated") == 0, str({k: data.get(k) for k in
                                                   ("parts", "instances", "generated")}))
        check("the default colour is published", data.get("default_colour") == "#FFFFFF",
              str(data.get("default_colour")))

        # ── 4: replace a component with another file ────────────────────
        say("\n4  P1-2: replace PZ02.1 with PZ03 from another folder")
        elsewhere = os.path.join(ROOT, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        docs.execute("catia_new_part",
                     {"name": "PZ03", "file_path": os.path.join(elsewhere, "PZ03.CATPart")})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 35, "y2": 24})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 9})
        docs.execute("catia_save_document", {})

        # back to the assembly, put the instance somewhere not at the origin
        _document_at(conn, asm_path).Activate()
        assembly.execute("catia_set_component_transform",
                         {"component_name": "PZ02.1",
                          "translation": [80.0, 0.0, 0.0]})
        colour.execute("catia_set_color", {"target": "PZ02.1", "color": "#B33939"})
        pose_before = _pose(assembly, "PZ02.1")
        say(f"      PZ02.1 pose before: {[round(v, 3) for v in pose_before[9:12]]}")

        reply = assembly.execute("catia_replace_component",
                                 {"component_name": "PZ02.1",
                                  "file_path": os.path.join(elsewhere, "PZ03.CATPart")})
        say("      " + reply.replace("\n", "\n      ")[:600])
        listing = json.loads(assembly.execute("catia_list_components",
                                              {"names_only": True, "include_color": True}))
        names = [entry["name"] for entry in listing["components"]]
        check("the old instance is gone and the new one is there",
              "PZ02.1" not in names and "PZ03.1" in names, str(names))
        pose_after = _pose(assembly, "PZ03.1")
        drift = max(abs(a - b) for a, b in zip(pose_before, pose_after))
        check("the new instance sits exactly where the old one did",
              drift < 1e-6, f"drift {drift:.2e} mm")
        colours = {entry["name"]: entry.get("color") for entry in listing["components"]}
        check("the instance colour was carried over",
              colours.get("PZ03.1") == "#B33939", str(colours))
        check("the reply warns about constraints and about saving",
              "CONSTRAINT" in reply.upper() and "catia_save_document" in reply)

        say("\n5  the replaced assembly survives save + reopen")
        docs.execute("catia_save_document", {})
        _document_at(conn, asm_path).Close()
        docs.execute("catia_open_document", {"file_path": asm_path})
        listing = json.loads(assembly.execute("catia_list_components",
                                              {"names_only": True, "include_color": True}))
        names = [entry["name"] for entry in listing["components"]]
        colours = {entry["name"]: entry.get("color") for entry in listing["components"]}
        check("after reopening it still holds PZ03.1, not PZ02.1",
              "PZ03.1" in names and "PZ02.1" not in names, str(names))
        check("... and the colour is still there",
              colours.get("PZ03.1") == "#B33939", str(colours))

        # ── 6: the frame sketch's datum elements ────────────────────────
        say("\n6  P2-3: kind='construction' reaches inside a solid body")
        docs.execute("catia_new_part",
                     {"name": "ZFR380", "file_path": os.path.join(ROOT, "ZFR380.CATPart")})
        sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [20, 0, 20],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 20, "y2": 10})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 5})
        part = conn.get_active_part()
        body = conn.get_active_part_body()
        hybrids = [body.HybridShapes.Item(i)
                   for i in range(1, body.HybridShapes.Count + 1)]
        say(f"      body hybrid elements: {[str(node.Name) for node in hybrids]}")
        before_flags = _show_flags(conn, hybrids)
        say(f"      flags before: {before_flags}")
        reply = design.execute("catia_set_visibility",
                               {"kind": "construction", "show": False})
        say("      " + reply.replace("\n", " ")[:220])
        after_flags = _show_flags(conn, hybrids)
        say(f"      flags after: {after_flags}")
        check("the frame's datum plane and points are hidden now",
              after_flags and all(flag == 1 for flag in after_flags), str(after_flags))
        check("the reply counts them",
              "datum element(s) inside bodies" in reply, reply[:160])

    finally:
        say("\ncleanup")
        # Close ONLY what lives under this probe's own folder.
        for index in range(conn.documents.Count, 0, -1):
            doc = conn.documents.Item(index)
            try:
                full = str(doc.FullName)
            except Exception:  # noqa: BLE001
                continue
            if not full.casefold().startswith(ROOT.casefold()):
                continue
            try:
                doc.Close()
                say(f"   closed {doc.Name} | {full}")
            except Exception as error:  # noqa: BLE001
                say(f"   (could not close {doc.Name}: {error})")
        for stale in os.listdir(ROOT):
            path = os.path.join(ROOT, stale)
            try:
                os.remove(path)
            except OSError:
                try:
                    for inner in os.listdir(path):
                        os.remove(os.path.join(path, inner))
                    os.rmdir(path)
                except OSError:
                    pass
        try:
            os.rmdir(ROOT)
        except OSError:
            pass
        if before_active and _has_at(conn, before_active):
            _document_at(conn, before_active).Activate()
        say(f"session after: {conn.documents.Count} document(s)")

    failed = [label for label, ok, _ in RESULTS if not ok]
    say(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        say("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _document_at(conn, path: str):
    """The open document AT this path.

    By full path, not by name: two documents can carry one name and this exact
    mistake is what made the first version of this script act on the caller's own
    open assembly (both were called ASM.CATProduct) and then save it.
    """
    wanted = _key(path)
    for index in range(1, conn.documents.Count + 1):
        doc = conn.documents.Item(index)
        try:
            full = str(doc.FullName)
        except Exception:  # noqa: BLE001 - an unsaved document has no path
            continue
        if _key(full) == wanted:
            return doc
    raise LookupError(path)


def _has_at(conn, path: str) -> bool:
    try:
        _document_at(conn, path)
        return True
    except LookupError:
        return False


def _pose(assembly, name: str) -> list[float]:
    product = assembly.conn.get_active_product()
    active = str(assembly.conn.active_document.FullName)
    if "catia_probe_v0380e" not in active.casefold():
        raise SystemExit(f"refusing to read a pose from '{active}': not this "
                         "probe's assembly")
    for index in range(1, product.Products.Count + 1):
        component = product.Products.Item(index)
        if str(component.Name) == name:
            return assembly._position_components(component.Position)
    raise LookupError(name)


def _show_flags(conn, nodes) -> list[int]:
    """GetShow()[1] is the flag on this build - [0] reads 0 in both states."""
    doc = conn.active_document
    flags = []
    for node in nodes:
        doc.Selection.Clear()
        doc.Selection.Add(node)
        value = doc.Selection.VisProperties.GetShow()
        doc.Selection.Clear()
        flags.append(int(value[1]) if isinstance(value, (tuple, list)) else int(value))
    return flags


if __name__ == "__main__":
    raise SystemExit(main())
