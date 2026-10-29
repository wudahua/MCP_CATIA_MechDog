# CATIA V5 MCP Server — lite fork

**0.3.38** · [what changed](CHANGELOG.md) ·
[releases](https://github.com/wudahua/MCP_CATIA_MechDog/releases) ·
[known limits](#known-limits)

A trimmed, verification-first fork of
[**daiemon12/catia-v5-mcp-server**](https://github.com/daiemon12/catia-v5-mcp-server)
(MIT, by Adam M'HAMDI). Upstream keeps the whole CATIA surface; this fork keeps
only what builds and checks a **solid from a written spec**, and fixes the
defects that make that loop lie to you. Most of the work since 0.3.0 came from
people rebuilding real assemblies with it: the changelog is a list of things that
produced a plausible number, a wrong number, or no explanation at all.

**101 tools** (upstream: 98 — 16 dropped, 19 added). Nothing in the COM layer was
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
  components' volumes, areas and world centroids (verified to 0.009 mm on a
  9-part assembly) and **expands sub-assemblies** — their leaves are reported with
  a path (`ASM/PART.1`) and their placement composed, so `components` adds up to
  the totals. The totals carry `complete`, `instances` and, when anything was left
  out, `incomplete_because`: a partial sum says so instead of wearing the
  assembly's name. `catia_get_bounding_box` reports per-component world boxes and
  expands sub-assemblies the same way, so a nested assembly is bounded rather
  than listed as skipped.
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

### Added (19 tools over upstream)

Five of them came with the fork, fourteen were added while building parts from
a spec (0.3.1: three, 0.3.2: four, 0.3.4: one, 0.3.5: one, 0.3.8: two, 0.3.17:
two - `catia_delete_body`, `catia_solid_loft`; 0.3.19: one - `catia_copy_component`).
Upstream 98 − 16 dropped + 19 added = 101. 0.3.20 added arguments instead of
tools: a sketch support given as `origin`/`u`/`v`, edges given as coordinates,
arcs given as their two endpoints, and one plan per several documents.

| Tool | What it gives you |
|---|---|
| `catia_run_plan` | Runs an ordered `{tool, args}` recipe in **one** call, then asserts volume / area / face / edge / vertex counts / centre of gravity |
| `catia_measure_topology` | Face, edge and vertex counts — the cheapest guard against a part with the right volume and the wrong shape |
| `catia_set_component_transform` | Absolute placement from a 3×3 in either convention (`matrix` = `P = A·p + T`, `basis` = row i is where local axis i points) or CATIA's native 12 numbers; reads back and reports deviation, det, orthogonality **and where each local axis ended up** |
| `catia_get_component_transform` | The same placement in both conventions, plus where local X/Y/Z point in the assembly |
| `catia_rename_component` | A readable assembly tree. `Part.Name` is read-only on V5-6R2020, so every part arrives as `Part1.1 / Part2.1 / …`; the instance name is writable |

#### Added while building parts from a spec (14 tools)

| Tool | Version | Why it exists |
|---|---|---|
| `catia_new_body` | 0.3.1 | A spec's part is often several solids. `catia_pad` used to always land in the body that was already in work, and there was no way back once work had moved past the second body |
| `catia_copy_component` | 0.3.19 | A 24-instance assembly had to be built by calling `add_component` once per instance - N isolated processes, and N peer components instead of N instances of one reference. `Products.AddComponent(ref)` does it in one call (measured: the new instance's ReferenceProduct is the same product) |
| `catia_solid_loft` | 0.3.17 | The surface route cannot make a thin solid - offsetting a 2.30 mm section by 1.27+1.27 self-intersects - and this server had claimed CATIA had no solid loft at all. `ShapeFactory.AddNewLoft` does exist: two circles gave a 18 765.8 mm³ solid in one update |
| `catia_delete_body` | 0.3.17 | `catia_delete_feature` looks inside bodies and never sees one, so an experimental body could only be emptied, never removed — a part ended up carrying nine empty bodies |
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
| `catia_hole` ignored where the profile was drawn | `AddNewHoleFromSketch` takes the bore's axis from a **point** in the sketch, so a sketch holding one circle was drilled at the sketch origin: circles at (0, 0) and (10, 0) produced bit-identical parts, and a circle outside the material drilled through its middle. The removed volume stayed exactly `pi*r^2*h`, so nothing could see it |

The fixes: reads go through `SystemService.Evaluate` (the same route that fixed
`GetCOG` upstream in 0.2.2); `connection.part_of()` retries via
`dynamic.Dispatch`; `dialogs.py` removes a pre-existing target, retries, and runs
a watchdog thread that answers `#32770` message boxes **while the COM call is in
flight**; topology counts filter `Selection_Wire*` elements. Revolution now goes
through `AddNewShaftFromRef` + `SetProfileElement` + `RevoluteAxis` with
`FirstAngle.Value`, and every cut is measured before it is reported.

### Modelling a written spec: the arguments that were missing

| Argument | Why a spec needs it |
|---|---|
| `catia_create_sketch` → `offset`, or `{"type": "offset", "base", "distance"}` | A spec says "on the z=530 face". Only three datum planes existed, so most features of a real part could not be placed. The offset plane is inserted **into the body** — `AddNewPad` rejects a sketch whose support lives in a geometrical set |
| `catia_create_sketch` → `{"type": "face", "index"}` | For a face that is not parallel to a datum plane. Sketches on a face use **that face's own coordinate frame**, which cannot be read back through automation — prefer the offset form whenever the face is axis-aligned |
| `catia_create_sketch` → `{"type": "frame", "origin", "u", "v"}` | The way specs actually describe an inclined sketch: "O=(17.525, 185.862, 0), u=(0.995584, -0.093875, 0), v=-Z". The datum plane is built through the origin with H = `u` and V = `v`, so the numbers can be typed in unchanged. Without it, ten steps of a 2026-10-25 report could not be modelled at all (8 grooves at 5.39°, 2 faces at 14.04°) |
| `catia_sketch_arc` → `p1`, `p2`, `direction` | Reverse-engineered data gives "centre, R, (x1,y1) -> (x2,y2)" and no angles. Converting by hand is what turns the one 240° major arc of 236 into a 120° minor one. Every arc reply now also states its swept angle |
| `catia_sketch_line` → `construction` | A revolution axis or reference geometry that must not join the profile |
| `catia_pocket` / `catia_hole` → `through_all` | CATIA's "up to last": the cut finds its own extent instead of you guessing a depth taller than the part |
| `catia_shaft` / `catia_groove` → `axis` (`horizontal`/`vertical`) | Which of the sketch's two absolute axes to revolve about. A construction line is accepted by the property setter and then rejected at update time, so it is not offered |
| `catia_fillet` / `catia_chamfer` → `edge_anchors` (+ `anchor_tol`) | The other half of a spec: "fillet R2.5 on the edge at (0, 65, -20)". Each anchor is matched to the edge whose minimum distance to it is smallest, and the reply states the edge name and the distance — an anchor that matches nothing, or two edges equally well, is refused instead of filleting the wrong one |
| `catia_list_edges` → `coords: true` | Adds `point_on_edge` — a coordinate that lies ON the edge (the midpoint for a straight edge, measured) — which is both the value to feed back as an anchor and the thing to compare with a spec sheet |

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

When the source gives coordinates instead of edge names, skip the listing
entirely:

```jsonc
{"tool": "catia_fillet", "args": {"radius": 2.5,
  "edge_anchors": [[0, 65, -20], [50, 65, -20]], "anchor_tol": 0.5}}
// -> Fillet R2.5: 2/2 edge(s) done [applied Edge.6@R2.5, Edge.9@R2.5]
//    Edge anchors: Anchor [0.0, 65.0, -20.0] -> Edge.6 @ 0.0000 mm; ...
```

`Measurable.GetMinimumDistance` takes a **point reference** — it rejects a
Measurable with "type mismatch" — so the anchors are turned into scratch points
in a temporary geometrical set that is deleted before the reply. An anchor that
lands beyond `anchor_tol`, or exactly where two edges meet, is refused with the
candidates and their distances: a fillet on the wrong edge is a perfectly legal
feature nobody asked for.

Every bad anchor is listed at once, and `on_anchor_miss: "skip"` fillets the ones
that do match while reporting the rest — a 20-row coordinate table should not
cost 20 round trips because one row is wrong:

```jsonc
{"tool": "catia_fillet", "args": {"radius": 0.5, "on_anchor_miss": "skip",
  "edge_anchors": [[0, 5, 4], [999, 999, 999]]}}
// -> Fillet R0.5: 1/1 edge(s) done [applied Edge.1@R0.5]
//    Edge anchors: Anchor [0, 5, 4] -> Edge.1 @ 0.0000 mm;
//    SKIPPED (on_anchor_miss='skip'): 1 anchor(s) beyond anchor_tol=0.5:
//    [999, 999, 999] is 1710.727 mm from the nearest edge (Edge.3, L20.0).
```

### A hole goes where the profile is

CATIA's `Hole` takes the bore's axis from a **point** in the sketch, not from the
circle's centre, so `catia_hole` on a sketch holding one circle drilled at the
sketch origin — six positions, one bore, and the volume matched `pi*r^2*h` every
time. Three things changed:

* A construction point is added **at the circle's centre** before the feature is
  built when the sketch was drawn in this session and holds no point, and the reply
  says so. A point that is *away* from the circle is refused before anything is
  created, with the distance: "holds a point at (0, 0) and a circle centred at
  (10, 0), 10.000 mm apart … Nothing was created."
* The bore is **verified after the cut**, not before it: the centroid of the
  removed material is recovered from the volume and centre-of-gravity readings
  (`V0·G0 − V1·G1 = v·g`), compared with the profile in the sketch plane, and a
  bore that missed is deleted and reported — the one failure mode a volume check
  cannot see. Above 500× amplification (a nick in a heavy part) the check declines
  to accuse instead of inventing an error.
* `depth` is measured **from the sketch plane**. With the sketch on an offset
  datum plane, `depth 5` in a 20 mm block drills 15 mm; the reply converts the
  removed volume into the length it drilled and says which datum it used. There is
  no `reverse` argument because `Hole` exposes no direction property
  (`DirectionOrientation` raises on B30) — a bore that removes nothing on the far
  side of the sketch plane is reported as such, with `through_all` or a sketch on
  the face as the way out.

### A rollback that takes the sketch puts it back

A punch consumes its input sketch, and whether CATIA removes it with the failed
node depends on the document's history — so after `NO_MATERIAL` the same call left
the sketch in place once and gone twice, and a retry answered `SKETCH_NOT_FOUND`
(0.3.30 disclosed this; 0.3.31 closes it). The sketch is now re-created from this
session's own drawing record — same plane, same drawing calls — and the old name is
aliased to the new one:

```text
... It has been re-created from its recorded definition as '草图.3' (same plane,
same drawing calls), and '草图.2' now resolves to it, so retrying the same call
works and nothing else in the part changed.
```

`NO_MATERIAL` therefore always means "the sketch is still there". Measured on B30:
after a failed pad, the retry with the **old** name answered `NO_MATERIAL` again;
a second rollback rebuilt 草图.3 as 草图.4. When the record cannot reproduce the
sketch — a reopened file, a support that is not a datum plane, an arc whose start
angle is not in the record — the message says which of those it is instead of
leaving a sketch-shaped hole.

0.3.32 makes the name mean the same thing everywhere. Every rebuild **re-points**
the entries that pointed at the sketch CATIA took, so three rollbacks leave one
alias (`草图.2 → 草图.5`) rather than a chain that dies on the third retry; the
alias is resolved by the read and edit tools too, not only by Part Design; and it
shows in the places a caller looks for "is my sketch still there":

```text
catia_sketch_get_geometry {sketch_name: "草图.2"}
  -> {"sketch": "草图.5", "found": "by alias '草图.5'", "elements": [...]}

catia_list_features        -> sketches: [{"name": "草图.6",
                               "aliases": ["草图.2", "草图.3", "草图.4", "草图.5"]}]
catia_pad {sketch_name: "草图.99"}
  -> No sketch named '草图.99'. Available: 草图.1, 草图.6 (alias: 草图.2, 草图.3,
     草图.4, 草图.5).
```

The rebuild's own reply also repeats what the sketch held (`It held: rectangle
(-10, -5) .. (10, 5) on plane 'xy' at offset 0`), so the profile is visible even to
a caller that never asks again.

Every tool that takes a sketch name resolves it that way — Part Design, the
sketcher's read/edit calls, GSD's `element=` (measured: `catia_gsd_spline
{element: "草图.2"}` answered "No outline recorded" before 0.3.33 and returns the
4 corners as "草图.2 (rebuilt as 草图.3)" after) and a loft section. And the error
messages are checked against the tool list: every `catia_*` name in a message has
to be a tool this server actually serves, because three of them used to send the
caller to `catia_list_sketches`, which does not exist.


### A pad that adds nothing tries the other side

`catia_pad` now behaves the way `catia_pocket` has since 0.3.4: if the requested
direction grows no material, the same feature is flipped and re-measured, and a
flip that works is kept and reported rather than turned into a diagnosis.

```jsonc
{"tool": "catia_pad", "args": {"height": 40}}   // sketch sits on the bottom face
// -> Pad created: 40.000 mm (reverse). Feature: '凸台.2'
//    Volume 96000.0 -> 100000.0 mm3 (delta +4000.0).
//    (direction auto-flipped to reach material: ...)
```

The failure path no longer builds a second pad to answer "is the direction the
problem?" — that probe was built from a sketch the failed pad had already
consumed, so it answered "nothing either way" for a direction that works, and
removing it deleted the caller's sketch. The same node is flipped instead, and
the reply says whether the sketch survived.

---

### Measuring an assembly without owning ActiveDocument

Two clients can drive one CATIA, and `ActiveDocument` is theirs to change between
your calls. Everything that can name its target should:

```jsonc
{"tool": "catia_get_bounding_box",
 "args": {"document": "M113_asm.CATProduct"}}          // include_curved defaults to true
// -> {"document": "M113_asm.CATProduct", "part_boxes": 28,
//     "part_boxes_by_vertices": 26, "part_boxes_by_mesh": 2, "complete": true,
//     "mesh_fallback": ["P09_cam_lens_body"], "mesh_fallback_instances": 6,
//     "bounds": "mixed: 6 instance(s) …",
//     "instances": {"total": 70, "measured": 70, "skipped": 0},
//     "components": [{"component": "P03_prop", "min": {…}, "max": {…},
//                     "dimensions": {…}, "source": "vertices"}, …],
//     "parts": [{"part": "P03_prop", "source": "vertices", "instances": 10,
//                "vertices_used": 64, "cells_scanned": 214,
//                "points_route": "batched", …}, …]}
```

The world box is arithmetic on each component's placement, so it never needed the
assembly to be active. Each PART is bounded once and only the placement repeated —
70 instances of 28 parts is 28 boxes, not 70. The cost counters (`vertices_used`,
`cells_scanned`, `nls_chars`, `points_route`) are per PART and are published once
in `parts`, not on every instance entry; `cells_scanned` counts CATIA topology
cells before construction geometry was filtered, and the per-part time is dominated
by that filter, not by the vertices. `nls_chars` is the total length of the NLS
paths that filter read (one per cell): a **size** indicator for how much
construction geometry a part carries, not a cost predictor — on 26 parts of one
model `nls/cell` varied 1.31× while `ms/cell` varied 5.49× (r = 0.34), and the part
with the longest paths was the fastest. `component='P24.1'` reads one placement,
not seventy.

A part with no vertex at any extremum (a fully curved part) cannot be bounded by a
vertex sweep. It is bounded from its STL mesh instead — `include_curved: false`
turns that off and reports those instances in `skipped` — and its entry says
`source: "mesh_fallback"` with `mesh_vertices`. **Every mesh vertex is transformed
by the instance's placement**, not the local box's two corners: a diagonal rotated
by 34° is a line with almost no extent across it, which made a disc report
±100.29 / ±21.32 where the truth is ±72.5 / ±72.5 (fixed 2026-10-26; the entry now
matches an independent cloud rotation to 0.00003 mm, while the 8-corner box of a
round part is 2–4 mm too big). A mesh box chords curved faces, so `bounds` says
"mixed: N instance(s) are bounded from their mesh" rather than claiming an exact
envelope. Measured on a 24-instance drone assembly: `complete: true`, 11 of 24
instances from the mesh, 106.8 s.

The STL export behind that route runs with `DisplayFileAlerts` off — on B32 it
otherwise puts up a modal "另存为" that, unanswered, holds the call for 89 s.

### What an exact box costs

The vertex route reads one topology cell at a time, so its price is set by the
*cell* count, not the vertex count — a 16-vertex plate can still have hundreds of
cells. Since 0.3.31 that read happens **inside CATIA**: one `SystemService.Evaluate`
call enumerates the selection, drops the construction cells by their NLS name and
returns the coordinates, instead of a Python→COM round trip per cell (measured:
**827 ms one-call vs 1134 ms per-cell, −27 %**, identical boxes — `sweep_route`
says which one ran, and a build that refuses CATScript still falls back to the
per-cell reader). **And the per-cell cost depends on the session, not only on the
part.** The same 70-instance assembly measured three times in one session:

| call, same session, same `method` | time | per cell |
|---|---|---|
| 1st | **194.7 s** | 53.7 ms (26 vertex parts) |
| 2nd | **41.4 s** | 10.6 ms |
| 3rd | **41.5 s** | 10.6 ms |

`cells_scanned`, `vertices_used` and `nls_chars` were **identical** all three
times, so the work is the same — the first pass pays CATIA's own setup for each
part. Warm the fit is `≈ 200 ms + 3–5 ms × cells` after 0.3.31's one-call sweep
(3.2 ms/cell on 0.3.32's session, 3.8–4.9 on 0.3.34's, 9.5 before the sweep); cold
it is 16–90 ms/cell and grows with the part. **Read these as ranges, not
constants** — the same model moved 4.65× → 7.6× → 7.24× in three versions with the
same geometry, and a single number quoted in a docstring reads as a promise the
next measurement can only break. Every
number in this section that was measured as "one part by name" is a COLD number,
and reading it as a property of the part is what made an earlier release claim
`17–93 ms per cell` as the model. `parts[].first_measure`, `cold_parts` and
`elapsed_note` say which regime a call ran in; compare `elapsed_ms` across
sessions only with that in view.

The 0.3.32 acceptance run on the same model measured **cold 173 s, warm 22.9 s**
— the warm side had been cut in half, so the ratio *grew* to 7.6×; 0.3.33 measured
**183 s cold / 25.2–26.0 s warm (7.24×)** on the same model, and 0.3.34's cold note
quotes the range plus that call's own `parts_ms`/`overhead_ms` instead of any
constant. That is why the
cold pass is now something you can **schedule** rather than something that happens
to you: `catia_get_bounding_box {warm_up: true}` runs the same measurement, throws
the geometry away and returns what it cost, so the next call that needs the box is
warm (`cold_parts: 0`). There is no cheaper warm-up: CATIA's first-read setup *is*
the work, and a per-part vertex box cannot be cached from a `(size, mtime)` the way
a mesh box is — the mesh box describes a file, the vertex box describes the live
part, which may hold edits that were never saved.

| call | time |
|---|---|
| one part, 24 cells / 16 vertices | ~0.3–0.5 s warm, ~0.5–1.5 s cold |
| one part, 535 cells / 335 vertices (B30) | 5.9 s warm / 36.1 s cold |
| one part, 1 207 cells (acceptance model, B32) | 11.4 s warm / 84 s cold |
| one part, 504 cells (same model, B32) | 5.0 s warm / 47 s cold |
| 24 instances / 11 parts (drone), warm | 94.4 s |
| the same call right after opening the assembly | 130.0 s (1.38×) |
| the same call again, same session (mesh boxes reused) | **16.0 s (8.1×)** |
| 70 instances / 28 parts (acceptance model) | **173–183 s cold / 23–26 s warm** |

Three things dominate, and none of them is "one fixed cost per part":

* **Cold vs warm is the biggest single factor.** 173 → 23 s (−87 %) with
  every work counter unchanged. The gap is per PART and it disappears once that
  part has been touched: `P05` 80.0 s cold / 11.4 s warm (7.0×), `P01` 45.5 /
  5.0 s (9.2×), a 12-cell plate 0.33 s warm. It is not "less work" and not the
  assembly layer — `overhead_ms` says how much of the call no part owns, and it was
  identical cold and warm.
* **Cost concentrates in a few parts — in both regimes.** Cold: `P05` 80.0 s
  (41 %), `P01` 45.5 s, `P07` 15.0 s, `P02` 12.2 s. Warm: `P05` 11.4 s (27 %),
  `P01` 5.0 s, `P02` 2.9 s, `P07` 2.2 s. `elapsed_ms` in `parts` is what makes
  that visible, and `first_measure` is what makes it honest.
* **The mesh fallback costs more than the vertex work when the assembly has curved
  parts.** Parts bounded from STL are in context, so each one is copied to a temp
  file, opened, updated, exported and closed: 12–17 s per part, unchanged by
  warming, and independent of the mesh size. They are now reused from that file's
  identity (path, size, mtime) — that saves one export per curved part (in the
  drone assembly, 5 parts ≈ 74 s), which is why a second call there costs 16 s
  instead of 94 s. In the 70-instance model only 2 of 28 parts are curved, so the
  saving is ~10 s (4.6 %), not a step change. The cache belongs to the copy route,
  and the copy route is what runs when the part has no window of its own — the test
  is whether activating it changes `ActiveDocument`. A part that does change it is
  exported in place and never cached, because its loaded state may hold unsaved
  edits that no file identity can describe; `catia_open_document` cannot put an
  already-loaded component into that state (it activates it instead). If you only
  need `complete: true`, `include_curved: false` is cheaper still and lists those
  instances in `skipped` instead.
* **The "two tiers" were the cold/warm split in disguise.** Every part sat at
  either 17–31 ms/cell or 66–92 ms/cell in one cold pass, and cell count could not
  separate them (`P27` 246 cells at 30.5 vs `P07` 239 at 66.4; `P31`/`P30` both 12
  cells at 34.2 vs 64.7). Warm, the same pairs read 9.1 vs 9.4 and 28.1 vs 30.5 —
  the factor was the first-touch cost, not the geometry. A per-cell figure is
  therefore only meaningful with the regime it came from.
* **Do not use a small part's ~400 ms as a regression baseline.** In that model
  twelve-cell parts sat at 398–424 ms, but `P30` was 776 ms, `P20` 732 ms and `P24`
  635 ms — per-part jitter of +20 % to +90 % on a field that reads like a constant.
* **The most expensive item may not be in `parts` at all.** Adding up
  `parts[].elapsed_ms` to find "the one part that eats the call" was working with
  76.4 % of the number: on the 70-instance model the difference was 5 394.6 ms
  (23.6 % of a warm call), larger than the most expensive part in the table, and it
  appeared nowhere. Every reply now carries `parts_ms`, `overhead_ms` and
  `overhead_breakdown` (`components_doc_ms` / `walk_other_ms` / `report_ms`). On a
  5-instance product measured here: total 1 308.1 ms, `parts_ms` 856.8,
  `overhead_ms` 451.3, of which `components_doc_ms` **413.3** — so the residual is
  dominated by one read of the component tree, not by per-part bookkeeping, which
  is also why it is identical cold and warm.
* **That read was one COM call per instance, and it is not any more.** On the
  70-instance model `components_doc_ms` was 5 036 of 5 563 ms (90.5 % of the whole
  overhead block) — seventy `Evaluate` calls for twelve numbers each, 86 ms apiece
  when timed directly. Since 0.3.34 the unfiltered path reads every child's pose in
  **one** script call (`batch_positions`, with a status byte per instance so a
  refused pose falls back to the per-component reader instead of reporting zeros).
  Measured on B30 with 10 instances: `components_doc_ms` 866 / 851 / 868 ms →
  **267 / 253 / 205 ms**; one instance is unchanged (95 → 101 ms) because there is
  nothing to batch. A narrowed call (`component=...`) still reads exactly one pose
  — batching all of them would bring back the 5.8 s entry fee 0.3.26 removed.
  `poses_route` has three values, one per thing that can happen: `one_call` (the
  whole assembly in one script call), `per_component` (this build refused the
  script, so it fell back to a round trip each) and `single` (the call was
  narrowed). The third one exists because "the key is missing" is not an answer a
  caller can branch on — the same rule `sweep_route: mesh` follows.

### Two documents, one name

`document=` used to return on the first name match, and its own docstring said
why: *"A name is unambiguous."* It is not, and this server is what makes it
ambiguous — `catia_new_part(name=…)` sets the part number, CATIA names the
document after it, so creating a part under a name that is already open leaves
**two documents called `DUP35.CATPart`**:

```
catia_new_part({name: "DUP35", file_path: "…/DUP35.CATPart"}) → pad 40 → 16000 mm³, saved
catia_new_part({name: "DUP35"})                              → pad 60 → 24000 mm³, unsaved

catia_get_inertia({})                              → 24000   ✔ the document in front
catia_get_inertia({document: "DUP35.CATPart"})     → 16000   ✘ the other model
                                                     and the reply says document: "DUP35.CATPart"
```

No error, no hint, a plausible number — the reporter nearly filed it as a
regression of the 0.3.28 sub-assembly work. A name that means two models is worse
than a name that matches none.

Resolution is one function on the connection (`resolve_document`) and it collects
**every** match:

| step | result |
|---|---|
| the argument matches one document's full path | that document — the path is the only thing that tells two same-named copies apart (`catia_list_documents` prints it) |
| exactly one document matches by name or stem | that document |
| more than one | `DOCUMENT_NAME_AMBIGUOUS`, `modified: false`, and `data.candidates` with each copy's `document`, `path`, `active`, `saved` |
| none | `DOCUMENT_NOT_FOUND`, listing what is open |

```json
{"ok": false, "code": "DOCUMENT_NAME_AMBIGUOUS",
 "message": "'v36_dup.CATPart' matches 2 open documents: 'v36_dup.CATPart' at
             'F:\\cat_bench\\v36_dup.CATPart'; 'v36_dup.CATPart' (never saved - no
             path to name it by) (active). This call did NOT pick one …",
 "data": {"candidates": [
   {"document": "v36_dup.CATPart", "path": "F:\\cat_bench\\v36_dup.CATPart",
    "active": false, "saved": true},
   {"document": "v36_dup.CATPart", "path": null, "active": true, "saved": false}]}}
```

The tools that can create the situation say so when it happens:
`catia_new_part` / `catia_new_product` append "⚠ Another open document is already
called 'DUP35.CATPart' … `document='<name>'` is ambiguous and this server refuses
it with `DOCUMENT_NAME_AMBIGUOUS` instead of picking one", `catia_activate_document`
says it activated the first of N, and `catia_list_documents` marks each duplicated
entry with `name_shared_by`.

A document can be both a component of an open assembly and one of two copies with
the same name, and until 0.3.38 that entry carried two notes glued with a space —
one advising `document='<name>'`, the next refusing it:

```
… open it with catia_open_document (or measure it by name with document=) 2 open
documents carry this exact name …
```

The notes are sentences now, and when the name is shared the component advice is
dropped rather than contradicted: for that document only the path works. The two
refusals (`catia_get_inertia` / `catia_run_plan`) also describe a candidate the
same way, through `CATIAConnection.format_matches` — the planner's copy used to
omit "(never saved - no path to name it by)", which is the one fact that decides
whether the advice ("pass the path instead") can be followed at all.

A wrong name is one code on every tool. `catia_activate_document` was the odd one
out: it raised `COM_ERROR` and appended the "CATIA reports most automation
failures as a bare 'method failed' … check `in_work_object`" paragraph, about a
call that never touched a body and had already printed the list of open
documents. It raises `DOCUMENT_NOT_FOUND` with two next actions now, like the
other three.

That warning only means something if it appears when there IS another copy. 0.3.36
compared the handles with `is`, which pywin32 never satisfies (`Documents.Item(i)`
mints a new Python wrapper per access: `Item(1) is Item(1)` is False,
`Item(1) == Item(1)` is True) — so every document was its own twin, every new part
was greeted with "Another open document is already called 'SOLO36.CATPart' (1 of
them)", and a unique name was reported as "2 open documents carry that name" while
the same reply's `catia_list_documents` said 1. One comparison now
(`CATIAConnection.same_document`, COM equality), used by the listing, the warning
and the activation count.

The same name-based restore was hiding in two more places: `_tensor_for` and
`_mesh_export` borrow the active document (the inertia tensor only exists for the
active one) and gave it back **by name** — with a duplicate pair that restored the
wrong twin, so a named `catia_get_inertia` left the other document in front and the
*next* no-argument call measured the other model. Both keep the document object and
re-activate that.

### A listing keeps its shape when it is empty

`catia_list_features` used to answer the bare string `"No features in the active
body"` when a body held sketches and no feature — the one moment its own
description says to call it ("this is how to check whether a sketch still exists
after a failed pad/pocket/hole"), and the moment a caller doing
`resp["sketches"]` gets a `TypeError` instead of its sketch. Reported 2026-10-28,
along with `catia_list_components` doing the same on an empty product and
`catia_list_constraints` answering a bare LIST when full and a STRING when empty.

All three always return an object now:

```json
{"body": "零件几何体", "count": 0, "bodies": 1, "features": [],
 "sketches": [{"index": 1, "name": "草图.1"}], "sketch_count": 1,
 "note": "the active body holds no Part Design feature yet, so 'features' is empty
          and 'count' is 0 - this is not an error, and the sketches in it are
          listed below"}
```

The rule is the one already applied to plan results: **a caller must never have to
branch on the SHAPE of a reply before it can read it.** An empty answer is a value,
not a failure, and it says which of the two it is. `catia_list_documents` follows
it too — `[]` instead of the string `"No documents open in CATIA"`.

An EMPTY PRODUCT is not "every component failed", and the two routes now agree on
that: `empty_assembly: true`, `instances` 0/0/0, `skipped: []`, and the reason in
`incomplete_because` naming the document that was measured. The old placeholder in
`skipped` was named after the *active window*, so an empty `EMPTY35.CATProduct`
measured while `F35S.CATPart` was in front answered with a skipped component called
`F35S.CATPart` — a document the call never mentioned — and counted it as one
instance while `catia_get_inertia` counted zero for the same document.
`instances.skipped == len(skipped)` holds on both.

### One failure, one code — on both paths

0.3.30 gave failures machine-readable codes so a caller could branch on them:
`NO_MATERIAL` means change the offset or profile, `SKETCH_NOT_FOUND` means read
`Available`. 0.3.33 kept those codes inside `catia_run_plan` and lost them on the
bare path, because the server derived the code from the Python exception type
(`COM_ERROR`) while the planner read the message. The same failed pad answered
`NO_MATERIAL` in a plan and `COM_ERROR` on its own — two semantics for one failure,
in one server.

There is one table now, and it prefers what the raise site declares:

| source | example | code |
|---|---|---|
| the exception's own `code` | `FeatureRejected` after a rolled-back pad | `NO_MATERIAL` |
| the exception type | `AttributeError` on a missing API | `UNSUPPORTED_CAPABILITY` |
| the message | CATIA's own refusals | `PART_NOT_LOADED`, … |
| the caller's default | nothing matched | `COM_ERROR` (bare) / `STEP_EXCEPTION` (plan) |

`recovery` follows the same rule — it is filled from facts, not from a template.
A pad that measured both directions and rolled back reports `rolled_back: true`
and "change the sketch's offset or profile, then retry", because retrying the same
call *is* the call that failed. A tool that cannot write reports no `recovery` block
at all: `catia_get_bounding_box{document: '<a document that is not open>'}` used to
answer "model changes were not verified — inspect the CATIA model" for a lookup
that wrote nothing. What such a tool still publishes are the next actions it can
know — they are about the argument, not the model — in `data.next_actions` beside
the `candidates` they describe. Suppressing the block had been dropping
`DOCUMENT_NAME_AMBIGUOUS`'s two sentences on the one code only read-only tools
raise.

| field | meaning |
|---|---|
| `rolled_back` | `true` the feature this call created was removed again, `false` it could not be removed, `null` no feature was ever created |
| `can_request_rollback` | always `false` — no tool takes a rollback argument (it was called `rollback_supported` until 0.3.35, where a false read as "this failure could not be rolled back" right beside `rolled_back: true`) |
| `rollback_note` | present only when `rolled_back` is not null: the sentence the constants stand in for |
| `next_actions` | what is left to do, written by the raise site when it knows better than the default |

A refusal that happens before anything is written says so: `CodedError` carries
`modified: false` and its own `next_actions`, so `catia_pad{sketch_name: '草图.99'}`
answers `SKETCH_NOT_FOUND` / `modified: false` / "pick a name from `Available` …
this call created nothing" instead of "inspect the CATIA model".

Two follow-ons from the same report. First, that block is absent on **every**
failure of a read-only tool, including argument validation — `is_read_only` used to
be asked only in the `except` branch, so `catia_list_components{names_only: "yes
please"}` answered `INVALID_ARGUMENT` with "inspect the CATIA model before
retrying" about a call that never opened one. `UNKNOWN_TOOL` and
`INVALID_ARGUMENT` carry no `recovery` block at all now.

Second, the message no longer ends with the COM_ERROR sermon when the tool knows
what it refused. `Session state: …` stays (it is a reading); the paragraph telling
you to check whether `in_work_object` is a geometrical set is only added when the
failure actually came from COM — otherwise it sat directly below a line saying the
sketch name does not exist, or, for `DOCUMENT_NOT_FOUND`, sent you to inspect a
body in a document you do not have open.

Tools run on a single worker thread, so a long box blocks every other MCP call
until it finishes. The reply states `elapsed_ms` (total and per part, `parts[]`),
`first_measure` (was this part touched for the first time in this session),
`cold_parts`, `after_cold`, `cells_scanned`,
`sweep_route` (`one_call` / `per_cell`, or `mesh` when no vertex sweep ran),
`poses_route` (`one_call` / `per_component` / `single`),
`parts_ms`, `overhead_ms` and
`points_route` (`batched` / `batched_retried` /
`per_point` for a vertex sweep, `mesh` for an STL extent), so the cost is
attributable instead of surprising. `catia_get_bounding_box {warm_up: true}` pays
the cold pass on purpose and returns only the cost. To bound one instance,
pass `component=` — with
or without an assembly active: if the active document is a Part, the component is
looked up in the open assembly that contains it, and the reply says so
(`measured_in`).

`after_cold` is three values, never a missing key: `null` when THIS call was the
cold one, `true` on the first call after a cold pass, `false` in the steady state.
`parts[].cold_ms` and `parts[].cold_warm_ratio` give the two regimes per part once
both have been seen. They are the only numbers to quote: **the notes publish no
multiplier at all** (not even a range), because every range this project wrote
down was falsified by the next session — 1.15×–1.81× for 12-cell parts became
1.93×–3.70×, above its own ceiling, one day later. The rule, adopted 2026-10-29:
a range is published only if it holds in three independent sessions; otherwise
mechanism plus the field. A warm call does still quote this session's own
difference against the previous warm call on the same document, which is a
reading rather than a claim about CATIA.

`component_lookup_ms` (narrowed calls only, published **beside**
`overhead_breakdown` rather than inside it) is the share of `components_doc_ms`
spent matching the name you passed against every component — 303 ms of a
70-instance narrowed call for one pose read, which is three times a single
unfiltered read. It sits outside the block on purpose: that block promises "these
three add up to `overhead_ms`", and a fourth key made
`sum(overhead_breakdown.values())` over-count silently (reported 2026-10-28, P3).
`catia_get_inertia` publishes the same `parts_ms` / `overhead_ms` /
`overhead_breakdown` / `poses_route` vocabulary on the assembly route **and on
both part routes** (`measure_ms`, `tensor_ms`, `parts_other_ms`), so the two
aggregators can be compared on one model, and "174 ms here, 2 117 ms there, same
tool" is answered inside the reply. On the part routes the three terms add up:
`measure_ms + tensor_ms + parts_other_ms = parts_ms`, where the last one names the
remainder (main body, shape count, density arithmetic, building the reply) that
used to be 18–19 % of `parts_ms` with no field to attribute it to.

The assembly inertia route answers the regime question too: `cold_parts`,
`parts_total`, `parts[]` (per part, with `first_measure` and, once both regimes
have been seen, `cold_ms` / `cold_warm_ratio`), `after_cold` and `elapsed_note`.
Its book is deliberately separate from the bounding box's — mass properties and
the vertex sweep do not warm up together — so a part can be warm for one and cold
for the other.

A narrowed call (`component=`) reads the component list **once**. Until 0.3.38 it
re-read the whole list — placements included — to answer "is this part also
nested in a sub-assembly?", and did it *after* `elapsed_ms` had been stamped, so
the reply was short by that time and the narrowed call could cost more than the
full scan it replaced. On a 10-instance assembly that was 340–360 ms of component
reading against 170–195 ms now, and 247–288 ms of unreported tail against 93–118.
So the advice stands, with its true shape: `component=` saves the per-instance
scan, and it no longer pays a hidden second read for it.

`catia_list_components` filters before reading any placement, and
`names_only: true` skips them entirely (measured: 370 ms → 32 ms on a 5-instance
assembly, 94.4 ms for 24 instances) — "are the instances all there" should not
cost one pose read each. With `names_only`, no entry carries a `convention`: the
key describes a rotation, and there is none in the reply. On a Part document it
says that no assembly is active (and which ones are) instead of "No components
in the active assembly". `occurrence` there is the ordinal within the part
(`occurrence_scope: "per_part"`), which is what `{part_number, occurrence}`
resolves against; `index` is the position in the assembly.

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
| Part Design | 25 | pad, pocket, groove, fillet, chamfer, hole, patterns, mirror, shell, solid loft, bodies (create / list / delete)… |
| Generative Shape Design | 25 | wireframe and surfaces, extract, revolve + close surface |
| Assembly | **13** | upstream 9 + get/set component transform, rename, list constraints, copy component |
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
* Assembly **inertia tensors** are not aggregated (volumes, areas and centroids
  are); a tensor would have to be rotated into world space per component. The
  tensor is also only computed for the ACTIVE document, and with the part's
  material density - `get_inertia(density=...)` does not change it, which is why
  the reply reports both masses.
* `catia_get_inertia` expands sub-assemblies; `catia_get_bounding_box` does too
  since 0.3.29. Both compose the two placements and report every leaf with a
  `path` (`ASM/PART.1`) and a `depth`, so one part is still bounded and measured
  once however many levels it appears at. `component=` still matches TOP-LEVEL
  components only — a nested instance has to be asked for through its
  sub-assembly, and the refusal says so. When the same part number exists BOTH at
  the top level and inside a sub-assembly, the narrowed reply reports
  `complete: false`, `scope: "top_level_only"` and the instances it left out in
  `also_nested` / `skipped`, so a partial box cannot pass for the part's whole
  envelope.
* A part that CATIA is editing **in context** (loaded inside an assembly, no window
  of its own) cannot be exported to STL. The mesh box falls back to a copy of the
  saved file, so it measures the file, not unsaved edits; the reply says when that
  happened.
* `catia_add_component` runs the insert in a **separate process** attached to the
  same CATIA. It is the one call that can raise CATIA's own file prompt on the
  automation thread, and that prompt is not a `#32770` message box the watchdog
  may answer - one reporter's session sat there for 2 h 14 min. The child is
  killed after `CATIA_MCP_GUARD_TIMEOUT` (default 60 s), the prompt is cancelled,
  and the reply reports the component count so you can see whether the insert
  happened anyway. `CATIA_MCP_GUARD=0` turns the isolation off. Killing the child
  frees the *server*; if CATIA itself is wedged behind a native dialog that no
  button answers, a human still has to look at it.
* The watchdog answers `#32770` message boxes only; CATIA's own `CATDlgDialog`
  panels are left alone so a human working alongside the agent is not interrupted.

## License and attribution

MIT. Upstream copyright is retained in [`LICENSE`](LICENSE)
(Adam M'HAMDI, Ondřej Schiller, Chong Zhu). This fork's changes are additions to
and modifications of that work; `README.md` and `FORK.md` state what they are.

If you send improvements back, upstream is the right home for anything that is
not specific to this fork — it accepts contributions.
