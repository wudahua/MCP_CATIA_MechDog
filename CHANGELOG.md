# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Fixed
- `catia_get_bounding_box` was wrong on curved solids, and the reason was not the
  routing but the export: `ExportData(path, "stl")` writes a **37-byte empty
  file** unless the part has been updated first - measured on B30 on both a block
  (37 bytes -> 3244 after `Update()`) and a cylinder (37 -> 28899). With the
  update in place, `auto` now takes the mesh, which is exact for planar-faced
  solids and a lower bound for curved ones:

  | solid | before (`auto`, vertices) | now (`auto`, mesh) | true |
  |---|---|---|---|
  | 40x20x10 block | 40 x 20 x 10 | 40 x 20 x 10 | same |
  | Ø60 x 50 cylinder | missed the diameter (up to -37.5 % on mixed bodies) | **60 x 60 x 50** | same |

  `method='vertices'` is still there for the cheap exact-on-planar route, and the
  reply says which one ran and what its error is (the mesh reads ~0.5-0.73 % low,
  stated as a lower bound rather than "a fraction of a millimetre").

### Added
- `catia_gsd_multi_section_surface` takes `ruled=true`: a straight-sided loft,
  built as one blend per section pair and joined. This closes the capability gap
  behind "the loft is bulged and the volume is +22.86 %".

  Measured on V5-6R2020 B30 (2026-10-24), three circles r=10/20/10 at z=0/50/100
  closed into a solid:

  | route | volume | vs analytic |
  |---|---|---|
  | GSM loft (NURBS through the sections) | 90 059.0 | +22.86 % |
  | `ruled=true` (blends joined) | 73 304.7 | ratio 1.0000 |
  | analytic, two frusta | 73 303.8 | — |

  `SectionCoupling` was measured first and is useless for this: 0, 1, 2 and 3 all
  give 90 059.0, because it changes the correspondence between sections, not the
  interpolation through them. The blend API is the ruled one.

Fourth round of 2026-10-24 reports. Three of the four named something this fork
got wrong in the previous release, and two of those were in the text a caller
reads to decide what to do.

### Fixed
- `catia_get_inertia` accepted `document=` since 0.3.9 but its schema never
  declared it, so a client that builds its form from `tools/list` could not pass
  it. Declared now, with a test.
- `catia_add_component` hung a whole session for 2 h 14 min on B32. Root cause
  measured by the reporter: `AddComponentsFromFiles` rejects **forward-slash**
  paths, then puts up a modal "打开" dialog that blocks the COM worker - and the
  old code reported success without checking. The path is normalised to Windows
  form for this call only (every other file tool is field-verified with forward
  slashes), file alerts are silenced around it, and success now requires the
  component count to have grown.
- `catia_sketch_delete_element` said the consuming features were rebuilt while
  only the sketch had been updated, so a pad kept its old volume. The part is
  rebuilt now.
- A `method='mesh'` measurement of a named document left the session on that
  document, because `ExportData` activates what it exports. The previously active
  document is restored before returning.

### Corrected claims
- Two messages said the opposite of what happens and steered users off the only
  working path: that activating a component's part "does not change
  ActiveDocument" (it does - measured three times on B32), and that a part
  "cannot be activated while its product is open" (it can). Both rewritten. The
  Product refusal for `component=` + `mesh` now gives the real reason - only
  vertices are reachable there - and names the two routes that work.

## [0.3.9] — 2026-10-24 · two regressions fixed, and a named document can be measured

Third round of 2026-10-24 reports (three at once). Two of them are regressions this
fork introduced, which is the honest headline.

### Fixed
- `catia_draft` without `neutral_face` crashed with "'str' object has no attribute
  'get'": the auto-pick iterated `catia_list_faces`'s reply as if it were still
  the bare array it was before 0.3.7 (it is `{count, raw_total, faces}` now). Four
  sessions, 100 % reproducible, and the failure was mislabelled
  `UNSUPPORTED_CAPABILITY`, which sends the user off to clear a gen_py cache.
