"""Can AddSection actually drive a spine, and is the result a real spine?

b30_v0356_spine.py dumped HybridShapeSpine's type library: 40 members, among
them AddSection(iSection), AddGuide(iGuide), GetNumberOfSections(), GetSection,
ModifySectionCurve, SetStartPoint, Orientation.

0.3.53 wrote "the typelib names no way to give it sections" into README. That was
wrong: the probe behind it listed dir(spine) looking for names containing "Add"
or "ection" and found nothing, which for a late-bound COM object proves nothing.
The interface has always declared AddSection.

Measured so far (SP3 in the first run): three sections go in, GetNumberOfSections
returns 3, UpdateObject SUCCEEDS, Length reads 40.0000 mm. But those three rings
were concentric and collinear, so 40.0000 is also exactly what a straight line
from the first centre to the last centre would measure - the number does not tell
the two apart.

So this run uses sections that are NOT collinear:

    centre (0,0,0)  r=10      centre (30,0,20) r=7      centre (0,0,40) r=4

    a spine through all three centres  -> about 36.06 + 36.06 = 72.11 mm
    a line from first centre to last   -> 40.00 mm

and reads the distance from the curve to each centre as well, since Length alone
could still be fooled. GetMinimumDistance (not MinimumDistance - the Measurable
member is GetMinimumDistance, and it takes a REFERENCE) gives that.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, r"F:\WorkBuddy Space\MCP catia-v5-lite")

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT_DIR = os.path.join(tempfile.gettempdir(), "b30_v0356")

# (x, y, z, radius): deliberately not collinear, and not coaxial either.
SECTIONS = [(0.0, 0.0, 0.0, 10.0),
            (30.0, 0.0, 20.0, 7.0),
            (0.0, 0.0, 40.0, 4.0)]


def say(text: str = "") -> None:
    print(text, flush=True)


def fresh(conn, document, tag: str):
    document.execute("catia_new_part",
                     {"name": tag, "file_path": os.path.join(OUT_DIR, f"{tag}.CATPart")})
    assert str(conn.active_document.Name).startswith(tag), conn.active_document.Name
    return conn.get_active_part()


def close_tag(conn, document, tag: str) -> None:
    for index in range(conn.documents.Count, 0, -1):
        try:
            if str(conn.documents.Item(index).Name).startswith(tag):
                conn.documents.Item(index).Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass


def run_case(conn, document, gsd, tag: str, reverse: bool) -> None:
    say(f"\n===== {tag}: three non-collinear rings, reverse={reverse} =====")
    part = fresh(conn, document, tag)
    factory = gsd._factory()
    geoset = gsd._get_geoset()
    names, centres = [], []
    for index, (x, y, z, radius) in enumerate(SECTIONS, start=1):
        names.append(f"{tag}_C{index}")
        centres.append(f"{tag}_P{index}")
        gsd.execute("catia_gsd_circle", {"center": [x, y, z], "support_plane": "xy",
                                         "radius": radius, "name": f"{tag}_C{index}"})
        gsd.execute("catia_gsd_point", {"x": x, "y": y, "z": z,
                                        "name": f"{tag}_P{index}"})

    spine = factory.AddNewSpine()
    geoset.AppendHybridShape(spine)
    spine.Name = f"{tag}_SP"
    say(f"  fresh: sections={spine.GetNumberOfSections()} "
        f"guides={spine.GetNumberOfGuides()}")
    order = list(reversed(names)) if reverse else names
    for name in order:
        ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        spine.AddSection(ref)
        say(f"  AddSection({name}) -> count {spine.GetNumberOfSections()}")

    try:
        part.UpdateObject(spine)
        say("  UpdateObject OK")
    except Exception as error:  # noqa: BLE001
        say(f"  UpdateObject FAILED: {str(error).splitlines()[0][:70]}")
        close_tag(conn, document, tag)
        return

    measurable = (conn.active_document.GetWorkbench("SPAWorkbench")
                  .GetMeasurable(part.CreateReferenceFromObject(spine)))
    length = float(measurable.Length)
    say(f"  Length = {length:.4f} mm   (through all three centres ~72.11, "
        f"first-to-last only 40.00)")
    for name in centres:
        point_ref = part.CreateReferenceFromObject(part.FindObjectByName(name))
        try:
            distance = float(measurable.GetMinimumDistance(point_ref))
            say(f"  distance from curve to {name}: {distance:.4f} mm")
        except Exception as error:  # noqa: BLE001
            say(f"  distance to {name} FAILED: {str(error).splitlines()[0][:60]}")

    say("  ── what GetSection returns ──")
    for index in range(1, spine.GetNumberOfSections() + 1):
        try:
            section = spine.GetSection(index)
            say(f"    GetSection({index}) -> {section}")
        except Exception as error:  # noqa: BLE001
            say(f"    GetSection({index}) FAILED: {str(error).splitlines()[0][:60]}")

    say("  ── remove the middle section, update again ──")
    try:
        middle = part.CreateReferenceFromObject(part.FindObjectByName(names[1]))
        spine.RemoveSection(middle)
        part.UpdateObject(spine)
        after = (conn.active_document.GetWorkbench("SPAWorkbench")
                 .GetMeasurable(part.CreateReferenceFromObject(spine)))
        say(f"    count {spine.GetNumberOfSections()}, Length now "
            f"{float(after.Length):.4f} mm (two rings left)")
    except Exception as error:  # noqa: BLE001
        say(f"    FAILED: {str(error).splitlines()[0][:70]}")

    close_tag(conn, document, tag)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)
    run_case(conn, document, gsd, "SPNC", reverse=False)
    run_case(conn, document, gsd, "SPNCR", reverse=True)
    say(f"\ndocuments open now: {conn.documents.Count}")


if __name__ == "__main__":
    main()
