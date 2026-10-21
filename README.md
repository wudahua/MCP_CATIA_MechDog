# CATIA V5 MCP Server — lite fork

A trimmed, verification-first fork of
[**daiemon12/catia-v5-mcp-server**](https://github.com/daiemon12/catia-v5-mcp-server)
(MIT, by Adam M'HAMDI). Upstream keeps the whole CATIA surface; this fork keeps
only what builds and checks a **solid from a written spec**, and fixes four
defects that make that loop unreliable.

**87 tools** (upstream: 98). Nothing in the COM layer was rewritten — the value
of upstream is the field-verified knowledge in its comments, so this fork is
strictly add / subtract / patch.

---

## What this fork changes

### Dropped (16 tools, 1,274 lines)

| Module | Tools | Why |
|---|---|---|
| `tools/drawing.py` | 11 | Needs a Drafting licence and produces 2D views, unrelated to solids |
| `tools/knowledge.py` | 5 | Parameters / formulas / design tables |

`export` (STEP/STL hand-off) and `http_transport` (remote agent) are kept: they
are domain-neutral and cheap. See `FORK.md` §8 if you want to cut further.

### Added (5 tools)

| Tool | What it gives you |
|---|---|
| `catia_run_plan` | Runs an ordered `{tool, args}` recipe in **one** call, then asserts volume / area / face / edge / vertex counts / centre of gravity |
| `catia_measure_topology` | Face, edge and vertex counts — the cheapest guard against a part with the right volume and the wrong shape |
| `catia_set_component_transform` | Absolute placement from a **row-major 3×3** (transposed server-side) or CATIA's native 12 numbers; reads back and reports deviation, det, orthogonality |
| `catia_get_component_transform` | The same placement in both conventions, plus where local X/Y/Z point in the assembly |
| `catia_rename_component` | A readable assembly tree. `Part.Name` is read-only on V5-6R2020, so every part arrives as `Part1.1 / Part2.1 / …`; the instance name is writable |

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

---

## Requirements

* Windows (COM automation; `pywin32` is a hard dependency)
* CATIA V5 installed and licensed — verified on **V5-6R2020 (B30)**, Simplified
  Chinese build
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

## Connect your MCP client

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

Placement from a spec that quotes `P_asm = R · P_local + T` — pass the matrix the
way the spec writes it, row by row:

```jsonc
{"tool": "catia_set_component_transform",
 "args": {"component_name": "P05_CylinderCap",
          "rotation_rows": [[0, -0.544639, -0.838671],
                            [0, -0.838671,  0.544639],
                            [-1, 0, 0]],
          "translation": [0, 0, 402.5]}}
```

The reply includes `max_deviation`, `det` and orthogonality, so a transposed
matrix cannot pass unnoticed — note that a transpose keeps `det = +1`, which is
why checking handedness alone never catches it.

## Tool reference

| Group | Tools | Notes |
|---|---|---|
| Document | 9 | new/open/save/close/list; `catia_save_document` retries around dialogs |
| Sketcher | 11 | lines, rectangles, circles, constraints |
| Part Design | 18 | pad, pocket, groove, fillet, chamfer, hole, patterns, mirror, shell… |
| Generative Shape Design | 24 | wireframe and surfaces, revolve + close surface |
| Assembly | **11** | upstream 9 + get/set component transform |
| Measurement | 6 | inertia, distance, bounding box\*, parameters |
| Export | 4 | STEP / STL / IGES hand-off |
| Planner | **2** | `run_plan`, `measure_topology` |
| Diagnostics | 1 | `catia_diagnose` |

\* `catia_get_bounding_box` is a vertex sweep inherited from upstream, and CATIA
V5 automation exposes no bounding-box method. It **under-reports curved solids**
and fails outright on a fully curved one. Use `catia_measure_topology` instead.

## Known limits

* No lump-count check (upstream has none either) — the only reliable detector for
  an isolated floating boss.
* No assembly-level centre of gravity: it needs each component's part measured.
* The watchdog answers `#32770` message boxes only; CATIA's own `CATDlgDialog`
  panels are left alone so a human working alongside the agent is not interrupted.

## License and attribution

MIT. Upstream copyright is retained in [`LICENSE`](LICENSE)
(Adam M'HAMDI, Ondřej Schiller, Chong Zhu). This fork's changes are additions to
and modifications of that work; `README.md` and `FORK.md` state what they are.

If you send improvements back, upstream is the right home for anything that is
not specific to this fork — it accepts contributions.