- `catia_close_sketch` after `catia_sketch_edit` left the consuming feature stale:
  the part is rebuilt now, so an edited profile shows up without a separate
  `update_part` (the reporter measured 12000.0 instead of 11214.6 and concluded
  the edit had been ignored).
- The profile echo lost its counts and its bbox when the sketch was closed, and
  reported only the elements drawn after a reopen. The summary is now built from
  the sketch name captured before closing, counts circles too, and covers what
  this session drew (it says so, since pre-existing geometry's coordinates cannot
  be read back).
- `catia_pad` now reports the volume delta like pocket/hole/shaft.

### Added
- `catia_get_inertia` / `catia_get_bounding_box` take `document=` (alias
  `document_name`): measure a named open document instead of the active one. On
  this build activating a component's part does not change `ActiveDocument`, so
  with an assembly open the part-level measurement was unreachable - the reporter
  had to close the assembly (which unloads all nine parts) and reopen them one at
  a time. The reply carries `document`.
- `catia_sketch_get_geometry` takes `hide_construction` (default true): the two
  construction points every line creates collapse into a count instead of
  tripling the element list.

### Refused instead of wrong
- Product-mode `get_bounding_box(method="mesh")` now errors instead of silently
  returning vertex-sweep numbers: an STL export only works for the active
  document. The message names the per-part call that does give a true box. The
  per-component world boxes remain vertex-based (revolved parts under-report);
  that is stated in the reply, and `document=` is the way to get a correct one.

## [0.3.8] — 2026-10-24 · a wrong sketch can be repaired

The 2026-10-24 report rebuilt a 9-part hydraulic cylinder and named its top pain:
"a closed sketch can never be read or changed again, so one wrong profile means
rebuilding the part". Three of its items were P0.

### Added
- `catia_sketch_edit(sketch_name)` reopens a **closed** sketch: `OpenEdition`
  on a closed sketch works (measured on B30), existing elements stay, new ones
  are appended, and the consuming feature rebuilds on close.
- `catia_sketch_delete_element(index, sketch_name)` deletes ONE element - the
  wrong third line - and leaves the rest of the profile and the features built
  on it alone. Index 1 (the absolute axis) is refused.
- `catia_get_bounding_box` and `catia_get_inertia` now work on a **Product**:
  each component's local values are moved into world space by its placement and
  aggregated. Verified on B30 with two blocks at a known offset: volume 16000,
  COG (50, 0, 7.5) exactly. `component=` returns a single instance's world box.
  `catia_list_components` also publishes the rotation and reports broken links
  per component instead of aborting the listing.

### Fixed
- `catia_circ_pattern` failed 100 % of the time at `UpdateObject`, for two
  independent reasons: the rotation axis was minted in a throwaway document that
  was **closed before** the pattern was built (a Reference dies with its
  document), and the seed passed to `AddNewCircPattern` was the **body**, not
  the last feature. Now the axis lives in the part's own `PatternAxis` set and
  the seed is `body.Shapes.Item(last)`. Measured: 6 instances of a Ø6 hole in a
  Ø80 disc remove 1131.0 mm3, exactly 5 x pi*9*8. `user_pattern` had the same
  seed bug.
- `catia_close_document` no longer reports "closed" for a document CATIA kept:
  it counts the document before and after and, when it is still there, says
  `CLOSE NOT CONFIRMED` and points at the assembly that references it - that
  lock is what made the next `SaveAs` fail with a message about nothing.
- A `symmetric` pocket whose primary half is air now keeps the half that has
  material and says so, instead of rolling the whole feature back.
- `catia_sketch_get_geometry` works on any sketch by name, defaults to the last
  sketch created, and reports each element's type and construction flag.

### Diagnostics
- `catia_close_sketch` echoes the drawn profile: element counts by kind, the
  bounding box of its endpoints and the total outline length
  (`Profile: 4 line(s); bbox (-20.0000, -10.0000) .. (20.0000, 10.0000); outline 120.000 mm`).
  The coordinates come from the drawing calls because CATIA's Point2D cannot be
  read back, and the reply says that rather than promising more.
