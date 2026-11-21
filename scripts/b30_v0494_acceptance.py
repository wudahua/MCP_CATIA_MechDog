"""0.3.157 acceptance on B30, 2026-11-21: the report's P1-11 / P1-12, rebuilt.

The reporter's scene is reproduced from nothing: a part, an assembly holding four
instances of it at four distinct poses, and then the part file is REBUILT over
itself with a different box - which is what left their eight instances unreadable.

The order of the cells follows what the state probe measured first
(``b30_v0494_state_probe.py``): with an assembly open, the parts it references ARE
in ``Application.Documents`` (``component_of=<assembly>``) and closing one answers
"STILL OPEN - the close request was accepted and deferred". So the rebuild is
BLOCKED there, and the warning belongs in that refusal, not in a successful
overwrite. What follows is therefore:

  A  the rebuild is refused, and the refusal names the 4 instances, the assembly,
     and the one-call repair.
  B  close the assembly (the part goes with it), rebuild -> reopen: 4 broken,
     poses still readable, and a second close/reopen cycle does not mend them.
  C  ONE call repairs all four with keep_name=true: names, poses and part numbers
     read back.
  D  the same successful rebuild a second time (the file is no longer loaded)
     warns, and counts the instances it cannot read.
  E  control: the repair WITHOUT keep_name leaves P.5 where P.1 was.
  F  a list with one bad name changes nothing, because every name is resolved
     before the first insert.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")

from catia_mcp.connection import CATIAConnection  # noqa: E402
from catia_mcp.tools.assembly import AssemblyTools  # noqa: E402
from catia_mcp.tools.document import DocumentTools  # noqa: E402
from catia_mcp.tools.measurement import MeasurementTools  # noqa: E402
from catia_mcp.tools.part_design import PartDesignTools  # noqa: E402
from catia_mcp.tools.sketcher import SketcherTools  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="acc0494_")
CHECKS: list[tuple[str, bool, str]] = []
POSES = [[0.0, 0.0, 0.0], [150.0, 0.0, 0.0],
         [0.0, 200.0, 0.0], [150.0, 200.0, 40.0]]


def say(text: str = "") -> None:
    print(text, flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail else ""), flush=True)


def payload(reply):
    return reply if isinstance(reply, dict) else json.loads(reply)


def main() -> int:
    conn = CATIAConnection()
    conn.connect()
    docs = DocumentTools(conn)
    sketcher = SketcherTools(conn)
    part_design = PartDesignTools(conn)
    measurement = MeasurementTools(conn)
    assembly = AssemblyTools(conn)

    def volume():
        data = payload(measurement.execute("catia_get_inertia", {}))
        if isinstance(data.get("data"), dict):
            data = data["data"]
        for key in ("volume_mm3", "volume"):
            if data.get(key) is not None:
                return float(data[key])
        return None

    def fill(x: float, y: float, z: float) -> None:
        """A box in whatever part document is active."""
        sketcher.execute("catia_create_sketch", {"plane": "xy", "name": "SQ"})
        sketcher.execute("catia_sketch_rectangle",
                         {"x1": 0, "y1": 0, "x2": x, "y2": y})
        sketcher.execute("catia_close_sketch", {})
        part_design.execute("catia_pad", {"sketch_name": "SQ", "height": z})

    def block(name: str, path: str, x: float, y: float, z: float) -> None:
        docs.execute("catia_new_part", {"name": name, "file_path": path})
        fill(x, y, z)

    def open_paths() -> set[str]:
        listing = payload(docs.execute("catia_list_documents", {}))
        items = listing.get("documents") if isinstance(listing, dict) else listing
        return {os.path.normcase(os.path.abspath(i["path"]))
                for i in items or [] if i.get("path")}

    def release(part: str, asm: str) -> bool:
        """Close the assembly and the part, and wait until the FILE is free.

        Measured on B30 2026-11-21: closing the assembly releases the parts it
        loaded, but not in the same instant - catia_close_document(part) one beat
        later answers "No open document matches", and catia_new_part one beat
        later still answers DOCUMENT_ALREADY_OPEN. So this polls the list instead
        of assuming the order of two effects.
        """
        docs.execute("catia_close_document", {"document": asm, "save": True})
        wanted = os.path.normcase(os.path.abspath(part))
        for _ in range(8):
            if wanted not in open_paths():
                return True
            try:
                docs.execute("catia_close_document",
                             {"document": part, "save": False})
            except Exception:  # noqa: BLE001 - "no such document" is the goal
                pass
            time.sleep(0.4)
        return wanted not in open_paths()

    # Anything an earlier failed run of THIS script left open is closed first:
    # the names repeat (A1_Asm.CATProduct), and catia_activate_document resolves
    # by name, so a leftover twin would make every activation ambiguous.
    for _ in range(2):
        stale = [path for path in open_paths() if "acc0494" in path]
        if not stale:
            break
        for path in sorted(stale, key=lambda p: not p.lower().endswith(".catproduct")):
            try:
                docs.execute("catia_close_document",
                             {"document": path, "save": False})
            except Exception:  # noqa: BLE001 - the next pass tries again
                pass
        time.sleep(0.4)

    def try_close(*paths: str) -> None:
        for path in paths:
            try:
                docs.execute("catia_close_document",
                             {"document": path, "save": False})
            except Exception:  # noqa: BLE001 - already closed is the goal
                pass

    def components():
        return payload(assembly.execute("catia_list_components", {}))

    def scene(tag: str, poses: list[list[float]]):
        """A 100x60x20 part, an assembly with one posed instance per pose."""
        part = os.path.join(ROOT, f"{tag}_Part.CATPart")
        asm = os.path.join(ROOT, f"{tag}_Asm.CATProduct")
        block(f"{tag}_Piston", part, 100, 60, 20)
        first_volume = volume()
        docs.execute("catia_new_product", {"name": f"{tag}_Asm", "file_path": asm})
        for _ in poses:
            assembly.execute("catia_add_component", {"file_path": part})
        names = [c["name"] for c in components()["components"]]
        docs.execute("catia_activate_document",
                     {"name": os.path.basename(asm)})
        for name, translation in zip(names, poses):
            if not any(abs(value) > 0 for value in translation):
                continue      # already at the origin, and a zero move is refused
            assembly.execute("catia_set_component_transform",
                             {"component_name": name,
                              "translation": list(translation)})
        docs.execute("catia_save_document", {})
        return part, asm, names, first_volume

    # ══ A: the rebuild is refused, and the refusal names the damage ═══════
    say("A: rebuild a part file while the assembly that uses it is open")
    part, asm, names, first_volume = scene("A1", POSES)
    asm_name = os.path.basename(asm)
    say(f"  instances: {names}")
    before = {c["name"]: [c["position"]["x"], c["position"]["y"], c["position"]["z"]]
              for c in components()["components"] if not c.get("broken")}
    say(f"  poses before: {before}")
    check("A four instances were placed and posed", len(before) == 4, str(len(before)))

    try:
        docs.execute("catia_new_part",
                     {"name": "A1_Piston_v2", "file_path": part, "overwrite": True})
        check("A the rebuild is refused while the assembly is open", False,
              "it was accepted")
        message = ""
    except Exception as error:  # noqa: BLE001 - the refusal IS the measurement
        message = " ".join(str(error).split())
        check("A the rebuild is refused while the assembly is open",
              getattr(error, "code", "") == "DOCUMENT_ALREADY_OPEN", message[:70])
    say(f"  refusal: {message[:600]}")
    check("A it names the 4 instances", all(name in message for name in names),
          "")
    check("A and names the assembly document", asm_name in message, asm_name)
    check("A and gives the one-call repair", "keep_name=true" in message, "")
    check("A and says the overwrite would cost those instances their part",
          "PartNumber" in message and "does NOT repair them" in message, "")

    # ══ B: close, rebuild, reopen - the damage, and that it stays ═════════
    say("\nB: close the assembly, rebuild the file, reopen the assembly")
    freed = release(part, asm)
    say(f"  the file is free again: {freed}")
    check("B closing the assembly releases the part file", freed, "")
    reply = docs.execute("catia_new_part",
                         {"name": "A1_Piston_v2", "file_path": part,
                          "overwrite": True})
    say(f"  rebuild: {' '.join(str(reply).split())[:160]}")
    fill(120, 80, 30)                       # a DIFFERENT box in the new document
    docs.execute("catia_save_document", {})
    second_volume = volume()
    check("B the file really was rebuilt to another box",
          None not in (first_volume, second_volume)
          and abs(first_volume - 120000.0) < 1.0
          and abs(second_volume - 288000.0) < 1.0,
          f"{first_volume} -> {second_volume}")
    try_close(part)
    docs.execute("catia_open_document", {"file_path": asm})
    listing = components()
    say(f"  reopened: broken={listing.get('broken')} "
        f"by_part={listing.get('broken_by_part')}")
    check("B all four instances are broken", listing.get("broken") == 4,
          str(listing.get("broken")))
    check("B they are grouped under one part",
          list((listing.get("broken_by_part") or {}).values()) == [names],
          str(listing.get("broken_by_part")))
    check("B the note names the one-call repair",
          "catia_replace_component" in str(listing.get("note")), "")
    broken_names = [c["name"] for c in listing["components"] if c.get("broken")]
    poses_now = {c["name"]: c.get("position") for c in listing["components"]}
    check("B their POSES still read (the report's observation)",
          all(poses_now.get(name) for name in names), "")

    docs.execute("catia_close_document", {"document": asm, "save": False})
    docs.execute("catia_open_document", {"file_path": asm})
    again = components()
    say(f"  second close+reopen: broken={again.get('broken')}")
    check("B closing and reopening AGAIN does not mend them",
          again.get("broken") == 4, str(again.get("broken")))

    # ══ D (before the repair): rebuilding again warns on the success path ══
    say("\nD: rebuild the same file once more - the file is not loaded now")
    for _ in range(8):
        try:
            docs.execute("catia_close_document",
                         {"document": part, "save": False})
        except Exception:  # noqa: BLE001 - already gone
            pass
        if os.path.normcase(os.path.abspath(part)) not in open_paths():
            break
        time.sleep(0.4)
    say(f"  part still open: "
        f"{os.path.normcase(os.path.abspath(part)) in open_paths()}")
    say(f"  open documents of this run: "
        f"{sorted(p for p in open_paths() if 'acc0494' in p)}")
    say(f"  scan before the rebuild: "
        f"{docs._referenced_assemblies(part, docs.conn.documents)}")
    reply = docs.execute("catia_new_part",
                         {"name": "A1_Piston_v3", "file_path": part,
                          "overwrite": True})
    fill(140, 80, 30)
    docs.execute("catia_save_document", {})
    text = " ".join(str(reply).split())
    say(f"  rebuild reply: {text[:400]}")
    check("D the rebuild goes through once the file is not loaded",
          "Created new Part document" in text, text[:60])
    check("D and its reply warns about the 4 instances it cannot read",
          "is also pointed at by 4 instance(s)" in text
          and "already broken" in text, text[len(text) // 2:][:0])
    say(f"  warning tail: {text[-520:]}")
    docs.execute("catia_activate_document", {"name": asm_name})

    # ══ C: one call, four instances, names taken back ═════════════════════
    say("\nC: one replace_component call for all four, keep_name=true")
    repaired = payload(assembly.execute(
        "catia_replace_component",
        {"component_name": broken_names, "file_path": part, "keep_name": True}))
    say(f"  reply: {json.dumps(repaired, ensure_ascii=False)[:700]}")
    check("C the batch reports 4 requested and 4 replaced",
          repaired.get("count") == 4 and repaired.get("replaced") == 4,
          f"{repaired.get('count')}/{repaired.get('replaced')}")
    check("C no entry failed", repaired.get("failed") == 0, "")
    check("C every name was restored",
          all(c.get("name_kept") for c in repaired.get("components", [])),
          str([c.get("name_kept") for c in repaired.get("components", [])]))
    check("C so replaced_by equals the old name",
          [c["replaced_by"] for c in repaired["components"]] == broken_names,
          str([c["replaced_by"] for c in repaired["components"]]))
    check("C the insert had used another suffix (the defect being fixed)",
          any(c["inserted_as"] != c["component"]
              for c in repaired["components"]),
          str([c["inserted_as"] for c in repaired["components"]]))
    check("C poses copied within 1e-6 mm",
          repaired.get("max_pose_difference_mm") is not None
          and repaired["max_pose_difference_mm"] <= 1e-6,
          str(repaired.get("max_pose_difference_mm")))

    final = components()
    say(f"  after repair: broken={final.get('broken')} count={final.get('count')}")
    check("C nothing is broken any more", not final.get("broken"),
          str(final.get("broken")))
    check("C the assembly still holds exactly four", final.get("count") == 4,
          str(final.get("count")))
    final_poses = {c["name"]: c["position"] for c in final["components"]}
    check("C every instance is back where it was",
          all(name in final_poses
              and all(abs(final_poses[name][key] - before[name][axis]) < 1e-3
                      for axis, key in zip(range(3), "xyz"))
              for name in names),
          str(final_poses))
    check("C the rebuilt part's part number is readable again",
          all(c.get("part_number") == "A1_Piston_v3"
              for c in final["components"]),
          str([c.get("part_number") for c in final["components"]]))
    try_close(asm, part)

    # ══ E: control - the same repair without keep_name ════════════════════
    say("\nE: control - the repair WITHOUT keep_name")
    part, asm, names, _ = scene("E1", POSES[:2])
    asm_name = os.path.basename(asm)
    release(part, asm)
    docs.execute("catia_new_part", {"name": "E1_Piston_v2", "file_path": part,
                                    "overwrite": True})
    try_close(part)
    docs.execute("catia_open_document", {"file_path": asm})
    listed = components()
    broken_names = [c["name"] for c in listed["components"] if c.get("broken")]
    plain = payload(assembly.execute("catia_replace_component",
                                     {"component_name": broken_names,
                                      "file_path": part}))
    say(f"  reply: {json.dumps(plain, ensure_ascii=False)[:400]}")
    check("E without keep_name the name is NOT restored",
          all(not c.get("name_kept") for c in plain["components"]), "")
    check("E and the new instance carries another suffix",
          all(c["replaced_by"] != c["component"] for c in plain["components"]),
          str([(c["component"], c["replaced_by"]) for c in plain["components"]]))
    try_close(asm, part)

    # ══ F: a bad name in the list changes nothing ═════════════════════════
    say("\nF: a list whose second name does not exist")
    part, asm, names, _ = scene("F1", POSES[:2])
    asm_name = os.path.basename(asm)
    before_count = components()["count"]
    try:
        assembly.execute("catia_replace_component",
                         {"component_name": [names[0], "NoSuchInstance.9"],
                          "file_path": part, "keep_name": True})
        check("F the bad name is refused", False, "it was accepted")
    except Exception as error:  # noqa: BLE001
        message = " ".join(str(error).split())
        check("F the bad name is refused", "NoSuchInstance" in message,
              message[:100])
    after_count = components()["count"]
    check("F and the assembly is untouched", before_count == after_count,
          f"{before_count} -> {after_count}")
    try_close(asm, part)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    say(f"\n{passed}/{len(CHECKS)} checks passed")
    say(f"workspace: {ROOT}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
