"""Repair the assembly this session damaged.

What happened: an acceptance script resolved "ASM.CATProduct" BY NAME, and two
documents carried that name - the probe one it had just created and the caller's
own open assembly. The first match won, so `catia_set_component_transform`,
`catia_set_color` and `catia_replace_component` all ran on the caller's file, and
the following `catia_save_document` wrote it to disk:

    P02.1                 removed
    P03.3 (a probe part)  appended, referencing F:\\catia_probe_v0380e\\...

The file: F:\\WokrBuddy Catia Space\\<...>\\out\\ASM.CATProduct
Its untouched source: F:\\WB_CATIA_Gripper\\ASM.CATProduct (2026-11-06 22:21, the
byte copy that this file was made from), which still holds all 25 instances in
their original order with their original placements.

So the repair is: take the STRUCTURE back from the source, then repaint the
colours the damaged file recorded, so the colour work is not lost either. P02.1
is the one entry the damaged file no longer has; its colour came from the same
run and is in the caller's own report (#00B894, the second palette entry).

Nothing is written until every reading has succeeded, and the repaired file is
re-listed afterwards to prove it.
"""
from __future__ import annotations

import datetime
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection  # noqa: E402

OUT = r"F:\WokrBuddy Catia Space\文本建模-简易机械爪夹具结构\out\ASM.CATProduct"
SRC = r"F:\WB_CATIA_Gripper\ASM.CATProduct"
PROBE_MARKER = "catia_probe_v0380e"
P02_COLOUR = (0x00, 0xB8, 0x94)      # the report's second palette entry


def say(line: str = "") -> None:
    print(line, flush=True)


def describe(conn, doc):
    """``[(name, part_number, reference path, colour)]`` for one product."""
    product = conn.product_of(doc)
    rows = []
    for index in range(1, product.Products.Count + 1):
        component = product.Products.Item(index)
        try:
            number = str(component.PartNumber)
        except Exception:  # noqa: BLE001
            number = "?"
        try:
            path = str(component.ReferenceProduct.Parent.FullName)
        except Exception as error:  # noqa: BLE001
            path = f"<broken {str(error).splitlines()[0][:30]}>"
        doc.Selection.Clear()
        doc.Selection.Add(component)
        try:
            colour = tuple(int(v) for v in
                           doc.Selection.VisProperties.GetRealColor())[-3:]
        except Exception:  # noqa: BLE001
            colour = None
        doc.Selection.Clear()
        rows.append((str(component.Name), number, os.path.normcase(path), colour))
    return rows


def main() -> int:
    for path in (OUT, SRC):
        if not os.path.isfile(path):
            say(f"missing: {path}")
            return 2

    conn = CATIAConnection()
    conn.connect()
    say(f"session: {conn.documents.Count} document(s)")
    say("out/ASM.CATProduct mtime before repair: "
        + datetime.datetime.fromtimestamp(os.path.getmtime(OUT)).strftime("%Y-%m-%d %H:%M:%S"))

    # ── 1: read both, write nothing ──────────────────────────────────────
    source_doc = conn.documents.Open(os.path.normpath(SRC))
    source = describe(conn, source_doc)
    source_doc.Close()

    damaged_doc = conn.documents.Open(os.path.normpath(OUT))
    damaged = describe(conn, damaged_doc)
    damaged_doc.Close()

    say(f"\nsource (untouched): {len(source)} instance(s)")
    say(f"damaged (on disk):  {len(damaged)} instance(s)")

    colours: dict[str, tuple[int, int, int]] = {}
    for name, _number, path, colour in damaged:
        if PROBE_MARKER.casefold() in path.casefold():
            say(f"   stray probe instance in the damaged file: {name} -> {path}")
            continue
        if colour is not None:
            colours[name] = colour
    colours.setdefault("P02.1", P02_COLOUR)
    say(f"   colours recovered: {len(colours)} "
        f"({', '.join(sorted(colours))})")

    missing = [row[0] for row in source if row[0] not in colours]
    if missing:
        say(f"   no colour known for {missing} - they will stay uncoloured")
    extra = [name for name in colours if name not in {row[0] for row in source}]
    if extra:
        say(f"   colour recorded for an instance the source does not have: {extra}")

    # ── 2: structure back from the source ───────────────────────────────
    backup = OUT + ".damaged-" + datetime.datetime.now().strftime("%H%M%S")
    shutil.copyfile(OUT, backup)
    say(f"\nbackup of the damaged file: {backup}")
    shutil.copyfile(SRC, OUT)
    say("out/ASM.CATProduct now holds the source structure again "
        f"({os.path.getsize(OUT)} B)")

    # ── 3: repaint ──────────────────────────────────────────────────────
    doc = conn.documents.Open(os.path.normpath(OUT))
    product = conn.product_of(doc)
    painted = 0
    for index in range(1, product.Products.Count + 1):
        component = product.Products.Item(index)
        colour = colours.get(str(component.Name))
        if colour is None:
            continue
        doc.Selection.Clear()
        doc.Selection.Add(component)
        # Four arguments with a non-zero flag, or the viewer ignores it.
        doc.Selection.VisProperties.SetRealColor(colour[0], colour[1], colour[2], 1)
        doc.Selection.Clear()
        painted += 1
    say(f"repainted {painted} instance(s)")

    # ── 4: save, reopen, and prove it ───────────────────────────────────
    doc.Save()
    doc.Close()
    check_doc = conn.documents.Open(os.path.normpath(OUT))
    final = describe(conn, check_doc)
    check_doc.Close()

    say(f"\nrepaired file: {len(final)} instance(s), "
        f"{os.path.getsize(OUT)} B, "
        + datetime.datetime.fromtimestamp(os.path.getmtime(OUT)).strftime("%H:%M:%S"))
    problems = []
    if len(final) != len(source):
        problems.append(f"{len(final)} instances, source had {len(source)}")
    for (name, number, path, _c), (sname, snumber, spath, _sc) in zip(final, source):
        if name != sname or number != snumber or path != spath:
            problems.append(f"order/reference differs at {name} vs {sname}")
            break
    if any(PROBE_MARKER.casefold() in row[2].casefold() for row in final):
        problems.append("a probe instance is still referenced")
    for name, _number, _path, colour in final:
        expected = colours.get(name)
        if expected is not None and colour != expected:
            problems.append(f"{name} colour {colour} != {expected}")

    for name, number, path, colour in final:
        say(f"   {name:10} pn={number:6} colour={colour}")
    say("")
    if problems:
        say("STILL WRONG: " + "; ".join(problems))
        return 1
    say("REPAIRED: structure and order match the source, every colour is back, "
        "no probe instance remains.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