- `catia_create_sketch`'s description spells out the plane -> world axis table
  (zx is H = Z / V = X, not H = X) and the SolidWorks-vs-CATIA "Top plane" trap.

## [0.3.7] — 2026-10-24 · edges stop being indices

The 2026-10-23 topology report (a 11-part quadcopter rebuilt from a written spec)
named one thing above all: "`Edge.N` is not a stable id". That is now true in the
tools instead of only in the report.

### Added
- **Edge fingerprints.** `catia_list_edges` returns, per edge, the faces that
  bound it and a paste-back-able `fingerprint` (`L17.6112|Pad.6;2|Sketch.7;2`).
  `catia_fillet` and `catia_chamfer` accept it - also as
  `{"length_mm": 17.6112, "faces": ["Pad.6;2"]}` - and resolve it against the
  body *at call time*. That removes the "fillet one edge, re-list all 67 edges,
  fillet the next" loop: the reporter measured 7 full listings (~50 000
  characters each) and 34 calls for 17 fillets on one part. Matching is by
  bounding faces, with the length only breaking ties, because a neighbouring
  fillet legitimately shortens an edge (17.6112 -> 9.8429 mm in their case).
  Verified on B30: a fingerprint captured before a topology change still resolves
  and fillets the intended edge after it.
- `catia_list_edges` filters and pages: `min_length`, `max_length`, `faces`,
  `offset`, `limit`, `include_nls`. The reply also carries `count` / `matched` /
  `total` / `raw_total`, so a shortfall is visible instead of silent.
- `catia_list_faces` reports `count`, `raw_total` and - when they differ - how
  many searched cells were construction geometry. The report counted 14 faces on
  a solid and got 10 listed; on B30 a plain block lists 6 of 6 with
  `raw_total` 6, so the numbers now disagree out loud if they ever do.

### Fixed
- `through_all` + `direction="both"` cut one way only. Measured on B30,
  40 x 20 x 10 block, Ø10 from a mid-plane sketch: 392.7 mm3 instead of 785.4.
  It is built as two up-to-last cuts now, and the reply says
  "through all, both directions". (The reporter's Ø12.7 x 25.4 read 1608.8
  instead of 3217.6 - exactly the same factor of two.)
- `catia_delete_feature` could not delete a sketch: `_delete_feature` only
  walked `body.Shapes`, and sketches live in `body.Sketches`. A wrong profile
  was therefore permanent (reported 2026-10-23). It now finds sketches, and its
  reply says what it found: "Deleted sketch '草图.2'."
- Deleting a feature that takes its support sketch with it now says so:
  "CATIA also removed 1 sketch(es) that belonged to it: 草图.3". The cascade is
  conditional - a pad + pocket test on B30 kept the pocket's sketch - so the
  message is built from a before/after comparison rather than assumed.
- `catia_get_bounding_box` mixed sketch vertices into the vertex sweep: a wire
  vertex that lies outside the solid stretched the envelope (reported as
  unusable for acceptance). Construction cells are filtered with the same rule
  the edge/face listings use, and the count of ignored points is reported.
