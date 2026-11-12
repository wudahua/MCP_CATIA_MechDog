"""Does the number of OPEN DOCUMENTS change how long a tool takes?

The 2026-11-04 report measured every GSD call roughly twice as slow in a session
holding 20 documents than in one holding 1 - spline 1.05 s -> 1.96-2.29 s, join
643 -> 1957 ms, fill 350 -> 695 ms - and 11 spline runs never came back near 1 s.
It then found the cause on its own machine: 18 CATIA sample documents loaded at
startup and never closed.

That is a claim about elapsed time, and this repository's rule for those is
fixed since 0.3.49: one CATIA process per condition, at least twice each, because
a single session drifts (2.35 s -> 13.65 s on identical work). So this script
measures ONE condition and quits the application:

    python b30_v0357_docs_bench.py --docs 0 --spline 90 --repeats 4
    python b30_v0357_docs_bench.py --docs 8 --spline 90 --repeats 4

``--docs`` opens that many extra empty Part documents BEFORE the timed calls, so
the machine state is set up once and the same three calls run afterwards.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catia_mcp.connection import CATIAConnection
from catia_mcp.tools.document import DocumentTools
from catia_mcp.tools.gsd import GSDTools

OUT_DIR = os.path.join(tempfile.gettempdir(), "b30_v0357")


def say(text: str = "") -> None:
    print(text, flush=True)


def points(count: int) -> list[list[float]]:
    """A helix: 90 nodes over 3 turns, 40 mm rise - the shape the report timed."""
    out = []
    for index in range(count):
        t = index / (count - 1)
        angle = 2 * math.pi * 3 * t
        out.append([round(30 * math.cos(angle), 4), round(30 * math.sin(angle), 4),
                    round(40 * t, 4)])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=0,
                        help="extra empty Part documents to leave open")
    parser.add_argument("--spline", type=int, default=90)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--join", type=int, default=8,
                        help="surfaces to join per timing (0 = skip)")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    conn = CATIAConnection()
    conn.connect()
    document = DocumentTools(conn)
    gsd = GSDTools(conn)

    # A benchmark that inherits documents measures the wrong thing, and app.Quit()
    # blocks on the "save changes?" dialog of a dirty one - which cost a run
    # (a SIGTERM, with Part5 left behind). Start clean or do not start.
    if conn.documents.Count:
        open_now = []
        for index in range(1, conn.documents.Count + 1):
            try:
                open_now.append(str(conn.documents.Item(index).Name))
            except Exception:  # noqa: BLE001
                open_now.append("?")
        raise SystemExit(
            f"{len(open_now)} document(s) are already open ({', '.join(open_now)}); "
            "close them and run again, so both conditions start from the same "
            "state and the timing is about the variable under test.")

    started = time.perf_counter()
    document.execute("catia_new_part",
                     {"name": "BENCH", "file_path": os.path.join(OUT_DIR, "BENCH.CATPart")})
    assert str(conn.active_document.Name).startswith("BENCH"), conn.active_document.Name
    say(f"own document ready in {(time.perf_counter() - started) * 1000:.0f} ms")

    for index in range(1, args.docs + 1):
        document.execute("catia_new_part",
                         {"name": f"PAD{index}",
                          "file_path": os.path.join(OUT_DIR, f"PAD{index}.CATPart")})
        assert str(conn.active_document.Name).startswith(f"PAD{index}"), \
            conn.active_document.Name
    say(f"extra documents open: {args.docs}")

    document.execute("catia_activate_document", {"name": "BENCH.CATPart"})
    assert str(conn.active_document.Name).startswith("BENCH"), conn.active_document.Name
    say(f"open documents now: {conn.documents.Count}")

    say(f"\n── {args.spline}-point spline x{args.repeats} ──")
    for run in range(args.repeats):
        name = f"SPLIN{run}"
        started = time.perf_counter()
        gsd.execute("catia_gsd_spline",
                    {"points": points(args.spline), "name": name})
        say(f"  run {run + 1}: {(time.perf_counter() - started) * 1000:.1f} ms")

    if args.join:
        say(f"\n── join of {args.join} extruded lanes x2 ──")
        for run in range(2):
            names = []
            # A zigzag of 8 segments: consecutive extrusions share the vertical
            # edge at their common vertex, which is what a join needs. Parallel
            # lanes 6 mm apart share nothing and fail - measured here first.
            nodes = [[lane * 10.0, 0 if lane % 2 == 0 else 8.0, 0]
                     for lane in range(args.join + 1)]
            for lane in range(args.join):
                line = f"JL{run}_{lane}"
                gsd.execute("catia_gsd_line",
                            {"point1": nodes[lane], "point2": nodes[lane + 1],
                             "name": line})
                gsd.execute("catia_gsd_extrude",
                            {"profile": line, "direction": [0, 0, 1], "limit1": 30.0,
                             "name": f"JS{run}_{lane}"})
                names.append(f"JS{run}_{lane}")
            started = time.perf_counter()
            gsd.execute("catia_gsd_join", {"elements": names, "name": f"JOIN{run}"})
            say(f"  run {run + 1}: {(time.perf_counter() - started) * 1000:.1f} ms")

    say("\n── closing what this script created ──")
    for index in range(conn.documents.Count, 0, -1):
        try:
            doc = conn.documents.Item(index)
            name = str(doc.Name)
            # Everything here was created by this run; the guard above made sure
            # the session started empty, so no close is aimed at a user document.
            if True:
                doc.Activate()
                document.execute("catia_close_document", {"save": False})
        except Exception:  # noqa: BLE001
            pass
    say(f"documents open now: {conn.documents.Count}")

    if conn.documents.Count:
        say(f"NOT quitting: {conn.documents.Count} document(s) are still open, and "
            "Quit would block on their save dialog")
        return
    say("\n── quitting CATIA so the next condition starts clean ──")
    try:
        conn.app.Quit()
    except Exception as error:  # noqa: BLE001
        say(f"  quit failed: {str(error).splitlines()[0][:60]}")


if __name__ == "__main__":
    main()
