# CATIA V5 MCP Server — lite fork

**0.3.11** · [what changed](CHANGELOG.md) ·
[releases](https://github.com/wudahua/MCP_CATIA_MechDog/releases) ·
[known limits](#known-limits)

A trimmed, verification-first fork of
[**daiemon12/catia-v5-mcp-server**](https://github.com/daiemon12/catia-v5-mcp-server)
(MIT, by Adam M'HAMDI). Upstream keeps the whole CATIA surface; this fork keeps
only what builds and checks a **solid from a written spec**, and fixes the
defects that make that loop lie to you. Most of the work since 0.3.0 came from
people rebuilding real assemblies with it: the changelog is a list of things that
produced a plausible number, a wrong number, or no explanation at all.

**98 tools** (upstream: 98 — 16 dropped, 16 added). Nothing in the COM layer was
rewritten — the value of upstream is the field-verified knowledge in its
comments, so this fork is strictly add / subtract / patch. The count is asserted
by `tests/test_readme_tool_counts.py`, so it cannot drift from the code again.

### What it can verify, not just build

* **Every cut is measured.** `pad` / `pocket` / `hole` / `shaft` / `groove` /
  `circ_pattern` report the volume they moved before and after, so a feature that
  silently did nothing is visible on the spot.
* **Edges have fingerprints, not indices.** `catia_list_edges` returns
  `L17.6112|Pad.6;2|Sketch.7;2`; `fillet` / `chamfer` resolve it at call time, so
  a multi-fillet script survives its own edits.
* **Sketches are editable after the fact.** `catia_sketch_edit` reopens a closed
  sketch, `catia_sketch_delete_element` removes one wrong element, and the part
  rebuilds. A wrong profile is no longer a full rebuild.
* **Assemblies are measurable.** `catia_get_inertia` on a Product aggregates the
  components' volumes and world centroids (verified to 0.009 mm on a 9-part
  assembly); `catia_get_bounding_box` reports per-component world boxes.
* **Out of range is `UNVERIFIED:`, not a success.** When a measurement cannot be
  made the reply says which one failed and why.

---

## What this fork changes

### Dropped (16 tools, 1,274 lines)

| Module | Tools | Why |
|---|---|---|
| `tools/drawing.py` | 11 | Needs a Drafting licence and produces 2D views, unrelated to solids |
| `tools/knowledge.py` | 5 | Parameters / formulas / design tables |

`export` (STEP/STL hand-off) and `http_transport` (remote agent) are kept: they
are domain-neutral and cheap. See `FORK.md` §8 if you want to cut further.

### Added (16 tools over upstream)

Five of them came with the fork, eleven were added while building parts from a
spec (0.3.1: three, 0.3.2: four, 0.3.4: one, 0.3.5: one, 0.3.8: two).
Upstream 98 − 16 dropped + 16 added = 98.

| Tool | What it gives you |
|---|---|
| `catia_run_plan` | Runs an ordered `{tool, args}` recipe in **one** call, then asserts volume / area / face / edge / vertex counts / centre of gravity |
| `catia_measure_topology` | Face, edge and vertex counts — the cheapest guard against a part with the right volume and the wrong shape |
| `catia_set_component_transform` | Absolute placement from a 3×3 in either convention (`matrix` = `P = A·p + T`, `basis` = row i is where local axis i points) or CATIA's native 12 numbers; reads back and reports deviation, det, orthogonality **and where each local axis ended up** |
| `catia_get_component_transform` | The same placement in both conventions, plus where local X/Y/Z point in the assembly |
| `catia_rename_component` | A readable assembly tree. `Part.Name` is read-only on V5-6R2020, so every part arrives as `Part1.1 / Part2.1 / …`; the instance name is writable |

#### Added while building parts from a spec (11 tools)

| Tool | Version | Why it exists |
|---|---|---|
| `catia_new_body` | 0.3.1 | A spec's part is often several solids. `catia_pad` used to always land in the body that was already in work, and there was no way back once work had moved past the second body |
| `catia_list_bodies` | 0.3.1 | Per-body volume and face count — the only way to check a nine-solid part body by body, since `catia_get_inertia` reports just the one in work |
| `catia_boolean` | 0.3.1 | Join / cut / intersect two bodies, for the cases where the spec merges what the source kept separate |
| `catia_set_active_body` | 0.3.2 | `catia_new_body` only moves forward; this is the way back to the main body |
| `catia_set_visibility` | 0.3.2 | A GSD surface stays visible after `close_surface` and overlays the solid, so screenshots and the STL envelope describe a part that does not exist. Measured enum: `SetShow(0)` shows, `(1)` hides, `(2)` is accepted and does nothing |
| `catia_save_as_copy` | 0.3.2 | `SaveAs` is refused once a model has geometry; this saves and then copies, which is the only way to snapshot such a model |
| `catia_activate_document` | 0.3.5 | With a Part and a Product open there was no way back to the Part except closing the Product; this activates by name |
| `catia_sketch_edit` | 0.3.8 | A closed sketch was unreachable: `OpenEdition` on it works, so a wrong profile can be repaired instead of deleting the sketch and rebuilding the part |
| `catia_sketch_delete_element` | 0.3.8 | Removes one element (the wrong third line) and keeps the rest of the profile and the features built on it |
| `catia_set_part_number` | 0.3.4 | Every part cloned from a template carries `Part1`, so a 24-instance assembly of 24 distinct parts reports 24 identical part numbers and CATIA fills the screen with “零件编号 Part1 已存在”. The number, not the instance name, is what the BOM reads |
| `catia_gsd_extract` | 0.3.2 | `catia_gsd_fill` needs a boundary *curve*, and a sketch reference is rejected — without an extract there was no way to cap a lofted surface into a solid |

### Fixed (latent upstream bugs, reproduced on a live V5-6R2020 B30 session)

| Defect | Effect upstream |
|---|---|
| `Position.GetComponents` never fills a Python list (SAFEARRAY out-param does not marshal through late-bound IDispatch) | `catia_move_component` wrote twelve zeros back and **erased the component's rotation**; `catia_list_components` reported every component at `(0, 0, 0)` |
| `doc.Part` / `doc.Product` raise `AttributeError: <unknown>.Part` on a gen_py-typed document | `catia_new_part` and every measurement failed depending on cache state |
| `SaveAs` leaves a modal dialog open, freezing the serial STA worker | Documented upstream as a runtime *limit* ("close the dialog yourself") — but the worker thread that would close it is the blocked one |
| `Topology.Edge` on a body also returns the consumed sketch's lines | A 40×20×10 box counted **16 edges / 12 vertices** instead of 12 / 8 |
| `catia_shaft` / `catia_groove` called `AddNewShaft(sketch)` and assigned `FirstAngle = angle` | The feature could never be updated (E_FAIL), so **no turned part could be built at all** — piston, cylinder-head cover and front cover had no other route |
| `catia_hole` with a finite depth reported success while cutting nothing | The depth is stored on `BottomLimit` but not applied on B30; the tree carried a dead feature that poisoned every later measurement |

The fixes: reads go through `SystemService.Evaluate` (the same route that fixed
`GetCOG` upstream in 0.2.2); `connection.part_of()` retries via
`dynamic.Dispatch`; `dialogs.py` removes a pre-existing target, retries, and runs
a watchdog thread that answers `#32770` message boxes **while the COM call is in
flight**; topology counts filter `Selection_Wire*` elements. Revolution now goes
through `AddNewShaftFromRef` + `SetProfileElement` + `RevoluteAxis` with
`FirstAngle.Value`, and every cut is measured before it is reported.

### Modelling a written spec: the four arguments that were missing

| Argument | Why a spec needs it |
|---|---|
| `catia_create_sketch` → `offset`, or `{"type": "offset", "base", "distance"}` | A spec says "on the z=530 face". Only three datum planes existed, so most features of a real part could not be placed. The offset plane is inserted **into the body** — `AddNewPad` rejects a sketch whose support lives in a geometrical set |
| `catia_create_sketch` → `{"type": "face", "index"}` | For a face that is not parallel to a datum plane. Sketches on a face use **that face's own coordinate frame**, which cannot be read back through automation — prefer the offset form whenever the face is axis-aligned |
| `catia_sketch_line` → `construction` | A revolution axis or reference geometry that must not join the profile |
| `catia_pocket` / `catia_hole` → `through_all` | CATIA's "up to last": the cut finds its own extent instead of you guessing a depth taller than the part |
| `catia_shaft` / `catia_groove` → `axis` (`horizontal`/`vertical`) | Which of the sketch's two absolute axes to revolve about. A construction line is accepted by the property setter and then rejected at update time, so it is not offered |

Details, measurements and caveats: **[`FORK.md`](FORK.md)** (Chinese).

### Fillets on a real part: the edges move

`Edge.N` is an enumeration position, not an identity — every feature that
changes the topology renumbers it. A part with 17 fillets therefore used to need
"fillet one, re-list all 67 edges, fillet the next": 7 listings of ~50 000
characters and 34 calls.

`catia_list_edges` now hands out a **fingerprint** instead, built from the faces
that bound the edge and its length, and `catia_fillet` / `catia_chamfer` resolve
it when the call runs:

```jsonc
{"tool": "catia_fillet", "args": {"radius": 2.54, "edge_names": [
  "L39.8982|Pad.6;1|Pad.6;0",        // paste back what the listing printed
  {"length_mm": 17.6112, "faces": ["Pad.6;2", "Sketch.7;2"]}
]}}
```

Faces decide the match and the length only breaks ties, because a neighbouring
fillet legitimately shortens an edge (measured: 17.6112 → 9.8429 mm while the
bounding faces stayed the same). The listing also filters (`min_length`,
`max_length`, `faces`) and pages (`offset`, `limit`), and `include_nls: false`
keeps the reply small.

Two related defaults: `propagation` defaults to `none`, so one picked edge is one
fillet instead of its whole tangent chain, and a batch whose rebuild fails is
retried one edge at a time rather than lost.

---

## Requirements

* Windows (COM automation; `pywin32` is a hard dependency)
* CATIA V5 installed and licensed — verified on **V5-6R2020 (B30)**, Simplified
  Chinese build. One known build difference: `catia_gsd_multi_section_surface`
  fails on **V5-6R2022 (B32)** for every configuration (reported 2026-10-23),
  while the rest of GSD — including `catia_gsd_blend` on the same curves — works.
  Everything else in the tool list is B30/B32-agnostic as far as is known.
* Python 3.10+

## Install

```bash
git clone https://github.com/wudahua/MCP_CATIA_MechDog.git
cd MCP_CATIA_MechDog
python -m venv .venv
.venv/Scripts/pip install -e .

# offline test suite, no CATIA needed
.venv/Scripts/python -m pytest tests -q
```

> **Do not install this into the same virtualenv as upstream.** Both provide the
> `catia_mcp` import package, so one silently shadows the other.

### Updating an existing checkout

`git pull` is the safe route. If you update from a downloaded archive instead,
**do not copy it over the old directory with `cp -r src/* dst/`** — a shell glob
(and Windows Explorer) skips every dot-prefixed entry, and four of them carry
things nothing warns you about:

| Path | What its loss costs |
|---|---|
| `.gitattributes` | `setup.sh` is delivered CRLF and dies on line 1 |
| `.github/workflows/ci.yml` | the only CI, i.e. the tool-count consistency tests |
| `.claude/skills/**` | the maintainer / production-operator skill packs |
| `.gitignore` | build output starts showing up as untracked |

Two ways to stay safe:

```bash
# 1. the packaged release (dot-files included, sha256 next to it)
python scripts/package_release.py          # -> dist/MCP_CATIA_MechDog-<ver>.zip[.sha256]
# 2. or copy correctly, from inside the extracted folder
cp -r src/. dst/                           # the "/." is what keeps dot-files
```

`tests/test_release_archive.py` fails if the packaging script ever stops
including them.

Neither route touches a file that a running server holds — which the launcher
`.exe` cannot say for itself: if `pip install -e .` dies on `Device or resource
busy` / `safe-delete`, the client has the old `catia-mcp.exe` open. Close it
first, or switch the client to the `python -m catia_mcp` form below.

## Connect your MCP client

`python -m catia_mcp` is the recommended entry point: it runs the package and
never touches a launcher `.exe`:

```json
{
  "mcpServers": {
    "MCP_CATIA_MechDog": {
      "command": "C:\\path\\to\\MCP_CATIA_MechDog\\.venv\\Scripts\\python.exe",
      "args": ["-m", "catia_mcp"]
    }
  }
}
```

The installed `catia-mcp.exe` is equivalent and works too, with one caveat: on
Windows a running executable is locked, so **upgrading the package while a client
holds the server open fails** — `pip` deletes the old launcher before writing the
new one, and the delete is refused (`safe-delete` / `trash-failed` /
`Device or resource busy`), which aborts the whole transaction and can leave a
`~cp_catia_mechdog-*.dist-info` husk behind. Reported 2026-10-23. Either point
the client at `python -m catia_mcp` (the `.py` files are read into memory at
import, so replacing them under a live process is fine) or close the client
before upgrading:

```json
{
  "mcpServers": {
    "MCP_CATIA_MechDog": {
      "command": "C:\\path\\to\\MCP_CATIA_MechDog\\.venv\\Scripts\\catia-mcp.exe"
    }
  }
}
```

`catia-mcp --http` serves Streamable HTTP at `/mcp` for a remote agent; see
[`docs/remote-access.md`](docs/remote-access.md).

## Usage: verify while you build

A plan is data, not conversation. This one builds a 40×20×10 block and proves it:

```jsonc
{
  "steps": [
    {"tool": "catia_new_part",         "args": {}},
    {"tool": "catia_create_sketch",    "args": {"plane": "xy"}},
    {"tool": "catia_sketch_rectangle", "args": {"x1": -20, "y1": -10, "x2": 20, "y2": 10}},
    {"tool": "catia_close_sketch",     "args": {}},
    {"tool": "catia_pad",              "args": {"height": 10, "direction": "normal"}}
  ],
  "expect": {
    "volume_mm3": 8000, "area_mm2": 2800,
    "faces": 6, "edges": 12, "vertices": 8,
    "cog": [0, 0, 5], "cog_tolerance_mm": 0.01
  }
}
```

```
ok: True | code: PLAN_OK | message: 5 steps executed, 6 assertions passed
```

Counts are compared **exactly**; volume and area get a percentage tolerance.
The asymmetry is deliberate: a volume-only judge passes a part that grew an
isolated boss, cut a pocket the wrong way, or drilled a hole into air — one
field case missed a wrong part by −0.24 % while the face count was 30 instead of 26.

Placement from a spec that quotes `P_asm = A · P_local + T` — pass A row by row,
with the default `rotation_convention`:

```jsonc
{"tool": "catia_set_component_transform",
 "args": {"component_name": "P05_CylinderCap",
          "rotation_rows": [[0, -0.544639, -0.838671],
                            [0, -0.838671,  0.544639],
                            [-1, 0, 0]],
          "translation": [0, 0, 402.5]}}
```

Specs that instead say *"R's three rows are the direction cosines of local X/Y/Z
in global"* — the form CATIA's own position dialog shows — are `P_asm = Rᵀ·p + T`,
so say so and copy the table verbatim:

```jsonc
{"tool": "catia_set_component_transform",
 "args": {"component_name": "drone stand I015",
          "rotation_rows": [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
          "rotation_convention": "basis",
          "translation": [46.551, 388.598, 543.367]}}
```

The reply includes `max_deviation`, `det`, orthogonality **and `basis_rows`** — the
last one is the only field that can catch a wrong convention: the two spellings
are transposes of each other, both keep `det = +1` and both are orthonormal, so a
transposed placement passes every other check (`max_deviation` only proves CATIA
stored what you sent). One field case: a quadcopter's landing stands came out
lying flat and its motors tilted 60° while every self-check reported success.

## Tool reference

| Group | Tools | Notes |
|---|---|---|
| Document | 12 | new/open/save/close/list + `catia_save_as_copy`; `catia_save_document` retries around dialogs |
| Sketcher | 13 | lines, rectangles, circles, constraints; reopen a closed sketch and delete one element |
| Part Design | 23 | pad, pocket, groove, fillet, chamfer, hole, patterns, mirror, shell, bodies… |
| Generative Shape Design | 25 | wireframe and surfaces, extract, revolve + close surface |
| Assembly | **12** | upstream 9 + get/set component transform, rename, list constraints |
| Measurement | 6 | inertia, distance, bounding box\*, parameters |
| Export | 4 | STEP / STL / IGES hand-off |
| Planner | **2** | `run_plan`, `measure_topology` |
| Diagnostics | 1 | `catia_diagnose` |

\* `catia_get_bounding_box` measures two ways, and `auto` takes the STL mesh:
exact on planar-faced solids, a **lower bound ~0.5-0.7 % small** on curved ones.
`method="vertices"` is the cheap sweep, exact only when every face is planar (a
revolved face has no vertex at its extremum, which is how an `auto` box once came
out 37.5 % small). On an assembly the per-component world boxes are vertex-based,
so any part whose extremum lies on a curved face is under-reported there;
`document="<part>.CATPart", method="mesh"` gives a true per-part box - the part is
opened by path if it exists only as a component, and exported from a temporary
copy when CATIA refuses to export it while it is edited in context (that refusal
is a modal dialog, and reads as a bare `[WinError 2]`). Place the result with
`catia_get_component_transform`.

## Known limits

* No lump-count check (upstream has none either) — the only reliable detector for
  an isolated floating boss.
* Assembly **inertia tensors** are not aggregated (volumes and centroids are); a
  tensor would have to be rotated into world space per component. The tensor is
  also only computed for the ACTIVE document, and with the part's material density
  - `get_inertia(density=...)` does not change it, which is why the reply reports
  both masses.
* A part that CATIA is editing **in context** (loaded inside an assembly, no window
  of its own) cannot be exported to STL. The mesh box falls back to a copy of the
  saved file, so it measures the file, not unsaved edits; the reply says when that
  happened.
* `catia_add_component` on a B32 build rejects forward-slash paths and can then
  put up a modal dialog, which blocks the COM worker. The path is normalised and
  a missing component is reported instead of claimed, but a dialog that is already
  open still needs a human - no in-process timeout can interrupt a blocked COM
  call.
* The watchdog answers `#32770` message boxes only; CATIA's own `CATDlgDialog`
  panels are left alone so a human working alongside the agent is not interrupted.

## License and attribution

MIT. Upstream copyright is retained in [`LICENSE`](LICENSE)
(Adam M'HAMDI, Ondřej Schiller, Chong Zhu). This fork's changes are additions to
and modifications of that work; `README.md` and `FORK.md` state what they are.

If you send improvements back, upstream is the right home for anything that is
not specific to this fork — it accepts contributions.
