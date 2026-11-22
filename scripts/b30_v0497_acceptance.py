"""B30 acceptance for 0.3.160 - the four items left open by the 2026-11-22 pair.

  A  P2-9  a sketch element carries the coordinates it was drawn with, matched to
           the LIVE index; a sketch this session did not draw carries none
  B  P1-17 listing another part's tree does NOT move the active document;
           deleting in it DOES, and says so
  C  P1-17 a Product, and a name that is not open, are refused with a way out
  D  P0-2  the feature-mirror refusal carries the three measurements that close
           it, and still creates nothing
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

results: list[tuple[str, bool, str]] = []


def say(text: str) -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  [{detail}]" if detail else ""))


def payload(reply) -> dict | list:
    if isinstance(reply, (dict, list)):
        return reply
    return json.loads(str(reply))


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sk = SketcherTools(conn)
    design = PartDesignTools(conn)

    root = os.path.join(tempfile.gettempdir(), "acc0497")
    os.makedirs(root, exist_ok=True)

    def fresh(name: str) -> str:
        path = os.path.join(root, name)
        try:
            docs.execute("catia_close_document", {"document": path, "save": False})
        except Exception:  # noqa: BLE001
            pass
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        return path

    def close_strays(*needles: str) -> None:
        """Close any document an earlier probe run left open, by the path it has."""
        listing = docs.execute("catia_list_documents", {})
        items = json.loads(listing) if isinstance(listing, str) else listing
        for item in items or []:
            path = (item.get("path") or "").casefold()
            if any(needle in path for needle in needles):
                try:
                    docs.execute("catia_close_document",
                                 {"document": item["path"], "save": False})
                except Exception:  # noqa: BLE001 - the next step reports it
                    pass

    def build(name: str, path: str) -> None:
        docs.execute("catia_new_part", {"name": name, "file_path": path})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 50, "y2": 30})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"sketch_name": "SQ", "height": 10})
        docs.execute("catia_save_document", {})

    try:
        # ── A. P2-9 ────────────────────────────────────────────────────────
        say("\n== A (P2-9). the coordinates each element was drawn with")
        geo_path = fresh("Geo0497.CATPart")
        docs.execute("catia_new_part", {"name": "Geo0497", "file_path": geo_path})
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "PR"})
        sk.execute("catia_sketch_rectangle", {"x1": 0, "y1": 0, "x2": 80, "y2": 50})
        sk.execute("catia_sketch_line", {"x1": 0, "y1": 0, "x2": 30, "y2": 40})
        sk.execute("catia_sketch_circle", {"cx": 60, "cy": 25, "radius": 8})
        geometry = payload(sk.execute("catia_sketch_get_geometry", {}))
        by_index = {entry["index"]: entry for entry in geometry["elements"]}
        drawn = {index: entry.get("drawn") for index, entry in by_index.items()}
        say("  " + str({index: (entry or {}).get("kind") for index, entry in drawn.items()}))
        rectangle = [index for index, entry in drawn.items()
                     if entry and entry.get("kind") == "rectangle"]
        check("A the four rectangle elements all carry its corners",
              len(rectangle) == 4
              and all(drawn[index]["x2"] == 80 for index in rectangle),
              str(rectangle))
        line = [entry for entry in drawn.values()
                if entry and entry.get("kind") == "line"]
        check("A the line carries its endpoints", line and line[0]["x2"] == 30
              and line[0]["y2"] == 40, str(line[:1]))
        circle = [entry for entry in drawn.values()
                  if entry and entry.get("kind") == "circle"]
        check("A the circle carries its centre and radius",
              circle and circle[0]["cx"] == 60 and circle[0]["radius"] == 8,
              str(circle[:1]))
        check("A the frame itself claims no coordinates", drawn.get(1) is None,
              str(drawn.get(1)))
        check("A spans are not leaked into the per-element copy",
              all("_span" not in (entry or {}) for entry in drawn.values()), "")

        # The record is keyed by document NAME + sketch NAME, so a close/reopen
        # inside one process keeps it. That is a fact worth pinning rather than
        # a defect: it still describes what was drawn, and the note says so.
        docs.execute("catia_save_document", {})
        docs.execute("catia_close_document", {"document": geo_path, "save": False})
        docs.execute("catia_open_document", {"file_path": geo_path})
        reopened = payload(sk.execute("catia_sketch_get_geometry",
                                      {"sketch_name": "PR"}))
        still = [entry["index"] for entry in reopened["elements"]
                 if entry.get("drawn")]
        check("A a reopened sketch of the same name keeps the record", bool(still),
              str(still))
        check("A and the note states that, with what it is keyed by",
              "survives a close" in reopened["note"]
              and "DOCUMENT NAME + SKETCH NAME" in reopened["note"], "")
        check("A and it says what a missing entry means",
              "NO 'drawn'" in reopened["note"], "")
        check("A the element list still reads after the reopen",
              reopened["count"] >= 6, str(reopened["count"]))
        docs.execute("catia_close_document", {"document": geo_path, "save": False})

        # ── B. P1-17, the two halves ──────────────────────────────────────
        say("\n== B (P1-17). naming a document instead of activating it")
        close_strays("p17", "acc0497")
        path_a = fresh("P17A.CATPart")
        path_b = fresh("P17B.CATPart")
        build("P17A", path_a)
        build("P17B", path_b)
        docs.execute("catia_activate_document", {"name": "P17B.CATPart"})
        before_active = str(conn.active_document.Name)
        listed = payload(design.execute("catia_list_features",
                                        {"document": "P17A.CATPart"}))
        after_active = str(conn.active_document.Name)
        say(f"  active {before_active} -> {after_active}; "
            f"document={listed.get('document')} count={listed.get('count')}")
        check("B a named read reports the named document",
              listed.get("document") == "P17A.CATPart", str(listed.get("document")))
        check("B a named read does NOT move the active document",
              before_active == after_active == "P17B.CATPart",
              f"{before_active} -> {after_active}")
        check("B and it says so", "read from 'P17A.CATPart' by name"
              in (listed.get("note") or ""), "")

        reply = str(design.execute("catia_delete_feature",
                                   {"document": "P17A.CATPart",
                                    "feature_name": "凸台.1"}))
        say(f"  delete: {reply[:200]}")
        check("B a named delete switched the active document",
              str(conn.active_document.Name) == "P17A.CATPart",
              str(conn.active_document.Name))
        check("B and the reply says the active document moved",
              "The active document is now 'P17A.CATPart'" in reply, "")
        check("B the feature really went", "Deleted feature '凸台.1'" in reply, "")

        # ── C. the two refusals ───────────────────────────────────────────
        say("\n== C (P1-17). what naming the wrong thing answers")
        refused_product = ""
        try:
            design.execute("catia_list_features",
                           {"document": "hexapod_assembly.CATProduct"})
        except Exception as error:  # noqa: BLE001
            refused_product = json.dumps(error.data) if getattr(
                error, "data", None) else str(error)
        check("C a Product is refused as NOT_A_PART_DOCUMENT",
              "not a Part document" in refused_product, refused_product[:90])
        refused_name = ""
        try:
            design.execute("catia_list_features", {"document": "NoSuch0497.CATPart"})
        except Exception as error:  # noqa: BLE001
            refused_name = str(error)
        check("C an unknown name says nothing was changed",
              "nothing was measured or changed" in refused_name,
              refused_name[:90])

        # ── D. P0-2 ───────────────────────────────────────────────────────
        say("\n== D (P0-2). the feature-mirror refusal")
        mir_path = fresh("Mir0497.CATPart")
        build("Mir0497", mir_path)
        sk.execute("catia_create_sketch", {"plane": "xy", "name": "PK"})
        sk.execute("catia_sketch_rectangle", {"x1": 10, "y1": 10, "x2": 30, "y2": 30})
        sk.execute("catia_close_sketch", {})
        design.execute("catia_pocket", {"sketch_name": "PK", "depth": 5})
        shapes_before = payload(design.execute("catia_list_features", {}))
        refusal = ""
        try:
            design.execute("catia_mirror", {"plane": "yz", "feature": "凹槽.1"})
        except Exception as error:  # noqa: BLE001
            refusal = str(error)
        say(f"  {refusal[:200]}")
        for needle in ("AddObjectToMirror", "242 000", "Nothing was created"):
            check(f"D the refusal states '{needle[:18]}'", needle in refusal, "")
        shapes_after = payload(design.execute("catia_list_features", {}))
        check("D and nothing was added to the tree",
              shapes_after["count"] == shapes_before["count"],
              f"{shapes_before['count']} -> {shapes_after['count']}")

        say("\n== cleanup")
        for path in (mir_path, path_b, path_a, geo_path):
            pass
        close_strays("0497")          # anything an earlier run of this script left
        for path in (mir_path, path_b, path_a, geo_path):
            try:
                docs.execute("catia_close_document", {"document": path, "save": False})
            except Exception:  # noqa: BLE001
                pass
        listing = docs.execute("catia_list_documents", {})
        items = json.loads(listing) if isinstance(listing, str) else listing
        left = [item.get("name") for item in items or []
                if "0497" in (item.get("path") or "").casefold()]
        check("D this script left nothing open", not left, str(left))

        failed = [name for name, ok, _ in results if not ok]
        say(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
        if failed:
            say("FAILED: " + "; ".join(failed))
        return 0 if not failed else 1
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