- A failed pad no longer dies formatting an unknown volume ("unsupported format
  string passed to NoneType"). The volume was measured *after* the factory call,
  where the measurable can fail because the new feature is not updated yet; it
  is measured before, and an empty body counts as 0 rather than "unknown".
  Pad, pocket and hole all report `UNVERIFIED:` when the volume really is
  unavailable, instead of claiming success.
- `catia_measure_distance` returning 0.0000 for "parallel" faces is explained
  instead of left ambiguous: two parallel non-adjacent faces of a 10 mm block do
  measure 10.0000 here, so 0 means the elements touch, overlap, or both names
  resolved to the same element - which is what a stale `Face.N` does.

### Changed
- `catia_fillet`'s `propagation` default is now `"none"`. With `"tangency"` a
  single picked edge fillets its whole tangent chain - reported as the cause of
  a non-target edge being rounded and the part losing its symmetry. Pass
  `"propagation": "tangency"` to get the old behaviour back.
- A batch fillet that cannot rebuild falls back to one edge at a time instead of
  losing the batch. The report's numbers: three edges of one face answered
  "0/3 done ... would not rebuild" as a batch and all three took one by one.
- `catia_create_sketch`'s reply names the sketch axes: `zx` is H = Z, V = X,
  normal = Y (measured), not the "H = X" a spec author assumes. A 90-degree
  wrong barrel came out of exactly that.

### Diagnostics
- `catia_close_sketch` warns when the profile it just closed has a near-miss
  endpoint pair, with both coordinates and the gap: "(10.2390, 5.0835) and
  (10.2390, 5.0800) are 0.0035 mm apart". The coordinates are the ones the
  sketch tools were given - CATIA's own `Point2D` cannot be read back through
  automation on this build (`GetCoordinates` needs a by-ref array it never
  fills), so this is computed on our side. A 3.5 um gap is enough to make Pad
  fail with "open polyline or an arc chain with a gap", which named no vertex.
- `catia_gsd_join` validates its argument instead of raising `KeyError`:
  `'surfaces'` is accepted as a synonym for `'elements'`, and a missing/too-short
  list is explained.

### Known limits (unchanged, honestly)
- There is no solid loft. GSD's multi-sections surface exists (B30 only, see
  0.3.6) and exposes no end-tangency condition through automation, so a part
  whose main feature is "three sections held tangent at both ends" cannot be
  rebuilt faithfully; blend and two-section lofts are the available approximations.
- `catia_run_plan` cannot interpolate one step's `list_edges` result into the
  next step's arguments. With fingerprinted edge references a plan can now carry
  the fillets directly, which covers the reported case, but there is no general
  `$ref` expression language.

## [0.3.6] — 2026-10-23 · the pocket gets its flip back

