# Changelog

All notable changes to this project are documented here.

## [0.3.0] — 2026-10-20 · lite fork

First public state of the fork. Based on upstream `0.2.2` plus the unreleased
98-tool work and a zh-CN CATIA compatibility patch. **90 tools** (98 − 16 + 8).
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
