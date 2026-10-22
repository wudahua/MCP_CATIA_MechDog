# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added
- `catia_list_edges` / `catia_list_faces` report `nls` (CATIA's topology path)
  and `created_by` (the features that produced the element). This is the only
  handle that survives when several edges share a length: the four corners of a
  square, the two circles on a disc and the tangent edge a fillet just created
  can all measure the same, but their paths differ - `(Brp:(Pad.5;0:(Brp:(Sketch.23;3)))`
  says "made by pad 5", and a spec that says "fillet the four vertical corner
  edges" becomes a lookup instead of a guess.
- `catia_pocket` takes `symmetric` / `direction: 'both'` for a mid-plane cut, and
  `catia_fillet` takes `propagation` (`tangency` = CATIA's default, `none` = only
  the edges you name). Propagation matters more than it sounds: on a smooth
  chain, `tangency` runs the fillet along the entire boundary, so "fillet these
  4 edges" silently becomes "fillet the whole outline".
- `catia_set_visibility`: show or hide a body, a geometrical set (and everything
  inside it), a single element, a feature or a sketch. A GSD surface stays
  visible after `catia_gsd_close_surface` turns it into a solid, and it overlays
  that solid - so the picture, `catia_get_bounding_box(method='mesh')` and every
  STL export describe a part that does not exist. Field case: a two-blade
  propeller looked like one blade had an extra half attached, and the STL
  envelope read 45.6 mm wide instead of 30.1, with the solidified geometry
  perfectly correct all along. Note the measured enum: `SetShow(0)` shows,
  `SetShow(1)` hides, `SetShow(2)` is accepted and does nothing.
- `catia_fillet` and `catia_chamfer` take `edge_names` and apply several edges in
  one call. Each edge becomes its own feature, so one refusal no longer throws the
  whole batch away - the reply says "R3.81: 3/4 edge(s) done" and names what was
  rejected and why.
- `catia_save_as_copy`: save the document, then copy the file to another path.
  This is the only way to snapshot a model that already has geometry, because
  Save As is refused from that point on; the open document keeps its own path.
- `catia_delete_feature` accepts `index`, and with no argument at all deletes the
  last feature - undoing the feature you have just created should not require
  knowing CATIA's localized names.
- `catia_list_edges` / `catia_list_faces` report CATIA's element `type`
  (`RectilinearTriDimFeatEdge`, `PlanarFace`, ...). With lengths unavailable on
  some topology, the type is often the only handle left for picking an element.
- `catia_gsd_extract`: extract a curve or surface from existing geometry into the
  active geometrical set, with a propagation setting (none / point / tangent /
  curvature). `catia_gsd_fill` needs a boundary *curve* - a sketch reference is
  rejected - so without this there was no way to cap a lofted surface and turn it
  into a solid. It was simply missing from the 24 GSD tools.

### Fixed
- A topology search returns construction geometry alongside the solid, and it
  comes first. On the drone body the search reported 30 "faces" for a shape with
  25 real ones: the datum planes and the sketch wires were mixed in, so `Edge.1`
  to `Edge.8` were plane borders with no length and every length-based edge pick
  was reading geometry that cannot be filleted. Both the listings and the
  `Edge.N` / `Face.N` resolution now share one filtered enumeration, so the index
  the user sees is the index the fillet resolves.
- A multi-edge fillet updated each feature as it was created, which invalidated
  the references still queued behind it. Measured: 12 edges at R5.08 on the drone
  body gave "1/12 done" and then every remaining factory call failed with a bare
  "method failed". The batch is now built first and rebuilt once, and if that
  rebuild fails the features are removed instead of left half-built.
- `catia_pocket(symmetric=true)` cut one side only. `Pocket.IsSymmetric` is
  accepted by CATIA and then ignored - measured: "mid-plane cut 38.1" passed as
  19.05 removed 5 609.6 mm3, exactly the single-sided figure, not the 11 150.9 a
  38.1 cut gives. The tool now builds the second side as its own cut, and drops
  that cut when the other side is air, so the tree stays honest.
- A multi-edge fillet resolved every edge in the batch *lazily*, one at a time,
  between factory calls. Each fillet renumbers the body's edges, so the second
  lookup landed on the tangent edge the first fillet had just created - no
  longer sharp, and CATIA answers that with an update diagnostic
  ("某些选定的边线不是锐边") rather than an error, so the batch looked like it
  half-worked. Measured on the drone motor disc: the two Ø69.85 circle edges and
  the R12.7 tangent edge are all 219.440 mm long, so neither length nor type can
  separate them after the fact. All references are now resolved before the first
  feature is created, and a rejected edge has its half-built feature removed so
  no update error is left behind.
- `catia_list_faces(body_name=...)` validated the name and then dropped it,
  listing the active body anyway. On a nine-body part that is silently wrong
  every time. It now searches the body it was asked about.
- `catia_pad(..., symmetric=true)` came out twice as thick as the schema
  promised. CATIA's `IsSymmetric` is a *mirrored extent*: each side gets the
  full dimension, not half of it (measured: r=10, height=10 -> y -10..+10,
  6283.19 mm3). The tool now hands CATIA `height / 2`, says so in the schema, and
  reports the resulting span. This turned a 31.75 mm motor disc into a 63.5 mm
  one and pushed its Ø45.72 blind hole entirely inside the material, where CATIA
  refused it as an enclosed void.
- `catia_pad` accepts `direction='both'`, which the schema advertised and the
  code silently treated as plain `normal`.
- Unmeasurable topology was published as a number. SPA does not raise for what it
  cannot resolve - a `Length` of 2e7 mm or an `Area` of 0.0 mm2 is a placeholder,
  not a measurement - and those values silently poisoned every length-based edge
  selection. They are now reported as `length_unknown` / `area_unknown`, and the
  limit is documented in the code.
- `catia_list_edges` / `catia_list_faces` enumerated `Shapes.Item(last)` while
  `Edge.N` / `Face.N` everywhere else resolve against the body's final shape. On
  any part with more than one feature the indices they handed out pointed at a
  different element by the time a fillet consumed them. Both now search the body
  through one shared helper.
- Automation failures now carry the session state: document, in-work object, body
  and feature count, selection size, geometrical-set count. CATIA reports almost
  everything as a bare "method failed", and the three bugs found in one session
  were all state problems invisible in that message.
- `catia_measure_distance` accepts `elem1_name` / `element1_name` / `elem1` (and
  the `2` variants) instead of failing validation, and its missing-argument error
  names every spelling it accepts.
- The `catia_circ_pattern` refusal now suggests what to do instead of only saying
  no: two `catia_gsd_symmetry` calls are exactly a 180 deg rotation.
- Topology references were invalidated before they were used. `topology_reference`
  resolved `Edge.N` / `Face.N` through `Selection.Item(i).Reference` and then
  cleared the selection in a `finally` block - and a reference obtained that way
  belongs to the selection and dies with it. Every factory call that consumed it
  failed with a bare "method failed" and no explanation, which made fillet,
  chamfer, shell and draft look like bad edges. Measured directly on
  V5-6R2020 B30: identical `AddNewSolidEdgeFilletWithConstantRadius` call
  succeeds with the selection alive and fails immediately after `sel.Clear()`.
  The selection is now left holding the element (`keep=True`), multi-element
  callers use the new `topology_references()` helper so one `Clear()` cannot
  pull the rug from the previous reference, and each feature clears afterwards.
- Part Design features now put the solid body back in work, and rebuild it,
  before calling the factory. GSD calls leave `Part.InWorkObject` pointing at the
  geometrical set (`GSD_Set`), and `AddNewPocket` / `AddNewFillet` /
  `AddNewChamfer` then fail with the same uninformative COM error. After
  `catia_gsd_close_surface` a pocket was rejected until `InWorkObject` was set to
  the body by hand; with the fix it cuts the expected 402.2 mm3 on the first
  try. `_prepare_body` also runs `part.Update()`, because a body whose last
  feature was added in the same call hands out stale edge references.
- `catia_list_faces` reported `"area_mm2": 0.0` for every face of a solid built by
  `catia_gsd_close_surface`. SPA resolves sub-element references of GSD-derived
  topology to nothing and returns `Area = 0` instead of raising, so the guard
  never fired. The key is now omitted when the measurement comes back as zero,
  rather than publishing a 0 that reads like a real measurement. Verified correct
  on ordinary Part Design solids (100x100x10 pad: 2 x 10000 + 4 x 1000 mm2).
- `catia_sketch_spline` with `closed: true` built **two** entities: the spline,
  plus a straight line from the last point back to the first. Anything that
  consumes an outline as a single curve then fails - a loft section, for
  instance, is accepted by `AddSectionToLoft` and rejected at update. A closed
  spline now repeats its first point so the result is one continuous curve, and
  a closed spline (unlike a four-line outline) can be used as a loft section -
  verified by lofting two closed splines and getting a surface.

### Known limitation
- A loft section must be a **single curve**. A closed outline made of several
  lines is accepted by `AddSectionToLoft` and then fails at `UpdateObject` - even
  two identical rectangles fail, so this is not a coupling or start-point problem.
  A closed spline works around it, except on a long narrow cross-section where
  the spline overshoots at the sharp corners and self-intersects, which then
  breaks `catia_gsd_fill`.
- A closed spline through the corners of a polygon **inflates its area by about
  2%** (measured on V5-6R2020 B30: a 106.40 mm2 parallelogram came back as
  108.61 mm2, ratio 1.0208; the same outline pre-shrunk 2% still came back at
  1.0204, so it is a systematic bias of the B-spline, not noise). If the section
  area matters, pre-shrink the outline by ~1/1.0206 - measured with a 1 mm pad on
  the section and read back through `catia_get_inertia`.
- **A solid that came out of `catia_gsd_close_surface` is a dead end for
  edge-based Part Design work, and for all sub-element measurement.** Once such a
  solid is in the body:
  - `catia_circ_pattern` is refused at `UpdateObject` (the factory builds the
    feature, then CATIA rejects it) - the same 0x80020009 class the tool already
    guards against.
  - `catia_fillet` / `catia_chamfer` are refused for edges that came out of the
    GSD surface - 55 of 56 edges on a two-blade part, the one that works being
    the hub's own pad edge. Native pad edges are fine, so this is the GSD
    topology, not the tool. Workaround for a second instance: two
    `catia_gsd_symmetry` calls (about XZ then about YZ) are exactly a 180 deg
    rotation about Y, and `catia_gsd_close_surface` on the result gives a solid
    identical to what the pattern would have produced - verified to 0.6 mm3
    against the analytic volume, with the centroid landing exactly on x = z = 0.
  - `catia_list_edges` reports `length_mm` as a constant garbage value (2e7, or
    8e7 on an extracted curve), `catia_list_faces` cannot give areas, and
    `catia_measure_distance` throws from `GetMinimumDistance`. Circles still
    measure correctly, so this hits B-spline and GSD-derived topology only.
  - Profile-based features (pad, pocket) are **not** affected - a Ø6.35 pocket
    through such a body cuts exactly as expected.

  To keep the tip fillets and the array, the blade has to be a Part Design
  multi-sections solid (`ShapeFactory.AddNewLoft`) rather than a GSD
  loft+CloseSurface. That object can be created, but its specific interface is
  not reachable through late-bound dispatch - the type info only exposes
  `CATIABase` (23 members: Name, Parent, QueryInterface, Invoke, ...), and every
  Loft-specific name (`AddSectionToLoft`, `SectionCoupling`, `Relimitation`)
  resolves to nothing. Reaching it needs the `CATIALoft` IID and an explicit
  `QueryInterface`.

## [0.3.1] — 2026-10-22 · multi-body parts, loft sections from topology, curved bounding boxes

Three gaps that a written specification hits as soon as it stops being a single
solid. All measured on a live V5-6R2020 (B30) session. **90 tools** (0.3.0 had 87).

### Added
- `catia_new_body` / `catia_list_bodies` / `catia_boolean`: multi-body parts.
  A spec that declares "Merge = false" (several solids in one part, possibly
  interpenetrating) could not be expressed before - everything went into
  MainBody and overlapping solids fused, losing the overlap from the volume.
  `catia_list_bodies` reports each body's volume and face count plus the totals,
  because a part's volume is the sum of its bodies. `catia_boolean` covers
  add / remove / intersect / assemble.
- A loft section may now be a **solid's own topology**: `"Edge.3"` is extracted
  from the finished body and used as a section, so "loft from the Ø38.1 edge at
  x=254" no longer needs that circle redrawn as a sketch. It has to be
  extracted first - a raw topological edge is rejected by `AddSectionToLoft`
  ("invalid number of arguments", which is CATIA's misleading way of saying the
  type is wrong).
- `catia_get_bounding_box` takes `method` = `auto` | `vertices` | `mesh`. A
  fully curved solid (a cylinder is one face, zero vertices) now gets a box from
  an exported STL instead of failing outright. The mesh chords the surface, so
  the true face lies a fraction of a millimetre outside the reported bounds -
  stated in the result.

### Not fixed, because CATIA does not offer it
- **No tangent / curvature condition on a loft.** `HybridShapeLoft` exposes only
  `SectionCoupling`, `Relimitation`, `AddSectionToLoft` and `AddGuide` - verified
  by enumerating its type information, not by guessing. Part Design's
  `ShapeFactory.AddNewLoft()` is not automatable at all on V5-6R2020 (every
  member lookup fails). A spec that relies on "maintain tangency, tangent
  factor 1.0" has to be approximated with extra intermediate sections instead.

## [0.3.0] — 2026-10-20 · lite fork

First public state of the fork. Based on upstream `0.2.2` plus the unreleased
98-tool work and a zh-CN CATIA compatibility patch. **87 tools** (98 − 16 + 5).
See `README.md` for the diff table and `FORK.md` for measurements.

### Removed
- `tools/drawing.py` (11 tools): requires a Drafting licence, emits 2D views.
- `tools/knowledge.py` (5 tools): parameters, formulas, design tables.
- Upstream repository-traffic plumbing (`traffic/`, its workflow and collector)
  and `.github/FUNDING.yml`: they belong to the upstream repository, not a fork.

### Added
- `catia_run_plan`: executes an ordered `{tool, args}` recipe in one call, then
  asserts volume, area, face/edge/vertex counts and centre of gravity.
  Counts compare exactly; volume and area use a percentage tolerance.
- `catia_measure_topology`: face/edge/vertex counts, excluding `Selection_Wire*`
  elements (a topology search also returns the consumed sketch's lines).
- `catia_set_component_transform` / `catia_get_component_transform`: absolute
  placement from a row-major 3x3 or CATIA's native 12 numbers, with read-back,
  deviation, det and orthogonality.
- `catia_create_sketch` takes `offset` (mm along the plane normal, negative
  allowed), or `plane` as an object: `{"type": "offset", "base", "distance"}`
  and `{"type": "face", "index"}`. The datum plane is inserted into the
  PartBody, because `AddNewPad` rejects a sketch whose support lives in a
  geometrical set. A sketch on a solid face uses that face's own coordinate
  frame, which automation cannot read back (measuring the 2D axis origin fails,
  and `Measurable.GetPlane` fails on a solid face), so the reply says so - a
  wrong guess removes no material rather than raising.
- `catia_sketch_line` takes `construction`: a revolution axis or reference
  geometry that must not join the profile.
- `catia_pocket` / `catia_hole` take `through_all` (CATIA "up to last"), so a
  hole no longer depends on guessing a depth taller than the part.
- `catia_shaft` / `catia_groove` take `axis` (`horizontal` | `vertical`). Only
  the sketch's own axes work; a construction-line reference is accepted by the
  property setter and then rejected at update time.
- `catia_rename_component`: a readable assembly tree. `Part.Name` is read-only on
  V5-6R2020 (`E_NOTIMPL`), so every part added from file shows up as `Part1.1`,
  `Part2.1`, … no matter what the file is called - nine parts of a cylinder all
  collapse to `Part1.1 / Part2.1 / Part2.2 / Part3.1 …` and cannot be told apart.
  The instance name is writable, so this is the only way to get `P01_CylinderBlock`
  into the tree. The reply reports whether CATIA actually kept the new name.

### Fixed
- `catia_shaft` / `catia_groove` never produced a solid: they called
  `AddNewShaft(sketch)` and assigned `FirstAngle = angle`. The feature object it
  returns cannot be updated (E_FAIL), `FirstAngle` is an object with a `.Value`
  rather than a number, and the profile and axis must be set explicitly. Now
  uses `AddNewShaftFromRef` + `SetProfileElement` + `RevoluteAxis`; verified on
  V5-6R2020 B30 at 0.000% volume deviation on a six-segment profile.
  This is what blocked every turned part in the cylinder spec - piston,
  cylinder-head cover and front cover had no other route.
- `catia_hole` with a finite depth cut nothing on V5-6R2020 B30: the depth is
  stored on `BottomLimit` but not applied. It used to report success with an
  unchanged volume. The call now measures the result, rolls the feature back and
  explains, instead of leaving a dead feature in the tree.
- `catia_pocket` returned a raw E_FAIL from `UpdateObject` when a through-all cut
  was attempted from a sketch on a face CATIA will not cut from (measured: the
  bottom face of a plain box). The feature is now rolled back and the error says
  to use a datum plane with an offset.
- `Topology` index resolution moved to `topo_search.topology_reference` so the
  sketcher and every face/edge tool share one rule; `catia_list_faces` and the
  fillet path are unchanged.
- `catia_run_plan` silently ignored misspelled keys: `assertions` instead of
  `expect`, `volume` instead of `volume_mm3`, `arg` instead of `args` all
  validated fine and were dropped, so the plan answered `PLAN_OK` with an empty
  assertion list. `additionalProperties: false` on the plan, its steps and its
  `expect` object turns those into `INVALID_ARGUMENT`.
- **`pad` / `pocket` could use the wrong sketch.** `body.Sketches` is ordered by
  **tree position, not creation order** - a cylinder cap with three sketches
  enumerated as 草图.1 / 草图.3 / 草图.2 - so `Item(Count)`, the previous "last
  sketch", returned the boss profile and a bolt-hole pocket cut the boss off
  instead (the removed volume landed exactly on the boss volume, 18 142 mm³).
  The sketch created last is now remembered on the connection and preferred; a
  stale handle falls back to the collection, an explicit `sketch_name` still
  wins.
- `catia_list_edges` / `catia_list_faces` returned only `Edge.N` / `Face.N`
  names. That is enough to fillet an edge whose index you already know, and
  useless for the case that actually comes up: "the outline arc" has no stable
  index, only a length (281.301 mm on the rear lug). They now report
  `length_mm` and `area_mm2`, so an edge can be selected by geometry.
- `catia_save_document` now states its ordering constraint: Save As only works
  while the document has no geometry, so a model is kept on disk by saving the
  empty part to its final path right after `catia_new_part`, then calling the
  tool with no path once the model is built.
- `Position.GetComponents` returned twelve zeros (SAFEARRAY out-param does not
  marshal through late-bound IDispatch): `catia_move_component` erased the
  component's rotation and `catia_list_components` reported `(0,0,0)` for every
  component. Reads now go through `SystemService.Evaluate`.
- `doc.Part` / `doc.Product` raised `AttributeError` on a gen_py-typed document:
  added `connection.part_of()` / `product_of()` with a dynamic re-wrap.
- `catia_save_document` now retries around CATIA modal dialogs and runs a
  watchdog thread that answers `#32770` message boxes while the COM call is in
  flight, so a blocked SaveAs no longer freezes the STA worker.
- `catia_new_part` reports a rename refusal instead of failing the whole call.

### Tests
- `tests/test_planner_assertions.py`: assertions, plan validation, matrix
  reporting, wireframe filtering, registration.
- `tests/test_modelling_gaps.py`: 26 tests locking the arguments that a written
  spec needs (`offset`, both `plane` object forms, `construction`, `through_all`,
  `axis`), the absolute-axis lookup by index rather than the localized name, and
  the sketch-support resolver. Subtracting `depth` from `required` is asserted
  too, since a schema that still demands it makes `through_all` unreachable.
- `tests/test_http_transport.py`: HTTP clients now pass `trust_env=False`. With a
  system `HTTP_PROXY` set, httpx sent an absolute-form request line, uvicorn put
  the whole URI into `scope["path"]`, and the server 404'd a correct request.
  The test failed on any machine with a proxy (upstream included); the suite is
  now independent of the environment. No server code changed.
- **78 offline tests** in total, none of which start CATIA.

## [Unreleased]

### Changed
- CATIA calls and connection cleanup run serially on one STA worker. Ordinary
  errors and client cancellations do not lock the server; queued calls run after
  the current operation completes. Cancellation does not cancel a COM call.
- Server-boundary errors for all 98 existing tools now return a structured envelope
  and MCP `isError=true`; successful text outputs and tool schemas are unchanged.
  `UNSUPPORTED_CAPABILITY` retains cache, release and workbench guidance.
- Require `mcp>=1.27.0,<2` for structured handler results and transport-close
  cancellation support; declare the directly imported `jsonschema>=4.20,<5`.
- Windows CI installs development dependencies and runs pytest regression tests.

### Runtime limits
- A CATIA modal dialog can block the worker and shutdown. Client cancellation
  does not prove model rollback or stop an in-flight COM call. Close the blocking
  dialog; if CATIA cannot recover, terminate CNEXT and restart the MCP process.

### Added
- **Remote access over Streamable HTTP**: `catia-mcp --http` serves the tools
  at `/mcp` so the agent can run on a Mac or another machine while CATIA runs
  on a Windows PC or a cloud workstation. Loopback-only by default; binding
  any other address requires a bearer token (`--token` or `CATIA_MCP_TOKEN`,
  `--new-token` generates one), compared in constant time, with DNS-rebinding
  protection on loopback. stdio stays the default and is unchanged. Setup for
  Claude Code, Cursor, VS Code and Claude Desktop in docs/remote-access.md.
- **Knowledgeware module (98 tools total)**: `catia_create_parameter`
  (lengths and angles valuated with explicit units), `catia_set_formula`
  (create or modify in place), `catia_list_relations`,
  `catia_delete_relation` and `catia_create_design_table` with column
  associations, through the documented Parameters/Relations interfaces.
  Derived from the knowledge module proposed in PR #3.
- **Drafting views and annotations (93 tools total)**:
  `catia_drawing_projection_view` (right/left/top/bottom/rear via
  DefineProjectionView with the documented CatProjViewType values),
  `catia_drawing_section_view` (section view or cut along a polyline
  profile via DefineSectionView), `catia_drawing_detail_view`
  (DefineCircularDetailView), `catia_drawing_add_text` (DrawingTexts.Add)
  and `catia_drawing_add_table` (DrawingTables.Add with cell filling and
  column widths, the building block for title blocks and BOMs). Signatures
  taken from the V5 automation reference; derived from the drafting work
  proposed in PR #3, rewritten to the project's conventions. Awaiting live
  validation.
- `catia_delete_feature`: delete a feature by name through the selection
  (from PR #2's proposal).
- **Dimensioning generative views the way CATIA actually allows it** (87
  tools total). A V5R20 live lab established that generated projection
  curves are never exposed to Automation (GeometricElements shows only
  the axis and manual 2D items), so `catia_drawing_generate_dimensions`
  implements the proven route: one associative dimension per supported 3D
  sketch constraint via DrawingSheet.GenerateDimensions, with the drawing
  activated first (otherwise CATIA silently does nothing) and a dimension
  count postcondition. `catia_drawing_list_dimensions` reports values and
  status (basic_2d vs 3d_driven). The manual tools are rescoped to
  Factory2D geometry and CatDimType codes corrected to the 0-based enum
  (radius = 5 verified live).
- `catia_drawing_add_view` gains face-driven projection (`face`: the view
  is projected on a planar face's own plane via Measurable.GetPlane and
  SetProjectionPlane with the two in-plane vectors, orientation-independent)
  and `angle` for in-plane rotation. Field-proven on a real industrial part.
- Shared `read_measurable_array` helper: array-filling Measurable methods
  (GetCOG, GetPoint, GetPlane) run inside CATIA via SystemService.Evaluate
  and return the array directly; coordinates come back in the document's
  length unit (mm), so center of gravity and bounding-box vertices are no
  longer scaled.
- **`catia_user_pattern`** (85 tools total): copies of a feature placed on
  the points of a sketch, via AddNewUserPattern + AddFeatureToLocatePositions.
  Field-validated on V5R20 (4 copies measured on their sketch points plus the seed).
- `catia_circ_pattern` takes `axis` (x/y/z) and `center`; the rotation axis
  Reference is minted from a GSD line in a throwaway donor document, the
  only route accepted on V5R20 (an in-document GSD line makes the call
  fail with 0x80020009). Validated: 4 bosses at radius 40 mm, 0/90/180/270.
- `catia_rect_pattern` defaults its directions to the origin planes
  (PlaneYZ -> X, PlaneZX -> Y), validated on a measured 2x2 grid; explicit
  edges remain available.
- `catia_drawing_add_view` takes `body_name` to draw a single body
  (validated with a control view on V5R20).
- `catia_close_sketch` reports the closed sketch's name so later tools can
  address it reliably.
- `catia_rect_pattern` accepts optional `dir1_edge`/`dir2_edge` ('Edge.N')
  to pin the pattern directions on real topology edges. Field-proven on
  V5R20: edge references from the selection are accepted as directions,
  and CATIA rejects two parallel directions, so two adjacent edges
  sharing a corner are required.
- **Drafting module (experimental, 84 tools total)**: `catia_new_drawing`
  creates a CATDrawing and `catia_drawing_add_view` projects an open Part
  as a generative front view (xy/yz/zx) on the active sheet, built from
  automation probes field-validated on CATIA V5R20 by contributor
  ESE3X. `catia_drawing_list_view_geometry` lists a view's manually drawn
  2D geometry and `catia_drawing_add_dimension` places dimensions between
  such elements via DrawingDimensions.Add (generated curves are not
  addressable, see the generate_dimensions entry above). Section views,
  text and tables followed (see the entry above).
- README repositioned as agent-agnostic: per-client MCP setup for Claude
  Desktop/Code, Cursor, Windsurf, Cline, VS Code and generic stdio hosts,
  plus release/CI/license badges.

### Fixed
- **Center of gravity and bounding-box coordinates were scaled by 1000**:
  GetCOG/GetPoint/GetPlane return coordinates in the document's length
  unit (mm, field-observed on V5R20 via GetPlane on a real part), unlike
  Area/Volume which are MKS. The 0.2.2 conversion multiplied them by
  1000; removed.

## [0.2.2] — 2026-10-04

### Added
- **`catia_diagnose` tool** (79 tools total): reports the exact CATIA
  version/release/service pack, the Python/pywin32 environment, and probes
  which automation APIs the installation resolves (ShapeFactory and
  HybridShapeFactory methods matching exactly what the real tools call,
  SPAWorkbench availability), with guidance notes. Motivated by a V5R20
  field report where Part Design feature creation was intermittently
  unavailable (root cause later identified: stale pywin32 cache).
- Compatibility notes section in the README (V5R20 and V5-6 2020 field
  reports).

- **`catia_list_faces` tool** (80 tools total) and canonical indexed names
  (`Edge.N` / `Face.N`) from `catia_list_edges`, feeding the new topology
  path of `catia_measure_distance`.

### Fixed
- **Part Design overhaul after an adversarial API audit (30 confirmed
  findings)**: fillet, chamfer, draft and thickness now target real
  Face.N/Edge.N references and honor their schema arguments (previously
  silently ignored); shell anchors on the face to remove per the
  documented 3-parameter signature; rectangular and circular patterns use
  the full 12-parameter signatures (they failed with
  DISP_E_BADPARAMCOUNT on every call, and circular counts now go in the
  angular slot); holes use AddNewHoleFromSketch, set Diameter.Value
  correctly and honor the type argument; mirror drops its dead
  feature_name argument. Center of gravity and the inertia matrix are
  now computed through SystemService.Evaluate because in/out safearrays
  do not marshal through late-bound COM (previous COG output was the
  caller's unmodified zeros), with inertia data from the SPAWorkbench
  Inertia object (Measurable.GetInertia does not exist); mass from the
  CATIA material is reported. The bounding box is computed from a vertex
  sweep (Measurable.GetBoundingBox does not exist in any V5 release) and
  states its exactness. Pocket honors an explicitly requested direction
  instead of silently flipping it. Arc sweeps crossing 0 degrees are
  normalized. HSO selection and sketch state are cleaned up on all error
  paths.
- **`catia_measure_distance` can now target faces and edges**: topology
  resolves via the final shape's HSO enumeration and the selection's
  Reference property (`CreateReferenceFromObject` rejects HSO topology
  cells, field-verified E_INVALIDARG). Awaiting live field confirmation.
- **`catia_sketch_constraint` never worked**: it passed raw geometry elements
  where the API requires Reference objects (DISP_E_TYPEMISMATCH on every
  call), and most CatConstraintType enum codes were wrong (only tangent was
  correct). Both fixed; constraint names are now returned for verification.
  Root-caused and validated on live CATIA by a V5R20 field test campaign.
- **`catia_sketch_arc` called a method that does not exist**: Factory2D has
  no CreateArc in any V5 release. Arcs are now created as open circles via
  CreateCircle with start/end parameters (field-validated fix).
- **`catia_pocket` could report success while removing no material**: the cut
  side was never established. Orientation is now set explicitly, the body
  volume is measured before/after, the direction is flipped automatically
  when nothing was removed, and the result message states the measured
  effect (or FEATURE_NO_EFFECT honestly). Field-validated: 10x10x5 pocket
  removes exactly 500 mm3.
- **`catia_close_sketch` lost track of open sketches across server
  restarts**: it now re-adopts the document's in-work sketch instead of
  failing while CATIA visibly holds a sketch open.
- Missing COM APIs now return a clear `UNSUPPORTED_CAPABILITY` message
  naming the likely causes instead of a raw AttributeError. Field testing
  on V5R20 identified a stale pywin32 `gen_py` cache as the most common
  cause (methods that exist stop resolving); the error message, the
  diagnostics notes and a new Troubleshooting entry now point to clearing
  it first. V5R20 is confirmed running the full core workflow (part,
  sketch, pad) once the cache is cleared.
- Measurement tools obtain SPAWorkbench from the active document first:
  `GetWorkbench` is a Document method in the V5 automation model, not an
  Application method.

## [0.2.1] — 2026-10-02

### Fixed
- **All measurement tools returned MKS values mislabeled as mm**: CATIA V5's
  SPAWorkbench Measurable API returns meters/m2/m3 regardless of display
  units, so `catia_get_inertia`, `catia_get_bounding_box` and
  `catia_measure_distance` were off by factors of 1 000 to 1 000 000 000
  (volume), and the derived mass was wrong by 1e9. All measurement results
  are now converted to mm-based units at the server boundary; the inertia
  matrix is explicitly labeled `inertia_matrix_kg_m2`. Feature creation
  (pads, sketches, holes) was always genuinely in mm and is unchanged.
  Reported by a user via LinkedIn after real-model testing.

## [0.2.0] — 2026-08-20

### Added
- **Generative Shape Design (GSD) module** — 24 new wireframe/surface tools
  (geometrical sets, 3D points/lines/planes/splines/circles, multi-sections
  surface, sweep, extrude, revolve, fill, blend, offset, join, split, trim,
  symmetry, ThickSurface/CloseSurface), bringing the server to **78 tools**.
  Contributed by @gaoflow in #4, runtime-tested against a live CATIA V5
  instance (30/30 smoke tests).
- GSD smoke test script (`scripts/gsd_smoke_test.py`).
- CI workflow running the offline test suite on Windows (Python 3.10/3.12).

### Fixed
- **All 14 Part Design tools raised `AttributeError`**: `Body` has no
  `ShapeFactory` in the CATIA V5 automation model — now uses
  `part.ShapeFactory`. Reported in #1 by @wuqing8577-netizen, fixed via #4
  (also proposed in #2 by @amirhossein199741).
- `Application.ActiveEditor` does not exist in CATIA V5 — view refresh,
  screenshot, set-view and fit-all now go through `ActiveWindow.ActiveViewer`
  (#4).
- Screenshots: CATIA V5 cannot capture PNG; the format is now chosen from the
  file extension (JPEG/BMP/TIFF) with a JPEG fallback for `.png` paths (#4).
- `pip install -e .` failed: invalid `build-backend` replaced with
  `setuptools.build_meta` (#4, also proposed in #2 and #5).
- Log file is written next to the package instead of the process working
  directory (which is `System32` when launched by Claude Desktop) (#4).
- `catia_new_part` / `catia_new_product` no longer crash when CATIA rejects
  the requested name (read-only `Part.Name` on some versions); the actual
  outcome is reported honestly. Contributed by @ewhenbula-svg in #5.
- **mcp SDK pinned to `<2`**: mcp 2.0.0 removed the low-level
  `Server.list_tools`/`call_tool` decorator API, breaking the server at
  startup on fresh installs.

## [0.1.0] — 2026-02-25

Initial release: 54 tools covering documents, 2D sketching, Part Design,
assembly, measurement, export and view control over CATIA V5 COM automation.