The 0.3.5 acceptance report found one real regression (the volume gate had made
0.3.3's pocket auto-flip unreachable) plus four smaller items; the second report
is about a launcher `.exe` being locked by a running client during `pip install`.

### Fixed
- `catia_pocket` flips to the side that has material again. 0.3.4 started
  judging every cut by the volume delta, and the new "removed nothing" branch
  **raised before** the old flip-and-retry block could run, which left that
  block unreachable dead code. Measured on V5-6R2022 (B32): a profile sketched
  on the top face, `pocket(depth=5)` → "removed no material, rolled back", the
  same call with `direction="reverse"` → 251.3 mm3. The flip now runs before the
  rollback, the retry is judged by the same volume gate, and the caller is only
  told it failed when *both* sides are air. An explicit `direction` is still
  never second-guessed. Pinned by `tests/test_reported_issues_2026_10_23_v035.py`
  (five stub-driven cases: flip, air-both-sides, explicit reverse, unverified,
  probe reason).
- An unmeasurable body is no longer reported as "volume verification unavailable
  on this installation". That wording blamed the installation for a cut the same
  session verified 754.0 mm3 thirty seconds later, and said nothing about what
  actually failed. `_volume_probe` now returns the failure reason — SPAWorkbench
  would not open / no reference to the body / `GetMeasurable.Volume` raised /
  measurable returned no volume — and `catia_pocket` and `catia_hole` print it
  under a leading `UNVERIFIED:` instead of burying it in a success message.
- `catia_activate_document` matches case-insensitively. `new_part(name="CaseTest")`
  + `save_document("casetest.CATPart")` leaves a document whose only name is the
  file's, so the string the caller still holds failed. Four tiers now: name,
  stem, then both again case-folded.

### Diagnostics
- A missing GSD element names the document that does hold it. Reported as
  intermittent after three `new_part`/`close_document` rounds in one session;
  the message was a flat "Element 'CA' not found", which pointed at nothing. The
  resolver now reports which document it searched, how many tree names that
  document has (previewed), and — if the name exists in another open document —
  which one, with the `catia_activate_document` call that fixes it. The
  remembered geometrical set is also invalidated when the active document
  changes, instead of being re-matched by a name CATIA reuses in every new part
  (`几何图形集.1`).

### Documented
- `catia_gsd_multi_section_surface` is marked build-sensitive: it works on
  V5-6R2020 (B30) and fails on V5-6R2022 (B32) with `UpdateObject failed` even
  for two coaxial equal-radius circles whose pair `catia_gsd_blend` accepts in
  the same session. Not reproducible here (no B32), so the tool description and
  the failure message now say so and point at blend for B32.

### Changed (docs only)
- README leads with `python -m catia_mcp` as the client entry point and keeps
  `catia-mcp.exe` as the equivalent alternative with its caveat: a running
  executable is locked on Windows, so `pip install -e .` under a live client
  fails at the launcher swap (`safe-delete` / `trash-failed` /
  `Device or resource busy`) and can leave a `~cp_catia_mechdog-*.dist-info`
  husk. Reported 2026-10-23 from a machine where exactly that happened.

## [0.3.5] — 2026-10-23 · the hole finally takes a depth

### Fixed
- `catia_hole`'s `depth` now does what it says. The value passed to
  `AddNewHoleFromSketch` is ignored by CATIA: measured on V5-6R2020 B30, a D4 hole
  asked for depth 4 in a 10 mm block removed **125.7 mm3 = pi*2^2*10**, i.e.
  straight through. The depth actually lives on the bottom limit, which defaults
  to the wall thickness. Setting `BottomLimit.LimitMode = 0` and
  `BottomLimit.Dimension.Value = depth` (with `BottomType = 0`, a flat bottom)
  makes it exact: D4 x 4 removes 50.3 mm3, D4 x 6 removes 75.4 mm3, both
  `pi*r^2*d` to the decimal. A V bottom adds the drill point (55.1 instead of
  50.3), so the flat bottom is part of the fix, not decoration.

  This also explains the reporter's "a second hole in the same body fails": with
  the depth ignored, the first hole went through the wall and the follow-up
  sketch no longer sat on material the way CATIA expected. Two holes in a row now
  both land (verified: 8000 -> 7949.7 -> 7899.5).

### Added
- `catia_activate_document(name)` — bring an open document to the front. With a
  Part and a Product open at once there was no way back to the Part except
  closing the Product. Matches the name exactly, then as a stem, so both
  `P05_stand.CATPart` and `P05_stand` work. 96 tools now.

### Changed
- `catia_sketch_get_geometry` accepts an optional `sketch_name` and reads that
  sketch's `GeometricElements` without opening its editor. Its schema used to be
  `{"properties": {}}` with `additionalProperties: false`, so the documented
  "inspect a sketch" could only ever mean "the one that is open" - the reporter's
  P2-9. Omitting the argument keeps the old behaviour.

### Investigated, not shipped: assembly constraints
Probed properly this time and the report is worth recording, because it narrows
the problem a lot. References into a component's geometry **can** be built: for a
file-backed component, `comp.ReferenceProduct.Parent` is its PartDocument, and
with that document activated, `HSO.Add(part.MainBody)` + `Search("Face")` +
`Item(n).Reference` yields a usable Reference. With those, `AddBiEltCst` really
does create constraints: `type 2 -> 相合`, `type 1 -> 偏移`, `type 6 -> 角度`
(thirteen face pairs succeeded in one sweep). What blocks shipping it is
stability: the same pair that builds in a fresh product fails in another
configuration with "运算不支持作为第参数给出的子元素 Face 的类型", and datum-plane
references (`CreateReferenceFromObject(OriginElements.PlaneXY)`) are rejected for
every type. `AddMonoEltCst` (Fix) never succeeds, whatever it is handed.

So 0.3.4's honest error stays, and the recipe above is what the next attempt
should start from. Anyone who needs a parameterised assembly today should place
parts with `catia_set_component_transform`.


## [0.3.4] — 2026-10-23 · conventions said out loud, failures that roll back

Three themes: an ambiguous parameter made explicit (rotation convention), every
feature judged by the volume it moves instead of by the COM exception, and two
tools fixed by measuring their arguments rather than reading the docs.

Two 3x3 spellings of the same placement are in circulation, and they are
transposes of each other:

- **matrix** — `P_asm = A · P_local + T`, `rotation_rows` is A row-major. The
  maths form, and what this server has meant since 0.3.0.
- **basis** — `P_asm = Rᵀ · P_local + T`, and `rows[i]` is *where local axis i
  points in global*. This is what CATIA itself prints, and what a
  reverse-engineered spec table lists when it says "R 的三行是局部 X/Y/Z 在全局中
  的方向余弦".

Both keep `det = +1` and both are orthonormal, so a wrong choice passes every
sanity check and is still wrong. Field case: a quadcopter's 24-component assembly
was placed from a spec table of the second kind; the server read it as the first,
so the two landing-gear stands that should have risen 189 mm (local Z → global
+Y, the highest points of the machine) lay flat, and the four motors tilted 60°
instead of pointing down. Neither the read-back deviation (5e-7) nor `det` said a
word — the read-back only proves CATIA stored what it was given.

### Added
- `catia_set_component_transform(rotation_convention="matrix" | "basis")` makes the
  choice explicit. Default stays `"matrix"`, so nothing that worked before changes
  meaning; a spec table of the second kind is now passed verbatim with
  `"basis"`.
- `catia_get_component_transform` reports both spellings — `basis_rows` (3x3, local
  axes in global) and `rotation_rows` / `matrix_a` (the maths form) — so a caller
  can copy the one its input format wants. `basis_rows` round-trips through
  `rotation_convention="basis"`.

### Fixed
- `catia_move_component` composed the incremental rotation on the wrong side
  (`A·Rᵀ` instead of `R·A`), so an incremental `rx/ry/rz` rotated about the
  assembly axes the wrong way round. It now post-multiplies per column, which is
  what CATIA's column layout calls for.

### Fixed — field report 2026-10-23 (B32): hole / loft / constraints / dirty state

A second user (V5-6R2022 B32) filed a full test report. Everything below was
re-measured on V5-6R2020 B30 before changing anything; where the two builds
disagree, the note says so.

**A failed feature no longer poisons the document.** The one that mattered most:
`pad` called `part.UpdateObject` unguarded, so a pad on an open profile raised
and left `凸台.N` in the tree, after which *every* later update failed - the
whole part was lost. `pad`, `pocket`, `hole`, `chamfer` and `draft` now go
through one rule: catch the update, then **decide by measuring the volume**
(`_settle`) instead of trusting the COM result. If nothing changed, the dead
feature is deleted and the error says so; if the geometry did change, the
feature is kept and the reply says CATIA raised anyway. Verified: after a failed
hole the tree still reads `凸台.1 / 孔.1` and the next hole can be built.

This also settles the report's "reported an error, but the cut was there": the
exception and the geometry disagree in both directions, and only the volume is
honest.

**chamfer: the mode argument was wrong.** `AddNewChamfer(..., iMode, ...)` was
called with 0 (catLengthLength), so the 45 that the caller passed as an angle was
read as a *second length* - CATIA was asked for a 2 x 45 mm bevel, which cannot
build, hence "UpdateObject failed" on every chamfer. Mode 1 (catLengthAngle)
works: 2 mm / 45 deg on a 10 mm edge removes exactly 20.00 mm3.

**draft: the neutral element must be a face.** It passed a datum plane and an
empty `CreateReferenceFromName("")` parting. Four variants measured; the one that
builds is `(face, neutral_face, 0, None, dx, dy, dz, 0, angle, 0)` with the
angle in degrees (3 deg gave a 52.41 mm3 wedge; 3 *radians* gave 0.91). The tool
gained `neutral_face` and, when it is omitted, picks the largest remaining face
and says which one in the reply.

**hole: two measured limits, documented instead of guessed.** `depth` is not
honoured - AddNewHoleFromSketch accepts it and the bore runs through the wall
anyway (D4 depth 4 in a 10 mm block removed 125.7 mm3 = pi*2^2*10), so the
description now says every hole behaves as through-all and blind holes belong to
`catia_pocket`. The feature is also state-dependent: a second hole in the same
body failed at update with a perfectly valid sketch. The tool now reports the
volume it actually removed instead of claiming success. The old schema note
("through_all is the only depth mode that works") was wrong and is gone.

**Constraints: one honest answer instead of four bare COM errors.** All four
tools were re-probed: `AddMonoEltCst` fails in five argument forms (component,
ReferenceProduct, root product, selection value, `CreateReferenceFromName("")`),
and `AddBiEltCst` fails for every operand pair - an assembly constraint needs a
Reference into each component's geometry and the late-bound Product exposes
neither one nor a Part document to build one from (parts created by
`add_new_part` have no PartDocument at all). They now all raise the same
explanation with the recommended pattern: absolute transforms.

**Saving a never-saved document.** `catia_save_document()` without a path used to
call `doc.Save()`, fail, and retry three times before saying "Save failed after 3
attempts". It now detects that the document has no file, explains that a part
with geometry cannot be SaveAs'd, and tells you to pass `file_path` in the same
call. `catia_new_part` also ends with "save it now", and its `name` argument now
sets 零件编号 (which works) instead of attempting `Part.Name` (read-only on both
builds) and reporting a half-success.

**Bounding box: unbounded wireframe points are dropped.** A body that carries a
GSD line or a shaft's axis exposed vertices at +-1e7 mm, so the box came back as
20 000 km wide. Points at or beyond 1e6 mm (and NaNs) are now filtered, and the
`method` text reports how many were ignored.

**Smaller ones from the same report.** `catia_list_features` reported `"unknown"`
for pads and `0` for holes because `Shape.Type` is not usable on this API; the
type now comes from the NLS name prefix (`凸台.1` -> `pad`) with the raw value
kept as `catia_type`. `catia_screenshot` said nothing when it wrote a `.jpg` for a
`.png` request; it now says so (V5 can only capture TIFF/BMP/JPEG). `pocket`
already compared volumes but reported the exception ahead of the measurement; it
now reports what the volume says and keeps a cut that landed.

Not reproduced on B30: `catia_gsd_multi_section_surface` worked in both
configurations tried (coaxial equal-radius and coaxial unequal-radius circles, 2
sections each), so the report's 7/7 failures look build- or curve-specific. The
tool is unchanged; the report's `spline.closed` duplicate-point note is left open.

### Fixed — field report 2026-10-23: dot-files vanish on a zipball update

Updating by extracting the GitHub zipball over a checkout loses every
dot-prefixed path, because `cp -r src/* dst/` and Explorer both skip them: no
`.gitattributes` (so `setup.sh` arrives CRLF and dies on line 1), no
`.github/workflows/ci.yml` (the only CI), no `.claude/skills/**`. Nothing fails
at import time, which is what makes it dangerous.

- `scripts/package_release.py` builds `dist/MCP_CATIA_MechDog-<ver>.zip` from an
  `os.walk` that sees dot-files, writes a `.sha256` next to it, and refuses to
  package if any required dot-path is missing.
- `tests/test_release_archive.py` (5 tests) pins that: the collector sees
  `.gitattributes`, `.github/workflows/ci.yml` and `.claude/skills/**`, skips
  `.git/`, `.venv/` and `__pycache__`, and the built zip really contains them.
- README "Updating an existing checkout": `git pull`, or `cp -r src/. dst/`
  (the `/.` is the whole point), or use the packaged release.

### Added (tree hygiene)
- `catia_set_visibility(kind=...)` hides a whole class of tree bookkeeping in one
  call: `sketch` (every sketch in every body, including those inside geometrical
  sets), `datum_plane`, or `construction` (both). Hiding them one name at a time
  is not usable - a part carries a dozen or more, and the HSO cannot resolve a
  sketch by name. Field case: a drone assembly looked like it had comb-shaped
  surface artefacts at each motor; they were sketch profiles and reference-plane
  borders drawn over the solids.

  Note two things measured while doing it: hiding a geometrical set does NOT hide
  the elements inside it (each keeps its own visibility), and
  `part.OriginElements.PlaneXY` is accepted by `Selection.Add` + `SetShow(1)` and
  then does nothing, so the datum planes still have to be hidden from the tree.

### Fixed (diagnostics)
- `catia_fix_constraint` now explains itself instead of failing with a bare
  "方法 AddMonoEltCst 失败". Measured on V5-6R2020 B30: the call returns E_FAIL for
  a component inserted with `AddComponentsFromFiles`, whether or not the component
  is put in the selection first, and the late-bound `Product` exposes no
  `.Reference` to pass instead. A placed assembly therefore has no constraints -
  which moves nothing, because `catia_set_component_transform` writes absolute
  positions; only the tree shows the root as not fixed. Field case: a 24-instance
  quadcopter whose 24 transforms all applied to 5e-7 and whose final Fix then took
  the script down.

## [0.3.3] — 2026-10-23 · the documentation matches the toolset

A user installed 0.3.2 on V5-6R2022 (B32) and reported three numbers for one
fact: the README header said 87 tools, its group table added up to 86, and the
server registered 94. Two of the three were stale documentation and one was a
real bug in what the server reports about itself.

The history, measured at each tag: v0.3.0 shipped 87 tools (the header was right;
the table's Assembly row said 11 when it was 12, so it had summed to 86 from the
day the fork was cut), v0.3.1 shipped 90, v0.3.2 94. Upstream 98 − 16 dropped
+ 12 added = 94. No tool was added or removed here — only the numbers describing
them, and one of those numbers was the server's own version.

### Added
- `tests/test_readme_tool_counts.py` compares the documentation with the code:
  the header count, every row of the group table, the "Added" table, the
  `serverInfo` version and `setup.sh`'s clone target. A user installing 0.3.2
  found the README claiming 87 tools, its group table adding up to 86, and the
  server registering 94 - three numbers for one fact, in the one project whose
  selling point is verification.

### Fixed
- `serverInfo.version` reported the **mcp library's** version (`1.30.0`) for a
  0.3.2 server: `Server("catia-v5-mcp")` was constructed without a version and
  the library fills in its own. A client reading `initialize` had no way to tell
  which build it was talking to. Now `Server("catia-v5-mcp", version=__version__)`.
- `setup.sh` cloned **upstream** (`daiemon12/catia-v5-mcp-server`) into
  `$HOME/catia-v5-mcp-server` - the 98-tool server with the defects this fork
  fixes, installed by this fork's own script. It now clones this fork and uses its
  own directory, so an existing upstream checkout is left alone.
- The tool count in `README.md` (87 → 94, and 86 → 94 in the group table, whose
  Assembly row had said 11 when it was 12), in `FORK.md` and in `PUBLISH.md`.
  The count had been wrong in the table since the fork was cut; the header went
  stale as tools were added in 0.3.1 and 0.3.2.
- `setup.sh` lost its line endings on the way to a Windows user: with the default
  `core.autocrlf=true` a clone turns it into CRLF and `bash setup.sh` dies on the
  first line with `$'\r': command not found`. Added `.gitattributes` pinning
  `*.sh` to LF.

## [0.3.2] — 2026-10-22 · edge identity, batched fillets, first release

Everything here came out of building two real parts from a written spec - a
two-blade propeller (P03) and a nine-solid drone body (P01). Each entry names the
measurement that forced it.

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
- `catia_mcp.__version__` said `0.2.2` while `pyproject.toml` said `0.3.1` - it had
  been stale for two releases, so anything that asked the package its version got
  an answer two versions wrong. Both now read `0.3.2`, and a test compares them so
  the next bump cannot leave them apart.
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
