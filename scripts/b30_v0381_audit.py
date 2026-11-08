"""0.3.81 audit: the twelve claims of the two 2026-11-08 reports, on B30.

Each numbered block is one `check()`. Nothing here is a mock: every verdict is a
read-back from CATIA after the call, because the reports are about replies that
described something the model did not do.

  1  a spline sketch survives a rollback (0.3.80 lost it for good)
  2  the rebuilt spline is the same profile (4 points, closed)
  3  the rebuilt record reached _drawn, so get_geometry reports it
  4  the alias resolves by the name the CALLER used, in that direction
  5  a duplicate geometrical set is renamed instead of duplicated
  6  list_elements publishes parent + depth for a nested set
  7  set_active_geoset refuses an ambiguous name instead of picking one
  8  replace_component refuses a byte copy of an already-open document
  9  ... and leaves the old instance alone when it refuses
 10  replace_component reads the reference back on the honest path
 11  list_components(include_reference) answers the same question
 12  close_document(document=...) closes that one and not the active one
 13  the pocket reply bounds the cut by profile area x travel
 14  a colour set to #D2D2FF counts as "no colour of its own"
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.colours import is_default  # noqa: E402
from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.gsd import GSDTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = r"F:\catia_probe_v0381_audit"
PASSED: list[str] = []
FAILED: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def check(number: int, claim: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    say(f"  [{mark}] {number:2d} {claim}")
    if detail:
        say("            " + detail.replace("\n", "\n            ")[:400])
    (PASSED if ok else FAILED).append(f"{number} {claim}")


def as_json(reply) -> dict:
    """A tool reply as a dict - several tools answer with json.dumps(...)."""
    if isinstance(reply, dict):
        return reply
    try:
        parsed = json.loads(str(reply))
    except Exception:  # noqa: BLE001 - not JSON, so nothing to index
        return {}
    return parsed if isinstance(parsed, dict) else {}


def text_of(reply) -> str:
    """The message of a structured reply, or a plain string."""
    if isinstance(reply, dict):
        return str(reply.get("message") or reply)
    for attribute in ("message",):
        value = getattr(reply, attribute, None)
        if value:
            return str(value)
    return str(reply)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    for folder in ("A", "B"):
        os.makedirs(os.path.join(ROOT, folder), exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    design = PartDesignTools(conn)
    gsd = GSDTools(conn)
    asm = AssemblyTools(conn)
    mine = os.path.normcase(os.path.abspath(ROOT))
    say(f"session before: {conn.documents.Count} document(s)")

    try:
        # ── 1-4: the spline that a rollback used to destroy ──────────────
        say("\nSpline sketch after a rollback")
        docs.execute("catia_new_part", {
            "name": "PZ81SP", "file_path": os.path.join(ROOT, "A", "PZ81SP.CATPart"),
            "overwrite": True})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 5})

        sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [0, 100, 0],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        sketcher.execute("catia_sketch_spline", {
            "points": [[0, 0], [10, 0], [10, 10], [0, 10]], "closed": True})
        sketcher.execute("catia_close_sketch", {})
        spline_name = str(conn.last_sketch.Name)
        say(f"   spline sketch is '{spline_name}'")

        # 'reverse' points away from the block, so this is the ordinary
        # wrong-direction mistake - the one that used to cost the whole section.
        reply = ""
        try:
            design.execute("catia_pocket", {
                "sketch_name": spline_name, "depth": 50,
                "direction": "reverse"})
        except Exception as error:  # noqa: BLE001 - the refusal IS the test
            reply = str(error)
        check(1, "a spline sketch is rebuilt instead of lost",
              "re-created from its recorded definition" in reply,
              reply[:300])
        check(2, "the message does not also say it could not be rebuilt",
              "could NOT be re-created" not in reply,
              reply[-260:])

        features = as_json(design.execute("catia_list_features", {}))
        geometry = as_json(sketcher.execute(
            "catia_sketch_get_geometry", {"sketch_name": spline_name}))
        say("            geometry: "
            + json.dumps(geometry, ensure_ascii=False)[:520])
        listed_sketches = [str(item.get("name")) for item in
                           (features.get("sketches") or features.get("features")
                            or []) if isinstance(item, dict)]
        kinds = [str(item.get("type") or item.get("name") or "")
                 for item in (geometry.get("elements") or [])]
        spline_element = [item for item in (geometry.get("elements") or [])
                          if str(item.get("type")) == "9"
                          or "样条" in str(item.get("name"))]
        check(3, "the rebuilt sketch holds a spline and is listed",
              bool(listed_sketches) and bool(spline_element),
              f"sketches {listed_sketches} | elements {kinds[:8]}")

        located = sketcher._locate_sketch(spline_name)
        how = located[1]
        check(4, "the alias is reported caller-name first",
              how == "by name" or how.startswith(f"by alias '{spline_name}'"),
              f"resolved {getattr(located[0], 'Name', '?')} -> {how!r}")

        # ── 5-7: geometrical sets ────────────────────────────────────────
        say("\nGeometrical sets")
        gsd.execute("catia_gsd_create_geoset", {"name": "OUTER"})
        gsd.execute("catia_gsd_create_geoset", {"name": "INNER", "parent": "OUTER"})
        dup = gsd.execute("catia_gsd_create_geoset", {"name": "OUTER"})
        check(5, "a duplicate geometrical set is renamed, not duplicated",
              "'OUTER#2'" in dup and "already exists" in dup,
              dup[:300])

        listing = as_json(gsd.execute("catia_gsd_list_elements", {}))
        nested = [entry for entry in listing.get("geometrical_sets", [])
                  if entry.get("name") == "INNER"]
        check(6, "list_elements publishes parent and depth",
              bool(nested) and nested[0].get("parent") == "OUTER"
              and nested[0].get("depth") == 1,
              str(nested[:1])[:260])

        # Make a real duplicate on purpose: only the tree can produce one now.
        part = conn.get_active_part()
        second = None
        try:
            fresh = part.HybridBodies.Add()
            fresh.Name = "OUTER"
            second = str(fresh.Name)
        except Exception as error:  # noqa: BLE001
            second = f"<could not force a duplicate: {error}>"
        ambiguous = ""
        ambiguous_code = ""
        try:
            gsd.execute("catia_gsd_set_active_geoset", {"name": "OUTER"})
        except Exception as error:  # noqa: BLE001 - the refusal IS the test
            ambiguous = str(error)
            ambiguous_code = str(getattr(error, "code", ""))
        check(7, "an ambiguous set name is refused with the candidates",
              ambiguous_code == "GEOSET_AMBIGUOUS",
              f"forced duplicate: {second}; code {ambiguous_code or '<none>'}; "
              + ambiguous[:240])

        # ── 8-11: the reference a component really points at ─────────────
        say("\nreplace_component and the reference it claims")
        original = os.path.join(ROOT, "A", "PZ81R.CATPart")
        copy_b = os.path.join(ROOT, "B", "PZ81R.CATPart")
        other = os.path.join(ROOT, "B", "PZ81S.CATPart")
        docs.execute("catia_new_part", {
            "name": "PZ81R", "file_path": original, "overwrite": True})
        docs.execute("catia_new_part", {
            "name": "PZ81S", "file_path": other, "overwrite": True})
        shutil.copyfile(original, copy_b)
        asm_path = os.path.join(ROOT, "B", "ASM81.CATProduct")
        docs.execute("catia_new_product", {
            "name": "ASM81", "file_path": asm_path, "overwrite": True})
        asm.execute("catia_add_component", {"file_path": original})
        # Load the original explicitly: this is the reporter's exact situation.
        docs.execute("catia_open_document", {"file_path": original})
        docs.execute("catia_activate_document", {"document": "ASM81.CATProduct"})

        mismatch = ""
        try:
            asm.execute("catia_replace_component",
                        {"component_name": "PZ81R.1", "file_path": copy_b})
        except Exception as error:  # noqa: BLE001 - the refusal IS the test
            mismatch = str(error)
        check(8, "a byte copy of an open document is refused",
              "REPLACE_REFERENCE_MISMATCH" in mismatch
              or "references" in mismatch and "instead" in mismatch,
              mismatch[:320])

        listed = as_json(asm.execute("catia_list_components", {}))
        components = listed.get("components") or []
        check(9, "the old instance is still there after the refusal",
              len(components) == 1,
              f"{len(components)} component(s): "
              + ", ".join(str(item.get("name")) for item in components))

        honest = text_of(asm.execute("catia_replace_component",
                                     {"component_name": "PZ81R.1",
                                      "file_path": other}))
        check(10, "the honest path reads the reference back",
              "read back, not assumed" in honest
              and os.path.basename(other).casefold() in honest.casefold(),
              honest[:340])

        audit = as_json(asm.execute("catia_list_components",
                                    {"include_reference": True}))
        refs = [item.get("reference_path") for item in (audit.get("components") or [])]
        check(11, "list_components(include_reference) answers the same question",
              bool(refs) and any(ref and os.path.basename(other).casefold()
                                 in ref.casefold() for ref in refs),
              str(refs)[:300])

        # ── 12: closing a document by name ──────────────────────────────
        say("\nclose_document(document=...)")
        first = os.path.join(ROOT, "A", "PZ81C.CATPart")
        keep = os.path.join(ROOT, "A", "PZ81D.CATPart")
        docs.execute("catia_new_part", {
            "name": "PZ81C", "file_path": first, "overwrite": True})
        docs.execute("catia_new_part", {
            "name": "PZ81D", "file_path": keep, "overwrite": True})
        active_before = str(conn.active_document.Name)
        closed = text_of(docs.execute("catia_close_document",
                                       {"document": "PZ81C.CATPart"}))
        names_now = [str(conn.documents.Item(index).Name)
                     for index in range(1, conn.documents.Count + 1)]
        check(12, "the named document closes and the active one does not",
              "PZ81C.CATPart" not in names_now
              and "PZ81D.CATPart" in names_now,
              f"active was {active_before}; now: {closed[:80]}; "
              f"PZ81D still open: {'PZ81D.CATPart' in names_now}")

        # ── 13: the arithmetic bound in the pocket reply ────────────────
        say("\nPocket arithmetic")
        docs.execute("catia_new_part", {
            "name": "PZ81P", "file_path": os.path.join(ROOT, "A", "PZ81P.CATPart"),
            "overwrite": True})
        sketcher.execute("catia_create_sketch", {"plane": "xy"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": 40, "y2": 40})
        sketcher.execute("catia_close_sketch", {})
        design.execute("catia_pad", {"height": 5})
        sketcher.execute("catia_create_sketch", {
            "plane": {"type": "frame", "origin": [0, 300, 0],
                      "u": [1, 0, 0], "v": [0, 0, 1]}})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 10, "y1": 1, "x2": 20, "y2": 4})
        sketcher.execute("catia_close_sketch", {})
        cut = text_of(design.execute("catia_pocket", {
            "sketch_name": str(conn.last_sketch.Name), "depth": 400,
            "direction": "normal"}))
        check(13, "the cut is bounded by profile area x travel",
              "at most 12000.0" in cut and "1200.0 mm3" in cut,
              cut[:340])

        # ── 14: the colour that means "no colour" ───────────────────────
        check(14, "#D2D2FF counts as no colour of its own",
              is_default((210, 210, 255), instance=True)
              and is_default((255, 255, 255), instance=True)
              and not is_default((255, 255, 255), instance=False),
              "instance: lavender+white are default; body: white is a real colour")

    finally:
        say("\ncleanup: closing only documents under " + ROOT)
        for index in range(int(conn.documents.Count), 0, -1):
            try:
                doc = conn.documents.Item(index)
                full = os.path.normcase(os.path.abspath(str(doc.FullName)))
            except Exception:  # noqa: BLE001
                continue
            if not full.startswith(mine):
                continue
            try:
                doc.Close()
                say(f"   closed {os.path.basename(full)}")
            except Exception as error:  # noqa: BLE001
                say(f"   could not close {os.path.basename(full)}: "
                    f"{str(error).splitlines()[0][:60]}")
        say(f"session after: {conn.documents.Count}")

    say("\n" + "=" * 66)
    say(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for item in FAILED:
        say(f"  FAILED {item}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
