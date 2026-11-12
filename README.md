# CATIA V5 MCP Server — lite fork

**0.3.107** · [what changed](CHANGELOG.md) ·
[releases](https://github.com/wudahua/MCP_CATIA_MechDog/releases) ·
[known limits](#known-limits)

A trimmed, verification-first fork of
[**daiemon12/catia-v5-mcp-server**](https://github.com/daiemon12/catia-v5-mcp-server)
(MIT, by Adam M'HAMDI). Upstream keeps the whole CATIA surface; this fork keeps
only what builds and checks a **solid from a written spec**, and fixes the
defects that make that loop lie to you. Most of the work since 0.3.0 came from
people rebuilding real assemblies with it: the changelog is a list of things that
produced a plausible number, a wrong number, or no explanation at all.

**142 tools** (upstream 98 + 44 added, and nothing dropped any more: knowledge
came back in 0.3.63 and drafting in 0.3.64, each after its tools were measured on
B30 rather than trusted). Nothing in the COM layer was rewritten — the value of upstream is
the field-verified knowledge in its comments, so this fork is strictly add /
subtract / patch. The count is asserted by `tests/test_readme_tool_counts.py`, so
it cannot drift from the code again.

### What it can verify, not just build

* **Every cut is measured.** `pad` / `pocket` / `hole` / `shaft` / `groove` /
  `circ_pattern` report the volume they moved before and after, so a feature that
  silently did nothing is visible on the spot. Volume proves a pattern built its
  **copies**, not that they point anywhere: an 8-blade fan came out with blades
  that were placed at 45° and never turned, at exactly 8× the seed's volume
  (2026-10-30). `catia_circ_pattern` now rotates each copy
  (`radius_aligned=true`, the default) and says which mode it used.
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

### Dropped (nothing, since 0.3.64)

Two modules were cut when this fork was made - `drawing` (needs a Drafting
licence) and `knowledge` (parameters / design tables). Both are back, measured:
knowledge in 0.3.63, drafting in 0.3.64. What was dropped was not the capability
but the claim that it worked.

`export` (STEP/STL hand-off) and `http_transport` (remote agent) stay for the
same reason they always did: domain-neutral and cheap. See `FORK.md` §8 if you
want to cut further.

### Added (36 tools over upstream)

Five of them came with the fork, twenty-one were added while building parts
from a spec (0.3.1: three, 0.3.2: four, 0.3.4: one, 0.3.5: one, 0.3.8: two, 0.3.17:
two - `catia_delete_body`, `catia_solid_loft`; 0.3.19: one - `catia_copy_component`;
0.3.51: one - `catia_check_surface`; 0.3.53: one - `catia_gsd_boundary`; 0.3.55: one - `catia_gsd_heal`; 0.3.56: two -
`catia_gsd_fillet_surface`, `catia_gsd_spine`; 0.3.60: two -
`catia_gsd_rotate`, `catia_gsd_translate`; 0.3.82: three - `catia_sketch_copy`,
`catia_gsd_circ_pattern`, `catia_gsd_plane_normal_to_curve`; 0.3.84: one -
`catia_gsd_plane_angle`).
98 + 44 added = 142: the 31 below, `catia_apply_design_table` under Knowledgeware,
`catia_drawing_set_sheet` and `catia_drawing_add_frame` under Drafting (0.3.65),
the three colour tools in their own table below (0.3.79), and
`catia_replace_component` (0.3.80), and `catia_split_solid` (0.3.94). 0.3.85 added arguments instead of tools:
`catia_gsd_boundary(edge=N, propagation=…)` takes one loop of a border (a
selector `AddNewBoundaryOfSurface` does not have), `catia_gsd_fill` accepts
`Edge.N`, and `catia_gsd_circ_pattern(cap_center=true, cap_curve=…)` closes the
centre of a full ring. 0.3.20 added
arguments instead of tools: a sketch support given as `origin`/`u`/`v`, edges
given as coordinates, arcs given as their two endpoints, and one plan per
several documents.

| Tool | What it gives you |
|---|---|
| `catia_run_plan` | Runs an ordered `{tool, args}` recipe in **one** call, then asserts volume / area / face / edge / vertex counts / centre of gravity |
| `catia_measure_topology` | Face, edge and vertex counts — the cheapest guard against a part with the right volume and the wrong shape |
| `catia_set_component_transform` | Absolute placement from a 3×3 in either convention (`matrix` = `P = A·p + T`, `basis` = row i is where local axis i points) or CATIA's native 12 numbers; reads back and reports deviation, det, orthogonality **and where each local axis ended up** |
| `catia_get_component_transform` | The same placement in both conventions, plus where local X/Y/Z point in the assembly |
| `catia_rename_component` | A readable assembly tree. `Part.Name` is read-only on V5-6R2020, so every part arrives as `Part1.1 / Part2.1 / …`; the instance name is writable |
| `catia_sketch_copy` | The "copy the profile, paste it on the next plane" step. Replays a sketch's recorded geometry into a new sketch on another plane - a main plane, a frame, or **a datum plane by name** - coordinates unchanged; redrawing by hand is what leaves two copies that are not coordinate-identical, and a loft section is sensitive to that. Refused for a sketch reopened from disk, because the record is the only source of coordinates. **The record now follows a delete** (0.3.90): measured 2026-11-09, two lines with the second deleted still left BOTH in the record, so the copy came back with twice the source's outline - the shape of a report's "2 spline(s)". A shape made of several elements (a rectangle is four lines) cannot be replayed after one side goes, so the record is dropped whole and the reply says `catia_sketch_copy` will refuse that sketch |
| `catia_sketch_strokes` | Draws a whole letter outline (or any set of polylines) in ONE call, as straight segments. A glyph is 8-20 segments and the one-element tools force one call per segment, so a 2026-11-10 report computed control points by hand for dozens of calls per letter. It does not interpolate: `catia_sketch_spline` through the same points rounds every corner - that is what turned their hand-built "A" into an omega. Each stroke that repeats its first point is closed, degenerate segments are refused before anything is drawn, and the segments are recorded as lines so `catia_sketch_copy` can replay them |
| `catia_gsd_circ_pattern` | Repeats one surface/curve N times around an axis and joins the copies in one call. **Not a CATIA GSD pattern**: this build's `HybridShapeFactory` declares 160 members and none contains "Pattern" (measured 2026-11-08), so the tree gets N-1 rotations plus a join, and the reply says so |
| `catia_gsd_plane_normal_to_curve` | Plane normal to a curve with its origin ON that curve at a fraction of its length - the tutorial's "Normal to Curve + Set Origin on Curve". A tree element, unlike the frame sketch that used to be the only way; the point's measured coordinates are reported back |
| `catia_gsd_plane_angle` | A datum plane rotated a given angle about an axis - the tutorial's 36-degree construction and the general 1/N-sector plane. A named, editable, referenceable tree element, unlike a frame sketch. The axis must LIE IN the reference plane (rotating a plane about its own normal leaves it where it was and CATIA answers with a generic failure), and the angle is in degrees |

#### Added while building parts from a spec (26 tools)

| Tool | Version | Why it exists |
|---|---|---|
| `catia_curve_on_surface` | 0.3.106 | Put a planar outline ON a curved face. This is the step seven reports named as the one structural gap: "computed on the surface" needed the surface's own equation, so the caller mapped the glyph by hand, guessed an offset, then calibrated by carving, reading removed/predicted and re-carving (one word, six ratios 0.93/1.45/1.40/1.20/1.42/1.24, three grooves deleted). Each point is snapped with `AddNewPointOnSurfaceWithReference` and the stroke is threaded into one open spline. **Measured**: an 18-point 'C' snapped from a plane 60 mm in front of a closed lofted bottle moved 26.053-26.137 mm, and `catia_surface_engrave` then removed 3.026 mm3 against a 3.042 mm3 prediction - 0.99, with no tuning. Two measured limits are in the reply: a point lands on the NEAREST part of the surface (four directions and three faces all moved the same point 26.0052 mm), and a SOLID BODY reference is refused outright |
| `catia_sketch_ellipse` | 0.3.106 | A full ellipse - centre, semi-axes, angle of the major axis - from `Factory2D.CreateClosedEllipse`. The 2026-11-11 report built a slanted handle hole out of a 28-point polyline because it believed no ellipse primitive existed; it does (37 members, read on B30), so the hole is one smooth wall instead of 28 flats |
| `catia_split_solid` | 0.3.94 | Cut a solid with a surface and keep one half (CATIA's Split), for the engraving chain a 2026-11-10 report could not finish. `ShapeFactory.AddNewSplit(surface, side)` is declared *and* works - measured on B30 before the tool was written: a 100×100×10 block cut by a plane at Z=3 gave 30000 mm³ with one side value and 70000 with the other, and the same block cut by a 400 mm extruded wall gave 70000 / 30000. Two things a caller cannot guess are in the message instead: **the cutter must divide the body completely** (a 40 mm wall on the same block fails at update, and CATIA only says "method Update failed"), and `side` means "the side the cutter's normal points to", which is why the argument is `keep='front'|'back'` and the reply prints both volumes |
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
| `catia_gsd_boundary` | 0.3.53 | A patch with a C0 seam has to be rebuilt from its own border, and `catia_gsd_extract` cannot do it - an extract of a face returns a surface, not the wire round it. `AddNewBoundaryOfSurface` takes one argument and updates: on a 30 mm extrusion of a r=10 circle the boundary measured 125.664 mm, exactly the two circles |
| `catia_gsd_heal` | 0.3.55 | Two surfaces that do not quite meet cannot be joined (a join wants a shared edge) and cannot be lofted together. `AddNewHealing` stitches them: measured on B30 2026-11-03, two lofts 0.05 mm apart went from 48 open seam edges to 0 with merging distance 0.5, while 0.001 changed nothing. It does NOT change continuity - a 0.52 deg seam stayed 0.52 deg - and the same operator had been written off in 0.3.51 because the test never registered a body with `AddBodyToHeal` |
| `catia_gsd_fillet_surface` | 0.3.56 | 0.3.53 called this object unverified and shipped nothing. Verified on B30 2026-11-03: the one orientation pair CATIA accepts on a straight corner, `(-1,+1)`, builds a true rolling-ball fillet - on 60/90/120 deg wedges every mesh vertex sat at exactly 5.0000 mm from the axis for r=5 (min = max) and the area was 314.159 / 418.879 / 209.440 mm² = `(π − θ)·r·L`, and a floor-and-wall corner built in another direction behaved identically. The tool tries the four pairs and keeps the survivor, refuses when none survives (two disjoint cylinders: 0 of 9) and refuses when more than one does (disc + standing cylinder built two different surfaces, 573.4 and 391.3 mm²) instead of choosing for the caller |
| `catia_gsd_spine` | 0.3.56 | 0.3.53 said the typelib "names no way to give it sections". It does: `HybridShapeSpine` declares 40 members including `AddSection`, `AddGuide`, `GetNumberOfSections` and `ModifySectionCurve`, and the old probe had only looked at `dir(spine)`. Measured: sections register, `UpdateObject` succeeds with two or more (one fails), and the curve is the spine - normal to the sections, not through their centres (three parallel rings gave 40.0000 mm and did not bend for a 30 mm sideways offset; a 45° tilted ring made it a 37.9224 mm curve that leaves that axis by 7.05 mm) |
| `catia_gsd_rotate` | 0.3.60 | A five-lobe bottle base is one lobe plus four rotations of 72°, and there was no way to rotate a surface at all - `catia_circ_pattern` is a Part Design feature and wants a solid - so the lobe was REBUILT five times, once per position, which is four extra constructions of the same geometry and a shape whose positions could not be moved later. `AddNewRotate(element, axis, angle)` was in the typelib the whole time. Measured on B30 2026-11-04: a cylinder whose axis sits at (20,0) rotated +90° about z lands at (0,20), so the sign is counter-clockwise by the right-hand rule, and the original stays |
| `catia_gsd_translate` | 0.3.60 | Same gap, same run. `AddNewTranslate(element, direction, distance)` measured on the same day: a cylinder spanning z 0..20 moved 30 mm along +z spans 30..50 and no longer meets z=0. The direction must be the Direction OBJECT, not a Reference - passing the reference raises “type mismatch, argument 2” - while `AddNewRotate` takes a Reference to an axis line, so the two members of one family disagree and neither argument kind is guessed |
| `catia_shape_audit` | 0.3.99 | A five-lug bottle base reproduced a tutorial to within 3.15 % of its volume, with the wall thickness matching to 0.0001 mm, and the shape was still wrong - and the tutorial's own appendix describes eight earlier versions that passed every such check. Volume and area cannot see a shape; r(theta) can: bins every mesh vertex by angle and reports the amplitude, the peak angles and whether N repeats agree |
| `catia_text_strokes` | 0.3.103 | Text into a sketch, from a font that ships with the server. `Factory2D` has no `CreateText` (37 members, measured 2026-11-10), so a glyph can only be geometry; `catia_sketch_strokes` already took a whole outline, and what was missing was the outlines. The face is **Hershey simplex**, single-line - one stroke per stem, because a 0.4 mm groove cannot resolve two walls of the same stem and comes out as one ragged slot. `height` is the cap height; ASCII 32-126 only, and a character with no glyph is skipped and named rather than drawn as a box. **Then the caller meets this**: engraving a glyph *through a projection* refuses a parameter point depending on how much of it lands - the same `'O'` failed at 81.4 % and passed at 57.2 %, 9 of 12 letters passing at their default placement. The reason is not confirmed; the numbers are in `catia_diagnose`'s ceilings, and the reply repeats them |
| `catia_check_surface` | 0.3.51 | A surface task's acceptance criteria were "check with zebra stripes that the valley is smooth", and CATIA V5 gives automation no zebra, no curvature comb and no Match object (measured: the viewer answers `RenderingMode` and returns an unresolvable attribute for `Zebra` / `EnvironmentMapping` / `AnalysisMode`). The mesh can answer it anyway: the dihedral angle across every interior edge, so a G0 fold reads as degrees instead of needing a picture |

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

| `catia_hole` with `threaded` set wrote `ThreadingMode = 1`, commented `# catThreaded` | `CatHoleThreadingMode` is **0 = threaded, 1 = smooth**, and 1 is also what a hole in a fresh part already carries - so the write was a no-op on a property nothing read back, and **every hole asked for as threaded since 0.3.0 was smooth** while the reply said it was created. Feature names do not differ (both `孔.1`), so nothing downstream could see it. A hole also **inherits the mode of the hole before it**, so `threaded: false` has to be written as well |
| `catia_sketch_constraint` passed `geometry_index_1` straight to `CreateReferenceFromObject` | `GeometricElements` item 1 is the sketch's **absolute axis**, not the first thing the caller drew (`绝对轴` at 1, `直线.1` at 2). So "my first line" = 1 landed on the axis and CATIA answered a bare `方法 AddMonoEltCst 失败`, a message that names neither. The delete path had refused index 1 for releases; the constraint path had not |

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

### A GSD write moves the in-work object, and fillets then fail for no reason

Any HybridShapeFactory write (a circle, a point, a loft) leaves CATIA's
`Part.InWorkObject` on the geometrical set. A Part Design factory called in that
state answers a bare `E_FAIL` — `AddNewSolidEdgeFilletWithConstantRadius 失败` —
about a perfectly good edge, and the message names nothing useful. Until 0.3.41
this made `catia_fillet(edge_anchors=...)` fail permanently in any document that
had ever touched a GSD tool, while `edge_name='Edge.1'` on the same edge worked:
the anchor path creates a scratch point set to measure distances, and creating
that set is itself a HybridBody write. It now hands the body back when the set is
deleted, and the rollback path (`_safe_update`) no longer raises `NameError`
instead of saying the feature was removed again.

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

### Rounding every edge: the feature reference never built

Omitting `edge_name` used to pass a Reference to the last feature into
`AddNewSolidEdgeFilletWithConstantRadius` and call that "all edges of the last
feature". Measured on B30, that call fails **at construction** on a 2-edge
cylinder, a 12-edge rectangle, a 54-edge polyline and a 246-edge polyline — and
the failure arrives as a bare `COM_ERROR` naming no edge, which is what a
2026-11-11 report hit twice. Four controlled cells rule out the arguments:
feature reference versus whole body, propagation 0 versus 1, four failures.
`AddNewChamfer` behaves the same, measured separately because it is a different
method.

Both tools now **enumerate the body's edges** and build one feature per edge, so
the reply can name what took and what did not:

```text
Every edge of '零件几何体' (12 edge(s)): …
Fillet R1.5: 4/12 edge(s) done (Edge.1 …, Edge.2 …) [applied Edge.1@R1.5, …].
Rejected: Edge.3 (L80.0000|Sketch.1;3|Pad.1;2) -> rebuild failed: …
```

**Four out of twelve is not a radius problem**, and this is the number worth
knowing before trying smaller radii: on an 80×40×20 box at all 12 edges,
R0.25 / 0.5 / 1.0 / 1.5 / 3.0 each took the *same four* 40 mm edges and removed
2.1460 / 8.5841 / 34.3363 / 77.2567 / 309.0266 mm³ — every one exactly
`4·40·(R² − πR²/4)`. The eight that refused are not short (the shortest edge is
20 mm, and `2·R` is 3 mm); edges that share a vertex fight over the same
material. A retry costs a rebuild and a feature per edge — about 0.59 s each,
246 edges in 145 s — so `max_edges` (default 200) bounds it, and above that the
batch runs once and the reply says which number it stopped at.


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
`17–93 ms per cell` as the model. `parts[].first_measure`, `parts[].cold_reason`,
`regime` and `session_first_parts` say which regime a call ran in; compare `elapsed_ms` across
sessions only with that in view.

The 0.3.32 acceptance run on the same model measured **cold 173 s, warm 22.9 s**
— the warm side had been cut in half, so the ratio *grew* to 7.6×; 0.3.33 measured
**183 s cold / 25.2–26.0 s warm (7.24×)** on the same model, and 0.3.34's cold note
quotes the range plus that call's own `parts_ms`/`overhead_ms` instead of any
constant. That is why the
cold pass is now something you can **schedule** rather than something that happens
to you: `catia_get_bounding_box {warm_up: true}` runs the same measurement, throws
the geometry away and returns what it cost, so the next call that needs the box is
warm (`session_first_parts: 0`). There is no cheaper warm-up: CATIA's first-read setup *is*
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

### A rejection before execution has one shape, from any tool

Everything this server refuses **before its first COM call** — an unknown tool,
arguments that fail the tool's own schema, a plan with no steps, a step whose
arguments are wrong, an `expect` that cannot be parsed — answers the same
envelope: `ok: false`, `data.failure_stage: "validate"`,
`effects.modified: false`, **no `recovery` block**, and `data.next_actions`
describing the argument to fix.

It used to be three shapes. `catia_run_plan` built its refusal dict by hand (no
`effects`, no `failure_stage`), a plan with no steps raised `ValueError` and came
back as `COM_ERROR` with "inspect the CATIA model before retrying", and a *writer*
whose arguments failed the outer schema got a full `recovery` block — advice to
repair a model that had not been opened — while the same failure on a reader got
none. `catia_mcp.results.reject_validation` writes all of them now, so the next
refusal cannot ship with two of the three facts.

`expect` is validated like `steps`: a value that is not a number is
`INVALID_EXPECT` **before** the model changes, not `ASSERTION_FAILED` (a verdict
on the model) after it. An `expect` entry that is not an object used to be skipped
silently, so a plan whose assertions were malformed answered `PLAN_OK` with an
empty `assertions` list — a pass that tested nothing.

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

### A refusal the TOOL makes is the same envelope

The three entry points above are all checks the *server* makes before dispatch.
There is a fourth, and it is the largest: the lookups a tool does for itself once
the schema has passed — `Edge.99 is out of range: the final shape exposes 12
edge(s)`, `was called without 'feature_name'`. Those used to raise a bare
`RuntimeError`, and a bare `RuntimeError` looks exactly like a COM failure to a
server that classifies by what the exception declares.

```
before:  COM_ERROR · failure_stage "execute" · modified null · recovery block
         + "check whether in_work_object is a geometrical set"
after:   ARGUMENT_REJECTED / ARGUMENT_OUT_OF_RANGE · "validate" · modified false
         · no recovery · data.next_actions
```

A refusal that never opened a socket is not a communication failure, and the
sermon about `in_work_object` had no business under a message that had just said
the edge number was too high. `ArgumentRejected` carries `stage = "validate"`
with it, so the server stops deciding that from the exception's Python type.

The case that makes it matter: an out-of-range edge (fix the argument) and a
fillet CATIA rejected and the tool rolled back (the model needs a look) used to
share a code, a stage and a shape. `rolled_back: null` was the only difference,
inside a block that the first case should not have had at all.

### An argument the tool does not read is refused, not dropped

`catia_get_inertia({component: "NP42.1"})` used to answer **5760.0 mm³** — the
*active* part's volume — for a part of 1536.0 mm³, with `isError: false` and no
warning. `component` is declared by `catia_get_bounding_box`, nothing in the
measurement module reads it, and a key nobody reads is a key nobody notices.
A reply of the wrong number in the right shape is the hardest failure there is.

The server now compares the arguments against the tool's own schema and refuses
anything undeclared (`UNKNOWN_ARGUMENT`, with `unknown_arguments` and
`declared_arguments` in `data`). `catia_run_plan` applies the same rule to every
step at validation time, so a dry run reports it too.

Two lessons from the day it shipped, both worth keeping:

**A refusal must not name a spelling the tool accepts.** `catia_measure_distance`
reads `element1`, `elem1_name`, `element1_name` and `elem1` — and its own
description said "Aliases accepted: … elem1." The schema declared two of the four,
so the new check refused a call the documentation called valid. The list now lives
in one place (`catia_mcp/arguments.py`) and `_pick()` and the schema both read it.
Listing the spellings twice fixes today's tools and re-opens the gap on the next
one.

**A declared argument nobody reads is the same defect from the other side.**
`catia_coincidence_constraint` declared `element1` / `element2` and read neither —
it aligns two whole components — so passing one was accepted, ignored, and
answered "constraint created". Those two keys are gone. The check cannot catch
this direction (a schema can only lie about what it declares); reading the
implementation did.

The unknown key is now reported **before** a missing one: a caller who typed
`elem2_name` and therefore has no `element2` is told about the typo, which is the
cause, rather than about the argument it left out, which is its symptom.

### A file name CATIA will not write

`catia_save_document {file_path: "零件.CATPart"}` used to spend six attempts,
pop a modal dialog on each, and answer "could not be saved" — which reads like a
file lock, and no retry can clear it. Measured on V5-6R2020 B30, same folder, same
length, only the name differing:

| path | result |
|---|---|
| `…\ascii_probe.CATPart` | saved |
| `…\中文探针.CATPart` | `DISP_E_EXCEPTION`, 6 attempts |
| `…\中文目录\probe.CATPart` | saved |

The limit is the file **name** — that one is refused before CATIA is asked, at
the validate stage, with that table in the message. A non-ASCII **folder** is a
different story and the measurements disagree from machine to machine: nine
successes here (2026-11-07/08, five then four path shapes) against eight failures
and no success on the 2026-11-09 reporter's machine (0.3.81/0.3.83/0.3.85/0.3.87,
empty session and multi-document alike). What is deterministic on both is
narrower than the folder: it is **the first save of a document that has no path**.
A `catia_save_document` into that same folder succeeds every time on both
machines, because by then the document has a path.

Since 0.3.89 **every** first save takes the staged route: `SaveAs` into `%TEMP%`,
then `SaveAs` onto the path asked for — the second call is exactly the one
measured working on both machines — and the reply says which route ran and whether
the folder is ASCII. 0.3.88 gated that on "the folder is non-ASCII", and a report
then described a path shape (an ASCII ancestor containing a space, Chinese
subfolder) where the route apparently never ran. The test is
`str(path).isascii()` on the whole string — no whitespace split — and that exact
path was measured through the staged route here, so the branch is gone instead of
defended: there is no folder shape left that can miss it. The cost is one extra
`SaveAs` on a document that has no path to lose. If the staged route fails too,
the direct `SaveAs` is still attempted and the failure names both. A save that
fails also says which COM call it was ("this call was a bare `doc.Save()`" vs
"this call was `doc.SaveAs(...)`"), because those are different problems with
different answers — and since 0.3.90 it carries `data.save_route`, every step in
order with the COM answer for each, so "the staged branch never ran" and "its
second step failed" are told apart by reading instead of guessing. Since 0.3.91
that route also separates the staged route's two SaveAs calls (`staging_save`,
`staging_move`) and publishes `data.staging_route` — `tried`, `temp_file_saved`,
`temp_error`, `move_error` — because one flag set before the call had been read as
"the document was saved to %TEMP% and the move was refused too" even when %TEMP%
had refused the file. A folder that cannot be created is now `SAVE_FAILED` with
`data.call = "os.makedirs"` rather than an escaped `OSError` reported as
`COM_ERROR`. Since 0.3.92 the route also opens with `route_decision` — the branch
taken (`bare Save`, `staged first save`, `direct SaveAs`, `refused: …`) and the
four facts behind it (`has_path`, `same_file`, `file_exists`, `holder`) — and a
document with **no path can never be sent to a bare Save**, whatever an
inconsistent `FullName` read says: that call cannot give it a path, so it takes
the staged route (`_same_file` and `_document_at_path` now both require an
absolute FullName). The one
bare-Save branch left in that path ("the target is this document's own file")
compares **FullName**, not `Name`: CATIA gives two open documents the same name,
and a document with no path can never be "the same file".

### A path can only hold one document

`catia_new_part(file_path=X)` twice on the same path used to leave **two**
documents in the session: one holding the file (an empty part, saved before any
geometry existed) and one with no path at all holding everything that was built.
After that every `document="P05"` answered "matches 2 open documents", and the
file on disk looked finished while containing nothing.

The second call is refused now — before the document is created, because there is
no recovery afterwards. `DOCUMENT_ALREADY_OPEN` names the document that holds the
path and the three ways forward. `catia_new_product(file_path=...)` gets the same
guard, and the same argument, which it had been missing: without it "create an
assembly" is two calls, and the one people forget is the one that leaves a product
nothing can be added to.

### An arc between two points takes the short way

```
catia_sketch_arc {cx, cy, radius, p1, p2, direction: "ccw"}
-> swept 355.6117° counter-clockwise, arc length 1608.4415 mm
```

for two endpoints 4.39° apart. `ccw` from −71.0° to −75.4° **is** 355.6°; the
argument was doing its job on a caller who meant "a little bit the other way".
The shaft built on that arc then failed with a bare `UpdateObject 失败`, and the
only clue had been a parenthetical two calls earlier.

`major` is an argument now, default **false**: two endpoints give the short arc,
and the reply names both sweeps so the choice is visible. `major=true` keeps the
240-degree arcs that are legitimate. Two endpoints at the same angle are refused
rather than swept a full turn, and an exact half circle says that direction
decides nothing there.

### Which way a `frame` normal points

`normal = u × v`, right-hand rule, and the sketch's H/V axes are `u` and `v`
exactly as given; `origin` is in global coordinates. To put material on the other
side, reverse `v` — not the profile, because mirroring the profile changes which
side of the sketch the geometry occupies, which is a different operation wearing
the same result.

### A plan that answers with its own size

A 285-step plan returned 285 step records. The client truncated the reply, and the
two failed steps were inside the cut, so locating them cost a re-run of the whole
plan. `catia_run_plan {summary: true}` returns the failed steps only, plus
`failed_indices`, `steps_omitted` and the totals — `executed`, `measurements` and
`assertions` are unchanged. Without `summary` nothing changes.

Tools run on a single worker thread, so a long box blocks every other MCP call
until it finishes. The reply states `elapsed_ms` (total and per part, `parts[]`),
`first_measure` (was this part touched for the first time in this session),
`regime` / `session_first_parts` / `parts_total` / `cold_reasons` / `after_cold`,
`cells_scanned`,
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

### Regime fields: one function, seven routes, one matrix test

`regime` is `cold` / `warm` / `not_measured`, `after_cold` is three values and
never a missing key: `null` when THIS call was the cold one, `true` on the first
call after a cold pass, `false` in the steady state. `session_first_parts` counts
the parts whose first reading by **this server** happened in this call, out of
`parts_total`, and `cold_reasons` breaks that count down by job
(`topology` / `mesh_export` / `mass_properties`).

All six fields are produced by one function (`catia_mcp.regime.SessionBooks.describe`)
and published by all seven measuring routes: bounding box on an assembly, on an
empty product, and on one part; mass properties on an assembly, on an empty
product, on a named part and on the active part. Before 0.3.39 they were written
out three times and missing from four branches — each acceptance round found one
or two of the empty cells, in the release that had just filled another pair by
hand. `tests/test_route_matrix.py` now asserts the set, the arithmetic and the
cost closure on every route, and reads the `route=` literals out of the source so
a new branch cannot be added without a row in the matrix.

`cold_parts` was renamed `session_first_parts`, and each part carries `cold_reason`.
The old name asserted a cause ("these parts were cold") that the count never
measured: in the 2026-10-29 run 26 of 28 parts were **no slower at all** on their
second reading, and the two that read 21×–26× were paying for an **STL export**,
not for a build. The note beside the count no longer claims a cause either — it
says what the book counts and points at this call's own fields.
`parts[].cold_ms` and `parts[].cold_warm_ratio` give the two regimes per part once
both have been seen (and `parts[].cold_reason` says which job the first reading
paid for). They are the only numbers to quote: **the notes publish no
multiplier at all** (not even a range), because every range this project wrote
down was falsified by the next session — 1.15×–1.81× for 12-cell parts became
1.93×–3.70×, above its own ceiling, one day later. The rule, adopted 2026-10-29:
a range is published only if it holds in three independent sessions; otherwise
mechanism plus the field. A warm call does still quote this session's own
difference against the previous warm call on the same document — **only when both
call measured the same scope**. Until 0.3.39 it quoted one anyway, and a
`component=` narrowing (1 instance) compared against the whole 70-instance
assembly read as −67 %, which is the scope, not the machine. Two different scopes
get a sentence saying so instead of a number.

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

The assembly inertia route answers the regime question with the same six fields,
plus `parts[]` (per part, with `first_measure`, `cold_reason` and, once both
regimes have been seen, `cold_ms` / `cold_warm_ratio`). Its book is deliberately
separate from the bounding box's — mass properties and the vertex sweep do not
warm up together — so a part can be warm for one and cold for the other.

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

#### Added for looking at the assembly (3 tools)

| Tool | Version | Why it exists |
|---|---|---|
| `catia_colorize_assembly` | 0.3.79 | An assembly built from a text description is checked with a screenshot, and until 0.3.79 every part came out the same grey: `catia_set_visibility` drove `Selection.VisProperties` for the show flag and the other half of the interface was never touched. One colour per **part** by default (instances of one part share it - that is what "colour by part" means), one per **instance** on request. Colours never wrap around the palette: past 12 parts they are generated, because two parts of one colour is the one outcome this tool must not produce. Measured on B30 2026-11-08: two instances of one part carried (220, 30, 30) and (30, 120, 220) at once, both survived save + close + reopen, and the .CATPart itself still read its default. The colours are visible on screen, not only in the property - which took a screenshot and a pixel count to establish, twice |
| `catia_set_color` | 0.3.79 | One target, two meanings, and the reply always says which: in an assembly it is an **instance** colour (stored in the CATProduct, the other instances and the part file untouched), in a part it is a **part** colour (stored in the CATPart, inherited by every feature inside the body). A geometrical set colours its contents too, because a set's colour does not reach them - each element carries its own, exactly like visibility. `Selection.Add(part)` on the Part node is accepted and reads back (0,0,0): the Part node is not a colour target, the body is |
| `catia_get_color` | 0.3.79 | What `colorize_assembly` produced, and which instance did not take. It reports hex + RGB and flags a value matching this build's uncoloured default, measured (210, 210, 255) - not white. That flag is **evidence, not proof**: the interface reports the DISPLAYED colour, so a part deliberately set to that exact value reads the same way, and the reply says so instead of claiming "not set" |

### Colour: the three signatures that are not the documented ones

Measured on V5-6R2020 B30, 2026-11-08. All three are in `catia_mcp/colours.py`
once, so `catia_list_components(include_color=true)` and the colour tools cannot
drift apart:

* `VisProperties.SetRealColor(R, G, B)` is **refused** with 无效的参数数目 ("invalid
  number of arguments"). The build wants a fourth value, and it is **not a
  dummy**: it is a status flag whose **even** values (0, 2) store the colour and
  leave the view untouched, while odd ones (1, 3) apply it. Measured by colouring
  a component red and sampling the screenshot after each: with 0 the histogram
  was byte-identical to the uncoloured one, with 1 the solid came out (174, 24,
  25). `GetRealColor` answers the new value in BOTH cases, so a wrong flag reports
  success on a gray assembly - which is exactly what this feature's first version
  did, and why the acceptance script now reads pixels instead of trusting the
  read-back.
* `VisProperties.GetRealColor()` returns **four** values with the RGB **last**:
  after `SetRealColor(220, 30, 30, 0)` it answers `(0, 220, 30, 30)`. Slicing
  `[0:3]` reports black for every colour - the same trap as `GetShow()` returning
  a 2-tuple whose flag is at `[1]`, which cost a release in 0.3.73.
* An untouched part reads `(0, 210, 210, 255)`, i.e. RGB **(210, 210, 255)**, not
  white. A part-level colour is stored in the **CATPart**; an instance colour in
  the **CATProduct**. Nothing is saved by these tools - both need
  `catia_save_document`, and the replies say so.


## Requirements

* Windows (COM automation; `pywin32` is a hard dependency)
* CATIA V5 installed and licensed — verified on **V5-6R2020 (B30)**, Simplified
  Chinese build. **Two** known build differences:
  1. `catia_gsd_multi_section_surface` fails on **V5-6R2022 (B32)** for every
     configuration (reported 2026-10-23), while the rest of GSD — including
     `catia_gsd_blend` on the same curves — works.
  2. **A threaded hole cannot be cut on B32.** `catia_hole(threaded=true)` sets
     `ThreadingMode` alone, which threads on B30 and does not on B32: there the
     update fails, the feature is removed again and the call answers
     `FEATURE_INCOMPLETE` / `execute` with `rolled_back` and the measured
     `modified` (reported on B32, 2026-11-05/06; also stated at the `threaded`
     argument itself). B30 reports `NO_MATERIAL` before the write instead, so
     the author's machine cannot reproduce it. Registering the limitation is not
     a fix — a real one needs `ThreadStandard`/`Pitch` probing on a B32 seat.

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

# the smoke check: 139 tool definitions == 139 routes
.venv/Scripts/python tests/test_server.py
```

Both commands run the same equality assert since 0.3.67. Until then the smoke
script kept its own list of nine modules and reported "108 tools defined but 127
in router" while `pytest` stayed green — the strong check was the one CI never
ran. The scan now reads the server's own module list, so a new module is
covered the moment it is registered (`tests/test_reported_issues_2026_11_05_v0367.py`).

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

A batch is one convention, not one per row: `placements=[...]` gives every entry
the top-level `rotation_convention` unless the entry sets its own, and the reply
says how many inherited it (`rotation_convention.inherited_by`). Before 0.3.40
the batch branch returned **before** that argument was read, so a basis table
placed as a batch went in transposed and the per-entry reply still said
`convention=matrix` — true, and useless. One acceptance build placed 32 of 80
instances wrong that way; the 48 mirrored ones hid it, because a mirrored
rotation is symmetric and its transpose is itself.

### Counts, volumes and bounding boxes do not see orientation

Three defects of the last two weeks — a transposed placement (0.3.40), a circular
pattern that translated its copies instead of turning them (0.3.42), and a
bounding box that measured a GSD surface nothing had cut (0.3.39) — share one
property: **the number that was checked stayed correct**. Volume is invariant
under a rigid transpose. A pattern's volume is `count × seed` whether the copies
turn or not. A symmetric seed's envelope is the same whichever way it faces.

So check the invariant the operation is supposed to produce, not the total:

| operation | what to assert | why the obvious check fails |
|---|---|---|
| `catia_circ_pattern` | `bbox_y == bbox_z` and `y_max == -y_min` for an n-fold pattern | volume is `count × seed` in both modes |
| rectangular pattern | `x_max − x_min == (n−1)·pitch + seed_w` | same |
| `catia_set_component_transform` | per-instance centroid, or `basis_rows` from the reply | 48 mirrored instances make a transpose invisible |
| mirror / symmetry | the mirror plane's coordinate is unchanged, the off-plane one flips | volume and face count are identical |

Two rules that follow, and cost nothing to follow:

1. **Use a seed without rotational symmetry.** A cube, a cylinder and a disc
   cannot tell "rotated" from "moved to that angle" apart — their envelope is the
   same either way. A 400 mm bar can. One acceptance probe measured ±505 on a
   cube and looked correct for exactly this reason.
2. **Prefer per-instance readings to aggregates.** An aggregate averages what it
   sums and cancels what it mirrors; a centroid per instance does neither.

These are the checks an acceptance round can run in one call each. They are also
the reason `catia_circ_pattern` now says which mode produced its copies: a reply
that states its own assumptions can be contradicted by the next measurement,
while a reply that only reports success cannot.

## Tool reference

| Group | Tools | Notes |
|---|---|---|
| Document | 12 | new/open/save/close/list + `catia_save_as_copy`; `catia_save_document` retries around dialogs |
| Sketcher | 17 | lines, rectangles, circles, constraints; reopen a closed sketch and delete one element; `catia_sketch_copy` replays a profile onto another plane; `catia_sketch_strokes` draws a whole letter outline in one call, and `catia_text_strokes` supplies those outlines from a built-in single-line font (and can hand the points back); `catia_sketch_ellipse` draws a slanted ellipse in one curve |
| Part Design | 26 | pad, pocket, groove, fillet, chamfer, hole, patterns, mirror, shell, solid loft, bodies (create / list / delete)… |
| Generative Shape Design | 36 | wireframe and surfaces, extract, `boundary`, `heal`, `fillet_surface`, `spine`, `rotate`, `translate`, `circ_pattern` (rotate+join), `plane_normal_to_curve`, `plane_angle`, revolve + close surface, and `catia_surface_engrave` (a constant-depth groove along a curve on a curved face); a GSD element or set is removed with `catia_delete_feature` |
| Assembly | **14** | upstream 9 + get/set component transform, rename, list constraints, copy component, replace component |
| Appearance | **3** | `catia_colorize_assembly`, `catia_set_color`, `catia_get_color` - per-instance colour in an assembly, per-node in a part |
| Measurement | 8 | inertia, distance, bounding box\*, parameters, `catia_check_surface`, `catia_shape_audit` |
| Export | 4 | STEP / STL / IGES hand-off |
| Knowledge | 6 | parameters, formulas, relations, design tables - upstream's five, measured and corrected, plus `catia_apply_design_table` |
| Drawing | 13 | upstream's drafting module, measured, plus `catia_drawing_set_sheet` and `catia_drawing_add_frame`: new drawing, generative view (plane / face / body), projection, section, detail, text, table, generated and manual dimensions, paper size, border and title block |
| Planner | **2** | `run_plan`, `measure_topology` |
| Diagnostics | 1 | `catia_diagnose` |

#### Knowledgeware (6 tools, 0.3.63)

Upstream's five were dropped when this fork was cut, and came back after each was
run on B30 (`docs/upstream-diff.md` §8 explains what that costs). Four things in
upstream's code did not behave as written, so the port is a rewrite:

| Tool | What it gives you | What upstream had wrong |
|---|---|---|
| `catia_create_parameter` | Six types; an existing name gets its value set | `bool("false")` is True in Python, so a boolean sent as text was inverted. Parsed properly now, and unknown text is refused rather than guessed |
| `catia_set_formula` | A formula driving a parameter, by name or by the parameter's full path | The target lookup is exact and LOCALIZED: a Chinese build names a pad's height `零件几何体\凸台.1\第一限制\长度`, so the English path in upstream's own docstring is not found. The failure now lists near matches |
| `catia_list_relations` | Formulas, tables, rules, checks with their bodies | — (a relation's `Value` really is its expression; measured) |
| `catia_delete_relation` | Delete one by name | — |
| `catia_create_design_table` | A table from a sheet, associated to parameters by column header | It could create a table and never apply one - see the next row |
| `catia_apply_design_table` | Switch the part to row N of a table | **New**: upstream calls `ConfigurationName`, which the DesignTable interface does not have. The real member is `Configuration`, it is writable, and writing 2 on a two-row sheet moved the part (LEN 20 → 30) |

**A length column without a unit is read in METRES.** A sheet row reading `30` set
a 20 mm parameter to 30000 mm; `30mm` and `0.03m` both gave 30. Both the create
and the apply reply say so, because the alternative is a part 1000x too large
with a success message.

#### Drafting (13 tools, 0.3.64-0.3.65)

Upstream's module, ported only after all eleven tools were run on B30. Four things
did not survive the measurement, so this is a rewrite of those paths:

| Tool | What it gives you | What upstream had wrong |
|---|---|---|
| `catia_new_drawing` | A `.CATDrawing` with the default sheet and its Main/Background views | — |
| `catia_drawing_add_view` | Generative front view of an open part: by plane (0.3 s for a plate), by **planar face** (~30 s), or one body only | Face-driven projection **could not work here**: it searched `"Topology.Face,sel"`, which is English - a Chinese CATIA raises `方法 Search 失败` and answers `拓扑.面,sel` - and its indices were the last FEATURE's, so `Face.N` meant different geometry to this tool and to `catia_list_faces` |
| `catia_drawing_projection_view` | right/left/top/bottom/rear, placed by the sheet's first- or third-angle method | — (`ProjectionMethod` reads 0 = first angle on a fresh sheet, as assumed) |
| `catia_drawing_section_view` | Section view or cut from a polyline profile, Offset or Aligned | — |
| `catia_drawing_detail_view` | Circular detail at a scale | — |
| `catia_drawing_add_text` / `_add_table` | Text and filled tables, incl. cells and per-column widths | — |
| `catia_drawing_generate_dimensions` | One associative dimension per 3D sketch constraint, `DimStatus 7` = 3d_driven, follows the part | Its "no dimension" note told you to add a `length` constraint, which `catia_sketch_constraint` does not have (a length is `distance` with one element) |
| `catia_drawing_list_dimensions` | Values, type codes, basic_2d vs 3d_driven | — |
| `catia_drawing_list_view_geometry` | Indexed 2D elements of a view | Index 1 is the view's **absolute axis** - the same trap as a sketch |
| `catia_drawing_add_dimension` | A dimension on hand-drawn 2D geometry | Index 1 reached `Dimensions.Add` and CATIA answered a bare `方法 Add 失败`; a pairing the geometry cannot carry (a horizontal line with a vertical one) was forwarded as that same error |
| `catia_drawing_set_sheet` (0.3.65) | A0-A4, landscape/portrait, sheet scale; replies with the size CATIA reports | Paper on a new sheet is `catPaperUser` (13) and `GetPaperWidth` raises until a real size is set; the ANSI names in the enum are refused by CATIA |
| `catia_drawing_add_frame` (0.3.65) | ISO border (20 mm filing margin) + an 8x5 title-block table with merged fields, plus notes above it; labels in **Chinese by default** (0.3.66) | New: upstream stops at "frame/title-block generators are the next step". `Tables.Add` anchors the TOP-left and grows down - the first version put the block below the sheet |

Two limits worth knowing before drawing: a **generative view cannot be**
hand-dimensioned at all - its curves are not addressable through automation, only
`GenerateDimensions` reaches them - and **`Face.N` is session-scoped**. The same
plate gave its hole's cylinder at `Face.1` in one build and at `Face.7` in another,
so list faces and use the index immediately.

**0.3.78: the sign my own audit could not see.** 0.3.77 fixed `dir1_axis` and
verified it by comparing `dimensions` — which cannot see a sign, so a mirrored
pattern grew the same 200 mm and passed 12/12. The acceptance read the box
**endpoints** and found every label inverted. Measured again on endpoints, the
two slots are different: slot 1 takes the plane's H axis, slot 2 its V axis, and
both run backwards — so `axis x+` now really goes to +X. Also from that report:
a new sketch whose name the part already uses is **renamed** (CATIA's numbering
does not check, and every by-name tool then picks one of two), `run_plan` keeps a
warning **untruncated** in `step_warnings` and always publishes that field,
`catia_open_document` reads back which file it actually opened, and
`catia_gsd_create_geoset` gained **`parent`**, without which 0.3.77's recursive
visibility descent could never run. The loft's failure also stopped drifting
between codes: it answers `LOFT_REJECTED`.

**0.3.77: `dir1_axis` was one axis off, and a loft that twists now says so.**
An 85 m frigate built from a text description turned up two silent geometry
defects. `catia_rect_pattern` mapped `x` to `PlaneYZ` on the assumption that a
plane reference gives its normal; measured on B30, a plane reference drives the
plane's **own horizontal axis**, so `dir1_axis='x'` moved the copies along Y —
and the `ok:true` came with a WARNING that had been standing in for a fix since
2026-11-02. The table is the measurement now. And two loft sections holding 59
and 60 points were paired by CATIA **by parameter**, bulging the stern +49.8%
while the volume looked reasonable: `catia_solid_loft` reads the drawing record,
prints every section's curve count, and warns when they disagree. Also: a frame
sketch can be re-created after a rollback (the record said `base`, the resolver
read `type`), `pocket(direction='normal')` flips like `pad` instead of failing,
hiding a geometrical set reaches a set inside it, and a part that cannot be
saved is closed again rather than left pathless.

**0.3.76: the full gap list, as data.** `catia_close_sketch` takes an optional
`structured=true` and answers `data.breaks[]` — every loose endpoint pair with
its gap in mm — because the sentence carrying them was truncated by clients
(1242 chars on a six-arc outline in the 2026-11-06 report). The default is still
the unchanged text reply, so no caller has to change. `data.breaks.closed` is
`true`, `false`, or `null` (no coordinates were recorded in this session — a
sketch reopened from disk, which is not a closed sketch). Measured on B30:
14/14, the 0.0002 mm break came back intact and the pad still gives 4000.0 mm³.

**0.3.75: a non-ASCII file name is refused in both directions.** `catia_save_as_copy`
can write `副本.CATPart`, but `Documents.Open` cannot read it — so the
"save ASCII, rename afterwards" advice this package had given since 0.3.71
produced files CATIA will never open. It is gone from all five places; the reply
now says keep the file name ASCII (the folder may be Chinese) and put the
Chinese wording in the part number. `catia_open_document` refuses such a name
before CATIA sees it, because the failed Open leaves a modal incident-report
window on the automation thread.

**0.3.74: "already open - activated it" was a guess.** `catia_open_document`
called `Activate()` and reported success. `Activate()` also returns for a document
with **no window of its own** — a part edited in context as a component of an open
product — and leaves `ActiveDocument` where it was, so the next tool answered
"ActiveDocument 'Drawing1.CATDrawing' exposes no 'Part'" while
`catia_activate_document` refused the very same part. Both tools now share one
read-back, and the advice they print says what works (close the product, or
measure by `document=`) instead of pointing at the call that cannot help.

The frame sketch's datum plane is no longer a white diamond over the solid: it
gets a free name, is hidden when created, and `catia_set_visibility` can find it
afterwards — the by-name walk used to skip `body.HybridShapes`, where these
features live, and every frame plane was called `Datum_frame` (the reporter's P04
holds four). `set_visibility` sets every node answering to the name and says how
many others there were. A `CATDrawing` also stopped being typed `Other` in
`catia_list_documents`, and the SaveAs message stopped blaming the folder: only
the **file name** matters, measured in all four combinations.

**0.3.73 closed the last two items of the 2026-11-06 batch.** `matches_request`
was `deviation < 1e-6`, which is a bit-equality test: a placement built from
4-decimal unit vectors deviates 1e-5…1e-4 **by construction**, so 13 of 25 correct
placements answered `ok: true` beside `matches_request: false`. It is a tolerance
now (`tolerance`, default 1 micron, published in the reply, inherited by every
entry of a batch), and a tighter value still reports the same deviation honestly.

`catia_set_visibility` answered `PRECONDITION_FAILED` on a Product — "exposes no
'Part'" — so cleaning 11 parts before a screenshot took 33 calls. It walks the
component tree now (sub-assemblies included, one entry per part) and applies the
change through each part's own `Selection`, so the assembly stays in front; the
reply says how many of how many parts it touched.

**0.3.72 made the assembly envelope usable, and made its cache arrive in time.**
`method='mesh'` on a Product has worked since 0.3.45; the schema entry for
`document=` still said it was refused, so a 2026-11-06 report read the text and
concluded there was no official way to get a precise assembly envelope. The text
is fixed and a test holds it to the behaviour.

The reply also stopped being two answers at once: `complete: true` means every
instance got a box, `bounds` said the boxes came from a vertex sweep — true, and
unreadable together. `exact` and `bounds_code` (`mesh_chord` / `vertex_sweep` /
`mixed` / `complete_failed` / `empty`) now say how the numbers can be short. On
the acceptance product `auto` returns x max 205 against a true 220 and says
`exact: false` rather than leaving the caller to reconcile prose.

The per-part mesh cache had existed since 0.3.32 and saved nothing, because the
lookup ran after `part.Update()` and `_edited_in_context` — 4.2 s of window
switching (Activate 2.4 s + restore 1.8 s) around a 10 ms export, paid again on
a call that then answered from the cache in 0.6 ms. It is now consulted before
any COM call and gated on `Document.Saved`: a clean part is answered from its
file in ~16 ms, a part with unsaved edits is measured and explicitly not cached.
Same product, reopened from disk: **32 938 ms → 184 ms**.

**0.3.71 closed the last two exemptions and fixed four replies that lied.** Nine
lookups raised inside an `except` still answered `COM_ERROR` / `execute` /
`modified:null` — including "No active document in CATIA", which is every tool
call of a cold session — and seven `CodedError`s carried `DOCUMENT_NOT_FOUND` /
`SKETCH_NOT_FOUND` at the execute stage for a typo. Both are validate now, and two
static tests hold the line: `except`-block raises that print a list to choose from
must carry a code, and no validate-family code may arrive without
`stage="validate"`.

The other four were replies stating a cause the code never measured: `seam_hint`
was popped for a surface patch and added straight back two lines later (so a reply
carried "cross_wall is NOT reported" beside "38 creases run THROUGH the wall");
`catia_close_sketch` called a plate with a hole self-intersecting because it
compared bounding boxes; a threaded hole that failed the update left a zombie
feature behind and reported `modified:null`; and `document=` spent 790 ms
computing, then explaining, a tensor that route cannot return (11 ms with one
document open). Saving to a non-ASCII path no longer hides inside a successful
reply — `catia_new_part` / `catia_new_product` raise `SAVE_FAILED` with
`modified:true` instead of letting `catia_run_plan` mark the step done.

**0.3.70 finished it in the core layer.** The same scan over the package rather
than its tools directory found eleven more, and they were the ones a caller can
do nothing with: "Duplicate tool: X", "unknown guarded operation", "route= is not
one of the 7 measuring routes", the "unreachable" branch of the topology
enumerator. Reported as `COM_ERROR` they send someone to inspect a model for a bug
that lives here. They carry `INTERNAL_ERROR` and a fourth stage, `internal`, whose
next action is "report the tool and this message" - no recovery block, because
there is nothing to repair.

Two more were states, not failures: "the active document exposes no 'Part'" and
"the active body has no solid shape yet" are `PRECONDITION_FAILED`, and
`dialogs.save_as`/`save` — six attempts around a modal dialog, then a real failure
— is `SAVE_FAILED` at the execute stage. The structural guard now walks
`catia_mcp/**`, so a core-layer `raise RuntimeError` fails CI the way a tool's
does. Zero left.

**0.3.69 finished that sweep: 169 raise sites, no register left.** 0.3.68
converted twenty and registered the other 81 in a JSON baseline; 0.3.69
converted all of them, and the 88 more that the old keyword scan could never
see. The count went **up** because the measurement was wrong, not the work:
"Pass either 'points' or 'element', not both" and "No active sketch" are
refusals too, and they contain none of the words ("must", "unknown", "requires")
the scan matched. Every one of the 169 now answers `ok:false` with
`failure_stage`, `modified`, and what to fix.

Three classes, because the fix differs: `ArgumentRejected` (a typo - change the
value), `PreconditionRejected` (the model is not in that state - build or open
what the message names, and changing the argument will not help), and
`ArgumentOutOfRange` (enumerate first). A failure that really is CATIA's answer
keeps `stage:"execute"` and gets its own code instead of `COM_ERROR`:
`NO_VISIBLE_GEOMETRY`, `COM_RESULT_UNEXPECTED`, `FEATURE_INCOMPLETE`,
`ACTIVATION_FAILED`, `COMPONENT_NOT_ADDED`, `CLOSE_NOT_CONFIRMED`. All three
refusal classes subclass `ValueError` as well as `RuntimeError`, so the handlers
written against the old types keep working.

The guard is structural now, in `tests/test_argument_envelopes.py`: a raise
inside an `except` re-wraps a CATIA failure (execute stage); a raise anywhere
else is a decision the tool made, and a decision carries a code. There is no
baseline to add to, because there is nothing left to register.

One thing this release found the hard way: `sketcher` and `document` raised
`PreconditionRejected` before importing it, and the caller saw `COM_ERROR` /
`execute` with the message `'PreconditionRejected' is not defined` - a `NameError`
wearing an envelope. pytest was green throughout, because no test walked those
two paths; the B30 acceptance script found it. `test_every_class_a_tool_raises_
is_reachable_from_that_module` now parses the package and fails on any raise whose
class is not imported in that module.

**0.3.68 closed the refusal-envelope gap the new modules reopened.** 0.3.43 gave
every pre-execution refusal one shape (`ok:false`, `failure_stage:"validate"`,
`modified:false`, no `recovery`, next actions) by converting fifteen raise sites
by hand — and missed the very call its own table used as the example. Knowledge
(0.3.63) and Drafting (0.3.64–0.3.66) then started from the old shape again.
Thirteen sites in Drafting, five in Part Design, one in Document and the
knowledge parameter path now raise `ArgumentRejected`; what schema can express
(`language` enum, `exclusiveMinimum` on margins and block size) moved there.

The guard is `tests/test_argument_envelopes.py`: an offline matrix asserting the
envelope for every refusal the fix touched, plus a static scan that fails CI when
a **new** bare raise starts talking about an argument. The 81 sites it could not
reach were registered in `tests/argument_rejection_baseline.json` as the honest
remaining count — a register 0.3.69 emptied and deleted (see above); the scan
matches structure now, not wording.

A server-layer inference ("a bare ValueError means a validate-stage rejection")
was tried first and **reverted within the hour**: `test_server_runtime` raises
`ValueError("Wrong sketch")` as its ordinary-failure stand-in, and the inference
reported that as `modified: false`. The envelope is declared by the raise site,
not guessed at the boundary.

**0.3.66 made the sheet furniture Chinese by default.** The English was never
CATIA: the labels were hard-coded and so were the default view names. Field
labels are now 材料 / 比例 / 制图 / 日期 / 审核 / 张次 and views default to 主视图,
右视图, 俯视图, 剖视图, 局部视图 - a view's `Name` is printed on the sheet, so this
is visible output. `language: "en"` restores the ISO wording, and
`labels: {"checked": "校对"}` changes a single field. Chinese view names work as
handles (`parent_view: "主视图"` resolves on B30).

The odd glyphs (`1` looking like `н`, so `1:1` reads as `1|1`) are CATIA's ISO
single-stroke font `SICH`, not a translation problem. Both `SetFontName` and
`TextProperties.FontName` accept `宋体`/`FangSong`/`SimHei` and read them back,
but the exported PDF renders all of them in SICH - four lines set to four fonts
came out identical. Changing it means changing the Drafting standard in CATIA.

**0.3.65 added the frame** (`catia_drawing_set_sheet`, `catia_drawing_add_frame`).
Three things had to be measured rather than assumed:

| What | What CATIA actually does |
|---|---|
| A new drawing's paper | It is `catPaperUser` (13), not A3 - an oversized custom sheet. `GetPaperWidth` **raises** until a real ISO size is set, so a frame drawn first lands on an unmeasured page |
| Paper values | `CatPaperSize` names Letter/Legal/A..F too, and **every one of them raises** on this install (values 0, 1, 7-12). Only A0=2 … A4=6 and User=13 write. `bestfit` orientation is accepted and silently becomes landscape |
| `Tables.Add(x, y)` | Anchors the **TOP**-left corner and grows **downward**. Passing the bottom margin drew the whole title block below the sheet and still printed a position |

The title block is a real table - 8 columns × 5 rows merged into title /
drawing number / material / scale / drawn / date / checked / sheet / footer - so
it stays editable in CATIA: `SetCellString`, `SetCellAlignment`, `MergeCells`,
`SetRowSize` all confirmed. Border margins are ISO 5457 (20 mm filing margin,
10 mm elsewhere). **Zone markings and the ISO corner grid are not drawn**, and
the tool does not move views - it reports the drawing area (`x 20..410`) so the
caller can place them; one view landed on the border when it was not.

\* `catia_get_bounding_box` measures two ways, and `auto` takes the STL mesh:
exact on planar-faced solids, a **lower bound ~0.5-0.7 % small** on curved ones.
`method="vertices"` is the cheap sweep, exact only when every face is planar (a
revolved face has no vertex at its extremum, which is how an `auto` box once came
out 37.5 % small). On an assembly the two routes differ, and it matters:
`auto` / `vertices` read each component's **vertices** and fall back to a mesh only
for a part with no vertices at all, so a part whose extremum sits on a curved face
is under-reported there — measured 2.392 mm and 8.99 mm on a 20-part bracket, in
the two directions its acceptance was being checked in. `method="mesh"` takes
**every part's mesh**, one per distinct part rather than per instance, which is
what you want when a criterion is measuring an envelope: the reply says which
route ran per part (`source: "mesh"`) and why (`mesh_reason`), and the counters
`part_boxes_by_mesh` + `part_boxes_by_vertices` add up to `part_boxes`.

Per-part `document="<part>.CATPart", method="mesh"` still works for one part at a
time — the part is opened by path if it exists only as a component, and exported
from a temporary copy when CATIA refuses to export it while it is edited in
context (that refusal is a modal dialog, and reads as a bare `[WinError 2]`).
Place the result with `catia_get_component_transform`.

## Known limits

* **A curve projected onto a CURVED surface cannot split or fill it — but it CAN
  engrave it.** Measured on B30 2026-11-10, three ways: `catia_gsd_split(element=<the
  extracted bottle skin>, cutter=<projected curve>)` fails, the same call with the
  projection's own extract fails, and a projected CIRCLE fails identically;
  `catia_gsd_fill(boundaries=[<that curve>])` answers `FILL_NOT_CLOSED`; and
  `catia_gsd_thick_surface` on the skin fails at every thickness tried (0.4/0,
  0.2/0.2, 0.1/0.1). The same closed curve on a PLANE patch splits cleanly
  (1486.903 mm² kept), so this is the curved case, not the curve. Note also that
  `gsd_split`'s "closest approach" number is a MINIMUM - it is 0.0000 mm for a
  curve that merely touches the surface once, so it cannot tell you whether the
  contour follows it.

  **The route that does work is `catia_surface_engrave`** (0.3.98): a plane normal
  to the curve, a circle of radius = depth on it, a sweep of that circle along the
  curve, `CloseSurface`, and `boolean(remove)`. Because the tube is CENTRED on a
  curve that lies on the surface, half of it is inside the material, so the groove
  depth is the radius — measured along the surface normal everywhere. Numbers from
  B30: a r=4 trace on a r=40 cylinder gave a 5.9657 mm³ tube (2π²Rr² = 5.97) and
  removed 2.974 mm³, which is the half. The curve has to lie on the body — the
  tool cannot arrange that — and it has to carry a point-on-curve parameter:

  | guide | works? | measured 2026-11-11, one fresh document per case |
  |---|---|---|
  | `catia_gsd_line`, or its extract | **yes** | 50 mm segment on a generator, 0.5 mm deep, removed 19.5829 mm³ (predicted 19.635) |
  | an OPEN `catia_gsd_spline` | **yes** | 65.682 mm, removed 25.6436 mm³ (predicted 25.793) |
  | a SKETCH outline projected onto the face | **yes** | the route this tool is built for |
  | `catia_gsd_circle`, or its extract | no | `ENGRAVE_CURVE_UNPARAMETRISABLE` |
  | a CLOSED `catia_gsd_spline` | no | same |
  | the projection of any of those | no | a closed spline's projection refuses too |

  **An OPEN guide is fine** — this used to say the opposite, and that was wrong:
  `CloseSurface` closes the two ends of the swept pipe itself. What refuses is a
  CLOSED curve, because the route needs a point-on-curve and a closed curve will
  not carry one. Note also that "it came from `catia_gsd_project`" is NOT the
  test — 0.3.100's `next_actions` said it was and sent a reporter down a branch
  that fails.

  **Reopening is NOT a reason to keep the calls in one session.** 0.3.101 said it
  was, from one observation: on a lofted r=40 cylinder closed into a solid, a
  projected r=6 circle engraved 0.6 mm deep removed 9.991 mm³, and after
  save/close/reopen the SAME projection (still 17.7831 mm long, still 0.0000 mm
  from the body) removed nothing. Re-measured for 0.3.105 — five cells, two
  geometries, a fresh document each time: a 100×100×20 pad with a line on its top
  face gave **376.9911 mm³ before and 376.9911 mm³ after** the reopen; the lofted
  bottle with a projected 32.111 mm glyph gave **7.9958 mm³ before and
  7.9958 mm³ after**. `part.Update()` first changed nothing either. So the advice
  is withdrawn, `NO_MATERIAL` on a reopened file means what it says, and the
  observation is recorded as *seen once, not reproduced* rather than explained.

* **The first projection in a document answers with a fragment; the second
  answers with the real curve.** Measured on B30 2026-11-11 with eight
  projections in a row in one document, same source and target: a sketch outline
  gave **2.200 mm** and then **32.111 mm** seven times. `catia_gsd_project`
  therefore projects twice and keeps the second, and both numbers appear in the
  reply. The case that makes this a rule rather than a quirk of one bottle: a
  20×20 rectangle onto a plane tilted 45° gave **20.000 mm** on the first pass and
  **68.284 mm** on the second — and 68.284 is exactly `2·(20 + 20·cos45)`. A
  straight cylinder is the one shape where both passes agree (17.783 mm), so the
  second pass has never measured less than the first.

  **What has actually been projected, and onto what** (B30, 2026-11-11 unless
  noted). This table exists because the advice in this file was twice written from
  memory instead of from a measurement — "extract the face and project onto that"
  was offered as a fix for a failure it does not fix, and "move the element closer
  with `catia_gsd_translate`" makes the result worse. Read a row before advising
  anyone:

  | element | support | landed | carries a point-on-curve parameter |
  |---|---|---|---|
  | planar sketch | `xy` / `yz` / `zx`, or a solid's flat face | refused — parallel or perpendicular is degenerate | — |
  | 80 mm rectangle | plane tilted 45° (`catia_gsd_plane_angle`) | 20.000 mm, then **68.284 mm** = `2·(20+20·cos45)` | yes |
  | 63.947 mm glyph outline | lofted bottle face | **4.000 mm (6.3 %)** before 0.3.102, **32.111 mm (48.2 %)** after | yes |
  | 26.164 mm glyph | padded cylinder, SOLID face `Face.3` | 13.6 % | yes |
  | 26.164 mm glyph | curve extracted from that same face | 13.6 % — extracting changes nothing | yes |
  | 26.164 mm glyph | lofted bottle, sketch on `zx` offset −40 | 16.0 % | yes |
  | 26.164 mm glyph | lofted bottle, sketch on a `frame` plane | 15.1 % | yes |
  | `catia_gsd_line`, its extract, an OPEN spline | curved face | lands | yes |
  | a CLOSED spline | curved face | lands | **no** — and neither does its projection |
  | `catia_gsd_circle`, its extract | curved face | lands | **no** — same |

  The practical reading: **a curved support always accepts the projection and
  always keeps little of it** (13–48 %), so a glyph that has to be legible on a
  curved face must be COMPUTED on that surface — take the 3D points from the
  surface's own equation and sweep an OPEN spline through them — not projected
  onto it. A solid face needs no `catia_gsd_extract` first; the "surface" the tool
  wants is whatever `_ref` can resolve, and a `Face.N` resolves.

  **Do not move the element with `catia_gsd_translate` before projecting.** A
  translated copy of that sketch landed **3.5 %** where the sketch itself landed
  **48.2 %**, and it stayed at 3.5 % through eight tries; it only becomes usable
  once the sketch it came from has been projected (44.9 %). 0.3.100's low-share
  warning suggested exactly that translation and was measured to make the curve
  worse. Why CATIA answers differently the first time is unknown and is not
  claimed anywhere in the code.

* **A planar element only projects onto a support that crosses it at an angle.**
  Measured on B30 2026-11-10 with a 40×40 rectangle on an XY plane offset 40 mm:
  `support="xy"` (parallel, collapses to a point), `"yz"` and `"zx"`
  (perpendicular, collapses to a line) and the block's own top face all fail; a
  plane tilted 45° about X succeeds. CATIA answers every one of them with the same
  `方法 UpdateObject 失败`, which is why `catia_gsd_project` now refuses with
  `PROJECT_FAILED` and states the rule plus the measured distance. A NON-planar
  support (an extracted curved face) is fine — the degenerate cases are
  plane-to-plane.

* **A measurable taken on a hybrid surface measures the enclosing SOLID.**
  Measured on B30 2026-11-10: `SPAWorkbench.GetMeasurable(<reference to a hybrid
  shape>)` answers `Area` = 24000 mm² for a 100×100 face of a 100×100×10 block,
  and its COG is the block's (0, 0, 5) — not the face's. So "compare the centroid
  before and after" measures the same body twice, and `catia_gsd_offset_surface`
  cannot name inside/outside that way. It reports the flag it used, the measured
  gap (0.4000 mm for a 0.4 mm offset, both directions) and the route that does
  answer it: cut toward the surface and read the volume.

* **A projection can keep a sixth of the element and still report success — it
  now reports the number.** `AddNewProject` runs along the support's own normal,
  so on a curved support part of the element can miss it entirely. Measured on
  the 2026-11-10 bottle part (a copy, never saved): a 63.947 mm glyph outline
  onto a lofted body face gave a **4.000 mm** curve — 6.3 %, one segment of seven.
  `catia_measure_distance` then answered `0.0000 mm`, which is a MINIMUM and only
  says the fragment touches; `catia_surface_engrave` swept a 2.0106 mm3 tube along
  those 4 mm and removed nothing, correctly. The reply now begins with
  `WARNING: ONLY PART OF THE ELEMENT LANDED` below a 25 % share, and always prints
  the projected length against the source's. 25 % and not 50 % because a CLOSED
  curve on a round support legitimately keeps about half (measured: 47.2 % on the
  0.3.98 acceptance cylinder, which engraves fine).
  The other routes to a curve that lies on such a face do not reach the chain
  either, all measured the same day: an **intersection** is a real curve on the
  face (37.699 mm, 0.0000 mm) but all four point-on-curve overloads refuse it, and
  `AddNewSweepCircle` + `SetRadius(1, r)` — a tube with no profile at all — is
  accepted and never updates.

* **A projection can keep a sixth of the element and still report success — it
  now reports the number.** `AddNewProject` runs along the support's own normal,
  so on a curved support part of the element can miss it entirely. Measured on
  the 2026-11-10 bottle part (a copy, never saved): a 63.947 mm glyph outline
  onto a lofted body face gave a **4.000 mm** curve — 6.3 %, one segment of seven.
  `catia_measure_distance` then answered `0.0000 mm`, which is a MINIMUM and only
  says the fragment touches; `catia_surface_engrave` swept a 2.0106 mm3 tube along
  those 4 mm and removed nothing, correctly. The reply now begins with
  `WARNING: ONLY PART OF THE ELEMENT LANDED` below a 25 % share, and always prints
  the projected length against the source's. 25 % and not 50 % because a CLOSED
  curve on a round support legitimately keeps about half (measured: 47.2 % on the
  0.3.98 acceptance cylinder, which engraves fine).
  The other routes to a curve that lies on such a face do not reach the chain
  either, all measured the same day: an **intersection** is a real curve on the
  face (37.699 mm, 0.0000 mm) but all four point-on-curve overloads refuse it, and
  `AddNewSweepCircle` + `SetRadius(1, r)` — a tube with no profile at all — is
  accepted and never updates.

* **A loft's end conditions are a support, not a strength, and a datum plane is
  one.** `HybridShapeLoft` exposes 78 members on B30 (read from its own type
  information, 2026-11-10) and not one of them carries a tension, a factor, a
  weight or a per-guide influence — so a tutorial's "Normal to Profile, strength
  1.5" and "To Next Guide" cannot be set. What can: `start_tangent` /
  `end_tangent`, and a PLANE is a legal support, which the schema used to
  understate. Measured: two circles r=10 at z=0 and r=14 at z=40 loft to
  3 030.971 mm² with no condition, 2 869.025 mm² with `start_tangent='xy'` and
  3 202.807 mm² with `end_tangent='xy'`. Read that literally — tangency TO the
  plane, so the surface leaves flat; "normal to profile" asks for the opposite
  direction. `catia_gsd_multi_section_surface` accepts the three tutorial names as
  aliases and refuses each with these numbers (`NO_TANGENT_FACTOR`,
  `NO_NORMAL_TO_PROFILE`, `NO_GUIDE_INFLUENCE`) instead of leaving JSON Schema to
  answer for the API.

* **A sketch has no readable coordinates, so it cannot be read back.** `Point2D`
  exposes no `X`/`Y` through automation and a sketch's own frame cannot be read
  either (2026-11-10). Consequences, stated rather than hidden:
  `catia_sketch_copy` replays the coordinates this session recorded when it drew
  the sketch, so a sketch that came from disk, or one whose constraints solved to
  other positions, cannot be copied faithfully — the reply says which case it is.
  Ask CATIA about the built geometry instead: `catia_measure_distance`,
  `catia_list_edges`, `catia_get_bounding_box`, `catia_shape_audit`.

* **A mesh measurement is about what is VISIBLE, which is a choice the tool has to
  make for you.** `catia_check_surface` and `catia_shape_audit` now hide the
  geometrical sets, keep visible whatever carries the solid, and restore every
  flag afterwards — so the numbers describe the SOLID. Measured on B30
  2026-11-10: the same solid read 108 triangles and 0 non-manifold edges on its
  own, and 540 triangles with 162 non-manifold edges with its construction
  surfaces on screen. `include_construction=true` gives the second reading back,
  and `scope` says which one you got. The screenshot side of the same problem:
  `catia_screenshot(clean=true)` does the hiding, but the specification TREE
  cannot be hidden from automation — `Viewer3D` has 58 members, no `HideTree`,
  and `Width`/`Height` are read-only, so the picture is the 3D window's client
  area whatever size was asked for. Press F3 in the UI for a report image.

* **A loft's end conditions are a support, not a strength, and a datum plane is
  one.** `HybridShapeLoft` exposes 78 members on B30 (read from its own type
  information, 2026-11-10) and not one of them carries a tension, a factor, a
  weight or a per-guide influence — so a tutorial's "Normal to Profile, strength
  1.5" and "To Next Guide" cannot be set. What can: `start_tangent` /
  `end_tangent`, and a PLANE is a legal support, which the schema used to
  understate. Measured: two circles r=10 at z=0 and r=14 at z=40 loft to
  3 030.971 mm² with no condition, 2 869.025 mm² with `start_tangent='xy'` and
  3 202.807 mm² with `end_tangent='xy'`. Read that literally — tangency TO the
  plane, so the surface leaves flat; "normal to profile" asks for the opposite
  direction. `catia_gsd_multi_section_surface` accepts the three tutorial names as
  aliases and refuses each with these numbers (`NO_TANGENT_FACTOR`,
  `NO_NORMAL_TO_PROFILE`, `NO_GUIDE_INFLUENCE`) instead of leaving JSON Schema to
  answer for the API.

* **A sketch has no readable coordinates, so it cannot be read back.** `Point2D`
  exposes no `X`/`Y` through automation and a sketch's own frame cannot be read
  either (2026-11-10). Consequences, stated rather than hidden:
  `catia_sketch_copy` replays the coordinates this session recorded when it drew
  the sketch, so a sketch that came from disk, or one whose constraints solved to
  other positions, cannot be copied faithfully — the reply says which case it is.
  Ask CATIA about the built geometry instead: `catia_measure_distance`,
  `catia_list_edges`, `catia_get_bounding_box`, `catia_shape_audit`.

* **A mesh measurement is about what is VISIBLE, which is a choice the tool has to
  make for you.** `catia_check_surface` and `catia_shape_audit` now hide the
  geometrical sets, keep visible whatever carries the solid, and restore every
  flag afterwards — so the numbers describe the SOLID. Measured on B30
  2026-11-10: the same solid read 108 triangles and 0 non-manifold edges on its
  own, and 540 triangles with 162 non-manifold edges with its construction
  surfaces on screen. `include_construction=true` gives the second reading back,
  and `scope` says which one you got. The screenshot side of the same problem:
  `catia_screenshot(clean=true)` does the hiding, but the specification TREE
  cannot be hidden from automation — `Viewer3D` has 58 members, no `HideTree`,
  and `Width`/`Height` are read-only, so the picture is the 3D window's client
  area whatever size was asked for. Press F3 in the UI for a report image.

* **`AddNewSewSurface` is declared, accepts the call, and never updates.** Same
  shape of failure as the GSD pattern above, measured on B30 2026-11-10 before a
  tool was written for it: `ShapeFactory.AddNewSewSurface(iSewingElement,
  iSewingSide)` returns a feature that appears in the tree, and `part.Update()`
  then fails in **every** geometry tried — a 400×400 mm planar wall and a 40×200
  mm patch against a 100×100×10 block, a wall standing on the top face, a cap
  hanging over it, and a datum plane, with `iSewingSide` as `1`/`0` and as
  `True`/`False`. Eight attempts, three topologies, both argument types. So the
  engraving route a 2026-11-10 report asked for does not go through Sew Surface on
  this build; the two that do work are `catia_split_solid` (a cutter that divides
  the body completely) and a solid tool body through `catia_boolean(remove)`.
  `catia_diagnose` publishes these conclusions as `capability_ceilings` — one
  status per member (`UNSUPPORTED_ON_B30`, `NOT_IN_TYPELIB`,
  `DECLARED_NOT_MEASURED`, `AVAILABLE_WITH_LIMIT`) — so "is this declared or does
  it work" is a lookup instead of a report.

* **There is no text in a V5 sketch.** Not a missing feature of this fork: the
  `Factory2D` a sketch hands back declares 37 members and none of them creates
  text (`CreatePoint`, `CreateLine`, `CreateCircle`, `CreateSpline`,
  `CreateEllipse`, `CreateParabola`, …). Letter outlines have to arrive as
  geometry — a DXF import, or curves drawn elsewhere and projected — which is why
  "engrave a label" is a curve-projection problem here and not a text problem. A
  report in 2026-11-10 described this as a font gap; it is an object-model gap.

* **A single-feature GSD pattern is declared and not callable here.** The member
  exists — `ShapeFactory.AddNewGSDCircPattern`, 14 arguments, and it returns an
  object — but `part.UpdateObject` refuses that object in every shape tried on
  B30 2026-11-09: `iType` 0/1/2, `completeCrown` on and off, a GSD surface and a
  Part Design pad as the source, with the body set as the in-work object.
  `catia_gsd_circ_pattern` therefore builds count−1 rotations plus a join, which
  is why editing `count` means calling again and why a join can refuse when the
  copies share no edge. Two versions of this project stated that wrongly in
  opposite directions before this was measured: 0.3.81's report said the member
  was on `HybridShapeFactory` (it is not — that factory has 160 members, 131 of
  them `AddNew*`, none with "Pattern"), and 0.3.82's description concluded from
  that it was declared nowhere. `catia_diagnose` now prints both factories' own
  method tables, so the next question of this kind is a lookup.

* **Capping a hole in a surface: use the hole's own edge, not a circle.** One
  `catia_gsd_boundary` curve carries every loop, so filling it rebuilds the whole
  face over the original and the duplicate then breaks a thickening (measured
  2026-11-09 on a five-lobe base: 1714 edges shared by more than two triangles;
  on a cone the same call gave 1570.796 mm² = π·400 + π·100, one flat face per
  loop stacked, not the lateral area). Three routes exist and they differ by
  geometry:

  * `catia_gsd_boundary(edge=N)` returns ONE loop — 62.832 and 125.664 mm on a
    cone's two circles, against 188.496 for both — and a fill on it covers only
    that loop (314.159 / 1256.637 mm² measured). This works on a single feature.
    On a JOIN result (an array) all 30 border edges of a five-patch base failed
    at update, so pick the loop BEFORE the join. It also refuses on some SINGLE
    features: a four-sided flat fill and a five-section loft both failed at
    `UpdateObject` (2026-11-09), so "it is not a join" does not mean it will work
    — the reply names both possibilities rather than guessing.
  * `catia_gsd_circ_pattern(cap_center=true, cap_curve=<the hole's edge on the
    source piece>)` closes the centre in the same call: that curve is rotated
    count−1 times, the copies joined into the hole's border, filled, and joined
    onto the base. Measured 2080.436 + 59.441 = 2139.877 mm² on a five-lobe base
    — exact, because the cap shares the hole's edge by construction. ON A LOFT
    THE CURVE HAS TO BE ONE OF THE GUIDES, with the sections' corresponding
    points on it: a loft's inner border is interpolated, not an element, and a
    curve that merely touches it (0.0000 mm apart) is not the same edge, so the
    cap is refused even though the geometry looks right. That is how the
    2026-11-09 tutorial build closed its centre after two failures that read
    like parameter errors.
  * `catia_gsd_split` with a circle, then fill, then join — 1600.000 → 1486.903 →
    113.097 → 1600.000 mm², thickness 0.5 → 800.0 mm³. Works, but a circle has to
    be big enough to reach the border: a five-sided hole's nearest point to its
    axis is r·cos36 (4.045 for r=5), so a circle at the "boundary distance" lies
    wholly inside the hole and cuts nothing (measured 2026-11-09). Splitting
    twice on the same element does not stack: do both cuts from the original.
    **And the patch in that measurement is a PLANE.** On a curved surface a planar
    circle crosses the surface without lying on it, which cuts nothing: measured
    2026-11-09, a circle 3 mm above a patch and a circle on a 45° support both
    failed. Since 0.3.89 the call measures the closest approach first
    (`SPLIT_CUTTER_MISSES`, with the distance) and reports it after a refusal at
    update too (`SPLIT_FAILED`, `modified: false`, both candidate causes named).
    Where the centre is not flat, cap it with the hole's own edge instead — that
    is the only cap this build can make, so a five-sided hole gets a five-sided cap.

  `catia_gsd_fill` measures what it built (0.3.87). Before that, an open
  boundary answered "Fill surface created" while the `Edge.N` route was refused
  for not being closed — two routes, two answers, and the silent one left a
  zero-area face that only surfaced later as non-manifold edges when the result
  was thickened. The area is now published ("Its area measures 100.0000 mm2,
  which is what says the boundary really closed a region"), and below 0.001 mm²
  the fill is deleted again and the call raises `FILL_DEGENERATE` with the
  number: `catia_gsd_boundary(edge=N)` can return ONE SEGMENT of a border
  (25.0000 mm of a 91.1450 mm four-sided border), and filling a segment does not
  fail on this build — it makes that face.

* **A sketch frame cannot be READ back — but a rotated plane's can be
  reconstructed and checked.** `Sketch.GetAbsoluteAxisData` takes an out-array
  pywin32's late binding cannot deliver (the same wall as `Measurable.GetPoint`),
  and so do `HybridShapePlaneAngle`'s own `GetOrigin` / `GetFirstAxis` /
  `GetSecondAxis` / `GetPosition` — measured 2026-11-09 on a 44-member typeinfo
  dump, every call answers 「方法 … 失败」. So a plane built by
  `catia_gsd_plane_angle` in THIS session records what it was built from, and a
  sketch on it gets a frame reconstructed from that record (the reference plane's
  frame rotated about the axis by the angle). Because that is a reconstruction
  and not a reading, one corner is placed as a real point element and its
  distance to the sketch is measured: 0.0000 mm on B30, i.e. the frame matches
  CATIA — and a mismatch refuses with `SKETCH_FRAME_MISMATCH` and the gap. What
  still has no frame: a plane that came from disk, one rotated about a NAMED
  element, and any sketch on a FACE. For those, check the placement
  geometrically — `catia_measure_distance` between a copy and its original is the
  chord between the two supports, 2r·sin(A/2) for a copy on a plane rotated by A
  degrees (measured 7.0534 mm against 7.053 for r=6 at 72°) — or pass the
  corners explicitly as `points`.

* **Hiding a geometrical set does not hide the solid — measured twice.** One pad
  in the body and one surface in the set: STL triangle count 63 with the solid
  alone, 73 with the surface added, 73 after hiding the construction geometry,
  63 after hiding the set. It lands exactly on "solid alone". Two reports saw an
  empty 3D view after hiding a set; the likeliest explanation is a 0.5 mm plate
  seen edge-on. If the view does go empty, what you were looking at is an element
  INSIDE that set — show the set again (`show=true` on the same name), not
  `零件几何体`, which holds none of it.

* **A surface fillet only where the two surfaces form a plain corner, and a
  spine only from two or more sections.** 0.3.53 wrote that neither could be
  driven; 0.3.56 measured both and that was wrong twice over, in the same way -
  a declared call was tried in one arrangement and its failure read as a
  property of the object. `catia_gsd_fillet_surface` drives
  `AddNewFilletBiTangent` across the four orientation pairs and keeps the
  survivor - always (o1 = −1, o2 = +1) on the corners measured, whichever
  direction the supports were built in. That pair builds a true
  rolling-ball fillet (every mesh vertex at exactly r from the axis, area
  `(π − θ)·r·L` to 0.001 % on 60/90/120° corners), but it refuses when
  none survives - two surfaces that do not share an edge, which is what the two
  disjoint cylinders were - and it refuses when more than one does, because those
  are different surfaces (a disc with a cylinder standing on it gave 573.4 and
  391.3 mm²). `catia_gsd_spine` drives `AddNewSpine` with
  `AddSection`/`AddGuide`, which the typelib has always declared: 40 members, and the 0.3.53 probe had only looked
  at `dir(spine)`. It still needs two sections (`UpdateObject` fails on one) and
  its curve follows the sections' NORMALS, not their centres. Where neither
  applies, round the edge in the geometry you build (an arc in the profile, as
  the five-petal bottle base ended up doing with its R3), or fillet a SOLID edge
  with `catia_fillet` if the wall is thick enough for the radius. **Read
  `typeinfo` as "declared", never as "usable"** - that is how `AddNewHealing`
  cost a release (declared with one argument, and unusable until `AddBodyToHeal`
  was called) - **but read it properly before calling something unusable**: a
  40-member interface is not absent because `dir()` showed nothing.
* **A sliver along a seam reads as a fold, and `cross_wall` is how to tell.**
  On a thin wall the mesh uses its largest triangles where curvature is near
  zero, and one of them can bridge the two skins; its normal sits between them,
  so `catia_check_surface` reports 100 deg+ on a surface that is continuous.
  Those edges are now split out as `fold_deg_by_side.cross_wall` (a subset of
  `mixed`: the edge itself runs from the outer skin to the inner one), each
  `worst_creases` entry carries `cross_wall`, and `seam_hint` names the radius
  band and says what to do. Measured on a synthesised 0.3 mm tube: 0 cross-wall
  edges on a clean wall - its 64 rim folds at 90.0 deg stay `mixed` and nothing
  else - and exactly 1 (169.98 deg) once one triangle was made to bridge. On an
  OPEN patch (`boundary_edges > 0`) the split means nothing - one skin, and a
  border can look like a wall crossing - so `cross_wall` is withheld there and
  `skin_note` says why: a 2026-11-04 report had a surface-only model flagged at
  its skirt's top edge (r=33, z=110) with a hint to exclude r=33, which on a
  66 mm bottle excludes everything. The real case: a five-petal base whose seam fell on the near-flat bottom of its R3
  foot read 124.05 deg at r=25 and nothing above 47.59 deg anywhere else, and
  moving the seam off that radius took the region to 27.42 deg. Keep seams in
  curved regions on thin walls.
* **A 180 deg dihedral is not a bend, and a duplicated sheet is where it comes
  from.** The curvature estimate divides a length by an angle, so an edge whose
  two triangles face opposite ways reports a radius of "facet size / pi" - which
  is how a surface was told its tightest radius was 0.034 mm at the open top
  border of a skirt, with "widen that feature" attached, where the split was
  about to cut the border away and there was nothing to widen (reported
  2026-11-04). Measuring the mechanism first mattered: a plain open patch does
  NOT read 180 deg at its border (a 30 mm open tube's worst fold is 22.50 deg,
  its own chording; a flat patch's is 0.00), so "the surface ends here" was not
  the cause. Two COINCIDENT patches with opposite orientation do - every edge
  they share measures exactly 180.00 deg (B30 2026-11-04), which is what a
  split-and-copied surface leaves visible. `catia_check_surface` therefore
  judges fold-back (>= 175 deg) before anything else: those edges, and any
  shared by more than two triangles, are counted in `excluded` and kept out of
  both the radius ranking and the thickness verdict, `min_radius_at_open_border`
  and `tightest_edges[*].at_open_border` flag a real reading that ends on a
  border, and the thick-surface failure message repeats the count so a number
  cannot silently vanish either. A genuine zero-radius knife edge reads exactly
  the same, and the reply says so rather than choosing. `region` now scopes BOTH
  blocks - before 0.3.59 it scoped the fold statistics only, so "exclude the
  skirt and look at the base" changed the angles and left the radii whole-mesh.
  `excluded.unresolvable_edges` is the THIRD channel, added 0.3.61: a radius
  smaller than a tenth of the edge it was measured across is smaller than this
  mesh can resolve, not a measurement, and the verdict is decided by the 5th
  percentile rather than by the single smallest edge - a minimum is one triangle,
  a verdict is about a surface. Both were needed before a surface CATIA had just
  thickened by 0.3 mm stopped answering "cannot be built": reproduced on B30
  2026-11-04 with 0.0888 mm across a 3.3218 mm edge (2.7 %, 7.16 deg of fold,
  i.e. a bend of ~27 mm). A too-tight reading that is only a MINORITY now says
  INCONCLUSIVE and names its `radius_over_edge` instead of prescribing a
  redesign.
* **A `.CATPart` written by a newer CATIA may not open on an older one.**
  Measured 2026-11-04: B30 `Documents.Open` on a file written by V5-6R2022
  (B32 SP6) fails with a bare "method Open failed" - no version message, nothing
  about the release - which reads like a corrupt file. Files do travel between
  installations in a report chain, so check the writing release before believing
  that. STL exports of the same part stayed readable on the older release, and
  re-saving in a release both sides share avoids it. The reverse direction is
  the same story.
* **`set_view` works in the ABSOLUTE axis system, not the part's.** `front` means
  "look along −Y with +Z up" in the document's axes whatever the model's own
  orientation, so a part rotated in the tree (or a component placed with a
  transform) will not line up with any of the named views. No automation call
  reports the current view's right/up vectors the way the UI's "Normal to" does,
  so the seven names are the whole vocabulary. Build in the axes you want to look
  at, or take the screenshots before placing the component.
* **CATIA allows two elements to share a name, and this server used to produce
  them.** Assigning `Name` checks nothing, so building `P_04_06` twice left two
  elements with that name; `FindObjectByName` returns one of them and a delete
  removes one of them, which means "I deleted it and rebuilt it" can be true
  while the old element is still answering to the name. Measured 2026-11-03 —
  and it is the state a report read as "delete + rebuild breaks the loft
  topology", where every join failed while the geometry was identical. New
  elements now take a free name (`P_04_06.2`) and say so; deleting by a
  duplicated name is refused with the count; `names=[…]` removes every element
  carrying the name. **Join itself does not care**: two lofts whose facing
  sections are separate objects built from the same parameters join fine, and
  so do pieces rebuilt with their shared section also rebuilt (both measured the
  same day, four rebuild orders, 60/90/120/180/600 point sections).
* **No zebra stripes, curvature comb or Match surface.** CATIA V5 does not expose
  them to automation at all — measured 2026-11-03 on B30: the viewer answers
  `RenderingMode` and returns an unresolvable attribute for `Zebra`,
  `EnvironmentMapping` and `AnalysisMode`, and `HybridShapeFactory` has
  `AddNewHealing`, `AddNewBlend`, `AddNewConnect`, `AddNewCurveSmooth` and
  `AddNewFill` but no `AddNewMatch`. `catia_check_surface` answers the same
  question from the mesh (dihedral angle across every interior edge), and it is
  honest about what that means: the median is the mesh's own chording, so the
  number to read is the max and the crease list, not the median.
  **`AddNewHealing` STITCHES, it does not SMOOTH** — and the first measurement of
  it here was invalid, which is worth writing down. `HybridShapeHealing` exposes
  `AddBodyToHeal(iBody)`, `SetMergingDistance`, `SetTangencyAngle`,
  `SetDistanceObjective`, `SetSharpnessAngle` and `ReplaceToHealElement`; the
  0.3.51 probe set a `Continuity` and registered NO body, so the feature was a
  pass-through and every Continuity value gave a byte-identical mesh. Measured
  again with the body registered on B30 2026-11-03 (every state isolated in its
  own STL):

      two lofts 0.05 mm apart        112 tris,  0 interior edges, 112 open
      heal C1, merging 0.5           112 tris, 24 interior edges,  64 open
      heal C1, merging 0.001         112 tris,  0 interior edges, 112 open
      two lofts sharing a section   0.52 deg before, 0.52 deg at C1 and C2
      two walls at 90 deg           90.0 deg before and after, 4 tris either way

  So: it closes gaps smaller than the merging distance, and it changes nothing
  about continuity. `catia_gsd_heal` ships with those numbers in its description.
  A seam that is already joined and has to be smooth needs the surfaces rebuilt
  tangent (`catia_gsd_blend`, `catia_gsd_connect`, or sections that meet
  properly); there is no smoothing operator in this automation.
* **No sheet metal (Generative Sheetmetal Design).** This server covers Part
  Design, GSD, Assembly, Sketcher, Measurement and export — there is no wall,
  flange, bend, unfold or flat-pattern tool, and no K-factor/bend-radius
  parameter. A sheet-metal part has to be modelled as extrude + cut with the
  bend geometry worked out by hand, which is why a bracket that is 57 faces in
  SolidWorks comes out 76 here (+19 faces, +0.49 % volume — measured
  2026-11-03 on a 2 mm bracket with two bends): the extra faces are the prismatic
  substitutes for the bends. `catia_diagnose` reports whether the workbench's
  DLLs are installed beside the running CATIA, but no probe can read the
  licence, and the automation objects this server drives expose no sheet-metal
  feature either way.
* **A loft section cannot be a point or a vertex** — measured 2026-11-03: a datum
  point is accepted by `AddSectionToLoft` and then fails the update, and a solid
  `Vertex.N` is refused by the same call with "invalid number of arguments",
  although the CATIA UI lets you pick a vertex as an end section. Both are now
  refused before anything is built. End the loft on a small closed section and
  cap it with `catia_gsd_fill` instead.
* **A smooth loft refuses to update when the section arrangement folds back** —
  the surface interpolates through the sections and stays inside their hull, so a
  section that sits past the turn of its neighbours forces a self-intersection.
  Measured the same day: sections r 2.9 / 3.0 / 8 with the middle one above both
  neighbours fail as circles, as 12-point closed splines and as 90-point closed
  splines alike, while the same radii in a monotone stack build. Changing the
  curve type is not the fix; keep the sections monotone, split the loft at the
  turn, or use `ruled=true`, which covers every section in one call.
* **The mesh bounding box is the envelope of the whole visible document**, not of
  the solid: the STL export includes every shown surface and wireframe element,
  and neither `Part` nor `Body` exposes `ExportData`, so the export cannot be
  narrowed. A 10 mm pad with a construction surface at z 30..35 measured z max 35
  with the set shown and 10 hidden. Since 0.3.50 the reply carries `box_scope`
  (no sets / all hidden / some shown) and warns only when a set is actually shown;
  hide the sets (or use `method='vertices'`) before trusting the numbers.
* **`rect_pattern`'s directions are measured, and they are not the obvious
  ones.** Omit all four axis arguments and this build drives dir1 along **−Y** and
  dir2 along **−X** (the origin planes are PlaneYZ and PlaneZX). `dir1_axis='x'`
  does move the copies to +X, and `reverse_dir1` / `reverse_dir2` flip exactly the
  slot they name. Measured 2026-11-08 by reading the body's box **endpoints** -
  0.3.77's audit measured the box SIZE, which cannot see a sign, and so passed a
  wrong table for four releases. If you need to confirm a direction yourself,
  record min and max, not the difference. `circ_pattern` and `user_pattern` were
  measured the same way and are exact.
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
* **`catia_replace_component` cannot re-point an instance at a byte copy of a
  document that is OPEN in the session.** This build identifies a document by an
  internal id, not by its path, so the insert answers with the already-loaded
  document: the count grows, the pose matches, and the instance still points at
  the original (measured on B30 2026-11-08 - asked for `B\PZ81A.CATPart` with
  `A\PZ81A.CATPart` open, the instance resolved to `A\PZ81A.CATPart`). The tool
  reads `ReferenceProduct.Parent.FullName` back and refuses with
  `REPLACE_REFERENCE_MISMATCH` rather than reporting success, and it removes the
  instance it just added so the assembly is left as it was. Making an assembly
  self-contained is therefore TWO steps: copy the files, **close the originals**
  (`catia_close_document(document=<full path>)`), then replace. A copy that is
  not open in the session resolves to its own path and works in one step.
* The watchdog answers `#32770` message boxes only; CATIA's own `CATDlgDialog`
  panels are left alone so a human working alongside the agent is not interrupted.

## License and attribution

MIT. Upstream copyright is retained in [`LICENSE`](LICENSE)
(Adam M'HAMDI, Ondřej Schiller, Chong Zhu). This fork's changes are additions to
and modifications of that work; `README.md` and `FORK.md` state what they are.

If you send improvements back, upstream is the right home for anything that is
not specific to this fork — it accepts contributions.
