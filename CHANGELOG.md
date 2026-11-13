# Changelog

All notable changes to this project are documented here.

## [0.3.118] — 2026-11-14 · a word in one boolean, and the sentence that called a working cut grazing

The last item of the 2026-11-12 report (P1-4): ten strokes of one word cost ten
booleans, ten cutter bodies and ~19 elements each. `catia_surface_engrave` now takes
`curves=[...]`.

### Which route works was measured, not guessed

Three cells on B30 2026-11-13, one fresh document each
(`scripts/b30_v0420_probe_batch_engrave.py`), three 50 mm traces 0.4 mm deep on a
60×40×10 block:

| route | result |
|---|---|
| `catia_gsd_join` on the three tubes | **REFUSED** — `方法 UpdateObject 失败`, feature removed from the tree |
| three CloseSurfaces in **one** tool body | 75.3982 mm³ body → one boolean removed **37.699 mm³** |
| three separate engraves (control) | removed **37.698 mm³** |

Joining disjoint tubes is not a route at all; a shared tool body is. It is the same
cut to a hundredth, from one boolean instead of three — measured 24000.0 → 23962.3
mm³ either way, and `scripts/b30_v0420_audit.py` 5/5.

What it actually saves, stated because the report's own numbers suggest more: a
cutter **body** and a boolean **feature** per curve, plus the update each of those
costs. It does **not** save the helpers — every curve still needs its own
point-on-curve, normal plane, circle and tube, because the sweep needs them.

Two design consequences worth knowing:

- Every name is resolved **before** anything is built, so a typo in the tenth name
  costs nothing instead of nine tubes and a cleanup.
- One boolean can only be judged on its **total**, so the reply says "THE NUMBER
  ABOVE IS A TOTAL" instead of dividing by the count and calling that per-curve.
  `curve=` remains the way to have one curve judged by itself. A side effect of
  measuring the body instead of the length: overlapping strokes count once, so the
  prediction stays exact where ten separate calls would each claim their own half.

### The defect this release caught in its own reply

The first end-to-end run said: *"The body touches the material and still removes
nothing: the curves GRAZE it instead of running through it"* — on a call that had
just removed 37.699 mm³. The gap measures 0.0000 mm **by definition** when a curve
lies on the surface, so the gap alone is not a verdict. The grazing sentence now
belongs to the failure path only, and both branches are pinned by tests and by
check 3 of the audit. This is the same class of error as 0.3.113's two seam
standards: one measurement, two conclusions.

### Also

- `_engrave_common` / `_engrave_cutter` / `_close_tube` now hold the parts both
  engrave paths share, so the POINT_ON_CURVE matrix and the "OPEN guides are
  normally FINE" correction exist once. A second copy of a correction is a second
  chance to keep the wrong one.
- A missing `depth` was escaping as `KeyError` (`float(args["depth"])` on a missing
  key is not caught by `except TypeError, ValueError`); it is now a coded refusal.
- Ceilings: new entry for joining tubes, with the `UpdateObject 失败` and the
  75.3982/37.699/37.698 numbers.

## [0.3.117] — 2026-11-13 · one open curve is enough, and an engrave's helpers are load-bearing

Two more items from the same reports. Both were settled by changing ONE thing per
document, and both overturned the explanation given in the report.

### An open curve anywhere in the sketch kills pad and pocket (P0)

The 2026-11-13 report's Arm7 sketch held one closed region plus the two open B-splines
that divide it into three, and the pocket answered `NO_MATERIAL`. Its explanation was
the region topology — "CATIA cannot find a usable closed contour when there are open
curves dividing the area".

Measured on B30 2026-11-13, four fresh documents, one variable each
(`scripts/b30_v0419_probe_multiregion.py`), a 60×40×10 block cut from a sketch on its
top face:

| sketch | pocket result |
|---|---|
| circle alone | **cut 603.2 mm³** |
| circle + open spline **through** it | 0 mm³, `NO_MATERIAL` |
| circle + open spline entirely **inside** it | 0 mm³, `NO_MATERIAL` |
| circle + open **line** through it | 0 mm³, `NO_MATERIAL` |

So it is neither the division nor the spline: **one free endpoint anywhere makes the
whole profile unusable**. A dividing curve is not a special case, it is just an open
curve that happens to cross something.

`catia_pad` and `catia_pocket` now read the drawing record **before CATIA is asked**
and refuse with `SKETCH_HAS_OPEN_CURVES`, naming the free endpoints and carrying the
four numbers above. The failure therefore costs no rollback and no feature node — which
is the report's second complaint ("试一次 → 改参数 → 再试" could not be done), and the
escape hatch `allow_open_curves=true` exists so a refusal is never final.

There is deliberately **no "cut only this region" argument**: with the open curves
removed the sketch is one closed contour, so a sub-region has to be its own sketch —
which is what the report did by hand (three pockets, 1164.92 mm³ against a 1142.849
source, +1.93 %).

The other half of that report — *"CATIA removed the sketch with the rollback"* — did
**not** reproduce: in both cells here `草图.2` was still in the tree afterwards, and the
reply (since 0.3.114) only claims a removal when it measured one.

### An engrave's helpers can be hidden, never deleted (P1-4)

The 2026-11-12 report counted ~194 intermediate elements after 10 strokes and asked for
`cleanup=true`, on the reasoning that they "are only referenced by that boolean, so
deleting them cannot break anything else".

That reasoning is exactly backwards, and it was measured: four separate documents,
deleting the swept tube, the normal plane, the point-on-curve or the profile circle —
**each one** answered `Feature 'X' deleted, but the part update failed afterwards`, and
the part never updated again. The volume only *looks* right because a failed update
keeps the last good state. They are the boolean's history; being referenced by that one
feature is precisely what makes them load-bearing.

Hiding is a different thing and was measured before it shipped: with all four hidden,
the same part answered `Part updated successfully` and its volume was unchanged to the
last digit (23987.4336290779 mm³ either side), with the solid still in the picture.

So `catia_surface_engrave` gained **`helpers='hide'|'show'|'keep'`** (default `hide`).
The reply names the elements it hid and says why they cannot go, and `catia_diagnose`'s
ceiling now carries the 4/4 measurement instead of a proposal. No purge tool was built:
a prefix purge would delete exactly the elements that cannot be deleted.

## [0.3.116] — 2026-11-13 · a letter is not a broken contour, and a batch close that checks the disk

The three items from the same reports that could be settled without guessing. Each
one is a case where the reply knew less than the record did.

### `catia_close_sketch`: lettering is now reported as lettering (P2-5)

The 2026-11-12 report's 26-line text sketch closed with `closed: false, 16 loose
endpoints, 8 breaks, 1 self_intersection(touch)` and eight gap widths. Every number
was right and every one of them read as a defect, because nothing in the reply knew
the lines were glyphs.

The drawing record now carries `source='text'` on every stroke `catia_text_strokes`
draws, so the close-time check can tell a letter from a broken contour:

- **`purpose='engrave'`** says it explicitly. **With no instruction**, a sketch that
  is *nothing but* text strokes is treated as an engraving on its own, because the
  record says so. A mixed sketch (glyphs plus a closed loop) stays a profile — that
  one really might be a pad profile with something else wrong, and the reply says
  which of its elements came from text.
- The breaks are still reported, in full, as data — they are the evidence. What
  changes is the verdict: `data.purpose = 'engrave'`, `data.expected_open = true`,
  `breaks.expected = 'open'`, and a **NOTE** instead of the warning, naming the free
  ends as the free ends of strokes and the gaps as the space *between* strokes.
- A stroke that crosses itself is a NOTE in engraving mode ("two grooves meet where
  the strokes do") and stays a WARNING for a pad profile ("fix the crossing before
  the feature"). Same arithmetic, different question — an engraved **X** is two
  crossing grooves and is exactly what was asked for.

Measured on B30 2026-11-13 (`scripts/b30_v0418_audit.py`, checks 1–3): "CATIA" at
height 6 → `purpose=engrave, expected_open=true, text_strokes=10, breaks.closed=false,
breaks.expected=open`, no `WARNING: the profile is not closed`; glyphs plus a circle →
`purpose=profile, text_elements=3`.

### `catia_solid_loft`: the reply names the sections CATIA kept (P2-7)

The report put 45 sections in, got "through 45 section(s)" and a volume, and had no
way to tell whether one had been dropped — it compared its own numeric integral
(69976 mm³) against 70047.2 (+0.1 %) and called that agreement.

B30 2026-11-13 measured the readback: `HybridShapeLoft.GetSectionFromLoft(i)` is
**1-based**, returns `(reference, orientation, closing point)`, **raises past the
last section**, and `reference.DisplayName` is the section's own name. A 3-section
loft answered at 1, 2, 3 and raised at 0, 4, 5.

The reply now walks that list after its own update: *"CATIA's own section list holds
3: '草图.1', '草图.2', '草图.3' — read back with GetSectionFromLoft, so none was
silently dropped."* If it ever holds fewer than were sent, that is a **WARNING**
naming the missing sections. If the readback refuses outright, the reply says the
count is the number **sent**, not one measured.

The limit is stated in `catia_diagnose`'s ceilings: this works only while the loft
this call created is in hand. Afterwards it is gone — `Part.FindObject` does not
exist on this build and a shape from `body.Shapes.Item(i)` is a bare base `Item`
with no `HybridShape` (both measured in the same probe).

### `catia_close_document`: `all_unmodified=true` (P2-10)

`catia_diagnose` reported 98 open documents and there was no batch way to clear them.
The parameter exists now, and it needs **three** conditions — each one measured, and
each one there because the obvious single test was wrong:

1. `Document.Saved` is `True`. CATIA's own answer, and honest about edits: `False`
   after a feature, `True` right after a save.
2. The document has an **absolute `FullName` that exists on disk**. `Saved` is not
   honest about existence: a brand-new part *and* a brand-new product both answer
   `Saved=True` while their `FullName` is a bare `'Fresh.CATPart'` with no directory.
   Closing on `Saved` alone would have destroyed work that exists nowhere.
3. It is **not the active document** — every other tool acts on that one.

Measured on a 47-document session: **1 closed, 26 kept as modified, 4 kept with no
file, 1 kept as active, 15 catalogues STILL OPEN after `Close()`**. That last group
is its own finding: CATIA's sample `.CATfct` files accept `Close()`, return without
raising, and stay open — the same deferral an assembly-held part gets, with no holder
to name. The reply lists them instead of claiming they closed. Every refusal is named
with its reason; nothing is skipped silently.

## [0.3.115] — 2026-11-13 · the picture went empty, and the body was never the thing that was hidden

Two more items from the same three reports, both settled by pictures rather than by
prose.

### Hiding construction elements takes the solid with them — reproduced, and now recoverable

The 2026-11-12 report (P0-2) listed four sequences and two of them ended in an empty
viewport. Measured on B30 2026-11-13, one frame at a time, on a 2-section solid loft
whose circles live in `GSD_Set` (`scripts/b30_v0417_visibility_fix.py`):

| frame | call | viewport |
|---|---|---|
| 1 | built | solid on screen, **53 978 B** |
| 2 | `set_visibility(name='GSD_Set', show=false)` | **still on screen**, unchanged |
| 3 | `set_visibility(name='GSD_Set', show=true)` | same picture |
| 4 | `set_visibility(kind='construction', show=false)` | **EMPTY — 61 008 B** |
| 5 | `set_visibility(kind='construction', show=true)` | back, **53 978 B** |

So the report's first suspicion was wrong and its second was right: hiding a
geometrical SET leaves a body solid alone, while `kind='construction'` takes it off
screen — a loft's section curves *are* construction elements, and CATIA stops
displaying a feature whose input is hidden. `clean=true` is innocent: measured on
the same solid, 58 522 B clean against 58 896 B plain, both with the solid.

The reply now says this, with those numbers, when a kind is hidden. And the second
half of the report — *"set_visibility(零件几何体, show=true) 这次没能救回来"* — was
true, for a reason worth fixing: the body node had never been hidden, its FEATURE
had. **Showing a body now shows the features inside it**, so the recovery the report
tried first works: measured on the same document, empty frame → `show=true` on
`零件几何体` → the solid back on screen (`and 1 element(s) inside it`).

### Two mirrored chamfers: six cells, all of them succeeded

The 2026-11-13 report (P0) measured 3 of 3: two symmetric top edges 8 mm apart,
chamfer at 70°, the first builds and the second always answers `UpdateObject failed`
— in either order, and as one call with both anchors. Not reproduced on B30
2026-11-13 (`scripts/b30_v0417_paired_chamfer.py`): a 40×10×30 block, two mirrored
10 mm top edges, chamfer 2 mm, A-then-B and B-then-A, at 45° **and** at the
report's 70°, plus both anchors in one call — six cells, 12 000 → 11 980 → 11 960 mm³
at 45° (each chamfer removing the analytic 20 mm³). So "a pair of chamfers" is not
the trigger; which part of that geometry is remains UNKNOWN and is written that way
in the ceilings, with the numbers a re-run has to hit.

### Also in this release

* `crossings()` and the ceilings now carry the visibility facts above, both with the
  frames they came from, so the next caller who blanks a viewport does not start by
  deleting their model.
* the ceiling for "a trim-by-plane tool" keeps its measurement (20 352.95 mm³
  against a hand calculation of 20 353.0) next to the sentence that says
  `catia_split_solid` was always able to do it.

NOT DONE, unchanged from 0.3.114: control-point B-splines, sheet metal, per-stroke
engrave cleanup, `catia_fillet(edge_chain=[...])`, a `close_sketch` "these are
engraving strokes" downgrade, `solid_loft` section counts, and a bulk close for
unmodified documents.

1800 tests, 143 tools.

## [0.3.114] — 2026-11-13 · a wrong warning is a defect, and "no tool" was a tool

Three reports arrived together (2026-11-12 and 2026-11-13). Two of their P0s were
not defects in the sense the reporter meant, and one of ours was worse than the
report said.

### The seam check no longer depends on the order the segments were passed in

`catia_sketch_profile` compared segment *i*'s end with segment *i+1*'s start. The
2026-11-13 reporter passed the 8 elements of `Arm1-S3` in the order his source data
listed them — the normal case for a spec table, a STEP dump or a SolidWorks feature
row — and got *"NOT CLOSED — 8 of 8 junction(s) are wider than 0.05 mm, widest
33.4548 mm"* for a profile that was never broken: `catia_close_sketch` answered
`closed`, and the pad came out at 284.8 mm³, the number he had computed by hand.

Every end is now matched against every **other** end. Measured on B30 2026-11-13,
a 40×20 rectangle given as four segments in the order `right, left, bottom, top`:
no warning, `close_sketch` closes, pad 8000.0 mm³ — identical to the same four
segments in order. When a connected loop is passed out of order the reply says so
(`"they do not follow each other in the order given … CATIA builds the loop from
the elements, not from the order they were drawn in"`), because the old sentence
would otherwise still be believed. A real gap is still reported, now as *"segment
2 ends at (60.0000, -20.0000) has nothing within 0.05 mm — its nearest end is
segment 3's starts at (60.0600, -20.0000), 0.0600 mm away"*.

### `ok: true` with "UpdateObject failed" in the same reply is gone

The report's problem 4: a pad whose profile crossed itself answered `ok: true`,
*"UNVERIFIED: … CATIA also raised during the update: (…, '方法 UpdateObject 失败',
…)"*, 3 such steps went into `run_plan`'s warnings, and the root cause surfaced ten
steps later as `catia_fillet` — *"the active body has no edges to fillet yet"* —
a sentence about the wrong feature. Pad, pocket and hole now refuse with
`UPDATE_FAILED` when CATIA raised **and** the volume could not be read; the node
stays in the tree (it is the only record of what was asked for), the part is marked
in error for `catia_list_bodies`, and the next action is `catia_update_part`, which
names the element that fails. When CATIA did not raise and only the volume is
unreadable, the reply stays `UNVERIFIED` — "no measurement" is not "failed".

### On an assembly, `method='auto'` is now the mesh route

`auto` meant "vertices first", and a vertex sweep cannot see an extremum that sits
on a curved face — so the **default** answer was a systematically small box on any
assembly holding a turned or lofted part. The 2026-11-13 report put numbers on it
(same assembly, same moment: X max 62.0003 by vertices, 67.954 by mesh) and asked
for the default to change. It did. Every component entry now also carries
`lower_bound: true` when its box came from a vertex sweep, so a box that is not an
envelope says so where the numbers are.

**The report's diagnosis of the 7–11 mm shortfall is not reproduced, and it was
specific enough to test.** It said a rotated instance's local axis is paired with
the wrong row/column of the rotation matrix. Measured on B30 2026-11-13 with a box
whose world box is known by hand (100×20×10, second instance 90° about Z at
x = 180…200): all nine boxes — unrotated, rotated and whole product × `mesh`,
`vertices` and `auto` — matched to **0.0000 mm**. Chording, the route's other
limit, costs 0.19 mm on an R35 disc (0.54 %), not 7 mm. So the mechanism is
neither, and the report's expected numbers are probably the source's conservative
`GetBox(0)` rather than a bug in the aggregation; what shipped is the diagnosis
that would have made a real shortfall visible.

### `catia_split_solid` does cut a tip off along an inclined plane

The other P1 asked for a new "trim by plane" tool because "split is NOT a local
cut". For cutting a bottle's mouth off, the plane **does** divide the body
completely and keeping one side **is** the operation. Measured on B30 2026-11-13:
a 40×40×40 pad, cut by a plane through three points tilted 20° (`z = 20 + x·tan20`),
`keep='front'` → **20 352.95 mm³** against a hand calculation of 20 353.0. The
datum plane needed no extension, because it is already infinite. The capability
was never missing — it was one tool away, and the tool's own warning about
"NOT a local cut" is about a patch that covers only part of the cross-section.

### `curve_on_surface`: the support is still not what picks the wall — and the reply now says when the landing is suspect

The 2026-11-12 report watched 36 points move 0.6000 mm (exactly its recess depth)
with `Face.1`, and 0.0000 mm with `support="Face.24"`, and concluded that the
support decides the landing face — which our docstring denied. Re-measured on B30
2026-11-13, in the two configurations where it would matter: an outline 0.6 mm in
front of one wall of a 40×20×10 block moved **0.6000 mm with no support and
0.6000 mm with an end face named**; on a block with a 0.6 mm recess, an outline
drawn **on** the recess floor moved **0.0000 mm** with no support, with the floor
named and with a side wall named. The support still selects the body, so the
report's effect is *not reproduced* — and the ceilings now say both facts, with
which cells they came from.

What the reply gained is the observable that was already in hand: when every point
moved the same non-zero distance (the signature of an outline that landed on a wall
offset from the drawing plane), it says so and points at `catia_list_faces` /
`catia_measure_distance` instead of letting the following `surface_engrave` report
`NO_MATERIAL` with the 10 strokes lost.

### Smaller, and the ones deliberately not done

* `catia_text_strokes(structured=true)` returns `strokes`, `bbox` (`h_min`,
  `h_max`, `v_min`, `v_max`, `width`, `height`), `center`, `baseline` and the
  glyph counts as JSON — the reporter had to parse `bbox 20.100 x 4.500` out of
  prose to place his text.
* `catia_new_product` now says which name later calls take: `document=` wants the
  **document** name, and the root product's PartNumber is not one.
* The 80 probe scripts that hard-coded `F:\WorkBuddy Space\MCP catia-v5-lite` now
  derive the repo root from their own location, and the 14 `b30_v04NN_audit.py`
  scratch directories go to `%TEMP%` (`CATIA_PROBE_ROOT` overrides). "12/12" is
  now reproducible by anyone (2026-11-12 acceptance, P3-A).
* `crossings()`'s docstring states the price of `tol = 1e-3`: a real crossing
  within that distance of a segment end is reported as `touch`, or dropped when it
  is that close to an end of both (P3-B).
* **NOT DONE**: the control-point + knot B-spline entry, sheet metal, per-stroke
  engrave cleanup, `catia_fillet(edge_chain=[...])`, and a centroid/normal column
  on `catia_list_faces`. The last one is not skipped for lack of interest: the
  obvious call, `GetMeasurable(face).GetCOG`, is measured to answer for the body
  that encloses the surface rather than the surface itself, so a `centroid` column
  built on it would be a wrong number in a new place. The B-spline and sheet-metal
  gaps are now ceilings with the measured cost of not having them (+29.3 % volume
  for interpolation against control points; +3.76 % and 15 faces instead of 21 for
  an extrude+cut standing in for a bend).

## [0.3.113] — 2026-11-12 · the seam had two standards, and a T junction is not a crossing

The 2026-11-12 report drew a 73-segment profile (55 lines + 18 arcs, built from a
least-squares fit) and got two answers three seconds apart: `catia_sketch_profile`
said *"every one of the 73 junction(s) meets within 0.0000 mm, so it should
close"*, and `catia_close_sketch` said *"not closed — 60 loose endpoint(s) in 30
break(s), smallest gap 0.0043, widest 0.0960"*.

### The junction check now measures the endpoints that get drawn

CATIA creates an arc from **centre + radius + angles**, so the point it draws is
the *radial projection* of your `p1` onto that circle. `catia_sketch_profile`
compared the `p1` it was given; `close_sketch` compared what was drawn. A fitted
arc whose ends sit a few hundredths off its circle therefore read as closed in one
tool and as 30 seams in the other — in exactly the case the check exists for.

`_plan_segment` now rebuilds each arc's circle through its two endpoints and its
own midpoint, so the drawn end **is** the given end. Measured on B30 2026-11-12,
a 4-element stadium with ends 0.05 mm off the circle:

| | before | after |
|---|---|---|
| profile junction | "within 0.0000 mm" | "within 0.0000 mm" (now true) |
| close_sketch | 8 loose endpoints / 4 breaks / 0.0500 mm | `closed: true`, no breaks |
| pad | — | 5736.1 mm³ vs nominal 5716.8 (+0.34 %) |

The radius grew R20.0000 → R20.1720 to take up the 0.05 mm — 3.4× amplification,
stated in the reply along with the centre shift. A rebuild that would move the
radius more than 5 % is **not** made (that is bad data, not rounding) and the
reply says where the ends will land instead. Ends already on the circle are left
alone and nothing is said.

### A T junction is a `touch`, not a `crossing`

All four "crossings" in the report sat on one legal shoulder corner, and the `I`
in `CATIA` reported `line #21 crosses line #22` where its serif bar *ends on* the
stem. `crossings()` now classifies: interior of both → `crossing` (the hard
warning, unchanged); endpoint of one → `touch` (nothing is crossed, but the
outline is branched, so it is not one closed region for a pad); endpoint of both →
a corner, not reported. Tried on B30: `CATIA` now reports one touch at
(52.7619, 20.0000) and no self-crossing; a figure-eight still reports one
`crossing`.

### The rest of that report

- **A sketch still in edit mode cannot be deleted** — and the reply said "this is
  what a feature that failed its update leaves behind", i.e. it guessed a dead
  reference. Measured: `DELETE_FAILED` while editing `草图.1`, and *"Deleted sketch
  '草图.1'"* the moment it was closed. The sketcher now records the open sketch on
  the connection (`Part.InWorkObject` stays on the body, so COM does not answer
  it) and the delete answers `SKETCH_IS_OPEN` with the one thing that works —
  and with "do NOT close or re-open the document", because the old advice cost a
  caller their work.
- **`catia_text_strokes` reports the box it occupies on every call**, not only
  with `include_points`, and takes `center_at: [h, v]` to place it in one pass.
  The report measured 32.6 mm of text against a 32 mm pocket, found it out by
  printing 36 points, and re-drew a whole sketch at a smaller height.
- **`catia_text_strokes` documents the baseline rule**: it runs along the
  sketch's own **+H**, so on `zx` (H = +Z) text climbs the vertical and comes out
  standing up — 12.5…43.8 mm of Z for a label area of 5…25. A `frame` plane with
  `u` horizontal is the fix; a `rotate` would tip the glyphs too, which is why
  there is none.
- **`catia_surface_engrave` stops asserting one cause for a shortfall.** On a
  *plane* the curve cannot leave the surface at all, so the report's 0.81 was its
  own letter `A` crossbar cutting into the slot its two diagonals had already
  made. The note now names both causes and says the call cannot separate them.
- **A message box now marks the result** `DEGRADED(result unverified)` and says
  what to re-check (`catia_gsd_list_elements` for count and names). The marker is
  also attached when the call FAILS after answering a box — it used to be
  appended only on the success path, which is the case where a caller most needs
  to know the operation was interfered with. What the box said genuinely cannot
  be read — it is another process's Win32 dialog — and that is now a ceiling
  entry rather than a missing feature.
- **`catia_pocket` warns that `direction: "both"` makes TWO features**, which
  shifts any bookkeeping done by feature count.
- A `frame` sketch's reply now says the datum plane and its three points **outlive
  the sketch**; deleting the sketch does not take them.

### Not done, by name

`cleanup`/purge for the ~7 elements one engrave stroke leaves, a batch engrave
entry, and `catia_fillet(edge_chain=[...])` — the last is now a ceiling entry
with its measurements (the whole-feature call never builds; one edge at a time
leaves vertex gaps; whether CATIA has a G0 chain selection is **unknown**, and the
entry says so instead of claiming an API is absent).

Two entries were removed rather than kept: a duplicated `_stroke_data` that had
been shadowing itself (dead code since 0.3.106), and the stranded imports below
it.

Also fixed: `_mm` writes `-0.000` as `0.000`, because the first run of this
release's own audit compared `centred on (0.000, 0.000)` against
`centred on (-0.000, 0.000)` and disagreed over a minus sign on zero.

1784 tests, 143 tools, `scripts/b30_v0413_audit.py` 12/12.

## [0.3.112] — 2026-11-12 · the profile's endpoint form never reached the arc tool

The 2026-11-12 ninth report rebuilt the siphon bottle with 21 elements (3 lines,
18 arcs): 381 edges became **147**, the shortest went 0.5770 → 2.2048 mm, and
R1.0 that had rounded 0 of 381 rounded **46 of 147**. The rebuild worked. Four
things around it did not, and one of them was found while checking the report's
own suggestion.

**The endpoint form of an arc in `catia_sketch_profile` was documented and
refused.** The schema said "an arc may give p1/p2 instead of the two angles";
`_plan_segment` converts p1/p2 into angles and then sets them to `None`, and its
result carries no `p1` key — so `_draw_profile` handed `_draw_arc` three `None`s
and it answered "needs either start_angle and end_angle, or p1 and p2". Measured
on B30 2026-11-12 on a 4-element stadium: the documented form threw before
drawing anything. The plan's own `start`/`end` are those endpoints, and the angle
form still goes the angle way. The reporter never hit it because it used angles —
which is exactly the form §2.1 asks to replace.

**The self-intersection check called a legal seam a crossing** (§2.2). Its
tolerance was `1e-6`; the report's largest junction gap is `0.0004 mm` and a
computed arc/arc intersection lands ~`1e-4` from the endpoint the record holds.
So four "crossings" were reported, all on ONE legal shoulder-to-hook corner at
`(84.0001, 25.9999)`, while shapely found none. `crossings()` now defaults to
`1e-3`: two and a half times the widest seam measured, and far below any real T
junction. Reproduced and fixed in three cells — a 0.0004 mm corner reports 0
crossings where the old tolerance reported 1, an arc running into a line the
same, and a real figure-eight still reports 1.

**The angle convention is now stated where it is read** (§2.1). The report opened
15 of 21 junctions by swapping start/end on its clockwise arcs. `catia_sketch_arc`
always said the rule; `catia_sketch_profile`'s `segments` entry did not, and that
is the tool the caller reads. It now says: the arc STARTS at start_angle and
SWEEPS toward end_angle along `direction`, takes the SHORT way round (≤ 180°)
unless `major=true`, accepts negative angles, and — the reporter's own
suggestion — PREFER `p1/p2`, because the endpoints are what you know and the
angles are derived from them.

**A dead handle answered a bare COM error** (§2.3). After a pad reported
UNVERIFIED, `catia_delete_feature('草图.11')` found the sketch in the tree and
then threw `方法 Add 失败` with no code and no next step. The selection calls are
now wrapped: `DELETE_FAILED`, the element named, whether it is still listed
afterwards, and the two things that answer it (`catia_update_part` names the
element that fails on its own; `catia_list_features` shows what is left).

**The all-edges fillet now says how long it took** (§3). 147 edges came back from
a long call with 46 built and 101 refused, and not one clock reading.

NOT DONE, AND WHY: §3 also asks for the refusal REASON per edge ("was it a shared
vertex, or a neighbour that already has a fillet"). `_edge_table` holds an edge's
length and its two bounding faces — not its endpoints — so "shares a vertex with
an edge that took" cannot be decided from what the tool can read. Guessing it
would be the same class of claim these reports keep catching.

§0 needed nothing: the sketch-coordinate ceiling is already recorded as
`NOT_IN_TYPELIB` and the report's advice is to leave it.

New acceptance script `scripts/b30_v0412_audit.py`, 6/6 — the 0.0004 mm seam
(0 crossings, then pads to 28566.4 mm³ against 28566.37 analytic), the
figure-eight (1), the short/major arc pair measured by arc length (15.7080 /
47.1239 mm for r=10), and the clock.

1762 tests, 143 tools.

## [0.3.111] — 2026-11-12 · the real part: 381 edges, and the count arrived after the cost

The 2026-11-11 eighth report's own model, `SiphonBottle.CATPart`, was run end to
end on B30 — as a COPY (CATIA refuses a second document with the same identifier,
so the copy is renamed), with the original's sha256 checked before and after.

**What the model actually is.** The report says "129 faces"; it has **381 edges**,
three times as many. 2 features, 129 faces, 67 278.8 mm³ all reproduce exactly.

| request | result | time | left behind |
|---|---|---|---|
| R1.5, default `max_edges=200` | **0 / 381** | 186 s (batch attempt only) | 2 shapes, volume unchanged |
| R0.25, `max_edges=400` | **101 / 381** | 458 s | 103 shapes, −3.73 mm³ |

Shortest edge 0.5770 mm, longest 22.0000 mm, and 166 of the 381 are shorter than
`2·R` at R1.5. At R0.25 every edge is longer than `2·R` and it still only rounded
101 of 381 — so on a real outline the short edges and the shared corners both
bite, and **a 381-edge part cannot be rounded by choosing a radius**. The outline
has to lose corners first, which 0.3.108's `catia_sketch_profile` now allows.

**Two things were wrong with the reply, not with the geometry:**

- `"166 of 381 are shorter than 2·R"` sat AFTER the attempt. It is pure
  arithmetic over lengths the call already holds, so callers paid 186 s before
  reading a sentence that could have told them not to start. It now comes first,
  and it is emitted whether or not the edges get retried one at a time (below
  `max_edges` the old reply never mentioned short edges at all).
- the cost estimate said "about 0.59 s per edge", which is the cost of an edge
  that TAKES. On a part where most refuse, 381 edges came to 458 s — 1.2 s each.
  The three places that quoted 0.59 s now say 0.6 s for an edge that takes and
  more for one that refuses, with the 458 s measurement attached. The same for
  `_chamfer_all_edges`, which now warns about `2·L` corners while saying
  "expect those to be refused" rather than claiming a bevel cannot fit — a
  bevel of length `L` does fit on an edge of length `L`; it is the neighbour that
  needs `2·L`.

**New acceptance script** `scripts/b30_v0411_audit.py`, 9/9: the copy is
byte-identical, the model numbers reproduce, the short-edge count precedes the
`max_edges` paragraph (character offsets 297 vs 689), the batch cost is stated,
the refused batch leaves the body untouched and updatable, the ceilings carry
R0.25's numbers, and the ORIGINAL file's hash is unchanged at the end.

1751 tests, 143 tools.

## [0.3.110] — 2026-11-12 · what refuses a projection is the support's KIND

The 2026-11-11 seventh report (§3) recorded a disagreement and asked for the case
that would settle it: it had hit `PROJECT_FAILED` three times, concluded the tool
refuses a planar element on a solid or surface support, and suspected its own
configuration — "reopened from disk, with `InWorkObject` pointing at a
`GSD_Set`". The acceptance case existed since 0.3.101, but it covered one
configuration, so it could not separate the variables.

**Four cells, one document each, all on B30.** Same 40×40 sketch, saved, closed
and reopened, `InWorkObject` on the geometrical set:

| source → support | landed |
|---|---|
| sketch → lofted `Wall` (fresh document) | 84.0 % |
| the same, after the reopen, `InWorkObject` = body | **84.0 %** |
| the same, after the reopen, `InWorkObject` = the GSD set | **84.0 %** |
| sketch → the face it LIES ON (0.0000 mm away) | **100 %** |
| sketch → that face EXTRACTED as a **surface** | **100 %** |
| sketch → a plane **PARALLEL** to the sketch | **100 %** |
| sketch → one of that face's edges EXTRACTED as a **curve** | **refused**, `PROJECT_FAILED` |

So reopening is not a variable and neither is `InWorkObject`. What refuses is the
support's KIND: a curve has no single normal to project along. Why a curve was
refused is a likely reason, not a measurement, and the reply says so.

**Two sentences this project had been shipping were wrong, and are now gone.**
Both came from a single 2026-11-10 run that none of these cells reproduced:

- the `PROJECT_FAILED` text said "parallel makes every point land on the same
  spot, perpendicular flattens the curve onto a line … both are degenerate", and
  its first `next_action` said **"PARALLEL is what fails"**. A parallel plane is a
  plain translation of the element: measured 100 %, twice.
- it also told callers that reaching a parallel plane needs `catia_gsd_extrude`.
  It does not.
- the README's projection matrix had a row "planar sketch → `xy`/`yz`/`zx` or a
  solid's flat face: refused — parallel or perpendicular is degenerate", and a row
  saying a curve extracted from a face lands. The first is false, the second
  depends on the curve: an extracted EDGE was refused. Both rows now carry the
  measurement and, where two runs disagree, say so instead of picking one.

What survives unchanged, because it was measured over eight repeats: the first
projection in a document is a fragment and the second is the real curve; a
translated copy stays a fragment; a curved support accepts but keeps 13–48 %.

**New acceptance script** `scripts/b30_v0410_audit.py`, 8/8, each cell in its own
document: the fresh control, the reopen with body IO, the reopen with set IO, the
co-planar face, the extracted curve (refused, but with a measured distance and a
structured code — never a bare COM error), and the cross-session source
measurement (100.0000 mm from the sketch's own four curves).

1745 tests, 143 tools.

## [0.3.109] — 2026-11-12 · the outline says where it crosses itself

The 2026-11-11 eighth report (§3) wrote its own O(n²) segment-intersection test in
Python before daring to feed a 100-point outline to `catia_pad`, because a hook
that curled into a loop showed up as "the shape looks wrong" and then as a
feature that would not build - never as a warning. `_loop_warning` caught several
OVERLAPPING CLOSED LOOPS by bounding box; nothing caught a chain crossing itself.

**`catia_close_sketch` now reports it**, from the same drawing record it already
uses for the outline echo and the break list. Measured on B30, one document per
cell:

| drawn | close_sketch answered |
|---|---|
| four-segment figure-eight | `WARNING: THE OUTLINE CROSSES ITSELF in 1 place(s): 'line #1' crosses 'line #3' at (5.0000, 5.0000)` |
| the 0.3.108 stadium | `does not cross itself (4 segment(s) checked, from the elements this session drew)` |

The pad that followed the figure-eight reported **UNVERIFIED** rather than a
volume, which is the honest answer: CATIA either refuses a self-crossing profile
or builds one lobe of it.

The check is a pure function in `catia_mcp/geometry2d.py` - a line and a circular
arc are the only primitives, everything else is decomposed into them (a rectangle
into four lines, a spline into its point chain, an ellipse into a 48-gon). Two
segments that SHARE AN ENDPOINT are not a crossing, that is what a closed outline
is; a T junction - one segment ending on another's middle - is kept, because that
is a real defect. Concentric and collinear overlaps are NOT reported: telling them
apart needs interval arithmetic on both curves, and a wrong line is worse than a
missing one.

**What it cannot see, said in the reply itself:** a sketch not drawn in this
session has no record, and the coordinates CATIA holds cannot be read back. So the
structured reply carries `self_intersection_checked`, and the sentence says
"from the elements this session drew" - an empty crossing list never means "clean"
unless that flag is true.

### The two remaining items from that report are not done, and why

- **Polyline-to-spline fitting (§3, P2).** 0.3.108's `catia_sketch_profile` draws
  the curved stretches as arcs already, which preserves the straight ones by
  construction. A spline fitted through the same points interpolates everywhere -
  measured 2026-10-25: a six-point outline padded **+29.3 % in volume** against
  +0.48 % for the same points joined with lines. Trading a heavy tree for a wrong
  shape is not a fix.
- **Selecting edges by geometry (§3, P3).** `catia_list_edges` has had
  `min_length`, `max_length` and `faces` plus paging since before that report -
  `min_length` is one of the three the report asked for. And since 0.3.107 the
  all-edges path names every edge it took, so picking 129 edges one at a time is
  no longer the only route. What is genuinely missing is `convex_only`, which
  needs a dihedral angle per edge: 129 edges x 2 face measurements, for a filter
  the all-edges path has made unnecessary.

143 tools, 1738 tests, `scripts/b30_v0409_audit.py` 7/7.

## [0.3.108] — 2026-11-12 · the mixed profile was never missing, it was unwrapped

The 2026-11-11 eighth report put this first (§2.1): "a profile that is straight
on one side and curved on the other has no workable combination". Both halves of
its evidence are right — `catia_sketch_spline` interpolates everywhere, so a
straight run through intermediate points comes out bowed, and
`catia_sketch_strokes` draws every segment as a chord, so a curve comes out
faceted. The conclusion is not: drawing each element with the primitive for its
own kind was never tried, and it is the oldest route there is.

**Measured before any code was written.** A 60×40 stadium as four elements —
two 60 mm lines and two r=20 half circles, endpoints typed to the same numbers:

| what | answer |
|---|---|
| `catia_close_sketch` | "2 arc(s), 2 line(s); bbox (-20, -20)..(80, 20); outline 245.664 mm" |
| `catia_pad(height=10)` | **36566.37 mm³ = 60·40·10 + π·20²·10**, which is the analytic area exactly |
| same profile, 0.05 mm gap at one corner | reported not closed, with both coordinates |

**`catia_sketch_profile` does it in one call.** `segments` is a list of `line`
(`p1`/`p2`) and `arc` (`center`, `radius`, angles or `p1`/`p2`, `direction`
cw|ccw, `major`), each drawn with the primitive for its kind, then every
junction measured and reported:

| part | drawn | what follows |
|---|---|---|
| stadium, 4 elements | 2 lines, 2 arcs, all 4 junctions within 0.0000 mm | close → pad → 36566.37 mm³ |
| stadium, 0.06 mm gap | 1 of 4 junctions, both coordinates named | pad reports UNVERIFIED rather than a volume |
| bad third segment | refused before the first primitive | nothing added: the sketch still held only its absolute axis, and stayed usable |

The last row is the point of planning first: every segment's endpoints are
arithmetic, so a zero-length line, a radius ≤ 0, equal start and end angles (a
full circle, not an arc), an unknown type or a direction that is not `cw`/`ccw`
is refused before COM is touched — a bad third segment cannot leave the first
two behind.

Both tools the report tried now point at the third route in their own
descriptions, and a ceiling records the measurement.

Still open by name: the self-intersection pre-check, CJK glyphs, and the
polyline-to-spline fitting from the same report.

143 tools, 1725 tests, `scripts/b30_v0408_audit.py` 8/8.

## [0.3.107] — 2026-11-12 · the all-edges fillet says which edges, because none of them worked

The 2026-11-11 eighth report (§2.2) sent `catia_fillet(radius=1.5)` and `(0.5)`
to a 129-face polyline part and got the same bare `COM_ERROR` twice - no edge, no
length, no next step - while the one-at-a-time machinery that would have named
them sat unused on the other branch.

**The entry never worked, and it is gone.** Leaving `edge_name` out passed a
Reference to the last feature into
`AddNewSolidEdgeFilletWithConstantRadius` and called that "all edges of the last
feature". Measured on B30 2026-11-12, that call fails AT CONSTRUCTION on every
shape tried:

| part | edges | whole-feature call |
|---|---|---|
| 20 mm cylinder | 2 | 方法 AddNewSolidEdgeFilletWithConstantRadius 失败 |
| 80×40×20 rectangle | 12 | same |
| polyline pad | 54 | same |
| polyline pad | 246 | same |

Four controlled cells rule out the arguments: feature reference versus whole
body, propagation 0 versus 1 - four failures, so neither the reference kind nor
the propagation was ever the reason. `catia_chamfer` has the same dead end,
measured separately because `AddNewChamfer` is a different method (six
arguments, mode 1 = length+angle): feature ref and body ref both fail on the
cylinder and on the rectangle, and the volume does not move at all.

**Both tools now enumerate the body's edges** and hand them to the batch path,
which already rebuilds once, falls back to one at a time, and names every edge
that took and every edge that did not. Measured end to end, one document per
cell:

| part | result | volume |
|---|---|---|
| cylinder, 2 edges | 2/2 | removed 210.9223 mm³ against 215.77 predicted |
| rectangle, 12 edges | **4/12** | removed 77.2567 mm³ = exactly four 40 mm edges |
| chamfer, 2 edges | 2/2 | removed 485.8997 mm³ against 502.65 predicted |
| polyline, 54 edges | 12/54 in 15 s | 42 refusals named with length and faces |
| polyline, 246 edges | over budget: one batch, then an honest stop | — |

**4/12 is not a radius problem, which is worth knowing before trying smaller
radii.** Five documents, same box, all 12 edges: R0.25 / 0.5 / 1.0 / 1.5 / 3.0
each took the same four 40 mm edges and removed 2.1460 / 8.5841 / 34.3363 /
77.2567 / 309.0266 mm³ - every one exactly `4*40*(R² - πR²/4)`. The eight that
refused are not short: the shortest edge on the box is 20 mm and `2*R` is 3 mm.
Edges that share a vertex fight over the same material, so a box cannot be
rounded all round in one pass at any radius.

**Reply size and time are bounded.** The retry costs about 0.59 s per edge -
measured 54 edges in 15 s and 246 edges in 145 s, leaving 44 features - so
`max_edges` (default 200, and on `catia_chamfer` too) decides whether that is
paid; above it the batch runs once and the reply says which number it stopped
at. Long lists are truncated: the first twelve edges that took and the first
eight that refused are named, the rest counted. A 246-edge part used to produce
twenty thousand characters of near-identical lines. (An earlier draft of the
limit was 40, set from 6.4 s per edge - that figure came from re-running
`catia_list_edges` before every edge, which is not what this path does.)

Also from this round: the all-edges reply prints the shortest edge and how many
are shorter than `2*R`, and two ceilings record the measurements - the
whole-feature call as `UNSUPPORTED_ON_B30`, and "rounding every edge at one
radius" as `AVAILABLE_WITH_LIMIT` with the five radii and their volumes.

Not done, still open by name: the mixed straight/curved profile from §2.1 of the
same report, the self-intersection pre-check, CJK glyphs, and the polyline
improvement tools. The envelope test that pinned every `UNSUPPORTED_ON_B30`
ceiling to one date (2026-11-10) now checks that a date is present, because
pinning one date made the next measured entry fail for having a later one.

## [0.3.106] — 2026-11-12 · the outline goes on the face, because the primitive was there

The 2026-11-11 report (seventh in the series) put it exactly: "0.3.105's README
wrote the conclusion correctly - text that has to be legible on a curved face is
COMPUTED on the surface - but the COMPUTED step has no tool". It quantified the
cost: six strokes of one word at one fixed offset came back 0.93 / 1.45 / 1.40 /
1.20 / 1.42 / 1.24, moving another 0.5 mm took the whole thing from buried to
floating, and the fix was three deletions, three rebuilt splines and four re-cuts.

**`catia_curve_on_surface` closes it.** Each point is snapped with
`AddNewPointOnSurfaceWithReference`, the stroke is threaded into one open spline,
and the reply says how far every point travelled. Measured end to end on B30: an
18-point `C` placed on a plane 60 mm in front of a closed lofted bottle moved
**26.053-26.137 mm** and `catia_surface_engrave` at 0.4 mm then removed **3.026 mm³
against a 3.042 mm³ prediction - ratio 0.995, with no tuning at all**. What the
report spent four calls calibrating is now one call per stroke.

Three things about the primitive had to be measured before the tool could be
described honestly, and all three contradict what the signature suggests:

| what the signature suggests | what was measured |
|---|---|
| `direction` aims the ray | ignored: +Y, -Y, +Z, +X all moved the same point **26.0052 mm** |
| naming a face picks the wall | ignored: Face.1, Face.2, Face.3 gave the same **26.0052 mm** |
| a body reference is a support | refused: every point failed `UpdateObject 失败` |

So a point lands on the NEAREST point of the body's surface (one already on it
moved 0.0022 mm), the support only selects the body, and a point inside a thin
body can come back on the far wall. The reply says all of that. Coordinates cannot
be read back either - `GetCoordinates` fails on a plain point and returns zeros on
a point-on-surface - so the tool reports measured displacement rather than a
position it would have to invent.

**Two claims in the report were wrong, both in the tool's favour.**
`catia_sketch_ellipse` did not exist because nobody had wrapped it, not because
Factory2D lacks it: `CreateClosedEllipse(cx, cy, majorX, majorY, majorR, minorR)`
is right there among its 37 members. A 30×10 ellipse at 30° pads to
**4712.3890 mm³ = π·30·10·5** with a 52.82 × 34.64 bounding box, i.e. one smooth
wall instead of the report's 28 flats. And `catia_text_strokes` now hands its
points back (`include_points`), so a caller no longer has to import
`catia_mcp.fonts` to get the numbers over MCP.

**One real defect found while writing the probes.** `catia_new_part`'s refusal
said "Pass overwrite=true if you mean to replace it" and the tool had no such
parameter - the flag never reached `_save_document`, and my own probe stopped on
its own leftovers exactly as the 0.3.104 audit had to work around. It does now,
and 0.3.104's audit can drop its workaround.

**Also from the report:** `catia_surface_engrave` prints `dialogs answered: 0`
instead of staying silent when nothing was raised - "the watcher ran and found
nothing" and "the watcher was never armed" used to look identical, and that task
lost two runs to modal boxes.

Not done, and still open by name: the fillet all-edges fallback and the
self-intersection pre-check from the eighth report, CJK glyphs, and the fact that
`catia_sketch_spline` cannot mix straight and curved stretches in one profile.
The eighth report's account of the drawing being misunderstood for seven rounds is
noted; none of its tool-level measurements are affected.

142 tools, 1700 tests, `scripts/b30_v0406_audit.py` 7/7 (twice).

## [0.3.105] — 2026-11-11 · answer the box, and withdraw what five cells deny

Two of the three open items from the 2026-11-11 report, and the third one turned
out to be the opposite of what it looked like.

**The modal dialog is answered, not chased.** `Application` has 82 members and not
one of them suppresses an operation warning - only `DisplayFileAlerts`, which
covers load/save prompts. So "watch it happen on the reporter's file" was never a
fix: an unanswered box blocks the STA worker that runs every tool call, which is
why 2026-10-24 lost 2 h 14 min to one. `dialogs.watch()` already exists and can
answer `#32770` boxes from a side thread; it was used for assembly insertion only.
`catia_surface_engrave` now runs inside it - the call that creates a body, sweeps,
closes a surface, runs a boolean that CONSUMES the tool body and then deletes four
elements by name - and the reply reports how many boxes were answered.

Verified against a real box, not by reading the code: a `SaveAs` onto an existing
path raises `#32770` titled 另存为, the blocked call stays blocked, `watch()`
answers it, `clicks` is 1 and the call returns. The note prints only the COUNT and
says so: this side sends the button message and never reads the dialog's text, so
it cannot claim which warning appeared, and it tells the caller to check the tree
with `catia_list_features` before trusting the result.

**"Keep the projection and the engrave in one session" is WITHDRAWN.** 0.3.101
published that from a single observation and promoted it to a ceiling. Re-measured
in five controlled cells, two geometries, a fresh document each time:

| geometry | before the reopen | after save/close/reopen |
|---|---|---|
| 100x100x20 pad, line on its top face, depth 2 | 376.9911 mm3 | **376.9911 mm3** |
| lofted bottle, projected 32.111 mm glyph, depth 0.4 | 7.9958 mm3 | **7.9958 mm3** |

`part.Update()` before the engrave changed nothing either, so "the reopened tree
has not been recomputed" is not the explanation. Reopening is not the variable. The
row is downgraded from `MEASURED_UNEXPLAINED` to `SEEN_ONCE_NOT_REPRODUCED`, the
advice is gone, and `NO_MATERIAL` on a reopened file now means what it says. This
is the same error as 0.3.100's `catia_gsd_translate` suggestion: an instruction
that survived because nobody had measured it.

**The projection grid, in one place, because advice written from memory has now
been wrong twice.** README carries every element/support pair that has actually
been tried, with the number: 80 mm rectangle onto a 45-degree plane 20.000 ->
68.284 mm; 63.947 mm glyph onto a lofted bottle 4.000 mm (6.3 %) before 0.3.102 and
32.111 mm (48.2 %) after; 26.164 mm glyph onto a padded cylinder's solid face
13.6 %, onto a curve extracted from that same face 13.6 % (extracting changes
nothing), onto a `zx` offset sketch 16.0 % and a `frame` sketch 15.1 %. The reading
is stated plainly: a curved support always accepts the projection and always keeps
little of it, so text that has to be legible on a curved face is COMPUTED on the
surface and swept as an open spline, not projected onto it.

140 tools, 1682 tests, `scripts/b30_v0405_audit.py` 7/7.

## [0.3.104] — 2026-11-11 · a cut that went somewhere else is not a groove

The 2026-11-11 report engraved three strokes into a bottle and read, three
times, "Engraved ... a 0.4 mm deep groove over 11.203 mm of it. Removed 5.624 mm3
(a 0.4 mm tube ... holds half of itself in the material: 2.816 mm3). The tube is
centred on the curve, which lies on the surface, so HALF of it is inside". The
measurement was right; the sentence about what it meant was wrong. 5.624 against
2.816 is a ratio of 2.00, which means the WHOLE tube was inside the material:
what was cut is a cavity under the surface, and the surface looks untouched.

**The judgement now exists.** `_engrave_outcome` reads removed against
predicted and returns one of three bands, and the half-inside sentence is
printed only in the middle one:

| ratio | verdict |
|---|---|
| under 0.5 | **FLOATING** - above the material or grazing it, so the groove is shallow or short |
| 0.5 .. 1.3 | the ordinary reply; a note when it is off 1 by more than 15 % |
| over 1.3 | **BURIED** - an invisible cavity; the model changed and the face did not |

BURIED also says how far to move the curve out (at least one radius, and expect
to need more, because a curve computed from a formula is only as good as the
surface that formula describes) and names the boolean feature, so the cavity can
be taken back out. That last part is the report's §2.5: it asked to delete
`GlyphA_Engrave0` - the name it had passed as `name` - got "Deleted 0 element(s)",
and found the real nodes were 移除.2 .. 移除.7. The reply now says
"'移除.5': catia_delete_feature('移除.5') takes it back out".

The prediction itself had to change too. It was `length * pi * r^2 / 2`, and
`length` is not always readable: a 60 mm SKETCH line left it None, so the ratio
check was skipped and the old sentence came out anyway (measured while fixing
this). It now comes from the cutter's own volume, which was already measured
before the boolean ate it - so the check works even when the curve is a sketch.

**The projection ceiling was wrong in one direction and the report in the
other.** The report hit PROJECT_FAILED on three configurations and concluded the
tool refuses a solid face; re-run here, a curved support accepted every one of
them - zx offset -40, a frame sketch, a padded cylinder's side face, and a curve
extracted from that face - at 13.6 to 16.0 %. What fails is a PLANAR element on
a PARALLEL PLANAR support. But the report's underlying complaint stands and is
now written down: a curved support accepts the projection and KEEPS LITTLE, and
13-16 % of a glyph engraves as nothing. So the failure advice no longer says
"extract the face, then project onto that element" as if it were a fix, and no
longer ends with "move the element to it with catia_gsd_translate" - 0.3.102
measured that a translated copy keeps 3.5 % where the sketch keeps 48.2 %. It
now says what to do instead: compute the curve ON the surface and sweep an OPEN
spline, which is the route the report made work by hand.

**Not reproduced.** The report's `catia_measure_distance` number (25.6670 mm
between a spline sitting on the face and that face) and the modal
「选定的元素不可用于此操作」 did not happen here: on a spline 5 mm above a pad, the
tool answered 5.0000 mm to the top face, 25.0000 to the bottom and 30.4138 /
45.2239 to the sides - all correct - and `GetMeasurable` resolved the spline
itself (Length 45.90 mm, Area and Volume both refusing, where the solid is the
other way round). No guard was added, because nothing here can tell a correct
reading from the reported one.

140 tools, 1654 tests, `scripts/b30_v0404_audit.py` 6/6.

## [0.3.103] — 2026-11-11 · text from a font in the box, and the Rib that adds nothing

`catia_text_strokes` draws a string into the open sketch. The 2026-11-11 report
put the gap as "the title's text still needs its point sets handed over by
hand": `catia_sketch_strokes` had taken a whole outline since 0.3.95, and what
was missing was the outlines. `Factory2D` has no `CreateText` (37 members,
measured 2026-11-10), so they can only be geometry - and they are now computed
from the **Hershey simplex** set, which ships in `catia_mcp/fonts.py`.

**Why a single-line face.** Simplex draws one stroke per stem. An outline face
gives every stem two walls a fraction of a millimetre apart, and a groove 0.4 mm
deep cannot resolve two walls that close: it comes out as one ragged slot. It is
also the ancestor of the SHX lettering CATIA's own drafting uses.

Measured on B30: a 10 mm capital is exactly 10.000 mm tall, `'g'` descends
7 units below the baseline, `'A'` is 18/21 of its height wide, and the
coordinates read back out of the sketch are the ones the font computed. ASCII
32-126 only: a character with no glyph is **skipped and named**, never replaced
by a box that would engrave as something nobody asked for.

**What the caller meets next, and now gets told.** Projecting a glyph and then
engraving the projection refuses a parameter point for some letters. 9 of 12
tried took it (H, E, T, L, A, V, C, S, G); O, 0 and D did not. It is not the
point count - a regular n-gon passed at 7, 9, 11, 13, 15, 17, 19 and 21 vertices,
`'S'` at 20 passed while `'D'` at 14 failed, and a Douglas-Peucker-simplified `'O'`
failed at 8 segments. **Coverage is what moves it**: the same `'O'` on the same
face failed at 81.4 % landed and passed at 57.2 %. The likely reason - a
projection that covers almost everything comes back closed, and a closed curve
takes no parameter, exactly as `catia_gsd_circle`'s own circle refuses - is
**not confirmed** and is recorded as a possibility in the ceilings, not as a
cause. `catia_text_strokes` repeats the numbers in its reply, because the caller
walks into this on the next call.

**`AddNewRib` was measured, and no tool was written.** It exists on
`ShapeFactory` - `AddNewRib(iSketch, iCenterCurve)`, `AddNewStiffener(iSketch)`,
plus `*FromRef` variants and `AddNewSlot` - and it does update into a solid:
1175.0633 mm³ on a quarter arc with a r=3 profile plane crossing it. But
`catia_gsd_sweep` + `catia_gsd_close_surface` on the same frame measured **the
same 1175.0633 mm³**, so a Rib wrapper would save one call and add a
prerequisite check to a feature that already has two ways to fail quietly: a
profile plane TANGENT to the centre curve updates "successfully" into a
0.0000 mm³ body, and one on the xz plane is refused outright.

So the 2026-11-11 report's "the ring handle is still a 9 mm plate" is a
modelling detour rather than a missing capability - sweep + close makes that
tube today - and the ceilings now say so with the numbers instead of
`DECLARED_NOT_MEASURED`.

140 tools, 1638 tests, `scripts/b30_v0403_audit.py` 9/9.

## [0.3.102] — 2026-11-11 · the first projection in a document is a fragment

Asked what was left of the 2026-11-11 report, one item was still open: its P2
"try `catia_gsd_translate` to move the element closer, that is the direction
0.3.100 suggests". It was measured, and **the suggestion is wrong**.

A glyph outline on a plane parallel to a lofted bottle face, produced from five
distances, looked like a success story — 3.3 % at the original distance, then
44.9 % 5 mm closer, 43.1 % at 8, 43.3 % at 10, 43.7 % at 12. Not monotone, which
is what made it worth checking: the copy really was 3.4580 mm from the face in
the 5 mm case and the share did not track the distance at all.

What it tracks is the ORDER of projections in the document. Eight in a row, same
source, same target, one document each:

| source | pass 1 | passes 2–8 |
|---|---|---|
| a sketch | 2.200 mm (3.3 %) | **32.111 mm (48.2 %)** |
| a translated copy | 2.316 mm (3.5 %) | 2.316 mm (3.5 %) — never improves |
| a copy, after the sketch was projected once | 29.951 mm (44.9 %) | 44.9 % |

So a translated copy is the WORST thing to project: 3.5 % against the sketch's
48.2 %, and it stays there for eight tries. 0.3.100's advice sent callers
straight into that. The low-share warning now says the opposite, with these
numbers.

And the first projection made in a document answers with a fragment; every later
one repeats the real value. `catia_gsd_project` therefore **projects twice and
keeps the second**, deleting the first again. The rectangle-on-a-45°-plane case
is what makes this unambiguous rather than a bottle oddity: the first pass
measured **20.000 mm** and the second **68.284 mm**, and 68.284 is exactly the
perimeter a 20×20 square has on a 45° plane (2·(20 + 20·cos45)). The 0.3.97 note
that "a plane tilted 45 degrees worked" was true about the call succeeding —
nothing measured the length until 0.3.100.

Both passes are kept in the reply: `the first pass measured 2.200 mm, the second
32.111 mm`, or `Both passes of this projection measured the same, 17.783 mm` on a
straight cylinder — the one shape where they agree, so the second pass has never
measured *less* than the first in any case tried. The thrown-away element is
deleted and its absence checked, and if it cannot be removed the reply says so.

**It took three attempts to measure this**, which is worth recording: two-call
experiments disagreed with each other three separate times (3.3 %, then 48.2 %,
then 3.3 % for the same sketch), and a two-pass implementation measured 2.200 mm
twice until the probe went through the same `_completed` path a standalone call
uses. Only the eight-in-a-row shape shows the plateau. The mechanism is unknown
and is not claimed anywhere in the code.

Acceptance: `scripts/b30_v0402_audit.py` 9/9 on B30 — 48.2 % on the loft with
both passes printed, one element added (not two), both-passes-agree on the
cylinder, 68.284 mm on the 45° plane, 3.5 % for a copy plus the warning, and the
resulting curve engraved **7.9958 mm³**.

## [0.3.101] — 2026-11-11 · the guard worked in the session that made the curve, and nowhere else

0.3.100 put the completeness guard on `catia_gsd_project` and it worked — in the
one situation the acceptance script tested, where the sketch had been drawn
minutes earlier by the same session. The report did the ordinary thing instead:
reopen yesterday's part and project the sketch that is already there. The reply
came back `Projection ... created. MEASURED: the projected curve is 4.000 mm
long; the element's own length could not be read ... so compare that number with
what you drew` — no share, no warning, no mention that the engrave downstream
would find nothing. The guard was reading a Python-side record of sketches *this
session* drew, and a sketch from disk has none.

**A sketch's own curves measure one by one, so the guard does not need the
record.** `Measurable.Length` on a sketch raises on B30, but the curves inside
`sketch.GeometricElements` answer. Measured 2026-11-11 against hand values: a
40×10 rectangle (100.000) read **100.0000** from four lines, a circle r=10 read
62.8319 (2πr=62.8319), a 90° arc r=10 read 15.7080, an open 4-point spline read
73.7960, a closed spline through a 30×20 rectangle read 113.760. The absolute
axis and the spline's control points refuse and are skipped — they are recognised
by not answering `StartPoint`/`EndPoint`, which a CLOSED spline does answer, so
this is not a test for "open". Construction geometry measures but is excluded:
counting it would grow the denominator and warn about a complete projection.
After a real Save/Close/Open with the session record emptied by hand, the same
rectangle still read **100.0000 from 4 curves, 0 unread**, and the projection
reported `97.5 % of the 100.000 mm`.

The reply now has three states, and the partial one is the important one: an
unread curve makes the total a LOWER bound, which makes the share an UPPER
bound — the direction that HIDES a fragment — so it says `COMPLETENESS UNKNOWN -
at most 20.0 % of the element landed` rather than presenting a ceiling as a
measurement.

**The point a refused engrave had already added is taken back out.**
`ENGRAVE_CURVE_UNPARAMETRISABLE` replied `Nothing this call created was left
behind ... Bodies now: 零件几何体` while `X_at0.5` was still in the tree, and
`catia_update_part` then named exactly that element as what stops every later
update — 2/2 reproducible. The cause is one line of ordering:
`catia_gsd_plane_normal_to_curve` adds the point-on-curve, updates it, and the
outer tool recorded the point's name only AFTER the call returned, so a failure
inside it left an element nothing knew about. The point is now removed by the
function that made it, the reply says which of the two happened ("was removed
again" / "COULD NOT BE REMOVED"), and `modified` reflects it instead of a
hardcoded `False`.

**And "nothing was left behind" is now backed by two checks instead of none.**
Every name is looked up AGAIN after its delete, the cutter body too, and the part
is asked to `Update()`. A tree that will not update cannot be called clean even
when every name is gone, so the sentence now ends `, and the part still updates.`
— and when it does not, the culprit is named with the same walk
`catia_update_part` uses. Only `Bodies` had been checked before; that is why the
2026-11-11 reply could describe a clean part it had just broken.

**Two statements this package was shipping turned out to be wrong**, both
measured on B30 2026-11-11 with one fresh document per case (a failed update is
document-wide, so several cases in one part measure the first failure over and
over):

- *"the guide has to be CLOSED — an open guide sweeps a pipe with two open ends
  and CloseSurface refuses it"* — **false**. An open 50 mm segment lying on a
  generator, 0.5 mm deep, removed **19.5829 mm3** of a predicted 19.635; an open
  65.682 mm spline removed **25.6436** of a predicted 25.793. `CloseSurface`
  closes the ends itself. The message that used to blame open guides now says so.
- *"a curve that came from `catia_gsd_project` works"* — **false**, and the
  0.3.100 `next_actions` sent the reporter down it. What decides is whether the
  curve is OPEN or a SKETCH outline projected onto the face: `gsd_line` YES, its
  extract YES, an OPEN `gsd_spline` YES, a projected SKETCH outline YES;
  `gsd_circle` NO, its extract NO, a CLOSED `gsd_spline` NO, and the projection
  of any of those NO — a closed spline's projection refused too. Both the error
  and `capability_ceilings` now carry the matrix.

**One thing found while measuring cannot be explained yet, so it is only
published.** An engrave that removed 9.991 mm3 in the session that built its
projection removed **nothing** after that document was saved, closed and
reopened: the same projection object still measured 17.7831 mm and still sat
0.0000 mm from the body, a projection made after the reopen failed as well, and
`part.Update()` then failed too. Which of the two changes across a reopen is
unknown, so no cause is claimed anywhere in the code — it is a
`MEASURED_UNEXPLAINED` entry in `capability_ceilings` and a sentence in the tool
description: do the projection and the engrave in one session.

## [0.3.100] — 2026-11-10 · the projection kept 6 % of the letter, and every reply said "created"

The bottle report ran `catia_surface_engrave` four times on a real lofted body
face and got `NO_MATERIAL` four times, while `catia_measure_distance` said the
curve was `0.0000 mm` from the face. Reproduced on a copy of that part: **the
engrave was right and the projection was the problem.** A 63.947 mm glyph
outline projected onto the lofted face produced a **4.000 mm** curve — 6.3 %, one
segment of seven — and every layer below said "fine": the reply said "created",
the distance said 0.0000 (a MINIMUM, so it only says the fragment touches), and
the engrave then built a 4 mm tube that removed nothing.

**`catia_gsd_project` measures what landed.** The reply now carries the projected
length against the source's, and starts with `WARNING: ONLY PART OF THE ELEMENT
LANDED` when the share falls below 25 %. On the report's part that reads
`WARNING: ONLY PART OF THE ELEMENT LANDED - 6.3 % of it. ... the projected curve
is 4.000 mm long, 6.3 % of the 63.947 mm outline this session drew`. A closed
sketch has no measurable `Length` at all (Measurable on a sketch raises on B30),
so the source comes from the outline the session recorded, and the reply says
which of the two it used.

The bar is at 25 % and not at 50 % because a CLOSED curve on a round support
legitimately loses half — the far side has nowhere to land. Measured on the
0.3.98 acceptance geometry: a r=6 circle onto a r=40 cylinder keeps **47.2 %**,
and that projection engraves correctly (2.974 mm3 out of a 5.9657 mm3 tube). A
50 % bar would have cried wolf on the one case that works.

**A failed groove now reports the cutter.** `catia_boolean` CONSUMES the tool
body, so after `NO_MATERIAL` the tube is gone and its numbers are unrecoverable —
which is why the report got "shares no VOLUME" and nothing else. The tube's
volume, its swept length and its distance to the body are measured BEFORE the
cut: on the report's geometry, `a 2.0106 mm3 tube swept over 4.000 mm of curve,
its body 0.0000 mm from '零件几何体' at the closest point`. A tube that touches at
0.0000 mm and removes nothing is GRAZING the body, not buried in it, and the
reply now says that and points at the swept length as the comparison to make.

**The cleanup stopped inventing its own failure.** It said "Cleanup could not
remove body 'CurveEngrave_A' - the tree may still hold them" while
`catia_list_bodies` showed one body: the boolean had already consumed the cutter
(`tool_body_consumed: true`), so deleting it BY NAME failed and the failure was
read as "still there". It now checks whether the body exists before speaking,
and ends with the measured tree: `Nothing this call created was left behind (the
cutter body had already been consumed by the boolean, so there was nothing to
delete). Bodies now: 零件几何体.`

**And the modal warning has a cause that can be fixed by construction.** The
deletion order took the reference POINT out before the PLANE that references it,
which is what makes CATIA raise `已移除授权输入 "参考点"`. `created` is now
ordered by dependency, so the reverse pass deletes the plane before the point.
That is a fix by construction, not a verified absence: CATIA's dialog is not
observable from here, and claiming otherwise would be the same defect in a new
place.

**What this release does NOT do, measured today.** The report's P0-2 asks for a
way to make a curve lie on a body. Three routes were tried on its own part and
none produces an engrave-able curve:

* **intersection** — `Bottle ∩ cylinder` gives a genuine 37.699 mm curve lying on
  the face (0.0000 mm), and `catia_surface_engrave` refuses it with
  `ENGRAVE_CURVE_UNPARAMETRISABLE`: **all four** point-on-curve overloads
  (`FromPercent`, `FromDistance`, `WithReferenceFromPercent`,
  `WithReferenceFromDistance`) refuse an intersection curve.
* **`AddNewSweepCircle(guide)` + `SetRadius(1, r)`** — a tube with no profile and
  therefore no normal plane and no point on the curve. Both calls are accepted
  and `UpdateObject` refuses every time, so it joins `AddNewGSDCircPattern` in
  the "configurable and never updated" family.
* **extract, then project onto the extract** — this is the 0.3.98 route, and on
  this part it gives the same 4.000 mm fragment.

So the honest answer to "the tool exists but cannot be used here" is: it can, on
a surface a projection can cover; on this lofted bottle face the projection
never produces the outline, and no other route reaches a curve the chain can
consume. `capability_ceilings` now says exactly that instead of implying the
cylinder acceptance was universal.

1582 tests (+2), 139 tools, `scripts/b30_v0400_audit.py` 8/8 on B30, plus the
report's own `SpecialBottle_Handle.CATPart` reproduced from a copy and never
saved.

## [0.3.99] — 2026-11-10 · 3 277 non-manifold edges that were not there, and a shape nothing could see

A five-lug PET bottle base was built end to end through this server and came out
3.15 % under the tutorial's volume, with the wall thickness back-computed to
0.4953 mm against 0.4954. The report's two structural findings were both right:
the tools measured the wrong thing twice over — once spatially, once
conceptually.

**`catia_check_surface` measured the screen, not the solid.** The STL export
contains every VISIBLE element, so the construction surfaces of a finished part
were measured as part of its geometry: the same solid read 5 boundary + 3 277
non-manifold edges and 11 049 triangles with its geometrical set on screen, and
0 / 0 with 4 568 triangles when hidden. The second pair is the solid; the first
pair reads as "my model is broken". It now hides the sets for the export and puts
every flag back, and it keeps visible whatever CARRIES the solid — a ThickSurface
lives inside its set, and hiding that set is how the same report got a blank
screenshot and an empty STL. Measured on B30: 108 triangles against 540,
0 non-manifold against 162, five flags hidden and five restored, and the set
measured as shown again afterwards. `include_construction=true` restores the old
view, so neither scope is a secret. `brief=true` drops the fixed prose (3 042
characters against 6 931) for a `catia_run_plan` that self-checks at every step.
`catia_screenshot(clean=true)` uses the same helper.

**`catia_shape_audit`: r(theta), because volume cannot see a shape.** The
report's point stands — every check this server had answered "the build worked",
and the tutorial's own appendix describes eight earlier versions that passed all
of them and were a smooth bowl. This bins every mesh vertex by angle about +Z and
reports the outer radius: amplitude, peak angles, peak count, and — with `fold` —
whether the repeats agree. Measured on a five-lug solid on B30: amplitude
13.0 mm (outer 33, inner 20), five peaks at 2.57 / 69.43 / 146.57 / 213.43 /
290.57 degrees, fold-5 deviation 0.0000 mm. The first run of that audit reported
TEN peaks for the five lugs, which is the important part of this entry: a coarse
mesh leaves empty angular bins, and an empty bin split each flat-topped lobe in
two. Bins are now interpolated circularly and maxima are filtered by PROMINENCE
(15 % of the amplitude), not by distance to the global maximum — on a plateau
every bin is near the maximum. The mesh volume and area it prints are computed
from the same triangles, so they cross-check `catia_get_inertia` instead of
copying it.

**The tutorial's three loft parameters now get an answer about the tutorial.**
`{"normal_to_profile": true, "tangent_factor": 1.5, "guide_influence": "To Next
Guide"}` used to die at `root: must NOT have additional properties` three times,
before any CATIA call, saying nothing about what the API can do. The three names
are now accepted as aliases and refused with the measurement:
`NO_TANGENT_FACTOR`, `NO_NORMAL_TO_PROFILE`, `NO_GUIDE_INFLUENCE`. What settled
it was reading HybridShapeLoft's OWN type information — 78 members, not one of
them a tension, factor or per-guide influence. That is the lesson 0.3.97 paid for
with `AddNewSpline`/`SetClosing`: ask the object, not the factory method's
parameter list. And a datum plane IS a legal end-tangency support, which the
schema did not say: two circles r=10 and r=14 loft to 3 030.971 mm2 with no
condition, 2 869.025 with `start_tangent='xy'` and 3 202.807 with
`end_tangent='xy'`. Tangency TO the plane, so the surface leaves flat — visibly
not the same as "normal to profile", and the reply says so rather than pretending
they are equal.

**Four new `capability_ceilings`**, so "can it?" is answerable without reading
133 KB of README: `ShapeFactory.AddNewGSDCircPattern` — the SURFACE pattern —
`UNSUPPORTED_ON_B30`, 16 of 16 flag combinations refused on top of the earlier
attempts, with the contrast that matters in the same entry (the SOLID pattern
works and is `catia_circ_pattern`); `HybridShapeLoft` tangent factor / guide
influence `NOT_IN_TYPELIB`; the sketch frame and `Point2D` readback
`NOT_IN_TYPELIB`; and `Viewer3D` capture size / tree visibility
`NOT_IN_TYPELIB` — 58 members, no HideTree, Width read-only.

**A duplicate that was caught before it shipped.** The same report item produced
a new "solid circular pattern" tool here for about twenty minutes: the
measurement `AddNewCircPattern` needed (`part.InWorkObject` = the feature) turned
out to be what the existing `catia_circ_pattern` had been doing since it was
written. It was deleted rather than merged — the report's claim that a
single-feature pattern is unavailable is true for SURFACES only.

1580 tests (+15), 139 tools, `scripts/b30_v0399_audit.py` 14/14 on B30.

## [0.3.98] — 2026-11-10 · the engraving chain was there all along

Three 2026-11-10 reports asked for "cut text into a curved face". Every route
through the surface is refused (split of a projected curve, fill of it, Sew/Wrap
never update), so 0.3.95 declined to wrap an engrave tool and wrote that refusal
into the changelog: *shipping a tool whose middle step is known to fail would be
the thing these reports keep paying for*. That reasoning was sound and the
conclusion was wrong — wrong about the ROUTE, not about the surface.

**`catia_surface_engrave` — and the measurement that makes it work.** One
controlled run on B30 2026-11-10, one call, seven steps, no new COM member:

    plane normal to the curve -> circle of radius = depth on that plane
    -> sweep the circle along the curve (a tube) -> CloseSurface
    -> catia_boolean(remove)

    r=4 trace projected onto a r=40 cylinder
    tube volume        5.9657 mm3   (2*pi^2*R*r^2 = 5.97)
    removed            2.974 mm3    (exactly the half)

The half is the whole idea: the tube is CENTRED on a curve that lies ON the
surface, so exactly half of it is inside the material. The groove depth is the
radius — measured along the surface NORMAL everywhere, which is precisely what a
planar pocket cannot do, and what the tutorial asked for (0.3~0.5 mm 包覆深度).
The reply prints the removed volume against that prediction, so a groove that
stopped following the surface shows up in the same call rather than three steps
later. It is a groove ALONG the curve, not a filled glyph: a letter is its
outline, and `catia_sketch_strokes` already draws one in a call.

Limits, both measured: the guide must be CLOSED (an open guide sweeps a pipe with
two open ends and `CloseSurface` refuses it — `ENGRAVE_TUBE_NOT_CLOSED`), and the
curve must lie on the body, which this cannot arrange. A failure cleans up after
itself and says what it could not remove.

**The periodic-spline entry 0.3.97 shipped was wrong, and it was two objects.**
0.3.97 wrote that "a periodic flag is not among AddNewSpline's parameters" and
stopped there. `AddNewSpline()` indeed takes no parameters — the flag lives on the
OBJECT as `SetClosing(type)`, and `catia_gsd_spline(closed=true)` has been calling
it since it was written. Measured on B30: four points at the corners of a 20×15
rectangle give an open spline 61.2161 mm long, and `SetClosing(1)` makes it
77.8649 mm. The object that genuinely cannot close is the SKETCH spline:
`Spline2D` exposes 46 members, `IsPeriodic` / `Period` / `Continuity` are
read-only, and there is no `SetClosing` at all. `capability_ceilings` now carries
those as two entries with their own statuses, `catia_sketch_spline(closed=true)`
says why it repeats its first point, and both point at `catia_gsd_spline` for a
curve that has to be genuinely closed.

1565 tests (+18), 138 tools, `scripts/b30_v0398_audit.py` on B30: one call engraves
2.974 mm3 out of the cylinder against a 2.983 mm3 prediction, the curve that does
not lie on the body leaves nothing behind, and `catia_diagnose` lists the sweep
route as available.

## [0.3.97] — 2026-11-10 · the five leftovers, and a half-rule caught by its own audit

The two 2026-11-10 reports on 0.3.94 were answered by 0.3.95 and 0.3.96. This is
what was left over: five items, none of them a headline, all of them the same
shape — **a reply that says something the measurement in front of it does not
support**, which is the defect this whole run of reports keeps paying for.

**`catia_gsd_project` answered a degenerate pair with a bare COM error.** Roughly
three lines of code and no handler, so a projection of a sketch on an XY plane
offset 40 mm onto support `xy` came back as `方法 UpdateObject 失败` and nothing
else. It now raises `PROJECT_FAILED` with the measured distance (40.0000 mm on
that pair) and the rule.

**...and the first version of that rule was half wrong, caught by the audit that
shipped with it.** The draft said "parallel planes collapse". The audit projected
the same rectangle onto `yz` and failed: a curve in a plane PERPENDICULAR to the
projection direction flattens onto a line, which is just as degenerate. One
controlled run settled the matrix on B30: `xy` FAIL, `yz` FAIL, `zx` FAIL, the
block's own top face FAIL, a plane tilted 45 degrees about X **OK** — a 40×40
rectangle, all five supports, one document. So the message states both cases and
names the support that works, and "not parallel" is explicitly called out as not
enough. This is the same failure mode as the reports themselves, caught one step
earlier because the acceptance script asserts what the reply SAYS and not only
what the geometry does.

**`catia_gsd_split`'s advice contradicted its own measurement.** 0.3.95 taught the
message body that a curve projected onto a curved surface is measured-unsupported
on B30, and left `next_actions[0]` telling the caller to measure a few points of
the cutter and rebuild it. That is the instruction the report followed three
times. The list is now ordered by what the measurement says: the unsupported case
first, the join/array and cap routes next, and "measure a few points" last and
scoped to a PLANE patch — the one case where the number decides anything.

**`catia_gsd_offset_surface` can now say what it cannot say.** It used to answer
`0.4 mm from 'BottleSkin'` and leave inside/outside to the caller, with `reverse`
being CATIA's orientation flag rather than a word for a direction. The first fix
measured the surface's area centroid before and after, and the audit run one hour
later reported "the centroid did not move" — investigation found why, and it
retired the idea:

* `SPAWorkbench.GetMeasurable(<reference to a hybrid surface>)` **resolves to the
  enclosing SOLID on B30** (measured 2026-11-10): a 100×100 face extract reports
  `Area` 24000 mm² — the whole 100×100×10 block — and a COG of (0, 0, 5). Two
  centroid readings therefore measure the same block twice.
* the distance from the surface to **either** of its two offsets is 0.4000 mm, so
  a distance cannot tell the directions apart either.

Both are documented in the tool and in `capability_ceilings`' neighbourhood
rather than shipped as a number that happened to look right once. What the reply
does say is the flag that was used, the measured gap (0.4000 mm, which shows the
offset landed where it was asked), and the operation that answers the question by
consequence: thicken it into a tool body, cut, and read the volume —
`catia_boolean(remove)` reports before/after, so a body that removes nothing went
the wrong way.

**`catia_sketch_arc`'s schema described the endpoint form only.** 0.3.95 made the
angle form honour `direction` and `major` and describe the arc it drew; the
description still read "With two endpoints this only matters...", which would
talk an angle-form caller out of a parameter that now works. Both parameters say
they apply to both forms, the short-way rule and `DEGENERATE_ARC` are in the
description, and the README bullet that called `catia_sketch_arc` a 0.3.92
feature was the only place the old rule was written down.

**`catia_diagnose` publishes `capability_ceilings`.** A member in
`typeinfo_addnew` is DECLARED; three reports in a row read that as "it works" and
spent their acceptance runs finding out. The new block keeps the measurement
apart from the reading, with a status per member: `UNSUPPORTED_ON_B30` (Sew
Surface 8/8, Wrap Curve constructs and never updates), `NOT_IN_TYPELIB`
(`Factory2D.CreateText`, the spline's periodic flag), `DECLARED_NOT_MEASURED`
(Rib / Stiffener — explicitly not an answer either way), and
`AVAILABLE_WITH_LIMIT` (`AddNewSplit` is a two-way cut, not a local one).

Not done, and the reason is measured rather than assumed: a periodic-spline flag
still needs a B30 session of its own (the flag is not in the spline's parameter
list, and `closed=true` repeats the first point), and `offset_surface` still
refuses to name inside/outside — for the two reasons above, not for lack of
trying.

1547 tests (+20), 137 tools, `scripts/b30_v0397_audit.py` 10/10 on B30: the arc
says clockwise, the offset reports `0.4000 mm` apart and says the side is
unreadable, the skewed support accepts the projection while `xy` refuses it as
`PROJECT_FAILED / 40.0000 mm`, and `catia_diagnose` lists six ceilings with four
distinct statuses.

## [0.3.96] — 2026-11-10 · the thick-surface message measured the wrong quantity

Split out of 0.3.95 rather than folded into it: that release was already tagged
and published, and a published artefact does not get its history rewritten.

**`catia_gsd_thick_surface` compared the radius with the TOTAL thickness.** A
2026-11-10 report put three attempts side by side and the contradiction was in
the numbers they pasted: `0.1 / 0.1` was answered with "NOT below the 0.200 mm
offset" while the tool's own description - and every other message in this
package - says the limit applies to **each side's** offset. So the one attempt
where the caller had already done the right thing (split the wall in half) was
measured against the sum, and the radius looked like the blocker when it was not.
It now says `0.100 mm single-side offset (of 0.200 mm total)`.
The branch that read `radius < total` when `side` was known was unreachable
anyway - anything that reached it had already passed `radius < side` - so it is
removed rather than repaired twice.

**"Look at a seam" was not something anyone could run.** The sentence now names
the tool that finds one: `catia_check_surface` reports creases and non-manifold
edges (an extract of a periodic loft face carries a seam), and if it is a seam,
`catia_gsd_boundary(edge=N)` takes one piece of that face to thicken instead of
the whole thing.

1527 tests (+3), 137 tools.

## [0.3.95] — 2026-11-10 · the copy that warned about the body it was in

Two reports on 0.3.94. The first confirms every 0.3.93/0.3.94 stability fix (five
P0s, 4/4 on the distance checks, `stale` first in the payload) and then finds the
one thing 0.3.94 got wrong about itself: **its own new feature warned on every
call while doing exactly what it was asked.**

**`catia_sketch_copy(target_body=...)` reported the wrong body.** 0.3.94 read
`sketch.Parent.Name` and compared it to the body's. `Sketch.Parent` is not the
body: measured on B30 the chain is

    草图.1 -> Sketches -> 零件几何体 -> Bodies -> Part -> document

so the comparison was against the string `"Sketches"` and could never match. The
copy landed in the right body (their pad built 50000 mm3 there), and the reply
said "the copy is in the wrong body", 2/2. The question is membership - "is this
sketch in THIS body" - so it is now asked of that body's own `Sketches`
collection: **identity across every body first, name second**, because a part can
hold two sketches called `草图.1` after a rollback and a name match in body order
answers confidently with the wrong one. An unreadable collection answers `None`
("unknown"), which never becomes a warning.

The audit that passed 9/9 in 0.3.94 asked whether the geometry landed in the body,
which it did - it never asked what the reply said. `scripts/b30_v0395_audit.py`
now asserts `"WARNING" not in reply` on the good path, and the new tests cover
the wrong-body case, the right-body case and the unreadable case separately.

**`catia_sketch_strokes` — the bottleneck behind "there is no text tool".** A
glyph outline is 8-20 segments and every sketch tool draws one element per call,
so a letter was dozens of hand-computed calls (their "A" needed 7 control points,
and `catia_sketch_spline` then interpolated it into an omega). This tool takes the
whole outline at once, as the `[[x,y],...]` lists any font or DXF outline arrives
in, and draws STRAIGHT segments - no interpolation, so corners stay corners; a
stroke that repeats its first point is closed; a degenerate segment refuses the
WHOLE batch before anything is drawn, because a zero-length line is accepted by
CATIA and breaks the profile three steps later. Segments are recorded as `line`
records, so a batch-drawn glyph is still copyable by `catia_sketch_copy`.
It is not a text tool: a font is data, and a hand-written font would be a guess
dressed as a feature. What was missing - and is now there - is the one call that
turns an outline into sketch geometry.

**The arc's angle form described a pair that is not an arc.** "from 90° to 0° -
swept 90° counter-clockwise" is self-contradictory: 90 -> 0 is clockwise. The
geometry was right, the sentence was not. The direction now comes from the arc
that was actually drawn, and a `major=true` clockwise arc still says clockwise.

**A boolean that removes nothing keeps both names.** The tool body is consumed by
the call, so `str(tool.Name)` afterwards is CATIA's internal name
(`'TouchTool'` read back as `'几何体.3'`) - and the message silently substituted
it, which reads like a different body was cut. `data` now carries
`tool_body_requested` and `tool_body_internal`, `tool_body_consumed: true`, and
the message uses the caller's name with CATIA's in brackets. The `next_actions`
lost `catia_get_bounding_box(component=...)` - that argument is for assembly
instances and cannot measure a body (their P2-2) - and the wording is now "shares
no VOLUME" instead of "does not overlap", because two bodies that meet along a
face measure 0.0000 mm apart and still remove nothing.

**The consumed tool body no longer leaves a stale handle.** After `NO_MATERIAL`
the session's active body still pointed at the body the boolean had consumed, so
`catia_list_bodies` reported three bodies with every `active: false` and did not
list the one the handle named. The rollback now moves the handle to the target,
and `list_bodies` publishes `active_body` plus `active_body_missing` with a
sentence naming the problem instead of leaving the caller to guess.

**`gsd_split`'s number was a minimum pretending to be a diagnosis.** Three
cutters (a projected curve, its extract, and a projected circle) got "closest
approach 0.0000 mm" three times, and the report spent three controlled experiments
rebuilding a cutter that lay on the surface by construction. A minimum is 0 for a
curve that touches the surface in ONE place, so it cannot answer "does the contour
follow it" - and this build has no way to sample along a curve (`
AddNewPointOnCurveFromPercent` does not update, measured 2026-11-09). The message
now says exactly that, states which combinations the measurements do separate
(plane patch: works; join/array: take the piece first; curve projected onto a
NON-PLANAR surface: unsupported on B30, three ways measured), and points at the
four routes that work.

**`catia_split_solid` says what it is not.** First sentence now: it cuts a body in
two and keeps one half, it is NOT a local cut / engraving / pocket, and CATIA's
Chinese UI calls both it and the surface split 分割. Its reply also prints the
discarded half (`kept 49000.0 mm3, discarded 48500.0 mm3`), which the description
had promised since 0.3.94 while the code printed a percentage.

Not done, with the reason measured rather than assumed: a "surface engrave" tool
(projected curve -> patch -> offset -> solid cutter) needs a way to cut a patch
out of a CURVED surface, and on this build `gsd_split` rejects a curve projected
onto one, `fill` rejects the projected closed curve, and `AddNewSewSurface` /
`AddNewWrapCurve` never update (0.3.94). Shipping a tool whose middle step is
known to fail would be the thing these three reports keep paying for.

1524 tests (+14), 137 tools, `scripts/b30_v0395_audit.py` 7/7 on B30: the copy
warns about nothing and is in `CopyTarget`'s own sketch list, an 8-segment glyph
drawn in one call pockets 2900.0 mm3, `90 -> 0` reads "clockwise", a touching tool
body answers `NO_MATERIAL` with `requested=TouchTool internal=几何体.3`, and
`list_bodies` reports `active_body=零件几何体` with no `active_body_missing`.

## [0.3.94] — 2026-11-10 · the chain, measured instead of promised

0.3.93 listed nine operations as "not done, needs a B30 session". That list was
honest about not shipping untested wrappers and wrong about why: CATIA was
running, so most of it could be settled in twenty minutes. This version settles
it, ships the one that works, and replaces the guesses with numbers.

**`catia_split_solid` — Part Design Split, verified before it was written.**
`ShapeFactory.AddNewSplit(iSplittingElement, iSplitSide)` is declared, takes a
surface or a datum plane, and works: a 100×100×10 block cut by a plane at Z=3
kept 30000 mm³ with one value of `iSplitSide` and 70000 with the other, and the
same block cut by a 400 mm extruded wall split 30000 / 70000. The tool exposes
`keep='front'|'back'` instead of a number, because which half a number keeps
depends on the cutter's own normal (both measurements above are consistent with
"front = the side the normal points to", and neither number means anything to a
caller who cannot see that normal), and every reply prints both volumes so a
wrong half shows in the same call.

**The rule that is not in any documentation, in the failure text.** The cutter
must divide the body **completely**: the same wall shortened to 40 mm failed at
update, and CATIA said only "方法 Update 失败". A partial patch leaves no valid
second half. The tool says that, takes the dead feature back out, and points at
`catia_boolean(remove)` for the "cut only here" case - which is what a Split is
not. Three post-checks make it hard to be lied to: an unchanged volume is
`SPLIT_NO_EFFECT`, an empty kept half is `SPLIT_KEPT_NOTHING`, and both roll the
feature back so the tree keeps updating.

**A cut is now judged BEFORE it is built.** 0.3.93 measured the target body after
a `remove` and reported `NO_MATERIAL`. The distance between the two bodies is
measurable *before* the feature exists, and the test is one-sided: a positive gap
proves the intersection is empty, while 0 proves nothing (two bodies can touch at
an edge and share no volume - the same reason "distance 0" was refused as "fits"
in 0.3.89). A `remove` or `intersect` whose bodies are apart is now
`TOOL_DOES_NOT_TOUCH` with the gap in millimetres, no feature created, nothing to
roll back. `add` is never judged this way: two disjoint bodies join perfectly
well.

**`catia_sketch_copy(target_body=...)`.** The copy went into the active body,
whatever the caller meant, so "draw the label on its own solid" needed a
`catia_set_active_body` detour whose side effect had to be tracked. The body is
named up front, the copy lands there, and the parent is read back afterwards
because `InWorkObject` reports no error while CATIA keeps pointing somewhere else.

**Three capabilities are now recorded as measured limits, not as guesses:**

    AddNewSewSurface   declared, creates a feature, and fails at update in all
                       eight geometries tried - a 400x400 wall and a 40x200 patch
                       against a 100x100x10 block, a wall standing on the top
                       face, a cap hanging over it, and a datum plane, with
                       iSewingSide as 1/0 and as True/False. The engraving route
                       does not go through Sew Surface on this build.
    AddNewWrapCurve    declared and constructible (0 arguments), accepts
                       reference curves and a direction, reports 2 curves - and
                       then UpdateObject refuses it. Third member in this family
                       after AddNewGSDCircPattern (0.3.85) and Sew Surface.
    sketch text        not a gap in this fork: the Factory2D a sketch hands back
                       declares 37 members and none of them creates text. Letter
                       outlines have to arrive as geometry.

**Left out on purpose, still.** `AddNewRib` / `AddNewStiffener` (declared, and a
rib is a sweep whose profile must stay normal to the path - the trap that makes
GSD sweeps fail, so it needs its own session), periodic splines (`AddNewSpline`
is declared but the periodic flag is not in the typelib this fork reads, and
closing a curve on itself achieves the same shape), and `Viewer.HideTree`.

1490 tests before this version, `1508` after (+18), 136 tools. On B30 the tool
itself passed 9/9 (`scripts/b30_v0394_audit.py`): both halves summing to the
block, a partial wall refused with the tree still updatable, a cut 40 mm short
refused before it was built, and the copy landing in the body it was told to.

## [0.3.93] — 2026-11-10 · the tools that broke the model and said they hadn't

A report from a ~200-call session (a hand-built bottle with an engraved label). It
is the most useful report this fork has had, because all three P0s are the same
category: **the tool answered success while the model was broken, or broke it on
its way out.** Long report, so this entry is ordered by what it cost them.

**`catia_boolean(remove)` measured nothing before saying "Boolean 'remove': …".**
Their tool body stopped 0.3 mm short of the material and the call answered success
twice, volume 168607.5 both times. `UpdateObject` had accepted the feature - a
boolean with an empty intersection is a perfectly valid feature - so only a
measurement can tell. It now compares the target body's volume before and after:
a `remove` that changes nothing is `NO_MATERIAL` with both numbers, the feature is
taken back out of the tree (`rolled_back: true`), and `next_actions` point at the
tool body's extent rather than at the boolean. A `remove` that did bite reports
`168902.1 -> 168607.5 mm3` in its own reply. Union and intersect are NOT judged
this way: a body inside the target adds no volume, and a superset tool leaves it
unchanged - both correct.

**A boolean that leaves the tree broken is no longer reported as success.** The
call now ends with `part.Update()`, not just `UpdateObject(feature)` - the old
code checked the feature and never the tree, which is exactly how their part went
bad silently and failed at the NEXT call. If the whole update fails the answer is
`PART_UPDATE_FAILED`, `rolled_back: false` (the feature is left in place on
purpose: deleting it would hide the evidence), the culprits are named, and
`catia_list_bodies` starts saying its numbers are stale.

**`catia_list_bodies` now says when its numbers are stale.** CATIA keeps
answering with the last valid volume and face count while the update is in error,
and gives no hint that it is doing so; the reporter trusted 168607.5 mm3 twice.
The one thing this server knows is its own update results, so a failed full update
sets a flag, a successful one clears it, and `list_bodies` puts `stale: true`,
`stale_because` (the error) and `stale_note` (what to run) FIRST in the payload -
before the numbers they qualify.

**`catia_update_part` / `catia_delete_feature` name the casualties.** Their
culprit was `图案_投影`, a projection inside a geometrical set: the probe that
finds "the feature that fails on its own" only ever walked `bodies[].Shapes`, so
it reported "no single feature reproduces it" about an element it had never
asked. The probe moved to `catia_mcp/update_errors.py`, walks both trees (nested
sets included) and returns EVERY culprit, because a part can carry two
independent casualties and naming one sends the caller round the loop again.
`catia_delete_feature`'s "features that depended on it are now in error" now names
them, or says outright that no single element fails and the damage is a relation.

**A lost sketch is rebuilt even when the part cannot update.** `pocket`'s rollback
takes the input sketch with it (CATIA cascades, and whether it does depends on the
document's history - one call in their session kept it), and the rebuild added in
0.3.30 ended with `part.Update()` - on a part that was in error precisely because
the feature had just failed. So the reply said "it could NOT be re-created:
方法 Update 失败" about drawing calls that had all worked. The rebuild now updates
the SKETCH (`UpdateObject(sketch)`), falls back to the whole-part update without
treating its failure as the rebuild's, and therefore leaves a sketch the caller can
retry with.

I did NOT add `keep_sketch=true`. Whether the sketch survives is CATIA's cascade
during the rollback of the feature node, not something this server chooses; a flag
that claims to control it would be the same kind of lie this release is about. What
it does now is put the sketch back deterministically.

**`catia_sketch_arc`'s angle form obeyed neither `direction` nor `major`.**
Reported as "I ask for 90 degrees and keep getting 270, and major=true/false makes
no difference" - `CreateCircle` only requires start < end, so every descending
pair wrapped the long way round. The angle form now follows the same short-way
rule as the endpoint form (0.3.45), says which way it went, and refuses two equal
angles as `DEGENERATE_ARC` instead of drawing a full circle.

**Not done, and why** - these are their P1s, and every one of them needs a B30
session to earn its place:

    AddNewRib / AddNewStiffener      the ring handle is the real use case, and a
                                     rib is a sweep along a path with a profile
                                     that must stay normal to it - the same trap
                                     that makes GSD sweeps fail. Shipping it
                                     untested would produce exactly the "tool
                                     exists but wrecks the model" this report is
                                     about.
    AddNewSplit / AddNewSewSurface   the engraving chain's missing link. It also
                                     needs the offset that gives the depth, and a
                                     Split on a non-planar projected curve has to
                                     be verified on a real bottle before it is
                                     advertised.
    AddNewWrapCurve / WrapSurface    declared by the typelib and, like
                                     AddNewGSDCircPattern in 0.3.85, quite
                                     possibly not callable - that is a measurement,
                                     not a wrapper.
    catia_sketch_text                letter outlines are a font problem (kerning,
                                     stroke order, curves), not a COM problem.
    sketch_copy(target_body)         small and useful; queued rather than rushed.
    periodic splines                 `AddNewSpline` is exposed, but the periodic
                                     flag is not in the typelib this fork reads,
                                     so it would be another untested guess.
    NO_MATERIAL "distance to the      the number they want is the shortest distance
    material"                        from the profile to the solid, which is not
                                     the same as "depth short by 0.3 mm along the
                                     normal" - publishing it as if it were would be
                                     another number that misleads.
    screenshot hide_tree             `Viewer.HideTree` is worth trying on B30, not
                                     assuming.

1490 tests (+15), 135 tools. Their §4 list of what already works (`catia_solid_loft`,
`catia_gsd_project`, `catia_diagnose`'s typelib tables, `created_by` on faces) is
unchanged - no tool was removed or renamed in this release.

## [0.3.92] — 2026-11-10 · the branch that answered a contradiction with a Save

Two reports. The first confirms the guide diagnostics work (their words: better
than what they suggested) and then isolates the last P0 residue with the
instrumentation 0.3.91 added. The second builds the tutorial's Step 5 properly
(three guides, all 0.0000 mm) and finds no new defect.

**The bare Save is now unreachable for a document that has no path.** Their
envelope — `SAVE_FAILED`, `data.call = "Document.Save"`, `attempts: 3`,
`data.save_route` with nothing but `enter`, on ONE folder while four similar ones
saved — is reproduced here against the doubles, and it comes from a single
contradiction inside one call: `_document_at_path()` says an open document's
FullName IS the target, while `_document_has_path()` says this document has no
path. The old code answered that disagreement by calling `doc.Save()`, which
cannot give a pathless document a path, and it did so with nothing but `enter` on
the route. Two hardenings and one condition:

    _same_file          now requires BOTH FullNames to be non-empty and ABSOLUTE.
                        `str(None)` is "None" and a bare name resolves against the
                        server's own working directory; either one turns "no file"
                        into "the same file".
    _document_at_path   skips a document whose FullName is not an absolute path -
                        it cannot be the holder of a target it does not name.
    bare-Save branch    requires has_path as well. Same-file-but-pathless falls
                        through to the staged route, which is what the description
                        has promised since 0.3.89.

**The route now records its decision** (their first suggestion — and the reason
this report had to *infer* a branch at all). Right after `enter`,
`route_decision` names the branch (`bare Save` / `staged first save` / `direct
SaveAs` / `refused: ...`) with the four facts behind it — `has_path`, `same_file`,
`file_exists`, `holder`. The bare-Save branch also logs `bare_save` with its
`attempts` and its error, so `attempts: 3` no longer travels alone (their second
suggestion). Their third — a regression case with that exact path — is
`tests/test_reported_issues_2026_11_10_v0392.py`, which drives the contradictory
state and asserts the staged route runs and no bare Save is called.

I am NOT claiming the folder is fixed: I cannot produce their CATIA state, and
the two reads that must disagree are both CATIA's. What is fixed is that the state
can no longer produce a Save, and that the next envelope names what it saw.

**`check_surface` puts the visibility hint first.** Their 1373 → 0 non-manifold
counts with the GSD set shown/hidden is the same story as the 2026-11-03 warning,
except the explanation sat in `non_manifold_note`, after the number that caused
the alarm. When the mesh includes visible GSD geometry the reply now opens with
the count, what it probably is, the one call that settles it, and their measured
before/after — quoted as theirs, since I did not measure it.

**Not done, on purpose**: `cap_shape='circle'` (their P2). It is not a flag - a
round cover needs the centre region cut flat first, and the measurements in their
own v6/v7 reports (0.0065 mm at r=8, 0.0000 mm at r=5.5 still not cuttable) are
the evidence that the interpolated surface does not hand over a planar contour.
Adding a flag that would usually fail is worse than the sentence that already says
not to try. The end-condition limit and the build limits they list need no code
either; both are CATIA's.

1475 tests (+11), 135 tools.

## [0.3.91] — 2026-11-10 · "tried" is not "worked", and F: is not a fact

A report that ran the suite on a machine with **no F: drive** (C:/D:/E: only).
`1445 passed, 1 failed`. Following that one failure down turned up two real
defects, both reproducible without CATIA. Nothing here needed a B30 measurement -
which is itself the point: all three were ordinary code, and two of them had been
in the package since 0.3.80.

**A test depended on the author's drive letters.** `dialogs.save_as(doc,
"F:/ascii/x.CATPart")` called the real `os.makedirs("F:\\ascii")` first; on their
machine that raised `PermissionError` before the fake `SaveAs` ever ran, so the
`CodedError` the test expected never appeared. The assertion was right, the
fixture was not. It uses `tmp_path` now, and a guard test scans every
`*save_as(...)`/`*save(...)` call in `tests/` and fails on a drive-letter literal
(same nature as the `//n` AST guard from 0.3.81). Writing the guard found two more:
`test_a_non_ascii_folder_is_not_refused` had been creating `C:\中文目录` on
whatever machine ran the suite, and another test named `C:/any/...`.

**A folder that cannot be created is a save failure, not a COM error.**
`os.makedirs(parent, exist_ok=True)` sat **outside** every `try` in
`dialogs.save_as`, so its `PermissionError` escaped the envelope and
`server._execute_tool` classified it `COM_ERROR` - with the "CATIA reports most
automation failures as a bare 'method failed'… check `in_work_object`" advice,
for a path that CATIA never saw. Reproduced here (`Q:\`: `PermissionError`, code
`COM_ERROR`), then fixed: `SAVE_FAILED`, `modified: false`, `data.call =
"os.makedirs"`, `data.folder`, the raw `OSError` text, and advice about a drive
that does not exist. The message says outright that CATIA was never asked.

**The staging route now knows the difference between "tried" and "saved".**
0.3.89 set `_staging_attempted` *before* the call and the failure prose read it as
*after the return*: "this document was saved to %TEMP% first and then moved … and
THAT move was refused too - so this is not the 'first SaveAs' limitation". Their
minimal reproduction (two `save_as` calls, both raising, no file in `%TEMP%`)
showed all three clauses false and the conclusion resting on the second one. Two
SaveAs calls fail for different reasons, and one flag cannot carry both:

    _staging_attempted   entered the route
    _staging_saved       the %TEMP% SaveAs RETURNED
    _staging_save_error  why it did not (kept, no longer swallowed)
    _staging_move_error  why the second SaveAs failed

`_staging_sentence()` picks the wording from those, and only claims "not the
first SaveAs limitation" where both of its premises hold (staging ran first AND
the document really had a path when the second call ran). Otherwise it says the
cause is undetermined and warns that the bracketed error belongs to the direct
attempt, not to the folder. `data.staging_route` publishes the same four facts,
`data.save_route` now traces `staging_save` and `staging_move` separately (a
single `staging: ok=false` could not say which of them failed), and a SAVE_FAILED
raised by `dialogs.save_as` itself - the production path - gets the same sentence
appended, instead of telling the caller less than the fake-doc path did.

**The tag-without-a-release gap (their §4.4) was mine, and it is closed**:
`v0.3.90` was tagged and pushed before its release existed, so `releases/tags`
answered Not Found while `main` already said 0.3.90. The release, zip and
`.sha256` are published; PUBLISH.md now says the two must leave together.

1464 tests (+8), 135 tools. Their §5 observation is right: `SPLIT_CUTTER_MISSES`
reporting `stage="validate"` after a `GetMinimumDistance` call matches the
documented meaning of that stage (CATIA was not asked to change anything).

## [0.3.90] — 2026-11-09 · the record that did not follow a delete

Two reports on 0.3.89. One is the folder that keeps failing (one folder, on their
machine, 3/3: `SAVE_FAILED` with `data.call = "Document.Save"`). The other is a
tutorial build whose copied section reported "2 spline(s)" with exactly twice the
source's outline.

**The record now follows `catia_sketch_delete_element`.** Measured on B30
2026-11-09: two 20/10 mm lines, delete the second, and the session's drawing
record still held BOTH - so `catia_sketch_copy` replayed a line the sketch no
longer had, the copy's outline came out 30.000 mm against the source's 20.000, and
the profile echo counted a shape that was gone. A rectangle would do worse: it is
ONE entry over four elements. Each recorded entry now carries `_span`, the live
element indices that draw produced, so a delete drops exactly that entry and
re-bases the ones after it - or, when one entry covers several elements, drops the
record whole and says so in the reply ("catia_sketch_copy will refuse this sketch
until it is drawn again") rather than leaving a record that lies about the sketch.
Same fix applies to the profile echo at close time and to the outline record GSD
reads.

**The bare-Save branch decides by FILE, not by name.** `_save_document` takes
"save onto its own file" when the target is the active document's own file, and
that test compared names - while CATIA freely gives two open documents one name
(the reason `document=` by name is refused in this same file). `_same_file`
compares FullName, so a document with no path can never be "the same file" - which
is precisely the case where a bare `Save` cannot work. This is the only bare Save
`new_part` could reach.

**A failed save publishes its route.** `data.save_route` lists every step in
order (`enter` with has_path/folder_ascii, `staging` with its answer, `save_as`
with target or error). The report could only say "the reply blames a bare
doc.Save() while the description promises a staged SaveAs"; those two possibilities
are now distinguishable by reading the envelope.

I could NOT reproduce the folder failure: the reported path was measured through
the staged route here again (two `save_as` calls, 40702 bytes, no bare save), so
this release does not claim it fixed. It removes the one name-based decision in
that path and makes the next report decisive.

**A loft refusal leads with the guides, measured.** The report's five attempts all
got the same prioritised list, whose first item (fold) was wrong every time; they
found by subtraction that 2 guides fail while 1 builds. The refusal now measures,
for each guide, the farthest section from it: a guide that misses one is named
first with its distance, and when every guide reaches every section it says so
outright and warns that touching is necessary but not sufficient - a guide
anchored to a SPLINE's control point touches the hull while missing the
interpolated curve the loft follows.

`SPLIT_FAILED` / `SPLIT_CUTTER_MISSES` now carry `next_actions` naming the cutter
measurement, the join/piece split and `cap_center`, instead of the generic
"This is a state, not a typo" template.

1456 tests (+10), 135 tools, `scripts/b30_v0390_audit.py` 4/4.

## [0.3.89] — 2026-11-09 · the rule that nobody could settle, and a refusal
## that names the number

Two reports on 0.3.88. The first says the staged save never ran for one path
shape - an ASCII ancestor folder containing a space, with a Chinese subfolder
under it. The second is the one that earned real code: `catia_gsd_split` refused a
circle cutter with nothing but `方法 UpdateObject 失败`.

**The folder rule is gone rather than defended.** `_path_is_ascii` is
`str(path).isascii()` on the whole string - no whitespace split, no prefix test -
and the reported path was measured here through the staged route twice (two
`save_as` calls, 40701 bytes on disk, no bare `doc.Save()` anywhere). Since this
can neither be reproduced nor argued down, the branch that decides on it is
removed: **every first save is staged**, so no folder shape can be described as
"the rule did not fire". The cost is one extra `SaveAs` on a document that has no
path to lose. The reply now also names the folder and whether it is ASCII, and the
replaced/new-file note survives the staged route (it is a safety message - a
2026-10-25 verification run silently replaced a finished part).

**`catia_gsd_split` measures before it builds.** A cutter that does not reach the
element is refused as `SPLIT_CUTTER_MISSES` with the distance (measured: 3.0000 mm
for a circle 3 mm above a patch) - previously that case and every other produced
the same generic update error. A refusal AT update is now `SPLIT_FAILED` with
`modified: false`, the closest approach measured after the failure, both candidate
causes named without claiming which one it is, the four control cases, and the
route that needs no coplanar centre (`cap_center` with the hole's own edge).

What those messages are built on, all measured on B30 2026-11-09 on one 40x40
PLANE patch:

    circle r=6 lying on the patch      split OK, 1486.903 mm2 kept
    the same circle 3 mm above it      fail; GetMinimumDistance = 3.0000 mm
    circle on a 45-degree support      fail; distance 0.0000 mm - it CROSSES the
                                       patch without lying on it
    after such a failure               every NEW point also refuses to update

That last one is why the sampling happens before the split and only a
`GetMinimumDistance` can be read afterwards, and it is why the refusal warns that
a second failure is not a second cause.

**The split description stops over-promising its own sample.** The 40x40 patch in
it is a PLANE, and the precondition is the part that matters: the curve has to lie
on the surface, not merely cross it. On a curved surface a planar circle touches
in places and leaves in between, and there is no contour to cut along.

`cap_center` now says outright that the cap has the HOLE'S OWN shape - a
five-sided hole gets a five-sided cap, and a round cover is not something this
build can make there.

1446 tests (+7), 135 tools, `scripts/b30_v0389_audit.py` 4/4.
`gsd_thick_surface`'s two-sided allocation has been in its description since
0.3.86 (EACH SIDE'S OFFSET, at character 598 of 2925, with 0.25/0.25 among the
four measurements) - the report calls it missing for the third time; it is not.

## [0.3.88] — 2026-11-09 · the P0 gets the route that is measured working,
## and the readback stops hiding behind its own refusal

Two reports on 0.3.87. Every information-layer item from the previous round was
confirmed fixed; what was left is the P0 that keeps failing on one machine, and a
readback that worked for lines but not for splines - which is what the tutorial's
sections are.

**The first save of a pathless document now stages through %TEMP% FIRST, and the
measurements are on both sides.** Nine successes here (2026-11-07/08), eight
failures and no success on the reporter's machine across 0.3.81/0.3.83/0.3.85/
0.3.87, empty session and multi-document alike - while `catia_save_document` into
that same folder succeeds EVERY time on BOTH, because by then the document has a
path. So the deterministic part is not the folder, it is "the first save of a
document that has no path", and 0.3.88 uses that: when the folder is non-ASCII,
`catia_new_part` writes to an ASCII staging file and then SaveAs onto the path
asked for. On B30 the staged route lands the file in the reporter's own Chinese
folder (40695 bytes, checked). If staging fails too, the direct SaveAs still runs.

**The save failures now name the COM call that ran**, which settles a contradiction
the reporter could not: the description said "the first save is a SaveAs, not a
Save" while the error said 「方法 Save 失败」. Both sentences were this server's,
about two different members - `doc.Save()` and `doc.SaveAs(path)` - and now the
first line of every failure says which one it was ("this call was a bare
doc.Save()" / "this call was doc.SaveAs('<path>')") and `data.call` carries it for
a machine to branch on. `SAVE_FAILED.next_actions` no longer advises re-running
`catia_new_part(file_path=...)`, which is the call that had just failed; it gives
the two-step order that works.

**"Intermittently" is gone from the description.** It was wrong twice over: nine
successes on one machine is not "intermittent" either, it is a different
environment. The text now carries both counts and the narrower invariant.

**A one-spline sketch hands over its coordinates with the refusal.**
`catia_gsd_spline(element=…)` is the only readback this server has, and it refused
a sketch that already IS one spline - correctly, since wrapping one curve in a 3D
spline is wrong - but that closed the only path to the tutorial's own sections
(a 7-point spline on Plane3_36). The frame is already reconstructed at that point,
so the refusal now ends with the points in global XYZ, checked by the same probe
point as any other readback: measured `(0, 0, 0); (0.9271, 2.8532, 2)` for the
control point (2, 3) on a zx plane rotated 72°, i.e. 3·(cos72, sin72) + 2·Z.

**`catia_create_sketch` stops saying "unknown for this support" about a plane it
can describe.** For a plane built by `catia_gsd_plane_angle` in this session it now
answers `H = (0, 0, 1), V = (0.309017, 0.951057, 0), normal = (-0.951057, 0.309017,
0)`, with the derivation and the note that it is checked by measurement - the same
frame `catia_gsd_spline` was already reporting two calls later. Also fixed while
there: the reply said "created on on the datum plane 'P72'".

`gsd_thick_surface`'s description has carried the two-sided allocation since
0.3.86 (EACH SIDE'S OFFSET, with the four measurements including 0.25/0.25) - the
report says it is still missing; it is not.

1438 tests (+9), 135 tools, `scripts/b30_v0388_audit.py` 3/3.

## [0.3.87] — 2026-11-09 · three things that were left open, and one of them
## was measurable all along

The three items the 2026-11-09 reports left standing, done. The middle one is
the interesting one: the frame of a rotated datum plane **cannot be read**, but
it can be reconstructed and then **checked by measurement**, and the check came
back 0.0000 mm.

**`catia_gsd_fill` now measures what came back.** Reported twice: an open
25.0000 mm line produced "Fill surface created" while the same call with
`Edge.1` was refused for "needs CLOSED boundaries" - two routes, two answers,
and the silent one left a zero-area face that only surfaced later as non-manifold
edges. The area is now read from CATIA (`Measurable.Area`, m², so 100 mm² reads
0.0001) and published: a closed 10 x 10 loop answers "Its area measures 100.0000
mm2, which is what says the boundary really closed a region". Below 0.001 mm² the
fill is **deleted again** and the call raises `FILL_DEGENERATE` with the measured
number and the reason - `catia_gsd_boundary(edge=N)` can return one segment of a
border (25.0000 mm of a 91.1450 mm border), and filling a segment does not fail
on this build, it makes that face. The CATIA-side refusal now carries the same
code family (`FILL_NOT_CLOSED`), so both routes answer alike.

**A sketch on a rotated datum plane can be turned back into global XYZ, and the
frame is checked.** `HybridShapePlaneAngle` has 44 members - `GetOrigin`,
`GetFirstAxis`, `GetSecondAxis`, `GetPosition` - and every one of them answers
「方法 … 失败」: they take by-ref out-parameters, the same wall as
`Sketch.GetAbsoluteAxisData` (measured 2026-11-09 with a typeinfo dump). So the
frame is RECONSTRUCTED from what the plane was built from - the reference plane's
own frame, rotated about the axis by the angle - and the reconstruction is a
claim, so one corner is turned into a real point element at the position it
predicts and the distance from that point to the sketch is measured. On B30:
**0.0000 mm**, i.e. the point IS on the sketch, so the frame matches CATIA.
The probe uses the corner FARTHEST from the origin (the first corner is usually
the sketch origin, which lies on any sketch through it - a probe there would
answer 0.0000 for a frame wrong by 90 degrees). On a mismatch the call refuses
with `SKETCH_FRAME_MISMATCH` and the measured gap.

Measured end to end on B30 2026-11-09 with the reporter's own geometry: sketch on
`P72` (= zx rotated 72° about z), a 6 mm line along V comes back as
`(1.8541, 5.7063, 0)` - 6*(cos72, sin72, 0) - and the frame is CHECKED at
0.0000 mm. The three call sites now share one table (`catia_mcp/sketch_frames.py`)
instead of three copies.

**`catia_sketch_copy` says where the copy landed**, with the coordinates and the
provenance of the frame, so the reporter's own derivation (2·r·sin(A/2) = 7.0534
against 7.053 for r=6 at 72°) no longer has to be done by hand. Found while doing
it: a rename split one sketch's record in two - the placement went under the auto
name `草图.2`, the elements under `COPY` - so GSD refused a sketch it had just
been told about. `_rekey_records` carries the record across.

**The cap failure now names the loft fix.** `cap_curve`'s description has said
since 0.3.86 that on a loft the curve has to be one of the guides; the failure
message still only said "measure the joined border", which is the one thing the
reporter had already tried. It now says it: a loft's inner border is an
interpolated curve, a curve 0.0000 mm from it is still a different edge, and the
fix is to pass that curve as a third guide and make the sections start on it.

Also: `catia_set_visibility` says that one element can be shown alone
(`set_visibility(name='加厚曲面.1', show=true)` brought a solid back without
showing its set - measured 2026-11-09).

1428 tests (+16), 135 tools (no new ones), `scripts/b30_v0387_audit.py` 5/5.

## [0.3.86] — 2026-11-09 · the schema that called its own description a lie

Three reports on 0.3.85. The two capability gaps it closed (angle datum plane,
`cap_center`) were confirmed working with independent geometry - a 1069.9521 mm³
thickened ring against 1069.94 theoretical. What they found instead is a set of
places where this server said something it had not checked.

**`catia_create_sketch` rejected a datum plane NAME for two releases.** Its
`plane` schema offered `enum ["xy","yz","zx"]` while the same field's description
said a datum plane by name was accepted - so `plane="Plane3_36"` died in
validation with "must be equal to one of the allowed values", before the server
ever looked at the tree. The resolver has understood names since 0.3.83. The enum
protected nothing (`"xy"` is a string too) and made the tutorial's Step 4
impossible: copy the end profile to Plane4 worked, DRAW the middle one on Plane3
did not. `anyOf` with a bare string now, and the description names
`_plane_angle` among the tools that produce such a plane.

**A 25 mm straight side was reported as "Loop #1 ... THIS loop only".** Measured
on a four-sided flat sector: the selector returned one edge of 25.0000 mm while
the border is 91.1450 mm in all four. The reply now reports the share -
"Border curve #1 ... (5.8779 mm of this surface's 91.1450 mm of border edges)" -
and says plainly that a piece of a border is not necessarily a closed loop, and
that a fill on an open one succeeds as a degenerate face instead of failing.

**That sum was wrong at first, and wrong in a way worth recording.** A datum
plane's border carries the same `Selection_BorderREdge` label a surface's does
(topo_search reads that label as "datum-plane border" - correct in a body search,
wrong in a set search), and its four borders measure 2e7 mm, CATIA's idea of an
infinite plane. Summing "the border edges" gave 80000091.1450 mm. The collector
now excludes `GSMPlane.N`, and a construction line's own edge
(`Selection_WireREdge`) was already excluded.

**Every `edge=N` failure claimed to be a JOIN result.** True of the one geometry
it was measured on, false for the next caller: the 2026-11-09 report hit it on a
single multi-section surface. A loft is not a join, and being told to "take the
loop from the piece before the join" sent them to check the wrong thing. The
refusal now names both situations and says it cannot tell which one it is.

**`SAVE_FAILED` asserted three things about a document it never looked at.** The
message carried "This document already has a file, so the path is not the problem"
next to "session state: document=`<none>` | no part document". Those sentences
were written for the one caller that always has a file and printed for every
caller. They are conditions now (IF it had a file / IF it never was saved / IF the
name is non-ASCII), and `effects.modified` is `false` instead of `null`.

**The Chinese-folder P0 has an answer, and it is not the one the report expects.**
`new_part(file_path=...)` calls `_save_document`, which takes `dialogs.save_as`.
The failure text every report quotes - "Save failed after 3 attempts:
<document name>" - can only come from `dialogs.save`, the bare `doc.Save()`, and
`new_part` has no such site; its own failure reads "Part '...' was created but
COULD NOT BE SAVED". Measured in the reporter's own folder, instrumented:
`F:/WokrBuddy Catia Space/饮料瓶瓶底曲面建模/_probe86_a.CATPart` saved through
`save_as` alone, 40695 bytes on disk, and a bare `Save()` on that document
afterwards also succeeded. So this is recorded as NOT REPRODUCED for the fifth
time, with the call trace that shows which function ran - and the misleading
suggestion text is fixed either way, because that part of the report was exactly
right.

**Hiding a geometrical set still does not hide the solid** - second measurement,
this time with a clean control: one pad in the body and one surface in the set,
STL triangles 63 solid alone -> 73 with the surface -> 73 after hiding
construction -> 63 after hiding the set. It lands exactly on "solid alone". The
0.5 mm plate seen edge-on is the likeliest explanation for the empty view.

**The angle-plane sketch still has no readable frame**, and now says so with a
way out: `Sketch.GetAbsoluteAxisData` needs an out-array pywin32 cannot deliver,
so the reply explains that the placement is checkable with
`catia_measure_distance` between the copy and the original (a copy rotated by A
degrees moves a point at radius r by 2r·sin(A/2) - measured 7.0534 mm against
7.053), or by passing the corners explicitly.

Also: `catia_gsd_thick_surface`'s description now compares EACH SIDE against the
local radius instead of "total below twice the smallest radius" - the old
sentence reads as "this wall can never be 0.5 mm" and the report abandoned a
0.5 mm wall because of it; `cap_curve` says a loft's inner border has to be one
of the guides; `gsd_fill` says closeness is not checked; `catia_new_part` no
longer promises "nothing else is needed when the path is given".

1412 tests, 135 tools, scripts/b30_v0386_audit.py 5/5 on B30.

## [0.3.85] — 2026-11-09 · the selector that was there, one argument away

The 2026-11-09 report asked for three APIs. Two of them were the right idea
about the wrong object, and the measurements are more useful than the code:

**`AddNewBoundary(edge, support, propagation)` IS the loop selector.** The
report said `AddNewBoundaryOfSurface` cannot pick a loop, and that is true - it
takes one argument and returns one curve carrying every loop. But the three-
argument sibling was in the same factory table the whole time. On a cone, whose
two border circles have deliberately different lengths so the answer cannot be
ambiguous:

    propagation 0 -> 188.496 mm   both loops
    propagation 1 ->  62.832 mm   the r=10 loop, when that edge is named
    propagation 1 -> 125.664 mm   the r=20 loop
    propagation 2 -> the same as 1 on a smooth border

and a fill on one of those loops measured 314.159 and 1256.637 mm², against
1570.796 for the whole border - which is π·400 + π·100, one flat face per loop
STACKED, not the lateral area of the surface it came from. That number is what
broke the report's Step 6: not a "duplicate face" in the abstract, but two discs.

**Where it does not work is worth as much.** On a JOIN of five patches, all 30
border edges refused at `UpdateObject` - while `AddNewBoundaryOfSurface` on that
same joined surface answered fine (border 205.725 mm). So the selector is a
single-feature tool, and an array result cannot use it. That is stated in the
tool, in the refusal, and in README, because "it worked on my cone" would
otherwise send the next caller into the same wall.

**`cap_center` closes the hole with the hole's own edge.** A circle cannot do
it: a five-sided hole's nearest point to its axis is r·cos36 - 4.045 for r=5 -
so a circle drawn at "the distance to the border" lies wholly inside the hole and
splits nothing (measured; that is why the report's circle route failed and not
only the shared-edge business). What works is rotating the hole's own edge
count−1 times, joining the copies into the border, filling that, and joining onto
the base. Measured on a five-lobe base (five 72° sectors, 2080.436 mm², hole at
r=5): joined border 29.3893 mm (the pentagon's perimeter), fill 59.441 mm² (its
area), closed 2139.877 = 2080.436 + 59.441. Exact, because the cap shares an edge
by construction. The curve has to be named - `cap_curve` - and that is not
laziness: a filled patch's border cannot be split into its sides by any call
tried (an extract of one edge came back as all four, 91.1450 mm), so guessing
would be inventing.

**`catia_gsd_fill` takes `Edge.N`.** A raw topological edge is not a curve
(`Fill.AddBound` answers E_INVALIDARG), so it is extracted first - and on a
surface in a geometrical set the search now walks the SET instead of the body,
which is what made every `Edge.N` answer "the active body has no solid shape
yet". The extract returns the whole ring (91.1450 mm for a four-sided sector),
and the description says so instead of implying a single side.

Also: the two `cap_center` refusals happen before any rotation is built, and a
cap that fails at join deletes what it built and says the array was untouched.

1403 tests, 135 tools, scripts/b30_v0385_audit.py 6/6 on B30.

## [0.3.84] — 2026-11-09 · ten members away from the answer

Two reports arrived the same evening and they disagree with each other about one
method. Both were partly right, which is the interesting part.

**The GSD pattern member is on ShapeFactory, and it still does not work.**
0.3.82's description said `AddNewGSDCircPattern` was not declared anywhere; that
came from measuring `HybridShapeFactory` (160 members, 131 AddNew*, none with
"Pattern") and stepping from "not in A" to "not anywhere". The 2026-11-09 report
read the diagnosis output and found the member ten members away, on the Part
Design factory. It is there, with 14 arguments, and it returns an object. What
that object then does is refuse `part.UpdateObject` in all seven shapes tried:
`iType` 0/1/2, `completeCrown` on and off, a GSD surface and a Part Design pad as
the source, and the body set as the in-work object. So the rotation path is not a
preference, it is what this build can do — but the description says that now,
instead of saying the member does not exist. The two factory counts are quoted
together with the reason they differ (whole table vs AddNew* subset), and
`catia_diagnose` gained `typeinfo_members` plus a description that points at the
method tables as the answer to "can this CATIA do X". That capability was already
there and nobody could find it.

**`catia_gsd_plane_angle`** closes the one gap the tutorial route still had: a
datum plane rotated a given angle about an axis, which is how one 1/N sector is
taken. `AddNewPlaneAngle(plane, axis, angle, orientation)`. Two measured rules,
one of which cost eight call shapes to find: THE AXIS HAS TO LIE IN THE REFERENCE
PLANE, because rotating a plane about its own normal leaves it where it was and
CATIA answers with a generic E_FAIL that reads like a bad argument; and the angle
is in DEGREES (72 landed 0.0000 mm from a point at 72°; 1.2566, the same angle in
radians, was read as 1.2566°). The refusal now names both, and names a
combination that works.

**Capping a hole** — the report's Step 6, where both of its routes failed — has a
third route, and it is the tutorial's own: split, fill, join. `AddNewBoundaryOfSurface`
returns ONE curve carrying every loop, so filling it rebuilds the whole face
(their 1714 non-manifold edges); a precise circle has no edge in common with a
wavy hole border, so the join refused. After `catia_gsd_split` with that circle,
the outer piece's inner border IS the circle, and the fill shares it by
construction. Measured end to end on B30: 1600.000 → 1486.903 → 113.097 →
1600.000 mm², thickness 0.5 → 800.0 mm³. Written into `catia_gsd_split`,
`catia_gsd_fill` and `catia_gsd_boundary` at the point of use, and into README's
Known limits, because a capability nobody can find is a missing capability.

**The thickening's blocker is ONE SIDE, not the total.** Four measurements agree:
0.5/0 refused at a 0.355 mm radius, 0.25/0.25 built a 0.500 mm wall, 2.54/0
refused on a 2.30 mm section, 1.27/1.27 refused there too. The old advice —
"keep the total below the radius" — reads as "this wall can never be 0.5 mm" and
is what sent the reporter to abandon the thickness the design needed. It now says
to split the same total across both sides, with those numbers. When the caller has
already split it and it still fails, curvature stops being the explanation
instead of being repeated.

**Hiding a geometrical set** now says where the solid actually is. Measured: a
set and the body are separate branches, so hiding the set took the surface's
triangles out of the STL (3778 → 3244 B) and left the body's solid alone. The
report's empty view was therefore not this tool hiding the body — the thing they
were looking at was inside the set, and the recovery is to show the SET, not
'零件几何体'. That sentence was missing and they had to guess feature names.

**The Chinese-folder regression did not reproduce.** Four path shapes in one
session with several documents open — plain ASCII, spaces, Chinese, spaces+Chinese
(the report's exact shape) — all saved, and `catia_save_document` into the same
folder saved too. Their message is "Save failed after 3 attempts: <document name>",
which is a bare `doc.Save()` (3 tries, name only); a first SaveAs says "SaveAs
failed after 6 attempts" with the full path. No bare-Save site is reachable from
`new_part(file_path=...)`, and 0.3.82's ASCII staging route would have made a
second SaveAs work anyway. Recorded as not reproduced with the measurements,
rather than "fixed" on a guess — the same call that was wrong about the folder in
0.3.74, and the reason that report's cause is not being adopted on faith.

1388 tests, 135 tools, scripts/b30_v0384_audit.py 5/5 on B30.

## [0.3.83] — 2026-11-09 · the tools that did not meet

The acceptance report on 0.3.82 is the best kind: it confirmed the fixes with
the same geometry that had failed before ("上次那条批评撤回"), ran the whole
surface route to a solid for the first time, and then found that two of the
tools added in that release cannot be used together.

**`catia_sketch_copy` refused every datum plane name (P1).** Its own schema said
"`'xy'`/`'yz'`/`'zx'`, or a frame, **or the name of a datum plane**" and the
resolver answered `Unknown plane 'Plane_NormValley'. Use 'xy', 'yz', or 'zx'.`
for all of them - so `catia_gsd_plane_normal_to_curve`, added the same day,
could be called but its output could not be used: the tutorial's Step 4 needs
exactly that plane, and its frame cannot be hand-computed because the curve's
tangent and normal are not readable. The name is now resolved through the tree
(`FindObjectByName`, which reaches into the geometrical sets), a named element
that is not a plane is refused as `SUPPORT_NOT_PLANAR` with its type, and an
unknown name answers `PLANE_NOT_FOUND` with the datum planes the part actually
holds - "Unknown plane" alone reads as a spelling mistake. Measured on B30:
both the report's cases (a plane from `plane_normal_to_curve`, one from
`plane_offset`) now take a sketch. `catia_create_sketch` accepts the same names.

**The profile summary for a closed spline was wrong (P2), and so was its copy's.**
A 6 x 6 square closed spline reported `bbox (10,10) .. (10,10)` and
`outline 18.000 mm`. The bbox came from `_profile_ends` - the endpoint PAIRING
table, where a closed spline repeats its first point - and the length from the
points that were passed in, which is three sides of a four-sided loop. The
extent now comes from the geometry (every recorded point of every element, with
an arc's quarter-turn extremes included) and a closed spline's closing segment
is counted. The number is labelled for what it is: a spline's outline is the
straight line through its control points, so it is a LOWER BOUND and the bbox is
the control polygon's.

**The join refusal asserted a cause it had not measured (P2).** 0.3.82's message
said copies fail to join when they "all touch one common line" - true of the one
experiment behind it, and wrong for the caller's next case, whose sector was a
ring with an arc as its inner boundary, nowhere near the axis. The real reason
was that his sector spans half a period, so its two side boundaries differ and
copy i's trailing edge is not copy i+1's leading edge; he rebuilt three times.
The message now measures the distance between the original and its first copy
and branches on it: a gap means they do not touch, 0.0000 mm means they touch
and the edges merely coincide rather than being the same edge - and if the
distance cannot be measured, the reply says it makes no claim about the cause.

**A failed pattern left visible copies and did not say so (P3).** The copies stay
on purpose (deleting them could take whatever was built on them), but they are
visible, and visible geometry enters the STL that `catia_get_bounding_box` and
`catia_check_surface` measure. The refusal now says that, and names the one call
that removes them: `catia_delete_feature(names=[...])` - which already took a
list, so the bulk delete the report asked for exists and was simply not findable.

134 tools (unchanged), 1377 tests (+11), `scripts/b30_v0383_audit.py` 8/8 on B30.

## [0.3.82] — 2026-11-09 · the endpoints the record never kept

A bottle-base report from a caller who built the whole model through this server
and counted what it cost him. Two of its four "capability gaps" were mine to fix,
one turned out not to exist on this build, and the P0 that led the report did not
reproduce here - but checking it turned up a defect introduced an hour earlier in
the same file.

**`catia_close_sketch` called a closed profile open (P1-1).** The reported sketch
was one 6-point spline plus four lines whose ends coincide to the digit, and the
reply said `closed: false` with a 29.39 mm gap between an end and itself. The
cause was two recorders that never ran: `_draw_arc` called `_note_ends` and
`_note_element` AFTER its `return`, so an arc had never entered the drawing
record since the code was written, and `_draw_spline` never called them at all.
Both are recorded now, and a sketch whose every element is a closed curve answers
`closed: true` with the reason in `basis` instead of `closed: null` ("nothing
recorded") for a plain circle. Measured on B30: the report's profile now answers
`closed: true, endpoints: 10, endpoints_loose: 0, breaks: []`.
*The first version of this fix recorded the arc twice - four endpoints per arc -
and a test written against the drawing helpers caught it.*

**`catia_new_part` into a Chinese FOLDER (P0) did not reproduce.** Nine measured
attempts on B30 (five on 2026-11-07, four today) save an ASCII name in a Chinese
folder, including the report's exact path; only a non-ASCII FILE NAME is refused.
The report's own message says `方法 Save 失败` and `Save failed after 3 attempts` -
that prefix belongs to a bare `Save()` (3 attempts), while a first `SaveAs` says
"SaveAs failed after 6 attempts", and none of the four bare-`Save` sites is
reachable from `catia_new_part(file_path=...)`. So the mechanism in that call
could not be confirmed from the message, and the folder theory is not supported.
What IS done: the message now branches on `isascii()` of the name instead of
telling the caller to work it out ("if this name IS ASCII, that is what to act
on"), and a failed FIRST `SaveAs` gets one more route - save to `%TEMP%`, then
move to the path asked for - because the report's control shows a document that
already has a path saving where a first SaveAs failed.

**Both of those were wrong once.** Re-running the audit hit `overwrite` on an
existing file: the refusal came back as `SAVE_FAILED` with a paragraph about
non-ASCII folders, and the staging fallback then *deleted* the existing target
(`save_as` removes an existing file) - exactly what `overwrite=false` promises
not to do. The fallback now only runs for a raw COM failure, a refusal this
server makes itself keeps its own code and advice, and the "a staging file was
already tried" sentence is printed only when one was.

**`catia_gsd_circ_pattern` (P1-5).** The report said
`AddNewGSDCircPattern` is "in the typeinfo, only not exposed". On B30 it is not
there at all: the `HybridShapeFactory` declares 160 members and NONE contains
"Pattern" (dumped 2026-11-08). So this is rotate-and-join behind one call, the
reply says the tree will hold N-1 rotations plus a join rather than a pattern
node, and `join=false` is the way out when the copies cannot be joined - a join
needs an edge shared by TWO faces, and three coplanar patches meeting the same
axis line are refused (measured; the refusal answers `PATTERN_JOIN_FAILED` and
leaves the rotations).

**`catia_gsd_plane_normal_to_curve` (P1-4).** The tutorial's "Normal to Curve +
Set Origin on Curve", which the report had to fake with a frame sketch. Both
type-library members are real: `AddNewPlaneNormal(iCurve, iPt)` and
`AddNewPointOnCurveFromPercent(iCrv, iLong, iOrientation)` - THREE arguments, and
a two-argument call answers `无效的参数数目` (the first version did, and the audit's
error attribution made it look like the plane call). `iLong` is a FRACTION, not a
length: measured, a point at 0.5 on a 100 mm line put the plane 0.000 mm from a
datum point at z=50. The reply reports the curve's measured length and the
distance along it, because the point's own coordinates cannot be read back
(`GetPoint` answers `方法 GetPoint 失败` - it wants a by-ref array).

**`catia_sketch_copy` (P1-3).** "Copy the profile, paste it on the next plane."
Replays a sketch's recorded geometry into a new sketch: same 2D coordinates
reinterpreted on the new plane, optional in-plane `offset`. Refused when there is
no record (a sketch reopened from disk cannot hand its coordinates back), and the
reply says the copy holds the coordinates that were PASSED IN, not the ones CATIA
solved to after constraints.

**P1-2 named where it is looked for.** The loft description already carried a
paragraph about SolidWorks "maintain tangency, factor 1.0"; it now also names
"Normal to Profile with a strength of 1.5", because the report's reader did not
find the fact under the name he was searching for.

P2-1 (screenshot resolution and the spec tree in frame) was already documented in
0.3.80 and is unchanged: the size is the 3D window's, and `width`/`height` are
advisory, which the schema says. P2-3 (keep the failed part in memory) stays as
it is: 0.3.77's P9 asked for the opposite, and the empty part is closed.

134 tools (+3), 1366 tests (+14), `scripts/b30_v0382_audit.py` 9/9 on B30.

## [0.3.81] — 2026-11-08 · the reply described something the model did not do

Two reports landed on release day. The first is a modelling report with seven
findings; the second reads the same release at source level and adds three. They
agree on the one that matters most:

**`catia_replace_component` announced "the new instance references the file at
that path" without ever reading it.** The only evidence behind that sentence was
"the component count grew by one" and "the twelve pose numbers match" - and on
B30 both are satisfied by an instance that resolved to a completely different
file. Measured 2026-11-08, in the reporter's exact shape: with
`A\PZ81A.CATPart` open, adding `B\PZ81A.CATPart` (a byte copy) produced an
instance whose `ReferenceProduct.Parent.FullName` was **`A\PZ81A.CATPart`**. Same
internal document id, so CATIA answered with the loaded one and ignored the path.
That is the tool's whole reason to exist, and it was the one thing not checked.

### P0 — the reference is read back, and a mismatch undoes the insert

`replace_component` now reads `ReferenceProduct.Parent.FullName` after the insert
and compares it with the path it was given (the same call
`catia_list_components` already used to detect dead links). A mismatch answers
`REPLACE_REFERENCE_MISMATCH`, **removes the instance it just added**, and leaves
the old one exactly as it was - no "free rename" for the caller to absorb:

```
'PZ81R.2' was inserted from '...\B\PZ81R.CATPart' but the instance references
'...\A\PZ81R.CATPart' instead, so 'PZ81R.1' was NOT replaced and nothing was
deleted (the instance this call inserted was removed again). This build
identifies a document by an INTERNAL id, not by its path, so a byte copy of a
file that is already open in the session is silently answered with the loaded
document ...
```

When the path cannot be read at all (a dead link, an unsaved document) the reply
degrades to "the POSE was verified but the REFERENCE was not" instead of claiming
success. On the honest path it states the path it read back. New
`catia_list_components(include_reference=true)` publishes `reference_path` per
instance, so "is this assembly self-contained" is now a question a caller can ask
about an assembly it did not just build.

### M3 — a spline sketch is no longer lost for good, and the message stops contradicting itself

`catia_sketch_spline` recorded how many points it drew and how long the chord
was, but **not where the points were** - so when a failed pad/pocket took the
sketch with the rollback, the rebuild path refused it. Since 0.3.77 forces a loft
section to be "one closed spline", the most ordinary mistake (wrong direction)
cost the entire section. The record now keeps the control points and whether the
profile was closed, and `rebuild_lost_sketch` replays them - repeating the first
point so the result stays ONE curve.

The old reply also said both things at once: "catia_create_sketch + the same
drawing calls + catia_close_sketch rebuilds it" and "It could NOT be re-created".
The failure branch no longer borrows the success advice, and the envelope's
`next_actions` no longer says "retry once with direction='normal'" when there is
no sketch left to retry with - `_recover_lost_sketch` returns
`(sentence, a_retry_can_work)` and all four raise sites use it.

### M6 — the drawing record follows the rebuilt sketch

Two stores hold the same elements: `conn.sketch_outlines` (which the rebuild
migrated) and `self._drawn` (which `catia_sketch_get_geometry` reads, and which
nothing migrated). After a rebuild the record stayed under the dead name, so
`drawn_this_session` vanished, and `catia_solid_loft`'s per-section point count -
which can only come from this record, CATIA cannot read a spline's points back -
silently stopped comparing. The guard was off precisely on the sections that had
been through a rollback. Both stores are now written.

### M1 — a duplicate geometric set is renamed; an ambiguous name is refused

`catia_gsd_create_geoset` assigned `hb.Name` and never looked. Two sets called
`OUTER` were therefore possible, and the three tools disagreed about them:
`set_visibility` hid BOTH, `gsd_spline` drew into one, `set_active_geoset`
answered "Active geometrical set: 'OUTER'" about the first match. A duplicate is
now named `OUTER#2` and the reply says why; `parent=` and `set_active_geoset`
refuse an ambiguous name with `GEOSET_AMBIGUOUS` and list the candidates
(`#1`, `#2`, in `catia_gsd_list_elements` order - two same-named sets at the same
depth are otherwise indistinguishable in prose).

### M2 — the tree can be read back

`catia_gsd_list_elements` walked recursively and printed every set flat, so the
one thing `parent=` promises could not be verified. Each entry now carries
`parent` (null at the root), `depth` and `index`, and the session line in a
failure message separates the two counts: `geometrical_sets=2 at the root, 3
including nested`.

### M4 — closing the document the advice names

`open_document`'s `DOCUMENT_IDENTITY_CONFLICT` told the caller to run
`catia_close_document(name='...')`, and `catia_close_document` had no such
parameter - it closed whatever was active. The parameter now exists
(`document=`, matched by full path first, then by name), an ambiguous name is
refused with the candidates rather than picked, and the advice names the full
path. It does NOT activate first: `Activate()` returns success on a document with
no window of its own and moves nothing (measured 0.3.74), so "activate, then
close the active one" can close the wrong document.

### M5 — `by alias` names the name that found it

`{"sketch": "草图.4", "found": "by alias '草图.4'"}` after asking for 草图.3 read
as "found 草图.4 by the alias 草图.4". It is now
`by alias '草图.3' -> '草图.4'`.

### //n — two user-visible messages, fifteen versions

`assembly.py` carried the literal `//n` in `_CONSTRAINT_EXPLAIN` (since
2026-10-23) and in the new `replace_component` success text. 1352 tests did not
look, so a mechanical guard does now: any string constant in `catia_mcp/**`
containing `//n`, `//r`, `//t` or a doubled backslash-escape fails the suite.
The class is machine-detectable, which makes not checking it a choice.

### P3 — the count label follows the mode

`colorize_assembly` said "25 part(s) were at the assembly default" about 25
instances in the same sentence that had just declared "one per INSTANCE". The
unit now follows `per_instance`.

### P1-1 again — a colour reset to the default is not "already carried a colour"

0.3.80 separated the two defaults but still tested `== (255,255,255)`, so a caller
who reset all 11 parts to `#D2D2FF` was told "11 already carried a colour" a
second time. Measured on B30, one body, four states - never coloured, explicitly
set to (210,210,255) with the displaying flag, set with the non-displaying flag,
set to (228,87,46):

```
(0, 210, 210, 255)  (0, 210, 210, 255)  (0, 210, 210, 255)  (0, 228, 87, 46)
```

The tuple's first element is 0 in every state, so nothing can distinguish "never
set" from "set to the appearance of never set". `is_default` now accepts
(210,210,255) in both contexts, and (255,255,255) on INSTANCES only - white on a
body is a colour somebody chose and it renders white. `structured=true` also
publishes `default_appearances`.

### The pocket reply bounds the cut

A removed volume cannot be predicted from `depth` alone: depth is how far the
cutter travels, not how thick the wall is. Measured on B30: profile 30 mm²,
depth 400, one 40 mm wall → 1200 mm³, not 12000. So the reply publishes the
BOUND it can defend - profile area, travel, and the product - for a single
rectangle or circle profile, and says a thinner wall removes less.

### M7 — not reproduced, and the arithmetic says why

The report's three runs of one pocket call, rebuilt here in the same geometry:

| the report | measured on B30 2026-11-08 |
|---|---|
| A: one block, profile x 10..20 / z 1..4, depth 400 → 1200 mm³ | **1200 mm³** (8000 → 6800) ✅ same |
| B: TWO disconnected blocks → "NO_MATERIAL twice, UpdateObject failed" | **succeeded, 1200 mm³** (16000 → 14800) - two lumps are not a problem |
| C: nine blocks, profile 30×30 → auto-flipped, 29 747.4 mm³ | **12000 mm³**, no auto-flip - and 12000 is exactly what the arithmetic gives (6000 for the y 0..40 lump + 6000 for y −60..−100, which the report's own 6000 ignored) |

So this build cuts frame-sketch pockets on multi-lump bodies correctly, and the
one number that "did not match any direction hypothesis" matches the plain
arithmetic once both lumps in the cutter's path are counted. **Not reproduced**;
if it reproduces on B32 the missing ingredient is in the report's own recipe
(its blocks came out of a rollback-rebuilt pad, not from clean pads).

### About `.damaged-082006`

The second report asks whether that suffix comes from this server. It does not.
It is the backup **the author** left while repairing an assembly that a
verification script of theirs had modified and saved on 2026-11-08 08:20 - a
mistake on this side, documented in the project notes. The server has no
`.damaged-<HHMMSS>` naming anywhere: nothing in `catia_mcp/` writes such a file.

### Numbers

1352 tests (was 1332), 131 tools, `scripts/b30_v0381_audit.py` **14/14** on B30.

## [0.3.80] — 2026-11-08 · two defaults, one missing capability

The 0.3.79 acceptance report, in one line: the colour work succeeded (11 parts,
25 instances, saved, reopened, read back identical) and four things around it
were wrong.

### P1-1 — "11 already carried a colour" about a file that had never been coloured

`catia_colorize_assembly` compared every instance's read-back with
`(210, 210, 255)` — the default measured on a **part body**. An uncoloured
**component** reads `(255, 255, 255)`. Measured on B30 with one part in one
session: the body in its own document answered (210, 210, 255) while all three
instances in the assembly answered (255, 255, 255). So every instance of a fresh
assembly counted as "already coloured", and a 25-instance / 11-part assembly was
told 11 of them had a colour.

Two defaults now, in `catia_mcp/colours.py` (`UNCOLOURED`,
`UNCOLOURED_INSTANCE`), and the reply names the one it compared against:

```
2 part(s) were at the assembly default #FFFFFF (no colour set); every colour read back exactly.
```

A second run then says what it really overwrites. `catia_get_color` and
`catia_list_components(include_color=true)` use the instance default too.

### P2-2 — the mapping was text, and the part number was case-folded

The reply printed the grouping key (`p01`) while the tree, the part number and
every other tool say `P01`. Fixed, and `structured=true` now returns
`data.mapping[]`:

```json
{"part_number": "P01", "colour": "#E4572E", "rgb": [228, 87, 46],
 "instances": ["P01.1", "P01.2"], "previous_colour": null,
 "previous_was_default": true}
```

plus `generated`, `were_at_default`, `were_coloured`, `failed`,
`default_colour`. `previous_colour` is **null** when there was nothing to
overwrite — a caller must not see CATIA's default dressed up as an existing
colour. Default stays text: making the envelope the default would break every
existing caller, the same rule 0.3.76 followed.

### P1-2 — no way to re-point a component (`catia_replace_component`, new)

The report could not make an assembly self-contained after byte-copying its parts,
because `Documents.Open` on a copy answers `DOCUMENT_IDENTITY_CONFLICT` (CATIA
identifies a document by an internal id, not a path) and nothing could change what
an instance references.

Measured on B30: this build's `Product` and `ProductDocument` expose **no**
`ReplaceComponent` / `Replace` / `Relink` / `Repath`, while `Products.Remove` and
`AddComponent` work and the twelve placement numbers can be written onto a fresh
instance with **zero** drift. So the new tool does what is available — insert,
copy the pose, **read it back**, then remove the old instance:

* a pose that does not match leaves **both** instances and answers
  `REPLACE_POSE_MISMATCH` — nothing is deleted on a guess;
* a constraint that holds the old instance answers `REPLACE_STUCK` with both
  still in the tree;
* the instance colour is carried over, and the reply says so;
* the reply states plainly that this is a **rebuild**: the constraints that
  referenced the old instance went with it.

### P2-3 — the white frame that survived `kind="construction"`

`set_visibility(kind="construction")` hid every sketch and the **three origin
planes** — 33 for 11 parts, exactly what the reporter saw — and never looked
inside a solid body, where a frame sketch puts its three points and its plane.
It walks `body.HybridShapes` now and reports them separately
(`4 datum element(s) inside bodies`). Geometrical sets are deliberately not
descended: a surface there is usually the design.

### P2-1 and 7-2 — documentation, because there is no honest fix

* A screenshot is the **window's client area**, so the specification tree and the
  axis are in it, and the size is the viewer's. There is no supported way to
  suppress the tree from automation: `Application.StartCommand` **does not
  validate the command name** — an unknown one is accepted and then opens a modal
  未知命令 dialog on the automation thread (measured the hard way, 2026-11-08). A
  guessed command is not a shortcut, it is a freeze. Both facts are in the
  `catia_screenshot` description now.
* The save limit is the **first SaveAs to a new non-ASCII NAME**. A Chinese
  folder is fine, and a document that already has a path saves normally
  (`catia_save_document` on a CATProduct under `…\WokrBuddy Catia Space\…\out\`
  took 200 ms and its colours were on disk afterwards). The `SAVE_FAILED` message
  said "first save, not later ones"; it now says both halves explicitly, because
  the report shows the old wording was read as "the whole Chinese workspace is
  unusable".

`1315 → 1332 tests`, 130 → **131 tools**.
`scripts/b30_v0380_audit.py`: **15/15** on B30.

## [0.3.79] — 2026-11-08 · colour, and the flag that made it invisible

Asked whether the server could colour different parts of an assembly differently.
It could not: 127 tools, not one of them touched colour. `catia_set_visibility`
had been driving `Selection.VisProperties` for the show flag since 0.3.2 and the
other half of that interface was never wired up, so every part came out the same
CATIA grey.

Three tools: `catia_colorize_assembly` (one colour per part, or per instance),
`catia_set_color` (one component, one tree node, or the whole solid) and
`catia_get_color`. `catia_list_components` gained `include_color`.

### The three signatures that are not the documented ones

Measured on V5-6R2020 B30, 2026-11-08, before anything was written:

* `SetRealColor(R, G, B)` is **refused** — 无效的参数数目, "invalid number of
  arguments". It takes four.
* The fourth is **not a dummy**. Even values (0, 2) store the colour and leave
  the view alone; odd ones (1, 3) apply it. The property reads back the new value
  in both cases, so the obvious implementation — pass 0, read back, report
  success — is a tool that answers `#3CA03C` about a gray solid. That is exactly
  what this feature's first version did, and what the acceptance screenshot
  caught: read-back is not display.
* `GetRealColor()` returns **four** values with the RGB **last**:
  `SetRealColor(220, 30, 30, 1)` reads back `(0, 220, 30, 30)`. Slicing `[0:3]`
  reports black for every colour — the same trap as `GetShow()`'s flag at `[1]`.
* An untouched part reads **`(210, 210, 255)`**, not white, so the read tools say
  when a value matches that default and never claim nobody set it.
* `Selection.Add(part)` — the Part node, not a body — is accepted and reads
  `(0, 0, 0)`: it is not a colour target. The body is.

The primitives live in `catia_mcp/colours.py` once, because `list_components`
needs the read side too and a second copy of those layouts is how the `GetShow`
index stayed wrong for a release.

### What the tools promise, and what they refuse to guess

* **Instance colour vs part colour, stated in every reply.** In an assembly a
  colour goes on the OCCURRENCE and lives in the CATProduct: two instances of one
  part carried (220, 30, 30) and (30, 120, 220) at once, both survived save +
  close + reopen, and the .CATPart still read its default. In a part it goes on
  the body and lives in the CATPart, inherited by every feature inside it — and a
  feature can carry its own on top of that.
* **The palette never wraps.** Past twelve parts the colours are generated by
  golden-angle hue, because two parts of one colour is the one outcome
  `catia_colorize_assembly` must not produce. The reply says how many were
  generated.
* **A geometrical set colours its contents.** A set's colour does not reach what
  is inside it — each element carries its own, the same asymmetry visibility has —
  so the descent is explicit and bounded at four levels.
* **Every node with a name, not the first one.** `Item(name)` returns the first
  match and says nothing about the rest (four `Datum_frame` in one tested part);
  colouring one while the reply says "coloured" is the defect 0.3.74 withdrew for
  hiding.
* A colour that does not take is reported, and a colour that was already there is
  reported as overwritten.

Refusals rather than silent no-ops: a Part document for `colorize_assembly` (it
names `catia_set_color`), an empty product (it names `catia_add_component`), a
missing target in a product (it lists the components), an unknown hex/name (it
lists the accepted forms), a part with no body.

### Verification

`scripts/b30_v0379_colour_*.py` are the probes: interface shape, instance vs
part storage, scope (body / feature / geoset), and the flag matrix that found the
display bug. `scripts/b30_v0379_audit.py` is acceptance through the tools —
**28/28** — and two of its checks are pixels, not properties: the palette colour
must be on screen after colouring and again after a reopen. The pixel comparison
is by HUE with a saturation floor, because comparing RGB distances "failed" a
perfectly coloured picture — `#E4572E` renders as `(181, 68, 36)` on a shaded
face.

`1266 → 1315 tests`, 127 → **130 tools**.

## [0.3.78] — 2026-11-08 · the sign my own audit could not see

Two reports: the B32 acceptance of 0.3.77 (a documentation item) and the B30
acceptance, which found six things — one of them in 0.3.77's own verification.

### N1 — `dir1_axis` was still wrong, and my audit was how it stayed wrong

0.3.77 fixed the AXIS and verified it by comparing `dimensions` before and after:
"x/y/z each grow only their own axis by exactly 200 mm". A **length delta cannot
see a sign** — a pattern that mirrors by 200 mm grows the same length — so a
reversed pattern passed 12/12. The acceptance measured the box ENDPOINTS and
found every label inverted: `axis x+` moved the copies to −X.

Measured again here, on endpoints, and the model is different from what 0.3.77
assumed (`scripts/b30_v0378_sign.py`):

```
slot 1 -> the plane's H axis, NEGATED     PlaneXY -> -X, PlaneYZ -> -Y, PlaneZX -> -Z
slot 2 -> the plane's V axis, NEGATED     PlaneXY -> -Y, PlaneYZ -> -Z, PlaneZX -> -X
```

So the two slots need different tables (`by_h` / `by_v`), and both need the
reverse flags flipped before `AddNewRectPattern` — the reply now says `axis x+`
and the copies really do go to +X. The default pair (no axis arguments) keeps
its geometry, and its sentence states the measured dirs: `dir1 (-Y), dir2 (-X)`.
The schema said `PlaneYZ -> X and PlaneZX -> Y` — two errors in one sentence, and
the third time that line has been rewritten (N6).

### N2 — two sketches with one name made every by-name tool ambiguous

A part ended up holding `草图.1 / 草图.3 / 草图.2 / 草图.4 / 草图.1 / 草图.2`.
CATIA numbers a new sketch from its own counter and does not check for a
conflict, and every downstream tool resolves BY NAME — loft sections, pad's
`feature_name`, `delete_feature`, `sketch_edit` — so a duplicate silently
re-points at the other object mid-session, and the drawing record (keyed
`document::name`) merged the two profiles, which is how a 4-line rectangle got
described as a 7-point spline.

* a new sketch whose name the PART already uses is renamed at creation
  (`草图.1#2`), and the reply says so. `Sketch.Name` is settable on B30 and
  CATIA keeps numbering from its own counter, so the check runs every time.
* `catia_list_features` reports `name_shared_by` per entry, plus
  `sketch_name_conflicts` and a note, for trees that already hold one.
* `catia_sketch_get_geometry` publishes `drawn_this_session_key` and, when the
  name is shared, a note that the record may describe the other sketch.

### N3 — the warning was cut off exactly where the advice was

`catia_run_plan` trimmed each step's result to 600 chars BEFORE deciding whether
it carried a warning, so a 735-char loft reply lost its `WARNING: ...` tail
entirely: the step looked clean and `step_warnings` — the machine-readable
channel the `summary_note` promises — was never written.

* warnings are detected on the **untrimmed** reply, and the warning text is kept
  (capped at 1500 chars) instead of the truncated head;
* `step_warnings` is **always present** under `summary=true`, empty or not.

### N4 — `catia_open_document` announced a password it did not open

CATIA can hand back the session's copy of a file whose identity is already
loaded, while showing a modal ("会话中已存在具有相同标识的文件"), and the reply
announced the requested path anyway. The opened document's `FullName` is now read
back and compared: a different file is a `DOCUMENT_IDENTITY_CONFLICT` (nothing
reloaded, nothing discarded), and the modal is dismissed so the next call is not
blocked. **Not reproduced on B30 with a byte-identical copy** — that opens
normally — so what this release verifies is the invariant (the reply names a file
that really is open at that path), not the conflict branch itself.

### N5 — P8's recursive descent was unreachable

`catia_gsd_create_geoset` had no `parent`, so no caller could build a set inside
a set — and 0.3.77's recursive visibility descent therefore could never run in
practice. `parent` now creates it inside the named set, and refuses by name when
the parent is missing.

### Also

* the solid loft's failure now carries `code="LOFT_REJECTED"`: it answered
  `STEP_EXCEPTION` before 0.3.77 and `COM_ERROR` after it, purely because the
  message was rewritten — a silent change for a caller branching on the code.
* **the B32 documentation item**: the README Requirements lists **two** known
  build differences now (the multi-section surface *and* the threaded hole), and
  `catia_hole`'s `threaded` description carries the same limit at the call site,
  which is where 0.3.59's rule puts it. Registering a limitation in the CHANGELOG
  is not a fix, and the README no longer claims the rest is build-agnostic.

`1244 → 1266 tests`. `scripts/b30_v0378_audit.py` — 17/17 on B30, and every
direction check now records which **endpoint** moved: x/y/z each move towards
their own positive axis, `reverse_dir1` moves the other way, `dir2_axis='y'`
lands on Y, a duplicate sketch name is renamed, `INNER` sits inside `OUTER`, a
plan's `step_warnings` carries the untruncated advice, and a rejected loft
answers `LOFT_REJECTED`.

## [0.3.77] — 2026-11-07 · eleven items from an 85 m frigate built out of a text file

The report came from a run that built a frigate from a natural-language
description in ~500 `catia_*` steps. Two items were **silent wrong geometry**,
and one of them had been carried as an "unreliable, check the bounding box"
WARNING since 2026-11-02 — a warning is not a fix.

### P3 — `dir1_axis` was one axis off, and now the table is the measurement

`catia_rect_pattern` mapped `x -> PlaneYZ`, `y -> PlaneZX`, `z -> PlaneXY` on
the assumption that "a datum plane's reference gives its normal". Measured on
B30 by patterning a 10x10x5 pad 3x1 at 100 mm and reading the body box after
each call:

```
PlaneYZ -> +Y      PlaneZX -> +Z      PlaneXY -> +X
```

A plane reference drives the plane's **own horizontal axis**, one axis past the
one the table assumed — which is exactly the "`dir1_axis='x'` moved the copies
along Y" the reporters kept hitting. The table is now `x -> XY, y -> YZ,
z -> ZX`, the warning is gone, and the reply states the axis instead of
hedging. The default pair (no axis arguments) is unchanged geometrically
(PlaneYZ, PlaneZX), but its sentence was wrong too and now says **dir1 = Y,
dir2 = X**, which is what it has always built. Anyone who wants X first should
pass `dir1_axis='x'`.

### P2 — a point-count mismatch is named instead of silently twisting the hull

Two closed splines of 59 and 60 points were lofted into a hull whose stern
bulged +49.8% (min y -10335 mm against a nominal -6900) while the tool answered
`ok:true`, printed a plausible 4039 m³ and raised no warning: CATIA couples
sections **by parameter**, so the sections were paired index by index.

`catia_solid_loft` now reads the drawing record (the only place a sketch's
point count exists — CATIA cannot give it back) and:

* prints each section's shape in every reply: `'草图.3' one closed curve, 180
  points; '草图.4' 4 curves (1 rectangle(s))`;
* **warns on a point-count mismatch**, naming both counts and the measured
  consequence, so a shape that cannot be seen from the volume is at least
  visible in the reply.

### P5 — the recovery path accepts the plane the creating path documents

A frame sketch could be created and then could not be **re-created**: the
outline record stores `base: "frame"` (the form the GSD side replays) and the
resolver reads `type`, so a rollback answered `Unknown plane type None` about a
plane type `catia_create_sketch` documents and supports. The frame is converted
on the way in now.

### P6 — `direction: "normal"` is the default side, not a pin

`catia_pocket` only flipped a no-effect cut when the caller had **not** named a
direction, so naming the default (`direction="normal"`) turned a flip into a
guaranteed failed round trip: measured on B30, a frame sketch whose material sat
on the far side answered `NO_MATERIAL` for `normal` and cut for `reverse`.
`normal` now flips like `catia_pad` always has, and says so
("the requested side had no material, so the cut was auto-flipped"). `reverse`
stays literal (0.3.5's rule) — but its `NO_MATERIAL` now carries the way back
("retry once with `direction='normal'`").

### P8 — hiding a geometrical set now reaches a set inside it

Descending one level was the 0.3.73 fix and it was not enough: a set holding
another set was skipped, and the reply said "hidden and 12 element(s) inside
it" while the bounding box still measured the wireframe. The descent is
recursive (bounded at four levels).

### P9 — a part that cannot be saved is closed again

`catia_new_part` with a non-ASCII file name raised `SAVE_FAILED` and left an
empty, pathless document open — which no assembly can reference and which a run
that ignores `ok:false` builds on for hundreds of steps. The empty part is
closed again, `modified` follows that (nothing is left modified), and the
`next_actions` say to create it under an ASCII name. Same for
`catia_new_product`.

### P4 / P10 / P7 / P1 / P11 — what was measured, what was not fixed

* **P4 (`through_all` + `both` removing nothing on a frame sketch) does not
  reproduce here**: the same combination cut 4000 mm³ from a 40x40x40 block
  under a frame sketch on B30. Since the reporter's workaround (an explicit
  depth) is measured to work, the `NO_MATERIAL` reply for a `through_all` cut
  now names it — "retry the same profile with `depth=..., direction='both'`" —
  instead of blaming the profile.
* **P10**: with P2's section facts, a refusal now separates the two cases. If a
  section holds several curves it says which one and how many; if none does, it
  says that too ("No section in this call was recorded as multi-element, so look
  at the second cause instead") rather than repeating a hint that does not fit.
* **P7** is documented, not added: `catia_pocket` cuts everything it passes
  through in the active body. The schema now says so and names the way to
  isolate a cut (own body + `catia_boolean`).
* **P1 (loft over a multi-element section) is a real limitation and stays one.**
  Measured: four separate lines are refused; the same four points as a closed
  spline build. Four ways to merge a sketch into one curve were tried and all
  failed on this build — a join of the sketch's 2D elements (no usable
  `AddNewJoin` methods through late binding), the `Sketch` object itself (type
  mismatch), and an `Extract` of the sketch (update fails). The error now names
  the offending section and its curve count instead of hinting.
* **P11 (steps referencing an earlier step's return value)** is not done: it is
  a new language for `catia_run_plan`, not a defect, and the reporter's 109-step
  plan ran with hard-coded sketch names.

`1224 → 1244 tests`. `scripts/b30_v0377_audit.py` — 12/12 on B30: `x`/`y`/`z`
each grow only their own axis by exactly 200 mm, `normal` flips and cuts, the
59-vs-60 loft warns with both counts, a 4-line section is named as 4 curves, and
a refused save leaves the session at 32 documents rather than 33.

## [0.3.76] — 2026-11-07 · the gap list as data, without changing the sentence

The last open item of the 2026-11-06 batch. Its third bullet asked for the full
loose-endpoint list in a structured field "rather than truncated in natural
language": on a six-arc outline the reply had been cut to `... (1242 chars)`,
so the coordinates needed to repair the profile were exactly the part that
disappeared. 0.3.71 had already moved the summary and the advice to the front
of the sentence (so a truncated reply keeps the actionable part) and 0.3.74 put
the coordinates last, but neither made the complete list *reachable*.

The pairing that used to build the sentence is now computed once in
`SketcherTools._profile_breaks()` and the sentence is derived from it, so the
text reply is character-for-character what it was. `catia_close_sketch` takes a
new **optional** `structured` argument:

```
catia_close_sketch {"structured": true}
  -> ok / code=SKETCH_CLOSED / message (the same sentence)
     data.breaks = {
       "closed": false, "endpoints": 8, "endpoints_loose": 2,
       "breaks": [{"from": [0.0, 0.0], "to": [0.0, 0.0002],
                   "gap_mm": 0.0002}],
       "unpaired": [], "smallest_gap_mm": 0.0002,
       "widest_gap_mm": 0.0002, "within_tolerance": 1,
       "tolerance_mm": 0.05, "endpoint_tolerance_mm": 0.0001,
       "measured_from": "..."}
```

`closed` has three states, not two: `true` (every endpoint matched), `false`
(the list says where), and **`null`** — no coordinates were recorded in this
session, i.e. a sketch reopened from disk whose points cannot be read back.
The last one matters: an empty echo is not a closed sketch, and a Boolean could
only have said `true` and quietly lied about a sketch nobody looked at. A gap
of 12 mm is still reported in the data but still does not raise the sentence's
WARNING: an intentionally open profile (a sweep guide) is not a defect, and
that distinction was settled in 0.3.71.

`effects.modified` follows whether this session actually drew geometry: a
reopen-and-close wrote nothing and does not claim it did.

**Default `false`.** Making the envelope the default would change the reply of
every existing caller from text to JSON, which is exactly the kind of
compatibility break this batch of reports kept finding. The schema documents
the flag, the description says how to ask for it, and an existing caller needs
to change nothing.

`1213 → 1224 tests` (11 new). `scripts/b30_v0376_breaks.py` measured on
V5-6R2020 B30: 14/14 — an open profile returns the full break with its
0.0002 mm gap, a closed one answers `closed: true` with no warning, the default
reply is still the plain sentence, and the pad on the closed profile still
gives 4000.0 mm³. The 19 documents that were open before the run were left
untouched and the active one put back in front.

## [0.3.75] — 2026-11-07 · the file name is refused in both directions

A measurement made while answering "should we build the ASCII-staging fallback
the 2026-11-07 report suggested". The answer is no, and the reason corrects
advice this package has been giving since 0.3.71.

**A non-ASCII file NAME cannot be opened either, only saved.** Measured on B30
(`scripts/b30_v0374_zh_copy.py`, plus an isolating run):

| step | result |
|---|---|
| `catia_save_as_copy` → `F:\…\中文目录\副本.CATPart` | the copy is written, 57 022 B |
| `Documents.Open` on that file | **REFUSED**, `DISP_E_EXCEPTION` |
| `Documents.Open` on an ASCII name, same folder | opened |

So this build's document layer is ANSI in **both** directions, and the copy the
fallback would produce is a file CATIA cannot read back. What makes the refusal
worse than an error message: it pops a modal **突发事件报告** window on the
automation thread, so the call does not just fail - the next tool call finds a
dialog in front (the reporter photographed exactly that, at
`F:\catia_probe_v0374\副本.CATPart`).

Which means four messages were telling callers to produce an unusable file:

    "save it to an ASCII path, then rename the file afterwards -
     the .CATPart does not care what it is called, only CATIA's SaveAs does"

That sentence is gone from `results.PathNotSupported`, `dialogs.save_as`,
`dialogs.save`, `catia_new_part` and `catia_new_product`. They now say: keep the
**file name** ASCII (the folder may be Chinese, that half still holds), and put
the Chinese wording in the **part number** - which is what the tree and the BOM
show - or in a parameter. A test scans the package for the old phrasing and fails
if it comes back.

**`catia_open_document` refuses a non-ASCII file name before CATIA sees it**, for
the same reason `save_as` refuses one: prevention is the only way to keep the
modal dialog off the worker thread. Verified on B30 - the call answers
`PATH_NOT_ASCII`, no document is opened, no dialog is left behind, and the
session still holds its 12 documents afterwards. The message names the
measurement and says the check is what to remove if a build ever accepts such a
name.

`1206 → 1213 tests`.

## [0.3.74] — 2026-11-07 · "activated it" was a guess, and a datum plane nobody could turn off

Two reports this round: a new one against 0.3.70 (two defects), and the re-check
appended to the 0.3.73 one, which had left three items at "未确认，仍需复现".
Every item below was reproduced on B30 before it was changed, except the one
described as not reproducing.

**`catia_open_document` reported an activation it never got.** For a file CATIA
already holds, the tool calls `Activate()` and answered "already open - **activated
it**". `Activate()` also returns without an error for a document that has **no
window of its own** — a part being edited in context as a component of an open
product — and leaves `ActiveDocument` where it was. So the caller was told the
switch had happened, the next tool answered `ActiveDocument 'Drawing1.CATDrawing'
exposes no 'Part'`, and `catia_activate_document` **refused the same part** with
`ACTIVATION_FAILED` in the same state: two tools, one fact, opposite answers
(reported 2026-11-07, 70-1 — four follow-up calls were rejected, all pointing back
at that sentence).

The read-back moved into one place (`_activate_or_refuse` / `_activation_refusal`)
that both tools now use, so the "already open" branch cannot succeed where the
other refuses.

**And the advice those tools printed could not work.** Both `ACTIVATION_FAILED`
route (b) and the `catia_list_documents` note said *"open it with
catia_open_document"* — which, while the product is open, takes the "already open"
branch and cannot give the part a window. The route now says what actually works:
measure it by name (`document=`), or close the product and open the part again.
The `DOCUMENT_NOT_FOUND` next action carried the same dead end and says the same
thing now.

**A CATDrawing was typed `Other`.** `catia_list_documents` decided the type by
trying `Part`, then `Product`, then giving up — and a drawing has neither, so it
landed on `Other`, the label this listing also uses for a `.CATfct` catalogue. A
drawing now answers `CATDrawing` (`DrawingDocument.Sheets` is the probe, and the
other two document types do not expose it) and the two can be told apart
(reported 2026-11-07, 70-2).

**The frame sketch's datum plane: visible, four of them with one name, and not
findable by name at all.** `create_sketch(plane={'type':'frame', …})` builds a
plane from three points and inserts all four **into the solid body** (a plane in a
geometrical set poisons the fillet on the pad built on it). Nothing hid them, so a
finished part carried a white diamond plus three crosses drawn over the solid —
the 2026-11-06 report read them as geometry defects and could not remove them:

* `set_visibility(kind='construction'/'sketch'/'datum_plane', show=false)`
  returned success and left the picture byte-identical — that walk covers sketches
  and the three origin planes, and these live in `body.HybridShapes`;
* hiding by name answered `ARGUMENT_REJECTED: No tree node named '平面.1'` —
  the by-name walk did not look in `body.HybridShapes` either, so the tool could
  not see features **this server had created**;
* and the name it would have needed was the wrong one anyway: every frame plane was
  called `Datum_frame`, so P04 of that build holds **four** features with one
  name, and `Collection.Item(name)` returns the first and says nothing about the
  rest.

Now: the plane gets a free name (`Datum_frame`, `Datum_frame.2`, …), the plane and
its three points are **hidden where they are created**, and the sketch reply names
them so `catia_set_visibility(name='Datum_frame', show=true)` brings one back. The
by-name walk covers hybrid shapes inside solid bodies, and `set_visibility` sets
**every** node that answers to the name and says how many others there were.

Measured on B30 (`scripts/b30_v0374_audit.py`, 15/15): after `create_sketch`
`GetShow()[1]` reads **1** for all four features; a 40×20 pad on that hidden plane
still builds **4000.0 mm3**; `set_visibility(name='Datum_frame', show=true)` reads
back **0**. On the reporter's own P04 (four same-named planes, read back one by
one): hide → `[1,1,1,1]`, show → `[0,0,0,0]`, reply "and 3 other tree node(s) that
carry the same name - all of them were set".

**The SaveAs message blamed the folder, and the folder is innocent.** 0.3.71 wrote
"the folder counts, not only the file name" from the 2026-11-06 report; the
package's own `PathNotSupported` docstring has said the opposite since
2026-10-31, so two messages disagreed about one limit. Measured on B30 in all four
combinations (`scripts/b30_v0374_paths.py`, same session, same document type):

| folder | file name | result |
|---|---|---|
| ASCII | ASCII | saved |
| ASCII | 中文 | **REFUSED** (DISP_E_EXCEPTION) |
| 中文 | ASCII | **saved** |
| 中文 | 中文 | **REFUSED** |

So the limit is the **file name**, exactly as the first measurement said; the
message in `tools/document.py` and the two `dialogs.py` next-actions were corrected
to match. The reporter's second step — "an ASCII name in the Chinese folder also
fails" — does not reproduce through this package either
(`scripts/b30_v0374_zh_save.py`: the same call answers "Document saved as:
…\中文目录\probe_ascii.CATPart"). Their first step, the silent one, was already
fixed in 0.3.71: `catia_new_part` now raises `SAVE_FAILED` instead of returning
`ok: true` with a sentence inside.

The report came back a second time with "evidence B" for the folder claim, so it
was measured one more time — `catia_new_part` with an ASCII name, in **their exact
path** (typo included), in a short Chinese folder, and in an ASCII control
(`scripts/b30_v0374_their_path.py`): **all three saved**, file on disk. That is
five controlled attempts against one unexplained observation, so the message no
longer asserts a cause it cannot reproduce: it states what was measured and tells
the caller to read the CATIA error it already carries in brackets ("if this name
IS ASCII, that is what to act on").

Two things the report asked for and got: `catia_new_product` is **aligned** with
`catia_new_part` — a Chinese file name answers `SAVE_FAILED`, `stage=execute`,
`modified=true`, and an ASCII name in a Chinese folder saves
(`scripts/b30_v0374_new_product.py`, 3/3) — and the refusal now quotes CATIA's own
wording for the case, `上述文件名无效` / "the above file name is invalid", which is
the same limit seen through the UI (the report's screenshot evidence A, matched by
this probe's DISP_E_EXCEPTION).

**A name holding an undecoded escape is now flagged.** `\u4e3b\u89c6\u56fe`
arrived as six literal characters, CATIA created a view with backslashes in its
name, and every later error echoed them back (reported 2026-11-07, observation 4).
The four view-creating tools now add a note naming the characters those codes
spell. Ordinary Windows paths (`F:\Users\…`) are not flagged — the match requires
the full `\uXXXX` shape.

**`close_sketch`'s gap warning was truncated where it mattered.** The coordinate
list came first, so a client cutting the message at ~1200 characters threw away
the only actionable part (the rounding-artefact advice). The summary and the
advice now come first, the coordinates last.

**Not done, and why.** `data.breaks[]` (the report's third suggestion) would change
`catia_close_sketch`'s reply from text to a JSON envelope for every caller — the
reordering above addresses the truncation without that. `catia_screenshot`'s
`width`/`height` descriptions already say ADVISORY and "the reply reports what was
written" (the round's first observation); a request that differs is ignored, not
scaled.

`1190 → 1206 tests`.

## [0.3.73] — 2026-11-07 · the last two open items from 2026-11-06

Both were left open on purpose in 0.3.71/0.3.72 and recorded as such; this closes
them. Nothing else in those four reports is still open.

**`matches_request` was a bit-equality test pretending to be a verification.**
The check was `deviation < 1e-6`, so a placement whose unit vectors came from a
spec table rounded to 4 decimals - deviation 1e-5…1e-4 **by construction** -
answered `ok: true` beside `matches_request: false`, 13 times in 25 correct
placements, with no threshold to compare against (reported 2026-11-06, P2-4).

`catia_set_component_transform` now takes `tolerance` (default **0.001 mm = 1
micron**, must be > 0) and publishes it in `verified.tolerance`. A batch hands
the same threshold to every entry and republishes it once, so one verdict rule
covers all 25 rows. Measured on B30 with a 30-degree row set rounded to 4
decimals: deviation **1.9052629e-05**, `matches_request: true`, no warning; the
same call with `tolerance: 1e-6` still answers `false` and says
"did not store the requested placement within 1e-06 mm (off by 1.91e-05 mm)".
The deviation is unchanged either way - only the verdict the caller asked for.

**`catia_set_visibility` refused a Product, so cleaning an assembly took a call
per part.** It began with `get_active_part()`, so on `ASM.CATProduct` it answered
`PRECONDITION_FAILED` "exposes no 'Part'" - the report's 11-part cleanup was 33
calls of activate / hide / save, each one moving the active document (reported
2026-11-06, P2-6).

On a Product it now walks the component tree (sub-assemblies included, depth 6),
takes **one entry per part** - a 70-instance assembly repeats a handful of
documents - and applies the change through **that part's own `Selection`**, so
the active document never moves. `kind=` and `name=` both work there; the reply
says how many of how many parts it touched and names them, and a name that exists
in no part is refused with that scope ("in any of the 2 part(s) of …") instead of
the part-only message. The single-part wording is unchanged.

Measured on B30 (2 parts, `GetShow` read back per part): `kind='construction'`
hides 2 sketches and 6 datum planes in 2 of 2 parts with the assembly still
active, both sketches read `1` (hidden); the same call with `show=true` reads
`0`; `name='RA_ONLY'`, a body in one part, answers "in 1 of 2 part(s)".
`GetShow()` answers a 2-tuple on this build and the flag is **[1]** - worth
knowing, because reading `[0]` returns 0 in both states and makes any check of
this look like it passed when it has not.

`1173 → 1190 tests`.

## [0.3.72] — 2026-11-06 · the assembly envelope, and a cache that ran too late

The 2026-11-06 report's P1-3 and P3-10, both about `catia_get_bounding_box` on a
Product. One of the three complaints was a **documentation** defect, which is why
it survived four releases of a working feature.

**`method='mesh'` had not been refused since 0.3.45 — the schema still said it
was.** The report quotes the sentence, and it is the sentence a caller reads
before deciding whether to try: `document`'s description ended
"(method='mesh' is refused for one, because a mesh box is per part)". Nobody
updated it when the refusal was removed, so the only official route to a precise
assembly envelope looked non-existent. Verified on B30 before changing the text:
`document='QA_ASM.CATProduct', method='mesh'` answers the exact envelope
x [-50, 220], y [-30, 65] — including a rotated instance, whose box exists only
because every mesh vertex is transformed rather than the local diagonal.

**`complete` and `bounds` answered different questions in the same reply.**
`complete: true` means "every instance got a box"; `bounds` said "lower bound:
each component's box came from its vertices". Both were true and together read as
a contradiction, which is exactly how the report read them. Two new fields say
what was missing:

| field | values | meaning |
|---|---|---|
| `exact` | true / false | no vertex sweep is involved and nothing was skipped |
| `bounds_code` | `mesh_chord` / `vertex_sweep` / `mixed` / `complete_failed` / `empty` | which way the numbers can be short |
| `complete_note` | text | `complete` is coverage, not accuracy |

Measured on the same product: `auto` returns x max **205** where the true
envelope is **220** — 15 mm, in the direction the report was checking — and now
answers `exact: false` / `bounds_code: "vertex_sweep"` instead of leaving the
reader to reconcile prose.

**The per-part cache was consulted too late to save anything.** The file-keyed
mesh cache has existed since 0.3.32, but the lookup sat inside
`_mesh_from_copy` — i.e. after `part.Update()` and after `_edited_in_context`.
On a part of an open assembly those two are **4.2 s of window switching**
(measured: `Activate()` 2.4 s, restore 1.8 s) around an export that takes 8-10 ms,
so a cache HIT still cost 4 s to avoid 0.6 ms. Every call paid it again:
8.9 s and 9.0 s for the same two-part product, twice in a row.

The lookup now runs before any COM call, and the gate is `Document.Saved`:

* `Saved: true` → the live part IS the file, so the file key describes the answer
  → return it with `mesh_cache: "reused"` and no export at all;
* `Saved: false` → the live part differs from its file → measure it, report
  `mesh_cache: "unsaved"`, and never answer it from that cache.

`part.Update()` is skipped when the document is clean (a clean document has
nothing pending, and `Update()` clears `Saved` — measured: it flips a freshly
reopened part from `True` to `False` while the file and the exported 3244 bytes
stay identical). An export of a clean part is then stored under its file, so the
next call over the same assembly is free.

Measured on the same three-instance product, reopened from disk: **32 938 ms →
184 ms**, both parts `reused` in 16 ms each, identical numbers. A part edited
without saving answers `unsaved` and reports the edit (x max 105, not the cached
65).

The `visit` callback — how `catia_check_surface` gets the STL itself — disables
the hit: it reads the file, and a cached answer has no file to hand it.

`1146 → 1173 tests`. Neither `exact` nor `bounds_code` is new information; the
numbers never changed. What changed is that the reply now says which of the two
questions it just answered.

## [0.3.71] — 2026-11-06 · the last two exemptions, and four replies that lied

Four reports arrived together against 0.3.70. Two are about the envelope; the
other two are about replies that state a cause the code never measured.

**P2-A — the `except` exemption housed nine lookups.** The rule was "a raise
inside `except` is re-reporting a CATIA failure". But the common pattern there is
translating a lookup miss into a sentence, and nine of them still answered
`COM_ERROR` / `execute` / `modified:null`:

`connection.active_document` ("No active document in CATIA"), six
`No <thing> named '…'` sites across Part Design / Knowledge / Drafting, and the
"smooth bore is not a design table" state. That first one is **every tool call of
a cold session**. All now `PRECONDITION_FAILED` / `ARGUMENT_REJECTED` / validate,
and the guard fails on any `except`-block raise whose message prints a list to
choose from.

**P2-B — seven `CodedError`s never declared a stage.** `DOCUMENT_NOT_FOUND`,
`DOCUMENT_NAME_AMBIGUOUS`, `SKETCH_NOT_FOUND` all said `execute`: "CATIA was asked
and failed" for a typo. They carry `stage="validate"` now, and a static test fails
on any `CodedError` whose code is in the validate family without it.

**The note told the wrong story.** `data.next_actions_note` said "they are about
the ARGUMENT this call used" on every refusal, including `PreconditionRejected`,
whose own next action says "this is a state, not a typo". The subject now comes
from the code (argument / state / index / name / server defect).

**`threaded=true` (B32): not supported on that build, and now it says so.**
Five failures across three reports, each leaving `孔.1` in the tree with no
geometry. The update failure now removes the feature again, reports
`FEATURE_INCOMPLETE` / `execute` with a **measured** `modified` (false when the
remove worked, true when it did not) and `rolled_back`, instead of
`COM_ERROR` / `modified:null` / "inspect the model". When the write itself is
refused the hole is a valid plain bore and the reply says exactly that.
**Recorded as a known B32 limitation**: this server cannot thread a hole through
`ThreadingMode` alone there; B30 reports `NO_MATERIAL` before the write, so the
failure cannot be reproduced here.

**`document=` no longer pays for a tensor it cannot return.** The route's own
`does_not_answer` has said `inertia_tensor` for releases, and the attempt plus the
"why" walked every open document: 11 ms with one open, 790 ms with three (B32
2026-11-06). `tensor_ms` is 0.0 now, and the reason is about the route instead of
a "focus refusal" that told the caller to activate a document that was already
active.

**`seam_hint` was popped and then added straight back.** `if boundary:` emptied
the bucket for a surface patch, and two lines later `if seam_hint:` put it back —
which is why a reply carried `skin_note` ("cross_wall is NOT reported on a
patch") beside "38 creases run THROUGH the wall". One `and not boundary`.

**`catia_close_sketch` called a plate with a hole self-intersecting.** The loop
check used bounding-box overlap, so a rectangle with an inscribed circle (and a
rectangle with a smaller rectangle) was reported as a self-intersection whose pad
then built the right solid. Containment is a HOLE; only partial intersection
warns. The 0.0002 mm gap text no longer asserts "CATIA still refuses the feature"
— this server cannot know that, and on the three parts measured the pad succeeded.

**Saving to a non-ASCII path stopped being a warning.** `catia_new_part` /
`catia_new_product` answered a successful-looking reply ("Created …") with the
failure buried in prose, so `catia_run_plan` marked the step done and the loss
surfaced at the last save (reported as the worst item of the round). They raise
`SAVE_FAILED` / `execute` / `modified:true` now, naming the ASCII workaround, and
`SAVE_FAILED`'s advice says "first save of a document whose path is non-ASCII"
instead of blaming a dialog.

Smaller, same reports: `radius_chord_mm` is published in the curvature blocks
(the note asked the caller to compare two radii and only one was there);
`catia_set_visibility` names the hidden parent set; `catia_add_component(count=N)`
lists every instance it created; a clockwise arc reports "from p1 … to p2 …
clockwise (stored as the reverse angles)" instead of only the stored form;
`catia_new_product` reports the name `document=` should use after SaveAs.

**Not in this release** (measured, not forgotten): `method='mesh'` for a Product
document and the systematic vertex-scan underestimate it causes;
`matches_request`'s 4-decimal tolerance; product-level `catia_set_visibility`;
the structured gap list from `close_sketch`. The first needs a per-part mesh cache
shared across instances, which is a design change rather than a fix.

B30, `scripts/b30_v0371_envelopes.py`, 16/16: cold start is
`PRECONDITION_FAILED` / validate / modified:false with no recovery;
`document:"NOPE"` is `DOCUMENT_NOT_FOUND` / validate; `tensor_ms` is 0.0 with
`not_on_this_route`; the plate still builds 8000.0 mm³.

1161 tests, 127 tools.

## [0.3.70] — 2026-11-06 · the core layer, where a bug looked like a COM failure

0.3.69 emptied `catia_mcp/tools/`: 169 sites, every one with a code. The same
scan over `catia_mcp/` — the package, not just its tools — still found **eleven**,
and they were the ones a caller is least able to interpret:

| Site | Was | Now |
|---|---|---|
| `server.__init__` duplicate tool | `ValueError` | `INTERNAL_ERROR` / `internal` |
| `guard.run` unknown operation | `ValueError` | same |
| `regime.describe` invented route | `ValueError` | same |
| `topo_search._enumerate_cells` "unreachable" | `RuntimeError` | same |
| `arguments.alias_properties` no names | `ValueError` | same |
| `connection.connect` no pywin32 | `RuntimeError` | `DEPENDENCY_MISSING` / `connect` |
| `connection._interface` no Part behind the document | `RuntimeError` | `PRECONDITION_FAILED` / `validate` |
| `topo_search.topology_reference(s)` no solid shape | `RuntimeError` | `PRECONDITION_FAILED` / `validate` |
| `dialogs.save_as` / `dialogs.save` after 6 attempts | `RuntimeError` | `SAVE_FAILED` / `execute` |

**A fourth stage, `internal`.** "Duplicate tool: X" is not an argument the caller
got wrong and it is not CATIA refusing anything — it is this package
contradicting its own tables, and telling the caller to inspect the model for it
is worse than useless. `server._execute_tool` treats `internal` like `connect`
and `validate`: `modified:false`, no `recovery` block, and the next action is
"report the tool and this message".

Two of the eleven were not defects at all. "The active document exposes no
'Part'" and "the active body has no solid shape yet" are **states**, and a state
is fixed by opening or building something — not by changing an argument, and not
by reading a COM error. They are `PreconditionRejected` now, the class 0.3.69
added for exactly this.

**The guard covers the package now.** `scan_bare_raises()` walks
`catia_mcp/**.py` instead of `catia_mcp/tools/*.py`, so the next core-layer
`raise RuntimeError` fails CI the same way a tool's would. Zero left.

`InternalError` subclasses `ValueError` like the other refusals, because
`guard.run("format_hard_disk")` and `describe(route="probe")` have been asserted
with `pytest.raises(ValueError)` for several releases, and an operation name that
is not in the table IS a value problem — just one only this package can cause.

B30, `scripts/b30_v0370_core_envelopes.py`, 20/20: `catia_list_components` on a
Part answers `PRECONDITION_FAILED` / `validate` / `modified:false`; `catia_pad`
with a drawing active answers the same about the missing Part; an injected
internal defect answers `INTERNAL_ERROR` / `internal` with no recovery block; and
a 40 × 20 × 8 pad still builds at 6400.0 mm³.

1146 tests, 127 tools.

## [0.3.69] — 2026-11-06 · every refusal has a code, and the register is gone

0.3.68 converted twenty raise sites and registered the other 81 in
`tests/argument_rejection_baseline.json`. That register was the wrong shape of
fix twice over: it made "unfixed" a state somebody could add to, and the scan
that fed it matched **words** — "must", "unknown", "requires", "out of range" —
so it never saw `"Pass either 'points' or 'element', not both"` or `"No active
sketch"`, which are refusals too.

**169 sites, 0 left.** The number went UP from 81 because the measurement was
incomplete, not because new problems appeared: 88 bare raises were invisible to
the vocabulary scan, and the four that were neither argument nor state (a CATIA
result that came back wrong) needed a fourth answer rather than a code of their
own invention.

**Three refusal classes**, because the caller's fix differs:

| Class | Code | Means |
|---|---|---|
| `ArgumentRejected` | `ARGUMENT_REJECTED` | a typo: change the value |
| `PreconditionRejected` (new) | `PRECONDITION_FAILED` | the model is not in that state; changing the argument will not help |
| `ArgumentOutOfRange` | `ARGUMENT_OUT_OF_RANGE` | enumerate first, the number is in the reply |

All three carry `stage:"validate"` and `modified:false`, and all three subclass
`ValueError` — 80+ existing handlers and tests catch that type, and moving the
raises must not break them.

A failure that really is CATIA's answer keeps `stage:"execute"` and stops being
`COM_ERROR`: `COMPONENT_NOT_ADDED`, `CALL_ABANDONED`, `CLOSE_NOT_CONFIRMED`,
`ACTIVATION_FAILED`, `DRAW_FAILED`, `VIEW_LINK_LOST`, `NO_VISIBLE_GEOMETRY`,
`COM_RESULT_UNEXPECTED`, `FEATURE_INCOMPLETE`, `FEATURE_REJECTED`,
`DELETE_NOT_CONFIRMED`, `INTERNAL_ERROR`.

**The guard is structural now.** A raise inside an `except` block re-wraps a
failure CATIA produced (execute stage, nothing for the caller to fix); a raise
anywhere else is a decision the tool made, and a decision carries a code. The
baseline file is deleted — there is nothing left to register, and no list to grow.

**A `NameError` wearing an envelope.** `sketcher` and `document` raised
`PreconditionRejected` before importing it; the caller got `COM_ERROR` /
`execute` / `modified:null` with the message `'PreconditionRejected' is not
defined`. pytest stayed green because no test walked those two paths — the B30
acceptance script found it on the first run. Added
`test_every_class_a_tool_raises_is_reachable_from_that_module`, which parses the
package and fails on any raise whose class is not imported in that module.

B30, `scripts/b30_v0369_envelopes.py`, 37/37: the three classes each answer with
their own code, `validate`, `modified:false`, no `recovery`, one next action and
no "inspect the CATIA model"; five refusals leave the volume at 115 200.0 mm³ and
the document count at 1; a second pad still builds (117 713.3 mm³) and a GSD point
and translate behave, so the sweep did not break the ordinary path.

1140 tests, 127 tools.

## [0.3.68] — 2026-11-05 · the refusal envelope, which the new modules never got

Reported 2026-11-05, P1. `catia_fillet {radius:3, edge_name:"NO_SUCH_EDGE"}`
answered `COM_ERROR` / `failure_stage: execute` / `modified: null`, with a
`recovery` block and the "check in_work_object" paragraph — on a call that never
touched CATIA. That exact call is **row three of the 0.3.43 fix table**: the
release that introduced the envelope missed its own example, and both modules
written since (Knowledge 0.3.63, Drafting 0.3.64–0.3.66) started from the old
shape. The report counts 104 uncoded raise sites whose message is about a
parameter, against 48 coded ones.

**The server-layer inference was tried and reverted the same hour.** The obvious
fix — treat a bare `ValueError`/`TypeError`/`KeyError` as a validate-stage
argument refusal — broke `test_server_runtime` immediately: it raises
`ValueError("Wrong sketch")` as its stand-in for an ordinary failure, and the
inference reported that as `ARGUMENT_REJECTED` with `modified: false`. A
**claimed fact about a model nobody had read**. A Python type is not evidence
that a tool only looked at its arguments, so the envelope stays something the
raise site *declares*. That decision is recorded in `server.py` where the note
would otherwise have been, because "why not just check the type" is the first
question the next reader will ask.

**What is actually fixed**

| Site | Was | Now |
|---|---|---|
| `_parse_edge_spec` (part_design) | `ValueError` → COM_ERROR | `ArgumentRejected`, next action points at `catia_list_edges` |
| `circ_pattern` count 0 (×2), `shell` walls, `set_part_number ""` | named in the report as leaks | `ArgumentRejected` |
| Drafting: paper, orientation, margins, label keys, block size, plane, dimension pairing, face format, section, detail, table, direction | 13 sites | `ArgumentRejected` |
| `catia_create_parameter` unknown type, non-numeric value | `ValueError` | `ArgumentRejected` |

`ArgumentRejected` is now a `ValueError` as well as a `CodedError` (like
`ComponentNotFound`), so the tool code and tests that have caught that type for
years keep working while the envelope gains its code.

**Schema took what schema can express.** `language` is an enum
(`zh`/`en`/`zh-CN`/`cn`/`chinese`/`中文`/`en-US`/`english`/`英文`), `margin` and
`margin_left` are `exclusiveMinimum: 0`, `title_block.width/.height` likewise.
Those four frame refusals now never reach the tool, which is why the matrix test
below runs **offline**.

**The guard, which is the part that keeps it fixed.** `tests/
test_argument_envelopes.py` does three things:

1. A 9-case matrix through `_execute_tool` asserting the validate shape
   (ok false, `failure_stage: validate`, `modified: false`, no `recovery`, no
   "inspect the CATIA model", next actions present) for every refusal the fix
   touched, including all four of the report's drawing cases.
2. A static scan: every bare `raise ValueError/RuntimeError/TypeError/KeyError`
   whose message talks about an argument and which is **not** inside an `except`
   block must be in `tests/argument_rejection_baseline.json`. 81 sites are
   registered there as the honest current state; a NEW one fails CI with the
   message and the fix. The count can only go down — a site that disappears
   fails too, so the register cannot rot.
3. The named call sites, called directly, plus a stub `Parameters` proving
   `catia_create_parameter` creates nothing before it converts.

**P2 — a failed call that wrote the model.** `catia_create_parameter{value:"abc"}`
answered a failure and left `LEN366B = 0` in the parameter tree, because the old
order was `CreateDimension(...)` **then** `float(value)`. Values are converted
before anything is created now; if CATIA still refuses the assignment after
creation, the parameter is removed again and the reply says which of the two
happened (`rolled_back`). Measured on B30 2026-11-05: the parameter list is
byte-identical before and after the failed call.

**P3 — one mistake, two codes.** A step argument the tool does not declare
answered `INVALID_PLAN` inside a plan and `UNKNOWN_ARGUMENT` on its bare call.
The plan keeps its own code (a plan-level refusal is a different fact) and now
carries `data.problem_code: "UNKNOWN_ARGUMENT"`, so a caller branching on `code`
does not need two paths.

**On the test count the report flagged.** 0.3.65's CHANGELOG says 1083 tests
because that is what 0.3.65 shipped; 0.3.66 added 17 (1100) and 0.3.68 adds 30
(1130). Each entry states its own release's number rather than being rewritten
later — the `--collect-only` figure is the one CI checks.

Verified on B30 through the real server envelope (`scripts/b30_v0368_envelopes.py`,
24/24): six refusals all validate / not modified / no recovery / next actions,
volume unchanged across all six, no orphan parameter, plan and bare call agreeing.

1130 tests, 127 tools, and 81 registered sites still to migrate.

## [0.3.67] — 2026-11-05 · the smoke script checked 108 of 127, and said so only outside CI

Reported 2026-11-05 (B32, v0.3.66), P1. Two commands, two answers:

```
python tests/test_server.py   -> "Mismatch: 108 tools defined but 127 in router", exit 1
pytest tests/test_server.py   -> 3 passed
```

Both were right. `test_imports()` and `_collect_tool_definitions()` each held a
**hand-written list of nine modules**; `knowledge.py` (0.3.63, 6 tools) and
`drawing.py` (0.3.64, 13 tools) were registered by the server and validated by
nothing. Nineteen tools had never been through the name/description/
`inputSchema.type`/duplicate/prefix assertions — 15 % of the guard's nominal
coverage, and neither `pytest` nor CI would have noticed a bad schema in them.
The equality assert lived only in `main()`, so the strong check was the one
CI does not run — the same "one command, one number" failure 0.3.53 fixed,
reappearing at the module dimension.

**All three fixes, not just the one that turns the light green**

- **Root cause**: `_collect_tool_definitions()` now walks `server._tool_modules`
  — the same list `_tool_router` is built from. A module added to the server can
  no longer be missing from the scan, because there is no second list to forget.
- **Imports**: `_import_sweep()` discovers every `.py` under `catia_mcp.tools`
  with `pkgutil.iter_modules`, instead of importing nine names.
- **Recurrence**: `_check_tools_match_router()` is one function used by both
  `main()` and the new `test_tool_definitions_match_the_router`, so
  `python -m pytest tests -q` now runs the equality assert too. `tests/
  test_reported_issues_2026_11_05_v0367.py` pins the fix from the side that
  keeps it fixed: fake servers with a missing description, a non-object schema,
  a duplicate name, a missing prefix, a ghost tool in the router and a tool
  missing from it. A test that only counted 127 would pass again the day
  someone pastes a new list in.

Verified after the fix: `ALL TESTS PASSED — 127 tools ready`, exit 0, and the
scan now prints Knowledge: 6 / Drafting: 13 alongside the other nine.

**P3 — a document about stale numbers whose own numbers were stale.**
`docs/upstream-diff.md` said "currently 108 tools, 951 test functions / 1020
collected" (0.3.62 figures) and told readers to update `FORK.md`, whose
"98 → 96" and "# 72 passed" were 0.3.5-era. Both updated. The FORK.md test
command now runs the full suite and **deliberately states no passed count** —
a hard-coded number has been wrong twice, so the third time it is not written.
The count was 72 → 31 over five releases, which is exactly how a stale number
stops being harmless.

**The two suggestions from the 0.3.60 report, open for three releases**

- *Is copy-mode explicit?* It is: both `HybridShapeRotate` and
  `HybridShapeTranslate` expose `SetCreationMode` / `GetCreationMode`. Measured
  on B30 2026-11-05, one part per condition: a fresh rotate reads mode 1,
  `SetCreationMode(0)` reads back as **2**, modes 1 and 2 read back as 1, and
  in every case the source line is still in the geometrical set
  (`scripts/b30_v0367_creation_mode.py`, `..._translate.py`). So no mode
  consumes the original and **nothing is set**: hard-coding a value whose enum
  the type library does not name would be inventing a constant. The finding is
  in both tool descriptions with its date, so it is not re-investigated a
  fourth time.
- *Two names for one count.* `non_manifold_edges` (whole mesh) and
  `excluded.ambiguous_edges` (curvature pass, region-limited) are one rule at
  two scopes. They now point at each other in the `catia_check_surface`
  description and in the report's `non_manifold_note` / `region_note`.

1113 tests, 127 tools.

## [0.3.66] — 2026-11-05 · the sheet furniture speaks Chinese by default

The user asked why their drawing came out covered in English. Nothing about that
came from CATIA: the field labels were hard-coded in `_add_frame`, and every
default view name ("Front View", "right view", "section view") was English too.
Chinese was never a font problem — CATIA's default drafting font `SICH` renders
it, verified by writing 材料/图号/比例/制图/审核 into table cells, exporting a
PDF and looking at it (`scripts/b30_v0366_zh_font.py`). 127 tools, unchanged.

**What is Chinese now**

- Title-block labels: 材料 / 比例 / 制图 / 日期 / 审核 / 张次 (was MATERIAL /
  SCALE / DRAWN / DATE / CHECKED / SHEET).
- Default view names: 主视图, 右视图 / 左视图 / 俯视图 / 仰视图 / 后视图,
  剖视图, 局部视图. A view's `Name` is what gets printed under it, so this is
  visible output, not just an API handle. Chinese names work as handles too —
  `catia_drawing_projection_view {"parent_view": "主视图"}` resolves by name on
  B30 (10/10 checks in `scripts/b30_v0366_zh_accept.py`).

**Two escape hatches, because a Chinese sheet is not always wanted**

- `language: "zh" | "en"` — ISO-wording sheets are one argument away. Aliases
  `zh-CN` / `cn` / `中文` / `chinese` normalise to Chinese.
- `labels: {"checked": "校对", "sheet": "SHEET NO."}` — overrides one field
  without flipping the whole block; takes precedence over `language`.

Your own strings (title, material, drawn_by, notes, footer) are never
translated. An unknown `language` or label key is refused **before** the border
is drawn, and the acceptance script asserts the Background view element count is
unchanged after the refusal — the same rule the oversized-block check follows.
The reply now states which language the labels came out in.

**Font: measured, and deliberately not touched.** Both `DrawingText.SetFontName`
and `DrawingText.TextProperties.FontName` accept a Windows font name and read it
back (`宋体`, `FangSong`, `SimHei`, `Arial` all "ok"), but **the exported PDF
renders every one of them in SICH**. Four lines set to four different fonts came
out identical (`scripts/b30_v0366_font_faces.py`). So the odd glyph is not
something this server sets: `SICH` is the ISO single-stroke font, and its digit
`1` looks like `н`, which is why `1:1` reads as `1|1` and `0.05` as `0.05` with
a hooked five. Changing it means changing the Drafting **standard** in CATIA,
not calling a COM setter — nothing here pretends otherwise.

## [0.3.65] — 2026-11-05 · the frame, which was not where the numbers said

Upstream's drafting module ends with "frame/title-block generators are the next
step". They exist now: `catia_drawing_set_sheet` and `catia_drawing_add_frame`.
127 tools.

**A new drawing is not A3.** It comes up as `catPaperUser` (13) — an oversized
custom sheet — and `GetPaperWidth()` **raises** on it (`方法 GetPaperWidth 失败`)
until a real ISO size is set. So the first thing a frame has to do is establish
the paper, and the first thing the tool has to survive is a sheet whose size
CATIA will not state. `catPaperSize` also names Letter, Legal and A..F; writing
any of them raises too (values 0, 1 and 7-12, measured), so only A0=2 … A4=6 are
offered, and the refusal says why. `Orientation = 2` (best fit) is accepted and
silently reads back as landscape.

**`Tables.Add(x, y)` anchors the TOP-left corner and the block grows DOWN.**
Passing the bottom margin put the whole title block below the sheet — visible in
the exported PDF, invisible in the reply, which printed a position and 6 cells
filled. The block now sits at `y0 + height` and is flush with the border.

**A refused call used to draw first.** The block-size check ran after the border
went down, so an oversized title block left four stray lines behind (Background
view: 5 elements → 9). Every number is now resolved before anything touches the
sheet, and the acceptance script asserts the element count is unchanged after a
refusal.

The border is ISO 5457 (20 mm filing margin, 10 mm elsewhere). The title block is
a **real table**, 8 columns × 5 rows merged into title / drawing number /
material / scale / drawn / date / checked / sheet / footer, so it stays editable
in CATIA afterwards (`SetCellString`, `SetCellAlignment`, `MergeCells`,
`SetRowSize` all confirmed). Notes are placed above it, anchored bottom-left.

Two things are deliberately not done: **zone markings and the ISO corner grid**
are not drawn, and the tool does **not** move views — it reports the drawing area
(`x 20..410, y 10..287`) instead, because a projection placed at x=20 landed on
the border in the A3 run and moving the caller's views would be worse than
telling them where the border is.

1 083 tests, 16 of them for this.

## [0.3.64] — 2026-11-05 · drafting, which could not work here at all

Upstream's eleven Drafting tools were the last module this fork had dropped. They
are in, and the port is a rewrite of two paths because **face-driven projection
could not work on this machine** and neither could hand dimensioning. 125 tools.

**`"Topology.Face,sel"` is an English query.** A Chinese CATIA raises a bare
`方法 Search 失败` for it and answers `拓扑.面,sel` with the six faces of the same
plate. Upstream's own note says "field-proven on V5R20" — true, on an English
install. This fork already had the localized fallback in `topo_search`; the
drafting module just did not use it. Its face indices were also the last FEATURE's
rather than the body's, so `Face.3` meant different geometry to `add_view` and to
`catia_list_faces`. Both fixed by resolving through `topology_cells`.

**A view's `GeometricElements` item 1 is the absolute axis.** Exactly as in a
sketch — the same defect fixed for `catia_sketch_constraint` in 0.3.62, repeated
in a new module. `element_index_1: 1` reached `Dimensions.Add` and CATIA answered
`方法 Add 失败`, naming neither the axis nor the index. Both slots refuse it now.

**`Dimensions.Add` failures are usually the geometry, not the call.** Measured
before writing any fix: two parallel lines give a distance (0 → 1, 1 → 2), while a
horizontal line and a vertical one raise the same bare error, and a radius on a
line does too. The reply explains which pairings work instead of forwarding COM.

**`add_view` demanded `part_name` whenever two parts were open** — including when
the caller had just built one of them and CATIA had named it `Part1.CATPart`. The
connection remembers the last part it created or opened, and that is used when the
name is ambiguous. An explicit `part_name` still wins.

Measured working, unchanged: drawing creation, plane-driven generative view (0.3 s
for a 60×40×10 plate), projection with the sheet's first-angle layout, section
view with hatching, circular detail, text, filled tables, and `GenerateDimensions`
producing one `DimStatus 7` (3d_driven) radius from one sketch constraint — and
zero where the profile carries no constraint, which the note now says instead of
pointing at a `length` constraint that does not exist.

Two limits recorded rather than worked around: a generative view cannot be
hand-dimensioned at all (its curves are not addressable through automation), and
**`Face.N` is session-scoped** — the same plate gave its hole's cylinder at
`Face.1` in one build and `Face.7` in another.

1 067 tests. Probes kept: `scripts/b30_v0364_drawing_two.py` (what Search and Dimensions.Add
really do), `_fix.py` (GetPlane via topology_cells), `_accept.py` (19/19).

## [0.3.63] — 2026-11-05 · knowledge came back, after being measured

Upstream's five Knowledgeware tools were dropped when this fork was cut. They are
back — but not as a copy. Each attribute name was exercised on B30 first, and
**four of them did not behave as written**. That makes 114 tools.

**A design table could be created and never applied.** Upstream calls
`ConfigurationName`, `Configurations` and `NumberOfConfigurations`. The
DesignTable interface — 52 members, read out of its own type library — has none of
those. What it has is:

```
Configuration    (get AND put)      ConfigurationsNb     ColumnsNb
Activate         Deactivate         Activated            Synchronize
```

So the property upstream wanted is `Configuration`, it is writable, and writing
`2` on a two-row sheet did move the part: LEN 20 → 30, REAL 3.5 → 4.5 (measured
2026-11-05). `catia_apply_design_table` is the sixth tool here because without it
a design table is a file association a human has to activate by hand.

**A length cell with no unit is read in METRES.** A sheet row reading `30` set a
20 mm parameter to **30000 mm**; `30mm` and `0.03m` both gave 30. A product family
built from a plain Excel column would come out a thousand times too large and
every reply would still say success. Both the create and the apply reply carry the
rule now.

**`bool("false")` is True**, so the boolean parameter type inverted every false
that arrived as text. `_as_bool` parses true/false/1/0/yes/no/on/off and refuses
text it does not understand instead of guessing.

**A feature's own dimension is named in the build's language.** A pad's height on
this Chinese B30 is `零件几何体\凸台.1\第一限制\长度`, not
`PartBody\Pad.1\FirstLimit\Length` — so upstream's own docstring example is not
findable here. The failure now lists the parameter names that end the same way,
and the reply points at `catia_get_parameters` for the exact one.

**A parameter a design table is associated with can no longer be re-valued** —
`ValuateFromString` fails with a bare COM error. The tool says which table owns it
and points at `catia_apply_design_table`.

What upstream got right, and is kept: a relation's `Value` **is** its expression
(CATIA rewrites it to full parameter paths — measured), `AddAssociation(parameter,
column)` takes them in that order, and a length has to go through
`ValuateFromString` rather than a bare `.Value` assignment.

## [0.3.62] — 2026-11-05 · two numbers that came from comments, not from CATIA

Checked the two real upstream commits (`f9751a1`, `b59a95b` — both field-verified
on V5-6R2020 by @Hemir95) against this fork. Six of their eight claims were
already covered here, sometimes more thoroughly; one was a live defect of ours,
and one turned into a second defect while being fixed.

**Every "threaded" hole since 0.3.0 was smooth.** `catia_hole` wrote
`hole.ThreadingMode = 1  # catThreaded`. The comment was the source; CATIA's own
type library, read out of the Hole interface on B30, says otherwise:

```
ENUM CatHoleThreadingMode
  catThreadedHoleThreading = 0
  catSmoothHoleThreading   = 1
```

Nothing caught it because **1 is also what a hole in a fresh part already
carries** — the write was a no-op on a property nothing read back, and both values
update without complaint. Feature names do not differ either (both are `孔.1`).

Measured while fixing it, a second trap upstream does not mention: **a hole
inherits the threading mode of the hole before it in the same part.** Three holes
built in a row read 0 / 0 / 0 after the first was threaded, and the two plain ones
had never been touched. So `threaded: false` has to be written too — "leave it
alone" keeps the previous bore's thread. Both holes are now explicit, the mode is
set **after** the first update (threading a hole that has not materialized breaks
the rebuild), and the reply says THREADED when it took.

**`geometry_index_1: 1` was the sketch's absolute axis.** `GeometricElements`
reads item 1 = `绝对轴`, item 2 = the first line drawn. The constraint path passed
the index straight through, so the natural reading of "my first line" produced
CATIA's bare `方法 AddMonoEltCst 失败` — naming neither the axis nor the index. The
delete path had refused index 1 for releases; the constraint path had not. Now the
listing marks `absolute_axis: true` on that entry, and both slots refuse it with
the index that would have worked.

**Checked and NOT adopted**, because the measurement did not support the claim on
this build:

- SPA `Measurable` scalars re-wrapped in `win32com.client.Dispatch` "so Volume and
  Area read correctly": on a 20 mm cube whose values are exact, raw and re-wrapped
  both read **8000.0000 mm³ and 2400.0000 mm²**. Adding the wrap to make two code
  paths look alike would risk an object that already works.
- `fix` constraints forced to `ReferenceType = 1`: a fresh fix reads 0, setting 1
  and back both read back fine, and there is no `CatConstraintReferenceType` enum
  in that library to say what either value means. Nothing written.
- `Mode = 0` before assigning `Dimension.Value`: a new constraint already reads 0,
  and `CatConstraintMode` is 0 = driving, 1 = driven. Nothing written.

Also confirmed by reading the enum, not by trusting the comment block: this
project's `_CST_*` constants are right throughout (Reference 0, Distance 1,
Length 5, Angle 6, Horizontality 10, Perpendicularity 11, Verticality 13,
Radius 14).

## [0.3.61] — 2026-11-04 · a verdict that contradicted the model it measured

Two reports on 0.3.60, both about one failure mode from different ends: a number
the mesh cannot support, quoted as geometry. Reproduced on B30 with this
project's own delivered bottle base, which is a 0.3 mm surface CATIA thickened
successfully.

**The verdict said "cannot be built" about a surface that had just been built.**
`check_surface(thickness=0.3)` on that solid: 103 edges below 0.3 mm, the tightest
a **0.0888 mm radius across a 3.3218 mm edge** — 2.7 % of its own length, with
7.16° of fold, which is a bend of about 27 mm, not 0.09. The reply's own `limits`
string has said since 0.3.51 that "a radius below the edge length is smaller than
this mesh can measure, not a measured value". Nothing acted on it; the reporter
found the same at 0.0141 mm across 2.2146 mm (0.6 %).

Now it acts. Edges whose radius is under a **tenth** of their own length go to
`excluded.unresolvable_edges`, out of the ranking and out of the verdict, and the
verdict is decided by the **5th percentile** instead of one edge: a minimum is a
single triangle, a verdict is about a surface. Same solid after: min 0.1412,
p5 1.326, 49 edges below 0.3 → **INCONCLUSIVE**, with the worst edge's
`radius_over_edge` published so the caller can see why. A r=1 tube against a 5 mm
offset still says "cannot be built", because there every edge agrees.

The tenth, rather than "below the edge length", is an order-of-magnitude
judgement and it is documented as one: it has to pass a cylinder's seam-direction
edge (L 30, R 10, L/R = 3 — the edge runs where there is no curvature, and its
reading is the same one the ring edges give) while dropping L/R = 37 and 157. A
first draft used radius < edge length and threw away every cylinder; the test
that caught it is in the suite.

A second rule was written, measured and **removed**: comparing the circumcentre
radius with L/(2 sin(f/2)) and dropping disagreements beyond 4×. On the same
solid it excluded 3388 of 9411 edges and changed neither the minimum (0.1412
either way) nor the count below the offset (49 either way). A rule that removes a
third of the evidence and moves no conclusion is a rule that gets quoted later as
if it did something.

**`thick_surface` now retries once by itself.** The operator is session-sensitive
the way the loft is — reported 2026-11-04: a 0.3 mm thickening failed on the last
step of a 13-step plan, succeeded when called alone, and succeeded again with the
whole tree visible, same document, same session, same numbers. One failure is not
evidence about the geometry; two are. The reply says so when the retry is what
worked, and the second failure's message states that it already retried.

**Its diagnosis was reading the wrong mesh and prescribing from it.** The measured
radius came from the document's whole visible geometry — nine overlapping lofts
plus a skirt in the report — while the element being thickened was one of them.
The message now carries a SCOPE line naming how many elements the mesh covered,
and a tight reading that sits on an open border gets a **conditional**
prescription ("IF THE BEND IS REAL: widen… / IF IT IS THE BORDER: nothing in the
model needs changing") instead of a flat "keep the total below 0.079 mm".

**An export with nothing visible writes 37 bytes and reports success.** Measured
on B30 too, and reproduced: every GSD element hidden, `catia_export` returns ok,
the STL holds 0 triangles. The empty file is now deleted and the call refuses,
after the export rather than before it — `HybridShape.Visible` is not readable
through automation at all (`AttributeError: Item.Visible`), so a pre-check would
have to spend one selection round trip per element. The same size check ends the
misdiagnosis in `check_surface`, where an empty mesh used to be reported as "the
part is loaded inside an assembly and has no window of its own" — a different
failure with advice that cannot help. It has its own exception now
(`EmptyMesh`) and is re-raised rather than wrapped.

**`catia_export` hung on an existing target.** ExportData runs CATIA's file-alert
path, so exporting over a file raised a modal "要覆盖它吗?" that nothing on the
automation thread can answer. `measurement._export_stl` has switched
`DisplayFileAlerts` off since 0.3.23; this tool never did. Found by hanging on it.

**Two smaller ones.** `catia_gsd_revolve` takes `axis='z'` like rotate does — it
used to answer "Element 'z' not found in ... searched 12 tree name(s)", which
reads like a data problem rather than a parameter one. And the plane aliases are
in the **schema** now: 0.3.60's CHANGELOG said "all three answer to all three"
while `required: [support_plane]` made two of them unreachable, because a client
validates before the server can normalise. The canonical name left `required`,
the alias list went into `properties`, and the server answers a missing one with
all three spellings named.

Measured: `tensor_ms` on a part with no product open **3103.8 ms → 42.0 ms**, by
asking whether any Product document is open before walking every assembly to
explain a tensor that was never available. 1006 offline tests.

## [0.3.60] — 2026-11-04 · a surface that could not be moved

No acceptance report this time. These five came out of building the five-petal
bottle base, in the order they cost time, and the first one changed the shape of
the build rather than just costing minutes.

**GSD had no way to move anything.** A five-lobe base is one lobe plus four
rotations of 72°, and the only transform in the tool set was
`catia_gsd_symmetry` (mirror). `catia_circ_pattern` is a Part Design feature and
wants a solid, so the lobe was rebuilt five times, once per angular position -
four extra constructions of identical geometry, and positions that could not be
changed afterwards without doing it again. `AddNewRotate` had been in the
typelib the whole time. `catia_gsd_rotate` and `catia_gsd_translate` now exist.
Measured on B30 2026-11-04: a cylinder whose axis sits at (20,0) rotated +90°
about z lands at (0,20) — counter-clockwise by the right-hand rule, original
kept — and a cylinder spanning z 0..20 translated 30 along +z spans 30..50 and
no longer meets z=0. `axis` takes 'x'/'y'/'z' (built as a line on demand, named
after the element it rotates) or a line element.

The two members of that family take **different argument kinds**, which is worth
stating because nothing about their names suggests it: `AddNewRotate` wants a
REFERENCE to an axis line, `AddNewTranslate` wants the Direction OBJECT and
raises "type mismatch, argument 2" if handed a reference. Both are pinned by
tests, so a future tidy-up that makes the code look consistent will fail loudly
instead of breaking one of them.

**`catia_gsd_revolve`'s angles were documented as a start and an end.** They are
the typelib's `iOffsetDebut` / `iOffsetFin` —
`AddNewRevol(iObjectToExtrude, iOffsetDebut, iOffsetFin, iAxis)` — a sweep from
the profile plane toward each side, so the surface spans their SUM. `(72, 72)`
is a 144° sector centred on the profile, not a zero-width one, and finding that
out cost a rebuild and an STL measurement. The angles only ever say how much, so
position comes from where the profile is drawn; the reply now names the total,
and the description says both of those things.

**A closed spline whose last point repeats the first is refused by CATIA** — the
whole spline, with a generic invalid-parameter error that reads like bad
coordinates. Closing the ring by hand is the intuitive thing to do (a hand-built
ten-point section was rejected until the repeat came out). The repeat is now
dropped before the call and the reply says so: 5 points in, 4 on the curve.

**One concept, three argument names.** "The plane this sits on" is
`support_plane` in `catia_gsd_circle`, `support` in `catia_gsd_project` and
`base_plane` in `catia_gsd_plane_offset`; a wrong spelling surfaces as
`KeyError: 'support_plane'`, which reads like a server bug. All three now answer
to all three. The one that mattered more: `catia_gsd_intersection` takes
`element2`, so a caller who wrote `cutter` (the name `catia_gsd_split` uses for
the same job) got a KeyError that a try/except turned into "the surfaces do not
intersect" — a wrong answer rather than an error.

**The wall thickness nobody published.** A 0.3 mm thick-surface base answered
volume 10099.5 mm³ and area 67392.8 mm², and 2V/A = 0.2997 mm was the only way to
say what the wall came out at. `catia_get_inertia` now returns
`uniform_thickness_mm` when both are readable, named "uniform" because it is
meaningless for a solid block or a part with one thick boss and one thin panel.

**A mesh export with nothing to export** hung for five minutes with no output and
had to be killed, so an empty part is refused before the call. The first draft of
that guard also claimed a curve-only part would raise a modal dialog — measured
the same day, a part holding one spline exported an STL fine, and the claim was
corrected in the message rather than kept for its tidiness.

## [0.3.59] — 2026-11-04 · a duplicated sheet is not a 0.034 mm radius

Two reports on 0.3.58. The technical one was right about the conclusion and its
mechanism needed correcting, which changed the fix.

**A steep dihedral is not always a bend, and the border was not the reason.**
0.3.58's thick-surface failure reported its tightest radius as 0.034 mm at
r=32.99, z=123.04 - the open top border of a skirt the split was about to cut
away, where there was nothing to widen. The report read that as "the surface
ends here", and that reading is wrong in a way worth recording: a plain open
patch does NOT produce a steep dihedral at its border. Measured on B30
2026-11-04, a 30 mm open tube's worst fold is 22.50 deg (its own chording) and a
flat patch's is 0.00 - the border contributes nothing. What does produce it is
two coincident sheets with opposite orientation: every edge they share measures
exactly 180.00 deg, because the two triangles face opposite ways. That is a
duplicated or double-sided sheet, which is what a split-and-copied surface
leaves visible - and with `radius = span / pi` a 0.1 mm facet becomes 0.03 mm.

So fold-back is now judged before the span, which matters twice over: two
exactly coincident sheets have coincident circumcentres, so 0.3.58 dropped them
as "degenerate" and they vanished from the count, while a nearly-coincident pair
(span 0.1 mm, fold 180.00) became the invented radius. Same fault, two different
silences. `catia_check_surface` now reports `excluded.fold_back_edges` with a
sample (`fold_deg`, `edge_length_mm`, `radius_this_would_have_read_mm`, r/theta/z,
`at_open_border`), counts edges shared by more than two triangles as
`excluded.ambiguous_edges`, and keeps both out of the radius ranking and out of
the thickness verdict. `min_radius_at_open_border` and
`tightest_edges[*].at_open_border` still flag a REAL reading whose edge ends on a
border, because that case exists too. A genuine zero-radius knife edge reads
exactly like a duplicated sheet and the note says so instead of choosing.

Measured end to end on B30 2026-11-04, one tube built once and then with a
coincident second extrusion of the same circle: one surface reads 16 edges,
min radius 9.745 mm, 0 fold-back; with the duplicate, 32 fold-back at 180.00 deg
plus 32 ambiguous and no radius at all; hide the duplicate and the reading is
9.745 again. The real geometry survives, the artifact does not.

**`region` now scopes the curvature block.** It only ever scoped the fold
statistics, so "exclude the skirt and look at the base" changed the angles and
left the radii whole-mesh - the most natural way to work around the problem
above was the one that did nothing. Both passes now share one region predicate
and each reports its own count (`region_note`, `curvature.region_note`,
`excluded.outside_region`).

**The failure message repeats what it excluded.** A number that silently
disappears is as bad as a wrong one, so `catia_gsd_thick_surface` names the
fold-back and ambiguous counts, states how many open boundary edges the mesh has,
marks a reading whose edge ends on a border, and points at
`catia_check_surface` with a `region`. It says "widen that feature" only when the
radius it quotes is a real bend - both directions are pinned by tests, so the
advice did not get quietly neutered.

**One docstring that described the estimator 0.3.58 replaced.** The acceptance
report caught it: `_curvature_stats` documented `kappa = angle / edge length`
while the code used the distance between the two triangles' circumcentres - the
edge-length formula is the one that read 57.3 mm on a r=10 cylinder and was
written off two revisions ago. Fixed, and a test now refuses "kappa" in that
docstring, because that sentence would have led a reader to "fix" a correct
implementation.

**And one standing item answered at the point of call.** `set_view` has been
documented as absolute-axes-only in README since 0.3.57, but the report asked
why six versions of it went unanswered. The limitation is now in the tool
description itself, where a caller reads it, with what to do about it.

What could NOT be measured: the report's own model is V5-6R2022 B32 SP6 and
does not open on B30, so the 0.034 mm figure was not reproduced on their
geometry. The mechanism was measured on constructed geometry instead, and the
failure TEXT with stubs over known STL files - B30 still refuses to fail a
thicken (0.1 mm slot at 0.6, r=0.2 cylinder at 0.3, r=5 cylinder at 400, all
built).

970 tests, 7 new. Tools 106, unchanged: this release adds fields and a region
scope, not tools.
## [0.3.58] — 2026-11-04 · the number a failed offset never gave

Two reports on 0.3.57, both about things 0.3.57 introduced or claimed, and both
were right.

**`seam_hint` fired on a surface patch.** The caller had a model whose thickening
had failed, so only the surface was left. Three creases at 180.0 deg on the
skirt's top edge - r=33, z=110, which is a BORDER - came back as cross-wall, and
the hint told them to re-measure with r=33 excluded. On a 66 mm bottle that
excludes everything. The split needs two skins; a patch has one, and a border can
therefore look like a wall crossing. `cross_wall` is now withheld whenever
`boundary_edges > 0`, the hint is withheld with it, and `skin_note` says which
question does not apply and why. The synthesised fixture that used to assert the
opposite now asserts this - a bridging triangle necessarily leaves the mesh open,
which is exactly the condition being excluded.

**A failed `catia_gsd_thick_surface` now carries the radius.** The report rebuilt a
22-section model twice before working out by hand that its valley walls were
0.19-0.24 mm against a 0.3 mm wall. The mesh answers that:
`catia_check_surface` has a `curvature` block (smallest radius, where it is, and
with the new `thickness` argument how many edges bend tighter than that), and the
thick-surface failure message measures the document's visible mesh and puts the
number and the location in the error - falling back to pointing at the tool when
the part is too broken to export.

The estimator is `radius = dual length / normal angle`, with the dual length taken
between the two triangles' CIRCUMCENTRES. Two cheaper lengths were wrong and are
recorded here so they are not tried again: the edge's own length gave 57.3 mm on a
vertical edge of a r=10 cylinder, and centroid-to-centroid gave 20.15 - both on a
surface whose radius is 10. Measured accuracy on B30 2026-11-04 against known
geometry: an extruded r=10 circle reads 9.74 (-2.6 %) and a r=5 fillet band reads
4.77 (-4.5 %). It runs LOW, i.e. it can call a surface tighter than it is, and the
reply says so rather than presenting the number as exact. A feature smaller than
the mesh's own facets cannot be resolved at all and reads as a crease; `limits`
says that too.

**Documents: the catalogs are named, and the note is about geometry.** 0.3.57's
`note` told the caller to close documents; the report found that 15 of the 18 were
`.CATfct` function catalogs that `Documents.Close()` accepts and CATIA re-mounts.
`session.open_documents` now counts and names `function_catalogs`, and the note
says catalogs cannot be released through automation, and that what costs time is
documents that HOLD GEOMETRY: 15 catalogs plus one Part ran a 90-point spline in
1.09 s, the same as a lone Part, while two finished Parts took it to 1.6 s and a
9-element join from 962 to 1957 ms - against eight EMPTY Parts changing nothing
measurable here (0.3.57). Same file, same author, three conditions: the variable
was never the count.

**One more loft case, in the message where the fold is explained.** A section with
no feature beside one carrying a deep one self-intersects the same way a
non-monotone stack does: r=8 was a plain circle and r=14 already carried an 8.6 mm
valley, so the loft had to create the whole feature inside one span; splitting it
into two lofts built.

963 tests, 13 new. What could NOT be measured: no surface built on B30 refused to
thicken - not a 0.1 mm slot at 0.6 mm, not a r=0.2 cylinder at 0.3, not a r=5
cylinder at 400 - so the failure TEXT was pinned with a stubbed export and a known
STL rather than by a real failing part. The measurement itself is exercised on
real geometry through `check_surface`.

## [0.3.57] — 2026-11-04 · a sliver is not a fold, and the documents CATIA opens for you

The 0.3.56 acceptance report retracted its own root cause and asked for the split
that would have saved it the retraction.

**Slivers get their own bucket.** It had read 124 deg on a 0.3 mm wall and
explained it as the inner skin of a thin wall. The explanation was wrong - two
solid controls had already shown both skins reading identically - and the real
shape of the fault is a triangulation bridge: where a joined seam cuts a nearly
FLAT region the mesh uses its largest triangles, one of them spans the two skins,
and its normal sits between them, so the dihedral reads like a fold that the
surface does not have.

`fold_deg_by_side.cross_wall` is that bucket, as a SUBSET of `mixed`: an edge
whose two endpoints belong to opposite skins. Vertex skins are a majority vote of
each vertex's triangles, so no wall thickness has to be guessed. The distinction
that matters is what stays out: a rim fold is `mixed` and NOT `cross_wall`,
because both of its endpoints sit on one skin and only the adjacent triangles
disagree. Measured on a synthesised 0.3 mm tube (36 segments, outer r=10, inner
r=9.7): 0 cross-wall edges clean - all 64 rim edges at 90.0 deg and un-flagged -
and exactly 1 at 169.98 deg, sitting at r 9.81 between the skins, once one
triangle was made to bridge. Every `worst_creases` entry now carries
`cross_wall`, and when three or more cross-wall creases share a radius band
`seam_hint` says so in words: which radius, why the mesh did it, and that the
answer is to re-measure with `region` excluding it and move the seam into a
curved region. The seam advice also went into `catia_gsd_join`'s description,
because that is where a seam is made.

**`catia_diagnose` now lists the open documents.** The same report measured every
GSD call roughly twice as slow in a session it believed held one document, and
found 18 CATIA sample catalogs loaded at startup. `session.open_documents` gives
the count, the names, and how many of them live under `CATInstallPath` - the ones
CATIA brought with it - plus a note above five documents.

**The 2x itself did not reproduce here, and the note says so.** Measured on B30
2026-11-04 with one CATIA process per condition: 90-point spline 855/872/873/898/
904/923/940 ms with ONE document open against 858/885/885/994 ms with NINE, join
467/541/550/560 ms against 558/600/631 ms. Eight extra empty Part documents cost
nothing measurable, so document COUNT is not the mechanism; eighteen sample
catalogs may well be, but that is their measurement, not ours. The note therefore
tells the caller what to look at instead of what caused it - a wrong cause is
worse than no cause, and this repository has already paid for that lesson twice.

**Two limits that only lived in release notes are now in README.** That a
`.CATPart` written by a newer CATIA fails to open on an older one with a bare
"method Open failed" (B30 on a B32 SP6 file, 2026-11-04), and that a seam on a
thin wall belongs in a curved region.

950 tests, 10 new. The fixture is synthesised in the test, so the split is
asserted without a CATIA session; the tool's own plumbing was checked once on B30
(a 30 mm extruded r=10 circle reports the five buckets, `cross_wall` empty,
`seam_hint` absent).

## [0.3.56] — 2026-11-04 · the fillet and the spine were never unusable

Two sentences from 0.3.53 came off the "Known limits" list. Both were wrong, and
wrong the same way: a declared call was tried once, in one arrangement, and its
failure was read as a property of the object.

**The surface fillet builds a true rolling-ball fillet.** 0.3.53 wrote "what it
builds is unverified". It is verified now, with the walls hidden so the STL
carried the fillet alone - the isolation 0.3.55 had to learn for healing, where
three features exported together read as their union:

    90 deg wedge, r=5, 40 mm edge   every vertex 5.0000 mm from the rolling-ball
                                    axis (min = max), area 314.159 mm2 = (pi/2)rL
    60 deg / 120 deg, r=5           radius min = max = 5.0000, areas 418.879 and
                                    209.440 mm2 = (pi - theta) r L
    floor z=0 + wall x=0, r=5       radius min = max = 5.0000, area 471.239 exact

The working pair was (o1 = -1, o2 = +1) in all four cases, including the
floor-and-wall corner built in a completely different direction, so it is a
property of the call and not of the build order. What 0.3.53 could not see is
why only one pair works - and what it never tried: the same call between a disc
and a cylinder standing on it builds TWO surfaces, (+1,+1) 573.4 mm2 and (+1,-1)
391.3 mm2. So `catia_gsd_fillet_surface` searches the four pairs, keeps the
survivor, and refuses in both other cases: none (two disjoint parallel
cylinders, the arrangement 0.3.53 mistook for a verdict) and more than one
(because two survivors are two different surfaces, and choosing one would be a
guess dressed as a result). Failed attempts are deleted immediately - a fillet
left in ERROR makes every later `part.Update()` fail, which is why the first
probe had to split its matrix and its geometry check across two documents.

**The spine has always had `AddSection`.** 0.3.53 wrote "the typelib names no way
to give it sections". The typelib declares 40 members on `HybridShapeSpine`,
`AddSection(iSection)` and `AddGuide(iGuide)` among them; the probe behind that
sentence listed `dir(spine)` filtered for "Add" and found nothing, which for a
late-bound COM object proves nothing. Measured: sections register, the count
moves, and `UpdateObject` succeeds with two or more (the 0.3.53 failure was on an
EMPTY spine - a different statement). The curve is the spine and not a line
through the centres: three parallel rings 40 mm apart gave 40.0000 mm along their
common axis and did not bend for a 30 mm sideways offset, because a spine follows
the sections' NORMALS; a second ring tilted 45 deg made it a 37.9224 mm curve
that leaves that axis by 7.05 mm at z=10.

**One measurement corrected on the way.** `Measurable.Area` is not always 0.0 for
a surface after all: the r=5 band read 0.000314, i.e. 314.159 mm2 in the MKS
units the SPA workbench uses, on every healthy document. In a document that
already carried six broken fillets the same kind of element read 0.0 and 0.005 -
so a zero area is evidence that the part is in error, not that a surface has no
area. The tools do not publish an area for this reason; they report the
orientation and point at `catia_check_surface`.

940 tests, 14 new. Measured on B30 before and after, with the geometry read from
the STL the same way the tool's own numbers are.

## [0.3.55] — 2026-11-04 · healing stitches, it does not smooth - and 0.3.51 measured air

The five-petal bottle model report asked for one thing: its five seams are "same
source sections, geometrically continuous, only parametrically broken", so would
AddNewHealing close them? The project had refused a healing tool since 0.3.51,
where the measurement said Continuity 0, 1 and 2 all left a 90 degree fold at
90.0. That measurement was taken on a feature with NO BODY REGISTERED.

`HybridShapeHealing` exposes `AddBodyToHeal(iBody)`, `SetMergingDistance`,
`SetTangencyAngle`, `SetDistanceObjective`, `SetSharpnessAngle` and
`ReplaceToHealElement`. With an empty body list it updates cleanly and changes
nothing - which is exactly what 0.3.51 recorded, and what this round's own first
probe reproduced (Continuity 0, 1 and 2, byte-identical meshes) before the type
info was read.

**`catia_gsd_heal` (104th tool)** registers every element with `AddBodyToHeal`,
sets the continuity and a merging distance. Measured on B30 2026-11-03 with every
state isolated in its own STL export:

    two lofts 0.05 mm apart        112 tris,  0 interior edges, 112 open
    heal C1, merging 0.5           112 tris, 24 interior edges,  64 open  <- closed
    heal C1, merging 0.001         112 tris,  0 interior edges, 112 open  <- no-op
    two lofts sharing a section    0.52 deg before, 0.52 deg at C1 and C2
    two walls at 90 deg            90.0 deg before and after, 4 tris either way

So the operator closes gaps up to the merging distance and does not change
continuity. Both halves are in the schema, in the reply and in README, because
"heal" reads like "smooth" and a caller who expects the second will get a
successful call with no change.

**The answer to the report is no** - not because healing is broken but because
its seams are already joined: a seam that is closed needs the surfaces rebuilt
tangent, and this automation has no smoothing operator.

Also measured, and worth recording: the five-petal bottle's CATParts cannot be
opened here at all. They were written by V5-6R2022 B32 SP6; `Documents.Open` on
this B30 fails with the generic "method Open failed". Its STL exports can be read
(and were - they reproduce the report's 124.05 deg worst crease, on a mixed edge),
but a solid exported at 4 degrees per facet carries 1-25 degree folds everywhere,
so a seam cannot be isolated from it. The synthetic arrangement above is what
answered the question.

`catia_check_surface` takes `top_creases` (default 8, previously a fixed cap):
two reports hit the cap with thousands of creases and got "the top 8", which
cannot answer "where are they". `creases` remains the total, and
`worst_creases_shown` says how much of the list you got.

926 tests, 9 new.

## [0.3.54] — 2026-11-04 · the two skins of a wall, and the refusals that were not written down

The 0.3.53 acceptance came back clean and then found one thing: the fillet and
spine refusals existed only in a release note and a test header, so the next
caller asking for a surface fillet would find nothing. That is fixed, together
with the thin-wall reading the model report asked for - measured first, and the
measurement changed what got written.

**`catia_check_surface` reports the two skins apart.** Every triangle is sorted
by the radial component of its outward normal (>= +0.5 outer, <= -0.5 inner,
otherwise axial) and every interior edge lands in `outer` / `inner` / `axial` /
`mixed`, each with its own edge count, median, p90 and max. Each `worst_creases`
entry now carries `r`, `theta_deg`, `z` and `side`, so the region keys and the
crease list speak the same language.

**But the mechanism the report proposed is not what the measurement showed.**
It reported 28.53 deg on the outer face of a 0.3 mm wall against 124.05 deg on
the inner one, and attributed it to the inner skin being the outer offset inwards
(R3 -> R2.7), where the mesh must chord harder. Two solids whose skins are known,
measured on B30 2026-11-04 (scripts/b30_v0354_wall.py):

    tube R10, wall 0.3             outer 32 edges, median/max 22.50  |  inner 32, 22.50
    shell 0.3 around an R3 fillet  outer 208 edges, median 13.38, max 29.89  |  inner 208, 13.38, 29.89

Identical on both skins, twice. And where the honest 90 degree folds are, is the
"mixed" bucket: on the tube all 64 rim edges score 90.0 there, because a wall's
END faces meet both skins at a right angle. So a 124 degree reading on an inner
face is a sliver along a seam, not a property of thin walls - and the reply says
that, with both measurements, instead of repeating the offset-radius story. The
`side_rule` field also says outright that the split means something only for a
part that wraps around the Z axis.

**The spline description now carries the technique that replaced the "deadlock".**
The same report retracted its own claim and handed back the way through: do not
put a plateau and a narrow transition band side by side in one closed section
(the band falls between two samples at 4 deg, and sampling it drives the spacing
under 0.1 mm), change the SHAPE of one continuous function instead -
`z = z_valley + (z_foot - z_valley) * smoothstep((psi/11)**n)` with
`n = ln(0.5)/ln(W_half/11)` setting the half-height width directly. Attributed as
reported, with the arithmetic in the text, because the caller does that work.

**README Known limits now says why there is no surface fillet and no spine.**
`AddNewFilletBiTangent(surface1, surface2, radius, o1, o2, trimMode,
relimitation)` updates for one orientation pair out of four on a 90° corner
(o1 = -1, o2 = +1) and fails at update for all nine combinations between two
disjoint parallel cylinders; `AddNewSpine()` takes no parameters and the typelib
names no way to add sections. Same rule as `AddNewHealing` in 0.3.51: declared is
not usable.

917 tests, 8 new. Measured on B30 before and after; the tool's numbers match an
independent reading of the same STL to the edge.

## [0.3.53] — 2026-11-03 · one number for the test count, one argument for boundary

Three reports arrived together: the 0.3.52 acceptance, a "what is still missing
for 100 %" gap list, and a curvature-deadlock post-mortem. Measurements first
(scripts/b30_v0353_probe.py, _signatures.py, _fillet.py, _corner.py), code after.

**The test count had two answers, and the report was right.** `pytest tests`
collected 888 while 0.3.51 and 0.3.52 both reported 891, because three tests
lived in a root-level `test_server.py` that only the CI command named. The file
now sits in `tests/`, so `pytest tests -q` is the one command and one number
(909 here), and CI, CONTRIBUTING and docs/server-runtime.md all say it.

**`catia_measure_distance {}` no longer answers COM_ERROR.** The empty call was
reported on 2026-10-31 and stayed open for seven versions: `_pick` raised a bare
ValueError, which carries no code and no stage, so the server fell back to
`COM_ERROR` / `failure_stage: execute` and attached the "check in_work_object"
paragraph to a call that never reached CATIA. It raises `ArgumentRejected` now,
names every accepted spelling, and the envelope says `validate`.

**`step_warnings` stops firing on `"warnings": []`.** Same report date, same
seven versions. Every envelope carries that key, and the substring test
`"WARNING" in result.upper()` matched the KEY, so a tool that always emits it put
every step into the warnings list. Empty arrays are stripped first; a non-empty
`warnings` field or a " WARNING:" anywhere in a paragraph still counts.

**`catia_gsd_boundary`** (103rd tool): the border curve of a surface. Measured on
B30 - `AddNewBoundaryOfSurface` takes ONE argument and updates, and on the 30 mm
extrusion of a r=10 circle the boundary measured 125.664 mm, exactly the two
circles. It is what "keep the outline, delete the face, build it again" needs;
`catia_gsd_extract` cannot do it, because an extract of a face returns a surface.

**`catia_check_surface` takes a `region`**: `{"r": [24, 28]}` or `z` or
`theta_deg` (wrapping when lo > hi) restricts the fold statistics to edges whose
midpoint falls inside, and the reply reports how many edges that excluded. The
whole-part median mixes a tight transition band with a smooth wall and hides the
band, which is the question the report could not answer. Boundary and
non-manifold counts stay whole-mesh and say so.

**The "angular features out of phase" failure mode is documented as a report,
not as a fact.** Measured on B30 on an isolated three-ring lobed loft
(r = R + 2·cos(5t + phase)) with a 30° lobe shift - far more than the 10-12° in
the model that reported it - the loft BUILT and THICKENED 0.3 mm, at 40 and at 90
points per section, as did 0/15/0 and 0/0/0. The loft and thick_surface
descriptions now say that and point at the joined seams (`region={}`, six patches
and a split in that model) instead of repeating a mechanism that did not
reproduce.

**Boundary could be shipped; the fillet and the spine could not.** The gap list
calls all three P0 and says the factory "already declares" them. `ITypeInfo`
gives the real signatures: `AddNewFilletBiTangent(el1, el2, radius, o1, o2,
trimMode, relimitation)` and `AddNewSpine()` with no arguments at all. The fillet
updates for exactly one orientation pair out of four on a 90° corner (o1 = −1,
o2 = +1) and fails at update for all nine combinations between two disjoint
parallel cylinders; the spine cannot be given sections, which the typelib does
not name. Neither ships until the geometry is verified - the same rule that
refused `catia_gsd_healing` in 0.3.51.

Also: the reply for a taken name said later calls would "pick one of two
elements at random"; measured, the OLD element keeps the name and keeps answering
to it, so it says that now. `_free_name` returns whether the lookup worked and
the reply admits it when it did not - the silent fallback was the one path that
could recreate the duplicate 0.3.52 removed. And `.sha256` is uploaded with the
zip again (it had been missing from three releases).

909 tests, 18 of them new. All measured on B30 before and after.

## [0.3.52] — 2026-11-03 · the rebuild was innocent; the duplicate name was not

The fourth report on the same bottle says that deleting a loft and rebuilding it
under the same name breaks its shared-edge topology, so join, split and thick all
fail, and that sections sampled finer than 1° produce a loft that will not join.
Both were reproduced on B30 before any code moved
(`scripts/b30_v0352_reloft.py`, `b30_v0352_join.py`,
`b30_v0352_names.py`). Neither holds. Something else does, and it explains the
two hours the report spent.

**A rebuild does not break a join.** Four orders, one document each, two lofts
sharing a middle section, join after each step: delete one piece and rebuild it
(join OK); delete both and rebuild both (OK); delete the shared section too and
rebuild it (OK); two lofts taking geometrically identical but *different* curve
objects for the shared section (OK). Join compares coincidence, not object
identity, so a new internal id cannot break it.

**Sampling density does not break a join either.** 60 / 90 / 120 / 180 / 600
points per closed section — 6.0° down to 0.60° apart, 0.29 mm of arc at r=28 —
two lofts sharing one section, and every combination built *and* joined. The
report's "below 1° the border stops joining" did not appear at any density.

**What is real: CATIA lets two elements carry the same name, and this server
made them.** Assigning `Name` checks nothing, so building `P_04_06` twice left
two elements called `P_04_06`; `FindObjectByName` returns one of them and a delete
removes one of them. Measured: a tree with two `C_P`, one `catia_delete_feature
{"feature_name": "C_P"}`, and one `C_P` left, still answering to that name. So
"deleted it, rebuilt it, still fails" is a true description of a tree that never
lost the old element — which is exactly the loop the report was in, and why the
same sequence succeeded once it deleted twice.

Three changes follow from that:

* a new element never creates a duplicate — a taken name comes back as
  `P_04_06.2` and the reply says so;
* deleting *by* a name that two elements carry is refused with the count and the
  way out, instead of removing an arbitrary one;
* `names=['P_04_06']` deletes **every** element carrying that name and reports
  `'P_04_06' x2`, so the tree can be cleared once the duplicates are known.
  A batch now counts elements removed rather than names given.

The join description also records what a shared edge is not, so the next session
does not spend two hours proving a rebuild innocent, and Known limits carries the
whole measurement.

891 tests, 9 of them new (`tests/test_reported_issues_2026_11_03_v0352.py`).

## [0.3.51] — 2026-11-03 · the 636 points were a choice, and healing does nothing

The third report on the same surface model asks for a healing tool, a zebra
check and a curvature comb, and says two items from 0.3.49 are still open.
Measured on B30 first (`scripts/b30_v0351_probe.py`, `b30_v0351_probe2.py`,
`b30_v0351_healing.py`, `b30_v0351_healing2.py`), which changed three of the four
answers.

**Two of the items were closed in 0.3.50, not open.** `catia_delete_feature`
gained `cascade` and `names[]` and `catia_gsd_join` gained the shared-edge
diagnosis in that release; the report's own §3.4 says the join failure was not
retriggered this round, and §3.3 describes a per-name delete that no longer
exists. Nothing was changed for them here beyond making the batch fast (below).

**Coordinate points need not be tree elements.** Measured before touching code:
90 points created with `AddNewPointCoord` and never appended, a spline built on
their references, saved, closed and reopened — the tree holds ONE element (the
spline) and the curve still measures 188.496 mm for r=30, the analytic answer,
before and after a `part.Update()`. So the 636 Point features the report counted
were the old code's choice, not the API's. `catia_gsd_spline` takes
`create_points` (default **false**) and says which route it took. Cost, one CATIA
process per condition, two runs each: 2.49 s with the points in the tree against
1.80 / 1.81 s without — a 1.4× difference, not an order of magnitude, because
building the points is the work; what changes is everything that walks the tree
afterwards.

**Deleting them is 25× faster.** A `names[]` batch used to call the single-name
path per element, and each name re-walked the whole tree and updated the part:
91 elements took 49.9 s. The GSD names now go into one selection after one tree
walk — **1.9 s** for the same 91. Part Design features still take the per-name
path, which is where body and index handling lives.

**`AddNewHealing` exists and does not do what the report expects.** It takes one
reference and exposes a writable `Continuity` — and a 90° join stayed at 90.0°
at Continuity 0, 1 and 2, with 4 triangles before and 4 after. It heals
discontinuities within a surface; it does not turn a G0 fold into a G1 blend. So
there is no `catia_gsd_healing` tool here: it would report success and change
nothing. `README` says so under Known limits, together with what to use instead
(`catia_gsd_blend`, `catia_gsd_connect`).

**Zebra, curvature comb and Match are unreachable, so quality is measured
instead.** New tool `catia_check_surface`: the dihedral angle between the two
triangle normals across every interior edge of the exported mesh, with the
distribution, the crease list and the endpoints of the worst edges, plus boundary
and non-manifold edge counts. Measured on B30: a smooth 20 mm cylinder reports
median 15.0° / max 15.0° / 0 creases (that is the mesh chord, not a defect), while
a 90° join reports median 0.0° / max 90.0° / 1 crease at exactly the shared edge.
The reply states its scope — the STL contains every visible element — and says
outright that CATIA offers no zebra here, so the tool is not mistaken for one.

**A loft that builds is not a loft that can be thickened.** The report's §3.1 is
right and was missing from the docs: a smooth loft interpolates through its
sections, so a rapid size change tightens the curvature between two of them, and
that region is what refuses a thin-wall offset later. Six of ten sections built
and then refused 0.3 mm where every 2–3 section span thickened. Both
`catia_gsd_multi_section_surface` and `catia_gsd_thick_surface` now say it, with
that case.

882 tests, 11 of them new (`tests/test_reported_issues_2026_11_03_v0351.py`).
Adds one tool: 102.

## [0.3.50] — 2026-11-03 · the view table was wrong, and five smaller lies

Two reports arrived: the acceptance receipt for 0.3.49 (which found the two
remaining P0s) and a capability report asking for sheet metal. Everything below
was measured on B30 first — including the one the 0.3.49 note had called
"not reproduced", which was a misdiagnosis on this side.

### Fixed — `catia_set_view` had front/back and top/bottom swapped

The 0.3.49 acceptance report said `set_view('bottom')` produced something that
looked like a front view, `set_view('top')` merely flipped the part 180°, and two
screenshots taken around a view change came back with the same md5 — which it
read as an asynchronous capture. It is not asynchronous: the direction table was
wrong, so "a view change" often was not one.

Measured on B30 with a plate 40 x 10 x 4 carrying a tower on its +Z face at +X,
one screenshot per view:

    through 0.3.49            sight          what it actually showed
    set_view('front')        (0, 0, -1)   top-down: the plate flat, the tower a square
    set_view('top')          (0, -1, 0)   an UPSIDE-DOWN front elevation (tower down)
    set_view('bottom')       (0, 1, 0)    a front elevation (tower up)

The camera had Z as its up axis everywhere, so every side view came out rotated
one quarter, and the two "front/back" entries were in fact top and bottom. The
new table, verified by looking at the pictures:

    front     sight (0, 1, 0)    up (0, 0, 1)     X right, Z up
    back      sight (0, -1, 0)   up (0, 0, 1)
    top       sight (0, 0, -1)   up (0, 1, 0)     X right, Y up
    bottom    sight (0, 0, 1)    up (0, -1, 0)
    left      sight (1, 0, 0)    up (0, 0, 1)
    right     sight (-1, 0, 0)   up (0, 0, 1)
    isometric sight (-1, -1, -1) up (-1, -1, 2)  perpendicular to the sight

The isometric up vector was `(0, 0, 1)` as well until a test pointed out that it
is not perpendicular to a `(-1, -1, -1)` sight; `(-1, -1, 2)` is.

### Fixed — `catia_screenshot` reported the size it was asked for, not the size it wrote

`width`/`height` were accepted, passed down, and never used: CATIA V5 captures
the 3D window's client area and exposes no capture-resolution setting (measured:
900 x 900 requested, 300 x 400 written, and setting `Window.Width/Height` changed
nothing). The reply printed the request anyway — so a caller resizing for a
report got a picture at a resolution nobody mentioned. It now reads the pixel
size out of the file it just wrote (JPEG SOF / BMP header, no image library) and
says explicitly when that differs from the request. The schema calls the two
arguments advisory, because that is what they are.

### Fixed — `catia_gsd_join` failed with 167 characters of raw COM

The 0.3.49 note had put "join needs a shared edge — join first, split afterwards"
in the docstring, which the caller cannot read at the moment it matters. Measured
on B30: two coaxial surfaces sharing an r10 circle join while untouched; split the
lower one at z=10, join the kept piece with the upper one, and the update fails
with nothing to go on. The failure now names what was joined, with its automation
types (`'J_LOW_BOT' (HybridShapeSplit), 'J_HIGH' (HybridShapeExtrude)`), states
that a join needs a shared edge, names the split as the usual cause, and lists the
two fixes (join first then split, or `catia_gsd_trim`, which needs an
intersection rather than a shared edge). 167 characters became 1044, all of them
actionable.

### Fixed — the mesh-box warning fired on hidden geometrical sets

0.3.49 warned whenever a part held a geometrical set, so a part whose sets were
all hidden got a correct box and a false alarm on it. The warning is now driven by
the set's own visibility flag (`GetShow()` answers `(0, 0)` shown / `(0, 1)`
hidden, measured on B30), and `box_scope` distinguishes the three cases: no sets,
all hidden, some shown. Measured: surface visible → z max 35 and one warning;
set hidden → z max 10 and none.

### Added — `catia_delete_feature` takes `cascade` and `names`

Deleting a coordinate spline left its points behind — six stayed after a
six-point spline was deleted, and a real session grew from 666 to 801 elements
that way, 28 s to hide and 2.1 MB of file for construction points nobody could
use. CATIA has no reverse dependency query, so the creator records the pairing:
`_point_refs` remembers the names it made, the tool that consumed them publishes
them as owned, and `cascade=true` deletes them with the feature. Without
`cascade` the reply says how many it left behind and by what name. Only what THIS
session created can be named, and the reply does not pretend otherwise.

`names` deletes several elements in one call — that cleanup took fifteen calls of
2.0-2.4 s each in the acceptance session — with per-name failures reported rather
than aborting the batch.

### Added — sheet metal: the limit is stated, and `catia_diagnose` probes it

The server has no sheet-metal capability at all: no wall, flange, bend, unfold or
flat-pattern tool, no K-factor or bend radius. A 2 mm bracket with two bends came
out 76 faces against the source part's 57 (+19, +0.49 % volume) because every
bend has to be approximated prismatically. README now says so in Known limits.

`catia_diagnose` reports what can honestly be reported: whether the workbench's
DLLs are installed beside the running `CNEXT.exe`, and that the automation
objects this server drives expose no sheet-metal feature either way. NOT via
`GetWorkbench`, which was the obvious probe and does not work: on B32 SP6 it
resolves `SPAWorkbench` and nothing else — `Drafting` and `PartDesign` fail the
same way — so a probe built on it would answer "no sheet metal" for every machine.
The licence is not readable from disk and the reply says that too.

## [0.3.49] — 2026-11-03 · the surface report: one cause misattributed, four replies that said nothing

The 2026-11-03 report is a real modelling session — a five-petal bottle base
built from ten wavy concentric rings lofted into one NURBS face — and it lists
seven findings. They were measured on B30 in three rounds
(`scripts/b30_loft_surface_claims.py`, `b30_surface_probe2.py`,
`b30_surface_probe3.py`, plus `b30_spline_bench.py`), one variable at a time.
Six survived; one was a misattribution, and correcting it is the most useful
thing in this release.

### Corrected — the loft failure is the section ARRANGEMENT, not the curve type

The report's headline conclusion was "`catia_gsd_circle` is more fragile than a
closed spline as a loft section", and its minimal reproduction was the trio
r 2.9 / 3.0 / 8 with the middle section at z −17.58, *above* both neighbours
(−19 and −25). Measured on B30, that trio fails as circles, as 12-point closed
splines **and** as 90-point closed splines — the last one is a circle to within
0.001 mm, so the curve type cannot be what changed. The same three radii in a
monotone stack build, and the outer pair alone builds:

    r2.9@-19, r3.0@-17.58, r8@-25   circles        FAIL
    same three, closed splines 12pt               FAIL
    same three, closed splines 90pt               FAIL
    r2.9@-25, r3.0@-20, r8@-17      circles        builds
    r2.9@-19, r8@-25 (pair)         circles        builds

What fails is the fold-back: the loft interpolates *through* the sections and
stays inside their hull, so a section sitting past the turn of its neighbours
forces a self-intersection and CATIA refuses the update. Swapping a circle for a
spline changes nothing — the report's own "same geometry as splines → ✓" run had
also changed the radii to 3/5/8 in a monotone stack. The tool description and
the failure text now say this instead of recommending the substitution.

### Changed — a failed loft now names the call it failed on

The six-line "common causes" speech came back verbatim for every failure and
never said what had been attempted. The reply now starts with the call's own
numbers — `sections: 3 (3 curve), guides: 0, ruled: false` — and puts the fold
first, with the measured numbers above, followed by the three older causes. A
point or vertex section is no longer among them: it is refused before anything
is built (below).

### Fixed — a point or vertex section is refused before the tree is touched

Both forms fail, one stage apart, and neither failure says why (measured on B30
2026-11-03, both orders):

    datum point     AddSectionToLoft ACCEPTS it; UpdateObject then fails with
                    the generic "CATIA rejected the feature" text
    Vertex.N        AddSectionToLoft fails at once with "invalid number of
                    arguments", which reads like a wrong argument count

The CATIA UI does let you pick a vertex as an end section; this automation
object does not. Both are now rejected in the validate stage — nothing is
appended, nothing is deleted — with the workaround in the message (end the loft
on a small closed section and cap it with `catia_gsd_fill`).

### Fixed — `catia_gsd_split` says which side it kept

A split of a 133 mm bottle at z=100 returned 742.80 mm³ instead of 9488.98 and
looked like a broken model. Nothing was wrong with the geometry: the default
kept the stub **above** the plane. Measured the same day with a cylinder r10
z 0..40 cut at z=10, the kept side read off the STL of the result (Measurable.Area
answers 0.0 for a surface, so it cannot answer this):

    reverse=false (default)   kept z 10.0 .. 40.0   the cutter's normal side
    reverse=true              kept z  0.0 .. 10.0

The reply now names the side, and the tool description carries the same table.

### Fixed — the mesh bounding box says what it measured

`method='mesh'` exports the document to STL, and that STL holds every **visible**
element, surfaces included — a Part and a Body have no `ExportData` of their own,
so "export the solid only" is not available. A 10 mm pad with a construction
surface at z 30..35 measured z max **35**; hide the set and it reads **10**.
The reply now carries `box_scope` and a warning with those numbers, instead of
letting a caller read the drawing's envelope as the part's:

    box_scope: the whole document as exported: the solid plus every VISIBLE
               wireframe/surface element in its geometrical sets

### Changed — a spline reports the tree elements it creates, and stops updating points twice

`catia_gsd_spline` with coordinates leaves one Point feature per point in the
tree — 90 for a 90-point ring, which is how a geometrical set reached 666
elements and 28 s to hide. That is the API: a spline references points, so the
points cannot be dropped. Two things changed: the reply now states the count
("90 of them were coordinate lists … expect 90 extra tree elements"), and the
redundant per-point `UpdateObject` is gone, along with two `ActiveDocument`
round trips per point, because the feature-level update cascades.

**This is not a speed fix, and the first measurement said it was.** Inside one
CATIA session, the same 90-point spline measured 5.21 s and 0.97 s a few minutes
apart. With one CATIA process per condition (cold both times) it is:

    per-point UpdateObject kept     3.50 s   4.68 s
    removed                         4.08 s   4.19 s

i.e. the update costs nothing measurable, and what a 90-point spline really
spends is the 90 datum points at ~39 ms each. Session drift inside one CATIA run
spanned 2.35 s to 13.65 s for identical work, which is why a within-session A/B
is worthless here.

### Fixed — `catia_diagnose` reports a pywin32 build

`win32com` has no `__build__`, so the field read `unknown` on every machine. The
DLL's file version carries the same number: this machine answers `3.13.312.0`,
i.e. built for Python 3.13, build 312.

### Documented — join first, split afterwards

A split piece has no shared edge left to join on, so joining it with the
neighbour it used to touch fails with the same generic update error, and the
message says nothing about it (measured: 167 characters, no diagnosis). The
`catia_gsd_join` and `catia_gsd_split` descriptions now carry the workflow note.
This is a geometric consequence, not something the tool can decide for you.

### Not reproduced — screenshot after `set_view`

`set_view → fit_all → screenshot` produced three distinct captures for three
views (front / bottom / isometric), and `viewer.Update()` before the capture did
not change the frame. Whatever produced the stale image in that session was not
reproducible here, and no change was made for it.

## [0.3.48] — 2026-11-02 · the two array findings that could be fixed, and the one that could not

The 2026-10-31 report listed three array problems and asked for them to be
reproduced on real geometry. They were, on B30, with a 4x4x10 pad and one
bounding box per call — and the three turned out to be three different things.

### Fixed — `circ_pattern` had no way to ask for the other direction (#7)

A 4x4 pad seeded at (30, 0), two copies at 90 degrees about Z, lands the copy at
(0, -30): **clockwise**, the opposite of the right-hand rule a caller reads off
"axis z". The flag that controls it (`AddNewCircPattern`'s reversed-axis) was
hardcoded `False`, so the only workaround was a negative `angular_spacing`.

    reverse=false (default)  copy at (0, -30)   unchanged behaviour
    reverse=true             copy at (0, +30)

The default is unchanged — a model built with negative angles keeps its geometry
— the argument is new, and the reply now says which way the copies went instead
of leaving it to be discovered.

### Fixed — `user_pattern` reported a number CATIA ignores (#6)

A 1000 mm3 through pocket, a 3-point locate sketch, and the same plate measured
after each call:

    count=1 -> 3000 mm3 removed (3 copies)
    count=3 -> 3000 mm3 removed
    count=5 -> 3000 mm3 removed

`count` does not decide anything: the locating points do. The reply used to
repeat the argument back — "5 instances placed" beside a tree holding three
copies — which is how a caller ends up explaining a volume that looks like twice
what he asked for. It now counts the sketch's locating points (subtracting the
sketch's own absolute axis, which is one of its geometric elements: three points
read as four elements, the first named "绝对轴") and says so when the argument
disagrees.

### NOT fixed — `rect_pattern` puts the copies on the wrong axis (#5)

Measured, and worse than reported: with `dir1_axis="x"`, three copies at 20 mm
came out along **-Y**, and `dir2_axis="y"` came out along **-X** — the roles are
exchanged *and* both directions are negative, on both the axis form and the
default. The reply said "axis x+ -> dir1" for all of it.

It is not a one-line mapping error, which is why this release does not touch it:

* the direction a plane reference produces depends on its **partner** — the same
  `PlaneYZ` drove Y next to `PlaneZX` and Z next to `PlaneXY`;
* swapping the two references is rejected at `UpdateObject`, so the order is not
  ours to choose;
* line references (the way `circ_pattern` builds its axis) are rejected outright
  by `AddNewRectPattern`.

A candidate fix was written and thrown away: it made (x, y) correct and (y, z)
wrong, which is worse than a known-wrong default because it moves the error
around. The next attempt has to check the result at run time — build, measure
which axis grew, rebuild with the slots exchanged if it disagrees — rather than
encode a table that this API contradicts.

Until then: **do not trust `dir1_axis`/`dir2_axis` to name the axis.** Read the
body's box after the call and use `reverse_dir1`/`reverse_dir2` or exchanged
spacings to get what you meant. The reply now says the reference pair may be
reordered; the README's known-limits section says the rest.

### Also measured, and not a defect — P12_Fan rebuilds

The 0.3.41 incident left an open question: after three R5 fillets were added to
`P12_Fan.CATPart` in memory (never saved) its `Update()` failed, and it was never
established whether the part or the probe was at fault. Opened from disk and
updated on 2026-11-02: **`Update()` succeeds**, with all four features intact
(凸台.1, 凹槽.1, 加厚曲面.5, 圆形阵列.11). The failure does not reproduce on the
file as it stands, and the file was written after that session, so the most it
can prove is that nothing in the part is broken today.

**Tests:** 830 passed (823 -> +7).

## [0.3.47] — 2026-11-02 · a remainder clamped to zero is a breakdown that does not add up

Not from a report: from the one test in this suite that failed about once per full
run and always passed on its own.

    test_the_documented_part_route_names_the_remainder_that_used_to_vanish
    -> assert abs(round(closed, 1) - reply["parts_ms"]) <= 0.1

`parts_ms` is split into `measure_ms` + `tensor_ms` + `parts_other_ms`, and the note
beside them says the three **add up exactly**. They did not, occasionally:

    parts_other_ms = round(max(parts_ms - measure_ms - tensor_ms, 0.0), 1)

Three separate clock readings, each rounded to 0.1 ms. Round both windows up by
0.05 and the total down by 0.05, and the two windows come out 0.15 ms past the
total — at which point `max(..., 0.0)` publishes a remainder of 0.0 and the reply
adds up to 0.15 ms more than the field it claims to decompose. The clamp was there
to avoid printing a negative number, and it bought that by breaking the one thing a
breakdown is for.

### Fixed — the last bucket absorbs the rounding, unclamped

    parts_other_ms, note = _cost_remainder(parts_ms, measure_ms, tensor_ms)

The remainder is now the rounded difference, signed. The three fields close to the
digit (|error| <= 0.05 ms, from rounding the remainder itself and nothing else), and
when it lands below zero the reply says why, in `parts_other_note`:

> the windows above were each rounded to 0.1 ms and round 0.1 ms past this part's
> total, so the remainder is negative by that much - no step took negative time, and
> the three fields still add up to parts_ms exactly

Both routes publish it the same way: `document=<name>` (`_part_inertia`) and the
active-part route (`_get_inertia`).

### Tests — `tests/test_cost_breakdown.py` (7)

* a positive remainder is just the difference, with no note;
* 5.1 + 5.0 against a total of 10.0 gives -0.1 **and** the sentence that says it is
  not negative time;
* 2 000 randomised cases of independently rounded totals and windows, asserting the
  closure holds every time;
* a source guard: `max(parts_ms - measure_ms - tensor_ms` may not come back;
* both routes, end to end, through their real payloads;
* a scripted monotonic clock (5.06 + 5.06 inside 10.12) that produces the negative
  remainder for real — no fake clock that runs backwards, which would have proved
  nothing about the failure being reproduced.

**Suite: 823 passed** (816 -> +7), run three times back to back to check the flake
is gone rather than merely absent this time: 823 / 823 / 823.

### Why this took a release of its own

A test that only fails in company is worse than a failing test: it teaches everyone
to re-run until green, and the next real regression gets re-run too. The arithmetic
is one line; the reason it survived four releases is that the failure was never
reproduced, only re-run.

## [0.3.46] — 2026-11-01 · a new spelling of a field, and five readers that never heard

The report is one paragraph and one line of traceback, and it is right: 0.3.45
promised "method='mesh' on an assembly now means every part from its mesh", and
the first real call died with

    STEP_EXCEPTION: 'float' object is not subscriptable

after the STL export had already been paid for (1.3 s for the whole product,
4.6 s for one component). Five of the six release items passed; this one crashed.

### Root cause — the producer learned a third value, the readers did not

`_part_box_entry(force_mesh=True)` returns a box bounded from the mesh:

    {"points": [x0, y0, z0, x1, ...], "source": "mesh"}      <- FLAT list

The existing mesh value, from the fallback path, is `source: "mesh_fallback"` —
same flat list, different word. `_box_walk` asked `if cached["source"] ==
"mesh_fallback"` to decide whether to transform the points one vertex at a time,
so `source: "mesh"` fell into the vertices branch, which indexes each element as
a triplet: `p[j]` on a float.

The report found the two lines in `_box_walk`. There were **five** readers
spelling the same question out by hand, and two of them were wrong in a way that
does not raise:

    _box_walk, world box        == "mesh_fallback"   -> TypeError (the crash)
    _box_walk, part detail      == "mesh_fallback"   -> mesh_vertices / points_route
                                                        / sweep_route silently absent
    cold_reasons                == "mesh_fallback"   -> a mesh-bounded part counted
                                                        as a topology build
    part_boxes_by_mesh          keyed "mesh_fallback" -> 0, while part_boxes was 46:
                                                        three numbers contradicting
                                                        each other in one reply
    from_mesh / _BOOKS.note     already correct, written as a two-item tuple

Fixing only the two lines the report named would have shipped three more silent
wrong numbers. The question is asked in **one** place now:

    MESH_SOURCES = frozenset({"mesh", "mesh_fallback"})

    def _from_mesh(source) -> bool: ...

and the one reader that genuinely needs to know *which* mesh route ran reads a
field the producer records rather than re-deriving it: `mesh_reason` is
`method_mesh` or `no_vertex_at_extremum`, so the sentence under the number can
say "because you asked for it" or "because no vertex lies at this part's
extremum" without guessing from a string.

`tests/test_reported_issues_2026_11_01_v0346.py` refuses the sixth spelling: an
AST scan fails the suite if any `["source"]` / `.get("source")` value is compared
against a mesh spelling anywhere in the package, or if the pair is written out by
hand. (Injected to check: putting `== "mesh_fallback"` back in `_box_walk` fails
8 tests, this one among them.)

### Measured on B30, same day — the call that crashed, and its control

A box 40×20×20, a cylinder R12 × h10, three instances, one placed at (100, 0, 50):

    whole product, method=mesh        ok  x[-20, 112] y[±11.8177] z[0, 60]
                                         counters (by_mesh, by_vertices, part_boxes) = (2, 0, 2)
    whole product, method=vertices    ok  same integers, counters (1, 1, 2)
    component MESH_DRUM.1, mesh       ok  x[88, 112] y[±11.8177] z[50, 60]
    component MESH_DRUM.1, vertices   ok  same, via the fallback

`scripts/b30_mesh_assembly.py` reproduces it: it creates its own three documents,
closes them without saving, and never activates anything it did not create.

### The number in the prose was too narrow, and the measurement says so

The mesh notes claimed "0.47–0.73 % small" without saying on what. An R12
cylinder's mesh box reads ±11.8177, which is **1.52 %** short of 12 — the STL
chord tolerance scales with the feature. Both spellings now name what was
measured and say to read the figure as an order of magnitude: a wrong-looking
constant is exactly the kind of thing the next acceptance runs into.

### Changed

* `parts[].mesh_reason` is published, alongside `source` / `points_route` /
  `sweep_route`; `source_note` distinguishes "you asked for the mesh" from "no
  vertex lies at an extremum".
* `bounds` on a fully meshed assembly says "every component's box came from its
  mesh" instead of the generic lower-bound sentence; a mixed one still says
  "mixed: N instance(s)".
* README: the assembly paragraph no longer tells you to fall back to per-part
  `document=...` calls — `method="mesh"` on the assembly does that now.
* `sketch_arc.major` says it has no effect on an arc already ≤ 180° (reported as
  a possible defect; it is not one).

### Not changed, and why

* `DOCUMENT_ALREADY_OPEN` was reported as having no way forward. It has three
  `next_actions` — activate it, close it, or pick another path — plus an explicit
  `next_actions_note`. Nothing to add.
* The fillet anchor error message the report calls high quality stays as it is.

## [0.3.45] — 2026-11-01 · two documents on one path, and an arc that went the long way

The first report from a finished build rather than a probe: a 20-part monitor
bracket plus a 46-instance rigid assembly, all of it built through this server on
B32, nine parts with volume, area and topology all exact. Six findings; four fixed
here, one added, one documented.

### Fixed — `catia_new_part(file_path=X)` twice left two documents on one path

`new_part` always calls `Documents.Add`, and the SaveAs onto a path another open
document holds then fails — correctly, the file is locked. What was left is a
second document with the same name and **no path**, holding all the geometry,
beside the empty part the first call had already written to disk.

    catia_list_documents ->
      { name: "P05.CATPart", path: "E:\\...\\P05.CATPart" }   <- empty on disk
      { name: "P05.CATPart", path: "P05.CATPart" }              <- everything built

After that every `document="P05"` answered "matches 2 open documents", and the
file looked finished but had no solid. Hit three times in one 20-part build.

Refused now, **before** the document is created — the failure is unrecoverable
once one exists. `DOCUMENT_ALREADY_OPEN`, validate stage, with the three ways
forward (activate it, close it, or pick another path).

### Fixed — an arc from two endpoints took the 355-degree way round

    arc(cx, cy, R, p1, p2, direction="ccw")
    -> "swept 355.6117 deg counter-clockwise, arc length 1608.4415 mm"

The endpoints were 4.39 degrees apart. `ccw` from -71.0 to -75.4 degrees really is
355.6 degrees, so the code was not wrong — it was doing what the argument said,
to a caller who did not mean it. The shaft built on that arc then failed with
`UpdateObject 失败`, which points at nothing, and the only clue had been a
parenthetical "MAJOR arc" two calls earlier.

`major` is now an argument, **default false**: with two endpoints the arc takes
the short way and the reply says so, naming both sweeps. `major=true` restores the
old behaviour for the 240-degree arcs that are legitimate. Two smaller things in
the same call: two endpoints at the same angle are refused (`DEGENERATE_ARC`,
previously a 360-degree sweep that failed three steps later), and an exact half
circle says that direction decides nothing there.

**Breaking**: an arc that used to come out as a major arc now comes out minor
unless `major=true`.

### Fixed — `summary` dropped a successful step's warning

The MAJOR-arc line was the only thing that explained the shaft failure, and
`summary=true` keeps only the steps that failed — so finding it cost a re-run of
the whole plan without summary. Successful steps whose reply carries a `WARNING`
line are now kept in `data.step_warnings`, because such a step is usually the
reason a later one fails.

### Fixed — an assembly box could not use the parts' meshes

`get_bounding_box(method="mesh")` on a product was refused outright. The refusal
was right about the cost (a mesh per part) and wrong about the need, and the
reason is narrower than it looked: the per-part mesh route already existed, but it
only fired for a part with **no vertices at all**. A part whose extremum sits on a
curved face has vertices, so the fallback never fired and its contribution was
short by however far the surface bulges — measured on the bracket as 2.392 mm (a
drum-shaped end cap) and 8.99 mm (a spherical boss), in exactly the two directions
its acceptance criterion was checking.

`method="mesh"` on an assembly now means what it says: every part from its mesh,
one per distinct part rather than per instance. `auto` keeps the old vertices-first
route, and the reply now states which route produced the box in `mesh_route`.

### Added — `catia_new_product(file_path=...)`

`new_part` had it and `new_product` did not, so "create an assembly" was always
two calls and the second is the one people forget — and an assembly with no path
cannot be referenced by `catia_add_component`, which surfaces three steps later as
"the component is missing". Same duplicate-path guard as `new_part`.

### Documented — which way a `frame` plane's normal points

The schema said "the sketch's H direction" and "the sketch's V direction" and
nothing about the normal, so a side lip landed at +Z where -Z was meant. It now
says: **normal = u × v** (right-hand rule), the axes are `u` and `v` exactly as
given, `origin` is in global coordinates — and to put material on the other side,
reverse `v` rather than mirroring the profile, because those two are not the same
operation.

### Recorded, not fixed

`catia_fillet` on an R6.35 edge of a 2 mm plate: 4 of 12 succeeded, and the reply
explained the topology reasons well but never said what the report calls the real
one — **R larger than the local thickness is a full round**, a different operation
that CATIA's edge fillet will not approximate. Also listed: no solid rib/slot, no
pad draft, no sketch fillet, and `catia_shaft` only turns about the sketch's own
H/V axes.

## [0.3.44] — 2026-10-31 · the check that refused a spelling the tool reads

Two more reports against 0.3.43, plus the first feedback from a real 2 700-step
build (a 63-link tracked belt, 143 029 376 mm3, +0.84 % against the analytic
integral). Everything here is a defect 0.3.43 introduced, worsened, or left behind.

### Fixed — `UNKNOWN_ARGUMENT` refused a spelling the tool accepts

`catia_measure_distance` takes `element1`, `elem1_name`, `element1_name` and
`elem1` — `_pick()` reads all four, and the tool's own description said so:

    "Aliases accepted: elem1_name, element1_name, elem1."

The schema declared two of them. Before 0.3.43 that was invisible, because the
server trusted every argument. The new check made it a refusal of a spelling the
tool documents as valid: the same class of defect this project keeps closing — the
code knew something the declaration did not — reintroduced by a fix.

The list now lives in `catia_mcp/arguments.py` and **both sides read it**:
`_pick(arguments, *ELEMENT1_NAMES)` and `**alias_properties(ELEMENT1_NAMES, …)`.
Listing the names twice would have fixed four tools and re-opened the gap on the
fifth.

Two things found while checking the report's table, which was not accurate about
them:

- `catia_coincidence_constraint` declared `element1` / `element2` and **read
  neither** — `AddBiEltCst(0, comp1, comp2)` aligns two whole components. Passing
  one was accepted, ignored, and answered with "Coincidence constraint created".
  Removed, so the call now says it does not take that.
- `catia_gsd_intersection` / `catia_gsd_trim` read `args["element1"]` and have
  never accepted an alias. Refusing `elem1_name` there is **correct**; the reply
  lists what the tool does take. Left alone.

Guard: every spelling passed to `_pick()` must be declared by some tool's schema.

### Fixed — the ghost-field guard could not see a bare word

The reviewer put `cold_parts` back into a `warm_up` description and the guard
**passed**: it scanned quoted tokens, and that ghost was never quoted anywhere
(`"(parts_measured, cold_parts, elapsed_ms, …)"`). Re-injected here; the old guard
passes, the new one fails.

- `RETIRED_FIELD_NAMES` — every renamed field, searched for as a **bare word** in
  any schema prose. Renaming a field now means adding to it, and a test keeps the
  list honest by checking that each replacement really is emitted.
- The emit-key set counted only `Dict` literals (435 keys); `payload["k"] = …`
  is a subscript store and was invisible. 548 now.

### Fixed — a plan of 285 steps answered with 285 step records

The client truncated the reply and the two failed steps were inside the cut, so
finding them cost a re-run of the whole plan. `catia_run_plan {summary: true}`
returns only the failed steps plus `steps_omitted`, `failed_indices` and the
totals. The default is unchanged.

### Fixed — a file name CATIA will not write

`catia_save_document {file_path: "零件.CATPart"}` failed after six attempts, each
one popping a modal dialog, and reported "could not be saved" — which reads like a
lock, and no retry can help. Measured on V5-6R2020 B30, same folder, same length,
only the name differing:

| path | result |
|---|---|
| `…\zh_probe\ascii_probe.CATPart` | saved |
| `…\zh_probe\中文探针.CATPart` | `DISP_E_EXCEPTION`, 6 attempts |
| `…\zh_probe\中文目录\probe.CATPart` | saved |

So the limit is the file **name**; a non-ASCII **folder** is fine. Refused now
before CATIA is asked (`PATH_NOT_ASCII`, validate stage), with the measured table
and the one route that works: save under an ASCII name, rename the file after.

### Changed — an unknown key is reported before a missing one

`catia_coincidence_constraint {element1: …, elem2_name: …}` answered
`INVALID_ARGUMENT` (missing `element2`). The typo is the cause and the missing
argument is its symptom, so the unknown key is checked first.

### Upgrading

0.3.43 renamed three test files to match their contents
(`…_v0339.py` → `…_v0340.py`, `…_v0340.py` → `…_v0341.py`,
`…_v0341.py` → `…_v0342.py`). An install that copies over an existing checkout
leaves the old names behind and runs those tests twice — which shows up as
`802 passed` instead of `783`. Delete the three old files, or unpack into a clean
directory.

## [0.3.43] — 2026-10-31 · the refusal that never reached CATIA

Two acceptance reports against 0.3.42, both dated 2026-10-31. Both found real
defects; neither asked for a new tool.

### Fixed — a tool's own refusal still answered COM_ERROR

0.3.39 promised one envelope for every pre-execution refusal and shipped three of
the four entry points. The missing one was the largest: the lookups a TOOL does
for itself after the schema passed. Those raised a bare `RuntimeError`, so the
server saw no declared code and reached for the worst defaults it has.

| call | before | now |
|---|---|---|
| `catia_delete_feature {}` | `COM_ERROR` / `execute` / `modified: null` | `ARGUMENT_REJECTED` / `validate` / `false` |
| `catia_delete_feature {index: 99}` | same | `ARGUMENT_OUT_OF_RANGE` / `validate` / `false` |
| `catia_fillet {edge_names: ["Edge.99"]}` | same | `ARGUMENT_OUT_OF_RANGE` / `validate` / `false` |
| `catia_fillet {edge_name: "NO_SUCH_EDGE"}` | same | `ARGUMENT_REJECTED` / `validate` / `false` |
| `catia_shell {faces_to_remove: ["Face.99"]}` | same | `ARGUMENT_OUT_OF_RANGE` / `validate` / `false` |
| `catia_fillet {edge_anchors: [[999, 999, 999]]}` | same | `ARGUMENT_REJECTED` / `validate` / `false` |

The code was the least of it. The envelope also carried a `recovery` block
telling the caller to inspect a model nothing had opened, and the COM_ERROR
paragraph — *"check the state above: a Part Design factory called while
in_work_object is a geometrical set fails exactly this way"* — printed directly
under a message that had just said the edge number was too high. Read by anyone
branching on fields, the fixable argument (T3) and the feature CATIA had rejected
and the tool had rolled back (R2) were the same failure.

The raise site now says whether CATIA was ever reached: `ArgumentRejected` and
`ArgumentOutOfRange` carry `stage = "validate"`, `modified = False` and their own
`next_actions`, and `_execute_tool` reads `stage` off the exception **before** it
chooses a message. Fifteen sites across five modules; the messages themselves are
unchanged, because they were always right — only the envelope was wrong.

The distinction the fix had to preserve is tested: a real `FeatureRejected` still
answers `failure_stage: "execute"` with `rolled_back: true`. A rejection and a
rejection you have to repair must not look alike.

### Fixed — an argument the tool never reads is no longer dropped in silence

`catia_get_inertia({component: "NP42.1"})` answered **5760.0 mm³** — the ACTIVE
part's volume — for a part that is 1536.0 mm³, with `isError: false` and no
warning. `component` is declared by `catia_get_bounding_box`; nothing in the
measurement module reads it; so a habit learned from the sister tool became a
silently wrong number. A nonsense key (`componentx`) succeeded too.

Rejected now, at the validate stage, by the server rather than tool by tool:
`UNKNOWN_ARGUMENT`, with `data.unknown_arguments` and `data.declared_arguments`
so the reply says what to pass instead of only what not to. `catia_run_plan`
applies the same check to each step, because a plan could otherwise pass an
argument the tool never reads and come back as a list of successful, wrong steps.

`document_name` was the one argument the dispatcher read without declaring it — a
real alias, so it is now in the schema rather than on a whitelist.

### Fixed — `cold_parts`, three versions after the rename

0.3.39 renamed the field to `session_first_parts`. Two schema descriptions kept
naming `cold_parts`, and the reason they survived is worth more than the fix:
a 0.3.30 test asserted that the bounding box description **contained the string
"cold_parts"**. The suite was holding the ghost in place. That assertion now
quotes the live name.

Two guards, so the class of defect does not need another round:

- **Ghost fields.** Every string used as a dict key anywhere in the package is
  collected by parsing the source; every quoted snake_case token in a schema
  description is checked against that set when it is shaped like a published
  field (`*_parts`, `*_ms`, `*_total`, `*_note`, `*_reason`, …). 433 emit keys,
  zero false positives today.
- **Routes.** `SessionBooks.describe` now refuses a `route` outside
  `KNOWN_ROUTES` at runtime, and the matrix test reads `route=` values from the
  syntax tree instead of a regex. The regex only saw `route="literal"`, so
  `route=route` — which `measurement.py` really does, on the mass-properties path
  — was invisible to it, and `route=f"box.{kind}"` would have been too. A regex
  cannot follow a variable; a runtime check does not have to.

### Changed — an undeclared argument is refused (breaking)

A call that passes a key the tool does not declare now fails instead of
succeeding with a number about something else. If you have been passing
`component=` to `catia_get_inertia`, it never worked — the number you got was the
active document's. Use `document="<name>.CATPart"`.

### Fixed — test files named one version behind

`test_reported_issues_2026_10_29_v0339.py` contained 0.3.40, `..._v0340.py`
contained 0.3.41, `..._v0341.py` contained 0.3.42. Renamed to their contents, so
"which version fixed this" is answerable from the filename again.

## [0.3.42] — 2026-10-30 · eight blades that were placed at 45° and never turned

`catia_circ_pattern` passed `False` in the last slot of `AddNewCircPattern` -
CATIA's "radius aligned" flag. That flag is what applies the angle to the
instance BODY; without it CATIA moves each copy to its angular position and
leaves it parallel to the seed. The volume is `count × seed` either way, so the
tool, its docs and its self-check all called it a success.

### Fixed — a circular pattern that only translated its instances

- Measured through the tool on B30, 8 copies of a 400 mm bar around X:

  ```
  radius_aligned=false   Y +-500   Z +-305     <- what shipped until 0.3.41
  radius_aligned=true    Y +-500   Z +-500     <- 8-fold symmetry, correct
  ```

- `Z +-305` is exactly `p → p + (R·c − c)`: the copies sit at 0/45/…/315° and
  each keeps the seed's orientation. Eight copies of one body at 45° must produce
  an 8-fold symmetric union, so the difference is not subtle - it is a fan whose
  blades point the wrong way, or a crank that does not crank.
- `radius_aligned` is now an argument, **default true**, and the reply states
  which mode produced the copies: "rotated with the pattern" or "placed at its
  angle but NOT rotated". `false` remains available for the case where it is
  right - a ring of identical bosses, where orientation carries no meaning.
  **This default is a breaking change**: a script that never passed the flag gets
  different geometry from 0.3.42 on, and the geometry it gets now is the one the
  call was always meant to produce. (Noted 2026-10-31 — the value changed in
  silence, which is the same mistake the flag itself had been making.)
- The tool description used to say the volume change "is the only trustworthy
  signal that the instances were actually built". That sentence is why this
  survived eleven rounds: the volume proves the count, not the orientation. It
  now says so, and names the bounding box of an asymmetric seed as the check.

### Note for anyone verifying this

A cube or a cylinder cannot tell the two modes apart: their envelope is the same
whichever way they are turned. The report's own `PatternProbe2` measured ±505 and
looked correct for that reason. Use a bar, an off-centre block, or anything
without rotational symmetry.

### Reviewed after shipment, and deliberately not changed

Re-verified 2026-10-31 against the scanner's two probe parts, independently:
`PatternProbe42` (default) gives bbox X ±5 / Y ±500 / **Z ±500**, inertia
Ixx 0.0330693 with **Iyy = Izz = 0.0165373**, all three products of inertia 0,
centroid (0, 0, 0), volume 320 000 mm³ - all three criteria hold.
`PatternProbe42b` (explicit `false`) gives Z ±305, reproducing the old envelope
digit for digit, which also proves the preserved behaviour is what it claims.

One suggestion came back with that check: put the expected bbox shortfall in the
`false` reply, along the lines of "Z will lack the 45° component". It is not
being done, and here is why, so it does not come round again:

- The number cannot be produced without knowing the seed's transverse extent,
  which `circ_pattern` does not measure. Getting it means one more envelope
  read (0.1-10 s depending on regime) added to the degraded path, for a branch
  the caller asked for explicitly.
- Whatever "the missing component" would say is already said, without the
  measurement: the `false` reply states the copies stay parallel to the seed and
  that the volume cannot show it. A tolerance printed in advance invites the
  reader to trust a number instead of looking at their own part.
- This layer is frozen. The rule agreed at 0.3.39 was: once two consecutive
  acceptance rounds bring no P1/P2 and every remaining finding is wording, stop
  shipping versions for findings of that kind. This round had none of either
  kind.

If a caller really wants the number, `catia_get_bounding_box` is one call away,
and it is the only answer that describes the part instead of predicting it.

### Measured

```
773 tests (3 added: the flag reaches the factory as True by default, the
translation-only mode says so, and the schema advertises both).

B30, one run, same document builder each time:
  shipped call (default)   Y +-500  Z +-500   correct
  radius_aligned=false     Y +-500  Z +-305   the reported defect
  radius_aligned=true      Y +-500  Z +-500   correct
```

`scripts/circ_pattern_probe.py` reproduces that comparison.

## [0.3.41] — 2026-10-30 · a scratch set that left the body out of work

0.3.40 said the fillet failures of 2026-10-29 were "not reproduced, probably edge
references rounded through 4-6 decimals". That was measured on a fresh document,
which is exactly the case that works. The 2026-10-30 report found the variable:
**any GSD write**. Both observations were true; the attribution in 0.3.40 is
withdrawn here.

### Fixed — `catia_fillet(edge_anchors=...)` failed in any document that had seen a GSD write

- Measured on B30 with one box and one `catia_gsd_point`:

  ```
  pure document          anchors CREATED    name CREATED
  after one gsd_point    anchors REFUSED    name CREATED
  after deleting it      anchors REFUSED    name CREATED
  ```

- The reference was never the problem. Adding a HybridBody leaves CATIA's
  `Part.InWorkObject` on the geometrical set, and a Part Design factory called in
  that state answers `AddNewSolidEdgeFilletWithConstantRadius 失败` about a good
  edge. The same reference builds the fillet the moment `InWorkObject` is set
  back to the body - measured one line apart.
- Why only the anchor path: it creates a scratch point set to measure distances
  (`_ProbePoints`), and creating that set is itself a HybridBody write. Deleting
  it does not move the in-work object back. In a pure document deleting the only
  set happens to fall back to the body, which is why the path looked healthy
  there.
- `_ProbePoints` now remembers what was in work when it was constructed and
  hands it back after the delete and the rebuild. A caller who deliberately put
  a datum plane in work gets that back, not the body.

### Fixed — a rejected single feature raised `NameError: name 'body' is not defined`

- `_safe_update`'s rollback line read a free variable `body` that no caller
  passed, so the first refused fillet/chamfer/mirror/shell/thickness/boolean
  raised a Python error instead of the "rolled_back: true/false" sentence - and
  the dead node stayed in the tree, which is the state that makes every later
  update of the part fail.
- `_remove_feature`'s docstring already promised to restore the in-work object
  after a rollback; the code did not. Both are now true, and the body is passed
  through from the fillet path.

### Not a defect — `catia_set_active_body` "does nothing the first time"

- The tool sets `Part.InWorkObject` and reads it back, and answers with a warning
  naming both values when CATIA kept a different one. The report's own note says
  a document switch happened between the two calls; no change made.

### Measured

```
770 tests (4 added: the probe set hands the body back, a foreign in-work object
is left alone, a rejected feature rolls back, the body reaches the rollback).

B30, one matrix run, same edge and same anchor each time:
  stage 1  pure document        anchors CREATED   name CREATED
  stage 2  + one gsd_point      anchors CREATED   name CREATED   (was REFUSED)
  stage 3  point deleted        anchors CREATED   name CREATED
```

`scripts/fillet_anchor_matrix.py` reproduces that matrix. It - and the other two
probe scripts - now assert the active document before every mutation, because
one run placed three fillets in an acceptance user's finished part: `catia_new_
part` does not reliably leave the new part active, and a mutation lands in
whatever is active instead.

## [0.3.40] — 2026-10-29 · a batch that forgot what it was told, a loft that added itself twice

The 2026-10-29 report was written against 0.3.39 by someone rebuilding a 12-part,
80-instance Strandbeest from a text specification. Nine findings. Four of them
were settled by one measurement each — reproduced on B30 in documents this
release creates and closes — and three of those four were code. The fourth is
recorded below as NOT reproduced, because "five fillets failed" and "fillets
always fail" are different claims and only one of them is true.

### Fixed — a `placements` batch dropped the rotation convention (report §1, P1)

- `catia_set_component_transform` read `rotation_convention` **inside** the
  `rotation_rows` branch, but the batch branch returned first. So
  `placements=[...]` plus a top-level `'basis'` applied every entry as `matrix`:
  the nine numbers went in transposed, the reply said `convention=matrix`, and
  nothing errored.
- 32 of 80 instances were placed wrong. The 48 mirrored instances hid it: a
  mirrored rotation is symmetric, and a symmetric matrix is its own transpose.
  Volume stayed correct to the digit, because a rigid transpose does not change
  a volume — only the per-instance centroid did, and the assembly centroid was
  off by ~38 mm.
- The convention is now read and validated **before** the batch branch, inherited
  by every entry that does not set its own, with `overridden_by` counted. The
  schema says so, and top-level keys the batch did not consume are named in
  `ignored_top_level_keys` instead of disappearing.
- The report's suggested fix — transpose in the `basis` branch — would have
  broken the single-call path, which was correct all along. Measured on B30 after
  the fix: two instances, `rotation_convention='basis'` at the top level,
  `inherited_by: 2`, local X reads back as `(0, 1, 0)` where `(0, 1, 0)` was
  asked for. Before the fix the same call stored `(0, -1, 0)`.

### Fixed — a two-section ruled loft failed with `AppendHybridShape 失败` (report §3)

- `_ruled_loft` appends each blend to the geometrical set as it builds it, then
  handed a **single** blend to `_finish`, which appends again. Appending an
  element that is already in a body fails with
  `方法 AppendHybridShape 失败`, which is the error the report measured twice.
- It only happens with exactly **two** sections: three or more end in a new Join,
  which is appended once. That is why the 2026-10-24 smoke test (three circles
  r=10/20/10) passed and a fan blade lofted from y=40 to y=540 did not.
- Measured on B30, two circles r=10 at z=0 and r=20 at z=100: before, `ruled=true`
  answered `方法 AppendHybridShape 失败`; after, the surface is created and the
  same pair still lofts smooth with `ruled=false`. Regression test asserts the
  geoset receives exactly one append for two sections and three for three.

### Added — `catia_delete_feature` reaches into geometrical sets (report §4)

- The search walked `body.Shapes` and `body.Sketches` only, so every GSD element
  answered "No feature or sketch named ...", stayed visible, and was measured by
  the STL envelope it was supposed to be out of.
- It now also walks `HybridBodies` depth-first — `HybridShapes`, `HybridSketches`
  and the sets themselves (nested sets included) — and reports the kind it
  deleted (`GSD element`, `sketch in a set`, `geometrical set`). A solid feature
  of the same name still wins, because that is the one a caller rebuilding a
  solid means.
- Measured on B30: `Deleted GSD element 'TMP_CIRCLE'.` and it is gone from
  `catia_list_features`.

### Changed — a closed spline says what its closure costs (report §5, §8)

- `closed=true` closes by repeating the first point. That is documented, but the
  consequence was not: the seam has no tangent continuity, so the curve can
  overshoot there, and a project + split + thick_surface chain built on it can
  leave a zero-volume sliver that **both** bounding-box routes then measure.
  The report's 81-point aerofoil did exactly that: volume correct to +0.085 %,
  envelope wrong by 2.8 mm, one 12.75 mm degenerate edge outside every source
  surface.
- The reply now says this at the moment the spline is drawn. The geometry is
  unchanged — a periodic spline is a different API, not a different flag.

### NOT reproduced — `catia_fillet` failing on every edge (report §2)

- The report read five failures across four parts and three radii as "systematic,
  not a usage problem". A fresh 40 x 20 x 20 box, R10 on its first edge, succeeds
  on B30 with 0.3.40 unchanged in that code path.
- So the failures are real but specific to those parts or those edges — most
  likely the edge references, since all four parts had sketches whose arcs had
  been round-tripped through 4-6 decimals (the report's §7 records open-profile
  failures from exactly that). Keeping this as a "known failure" would have sent
  the next reader looking for a bug in the fillet call that is not there.
- `scripts/b30_repro_v0339.py` repeats all four measurements; it creates its own
  documents and closes them, and never activates an open user document.

### Measured

```
766 tests (15 added).

B30, one run each:
  #1 batch basis        inherited_by 2/2, local X -> (0,1,0) == asked   MATCH
  #2 fillet R10 box     succeeded on Edge.1                            not systematic
  #3 ruled loft 2 sec   before: 方法 AppendHybridShape 失败 / after: created
  #4 delete GSD element Deleted 'TMP_CIRCLE', absent from the tree      ok
```

## [0.3.39] — 2026-10-29 · the acceptance table, done by a machine

This release adds no field. It deletes three implementations of one function,
renames a field that asserted a cause it never measured, and turns the
"which route is missing which key" table into a test. The previous eleven rounds
each closed one or two cells of that table — three fields across seven routes is
twenty-one cells, and every one was filled in by hand, which is why two people
were still finding one or two empty cells per round after eleven rounds.

### Added — one `regime` function, seven routes, and a matrix test that keeps it

- `catia_mcp/regime.py`: `SessionBooks.describe` returns the same six keys on
  every measuring route — `regime`, `after_cold`, `session_first_parts`,
  `parts_total`, `cold_reasons`, `elapsed_note` — including the two EMPTY
  branches, which published none of them.
- `tests/test_route_matrix.py` walks all seven routes (box: assembly / empty /
  single part; mass properties: assembly / empty / named part / active part) and
  asserts the key set, the arithmetic (`sum(cold_reasons) == session_first_parts`,
  `session_first_parts <= parts_total`), the cost closure
  (`parts_ms + overhead_ms == elapsed_ms`) and the three-key `overhead_breakdown`.
- It also reads the `route=` literals out of `measurement.py` and compares them
  with its own table, so a new branch cannot be added without a row. That is the
  seam the acceptance table rotted through: the code grew a route, the table did
  not.
- Measured on B30 through the server, one call each: box cold/warm/warm =
  `cold`/`warm`/`warm` with `after_cold` `null`/`true`/`false`; empty product =
  `not_measured` and it does not move the flag for the next real call; mass
  properties on the named part and on the active part answer the same six keys;
  closure exact on all seven (833.1 = 487.7 + 345.4; 394.0 = 141.6 + 252.4;
  9.8 = 0.0 + 9.8).

### Changed — `cold_parts` is `session_first_parts`, and each part says why

- The old name asserted a cause the count never measured: "these parts were
  cold". The 2026-10-29 run falsified it — 26 of 28 parts were **no slower at
  all** on their second reading (0.97×–1.13×), and the two that read 21×–26× were
  paying for an **STL export**, not for a build.
- `parts[].cold_reason` now says which JOB a part's first reading was:
  `topology`, `mesh_export` or `mass_properties`. Recorded at the moment of the
  reading, because a part's route is only known once its box exists.
- The cold note no longer claims "CATIA builds a part's internal representation on
  the first topology read". It says what the book counts, names the jobs in that
  count, and points at this call's own fields. Nothing in it is a multiplier, a
  range, or a cause.

### Fixed — a warm reply quoted a delta between two different scopes

- `elapsed_note` compared this call against the previous warm call on the same
  document, whatever either call had measured. A `component='P05.1'` narrowing
  (one instance) against the whole 70-instance assembly printed **−67 %**, which
  is the scope, not the machine.
- Each book now stores the scope with the warm time (component filter and instance
  count) and quotes a delta only between two calls of the same scope. Two
  different scopes get a sentence naming both. Measured on B30: the narrowed call
  after two full ones says "the previous warm call on this document measured a
  different scope (the whole assembly, 1 instance(s), against component 'REL38.1',
  1 instance(s) here), so no delta is quoted" — and contains no `%`.

### Fixed — every pre-execution rejection has one envelope (report B §5.1, §5.5)

- `catia_run_plan(steps=[])` raised `ValueError`, which the server turned into
  `COM_ERROR` plus "inspect the CATIA model before retrying" and a `recovery`
  block — for a plan that never opened a document.
- A **writer** whose arguments failed the outer schema got the same recovery block
  (`recovery=NO_RECOVERY if read_only else None`), so `catia_pad {}` was told the
  model changes were not verified. A validate-stage rejection happens before the
  first COM call, whoever the tool is.
- `catia_mcp.results.reject_validation` writes all of them: `ok: false`,
  `data.failure_stage: "validate"`, `effects.modified: false`, no `recovery`,
  `data.next_actions`. Measured on B30: unknown tool, `catia_pad {}`, empty plan,
  bad step args and bad `expect` all answer that shape, and a valid plan still
  runs (`PLAN_OK`) and still dry-runs (`PLAN_VALID`).
- `expect` is validated like `steps`. A value that is not a number is
  `INVALID_EXPECT` **before** the model changes, not `ASSERTION_FAILED` after it;
  an entry that is not an object used to be skipped silently, so a plan whose
  assertions were malformed answered `PLAN_OK` with an empty `assertions` list.

### Note — the tests that asserted wording were rewritten to assert facts

- Sixteen assertions across v0325–v0338 checked a sentence fragment in a source
  file ("324 ms and then 549 ms", "FIRST time in this ", `cold_parts`). Those are
  the same failure as the code: a contract bound to a phrase. They now assert the
  fields, the notes' properties (no multiplier, no unlabelled constant) and, where
  the sentence matters, that the one implementation contains it.
- `_MEASURED_PARTS`, `_part_times`, `_inertia_times`, `_last_call_was_cold`,
  `_last_inertia_cold`, `_warm_elapsed` and `_inertia_warm_elapsed` are gone.
  Session state is one `SessionBooks` at module level (`M.reset_session_books()`
  for tests), because two tool objects talking to one CATIA share a session and a
  second book answers "first time" about a part this server already read.

- 754 tests pass (37 new in `tests/test_route_matrix.py`), plus sixteen assertions
  in eleven older files updated — they required the field names and phrases this
  release removed.

## [0.3.38] — 2026-10-29 · one wrong name is one code, and the cost a narrowed call never reported

### Fixed — a wrong document name was a CATIA fault on the one tool about document names (report 1, P1)

- Four tools, one input (`document='NOPE37.CATPart'`), two answers: three said
  `DOCUMENT_NOT_FOUND` and `catia_activate_document` said **`COM_ERROR`** - and
  then appended the blanket paragraph *"CATIA reports most automation failures as
  a bare 'method failed' … check whether `in_work_object` is a geometrical set"*
  to a call that never touched a body and had already printed the list of open
  documents. The message said *"No open document named"*, the phrase table looked
  for *"matches"*, and the mismatch fell through to the bare-call default.
- The raise site now carries the code itself (`CodedError(code=
  "DOCUMENT_NOT_FOUND")`, `modified=False`, two next actions about the ARGUMENT:
  use a name from `catia_list_documents`, or open the file first). The phrase
  table gained `"no open document named"` as well - not because the contract
  should rest on wording (0.3.32/0.3.33 both fixed that mistake), but so a future
  raise site that forgets the code still cannot answer `COM_ERROR`.
- Measured on B30: all four tools answer `DOCUMENT_NOT_FOUND`; the reply has no
  `recovery` block, no `in_work_object` paragraph, and
  `data.next_actions` + `data.next_actions_note`.

### Fixed — two notes were glued together, and one of them contradicted the other (report 1, P3 / report 2, §6.4)

- A document that is both a component of an open assembly and one of two copies
  with the same name read:

  ```
  … open it with catia_open_document (or measure it by name with document=) 2 open
  documents carry this exact name …
  ```

  No full stop, and `document=) 2` reads like an argument. Worse, the first half
  ADVISES the name and the second half REFUSES it.
- Notes are now collected as sentences and joined; the component advice is
  dropped when the name is shared, because for that document only the path works.
  Measured on B30 with a real case (`REL38.CATPart` saved + a second unsaved copy,
  and used by `ASM38C.CATProduct`):

  ```
  used by an open assembly, so it may have no window of its own; if
  catia_activate_document refuses it, open it with catia_open_document. This exact
  name is carried by 2 open documents (CATIA names an unsaved document after its
  part number), so document='<name>' is ambiguous and is refused with
  DOCUMENT_NAME_AMBIGUOUS - pass this entry's path instead.
  ```

### Fixed — `parts_ms` said "these two terms" and had three (report 2, §6.1, P2)

- The part route's note described `parts_ms` as *"`measure_ms` reading volume /
  area / centroid, `tensor_ms` borrowing the activation"*, and three samples were
  **18.9 / 18.9 / 17.9 % short** of it (791.6 / 727.7 / 28.8 ms) - a stable gap,
  not jitter. Same failure as the four-key `overhead_breakdown` this project had
  just fixed, one field over: a caller who adds what the note names gets a number
  that disagrees with the field beside it.
- `parts_other_ms` names the remainder (main body, shape count, density
  arithmetic, building the reply) on **both** part routes, and
  `measure_ms + tensor_ms + parts_other_ms = parts_ms` closes to 0.1 ms. Measured
  on B30: `4432.4 = 101.6 + 4318.0 + 12.8`, delta 0.0.
- The window also moved: `tensor_ms` used to stop right after `_tensor_for`, so
  the work of explaining WHY the tensor is missing - which asks the connection
  which assemblies hold the part, walking every open product - landed in no field
  at all. That was most of the 18.9 %.

### Added — the assembly inertia route answers the same regime question (report 1, §5.1)

- `catia_get_bounding_box` has published `after_cold` / `cold_parts` /
  `elapsed_note` since 0.3.34-0.3.36; `catia_get_inertia` on an assembly - the
  route that reads every part's mass properties - published none of them, so "is
  this number cold or warm" had no answer on the route where it matters most.
- It publishes `cold_parts`, `parts_total`, `parts[]` (`part`, `instances`,
  `volume_mm3`, `area_mm2`, `elapsed_ms`, `first_measure`, and `cold_ms` /
  `cold_warm_ratio` once both regimes have been seen), `after_cold` and
  `elapsed_note`. The book is **separate** from the box route's: those numbers are
  the vertex sweep, these are mass properties, and a part whose box is warm can be
  cold here - dividing one by the other would produce a ratio that means nothing.
- Measured on B30, 10 instances of a 24 000 mm³ pad: cold 411.1 ms
  (`parts_ms` 138.8), then 371.0, then 371.1, with `after_cold` `null` → `true` →
  `false`, real per-part `cold_warm_ratio` 1.04 → 1.07, and
  `parts_ms + overhead_ms = elapsed_ms` exact on all three.
- An empty product says `after_cold: null` with a note that it is *not
  applicable* and does not move the flag for the next real call.

### Fixed — a narrowed call spent time it did not report, on a re-read it did not need (report 2, §3.1)

- `component=` narrows to one instance, and then asked "is the same part also
  nested in a sub-assembly?" by re-reading the WHOLE component list, poses
  included. On 70 instances that is a second full placement read for a question
  about structure - which is how a narrowed call came out **+31 % dearer than the
  full scan it replaced** (6 309.9 ms against 4 815.1 ms on one part).
- Two things were wrong, and the second is worse than the first: the re-read was
  unnecessary (`_top_level_sub_assemblies` asks each child whether it holds
  products - one call per child, no placement read at all), and it happened
  **after `elapsed_ms` had already been stamped**, so the reply was short by
  exactly the time it took. A call costing 1.31 s reported 1.02 s.
- Same-session A/B on B30, 10 instances, 6 calls each, alternating checkouts:

  | | 0.3.37 | 0.3.38 |
  |---|---|---|
  | wall clock | 1 110-1 314 ms | 934-1 078 ms |
  | spent after `elapsed_ms` was stamped | 247-288 ms | 93-118 ms |
  | reading the component list | 340-360 ms | 170-195 ms |

  On the report's 70-instance assembly the removed read is ~800 ms, which is the
  +31 % they measured; the tool now reads the list once.

### Changed — no multiplier without three sessions (report 2, §6.2 and §6.5)

- 0.3.37 replaced one session's `1.30x / 15.10x` with a range from the 0.3.36
  run. The next session falsified the range: all seven 12-cell parts read
  1.93x-3.70x, **above** its 1.81x ceiling, and P01 read 18.84x against 17.25x.
  Fourth time this pattern has appeared (0.3.34 `overhead_note`, 0.3.35
  `elapsed_note`, 0.3.36 and 0.3.37 `cold_warm_note`).
- `cold_warm_note` and the cold `elapsed_note` now quote **no** multiplier at
  all - not a range, not a version-tagged pair. They publish the mechanism and
  point at `parts[].cold_warm_ratio`, which is this call's reading. The rule the
  report proposed is adopted as the standard: **a range is published only if it
  holds in three independent sessions; otherwise mechanism + the field.**

### Fixed — an empty product got the cost block from one aggregator only (report 2, §6.3)

- `catia_get_inertia` answered an empty product with `parts_ms: 0.0`,
  `overhead_ms`, a three-key `overhead_breakdown` and `poses_route`;
  `catia_get_bounding_box` answered with `elapsed_ms` and nothing else - while the
  inertia note says *"the same three buckets `catia_get_bounding_box` publishes"*.
  The numbers are trivial (7.6 ms); the vocabulary is not, because a caller that
  reads the block had to branch on which tool answered.
- Measured on B30 (`v36_empty.CATProduct`): `parts_ms 0.0`,
  `overhead_ms 7.6`, `overhead_breakdown {7.4, 0.0, 0.2}` summing to it,
  `poses_route: one_call`, `after_cold: null`.

### Changed — the two ambiguity refusals share one formatter (report 1, §5.2)

- `catia_get_inertia` described a candidate as `'X' (never saved - no path to name
  it by)`, `catia_run_plan` as `'X'` - dropping the one fact the advice depends on
  ("pass the path instead" is impossible for a copy that has no path).
  `CATIAConnection.format_matches` now writes both sentences. Measured on B30:
  `'REL38.CATPart' at 'F://cat_bench//REL38.CATPart'; 'REL38.CATPart' (never saved
  - no path to name it by) (active)`.

### Changed — `poses_route_note`'s 303.3 ms has an owner (report 2, §6.6)

- It said *"in the 2026-10-28 acceptance run"*, which reads as the reporter's own
  run - the one where the same field read 166.5 ms (and 378.8 ms a day later). It
  now says "one B30 run on 2026-10-28" and quotes the other two readings as what
  they are: the same line, three machines, three numbers.

### Note — `tensor_partial_route` is from 0.3.24, not new (report 2, §5.4)

- It is documented under `[0.3.24]` ("replaces the seventh line of
  `tensor_reason` prose with a `{code, tool, shape, answers, does_not_answer}`
  object") and has been in every reply of that shape since. Nothing in 0.3.37
  added it; the previous report's harness did not reach the branch.

### Note — the warm number is not a baseline (report 2, §8.8)

- The report asks that this release's 16.2 s warm figure not be written into the
  CHANGELOG as the new baseline, because the same code measured 22.1 s one
  session earlier and the per-call resolver can account for ~10 ms of a 5 900 ms
  change. Agreed, and not written. What this release claims about performance is
  only what it measured itself: the narrowed route's removed re-read (table
  above), and nothing about the assembly's steady state, whose spread is
  session-shaped.

- 717 tests pass (12 new in `tests/test_reported_issues_2026_10_29_v0338.py`),
  plus two 0.3.32/0.3.34 assertions updated: they required the multipliers this
  release removed.

## [0.3.37] — 2026-10-28 · every document was its own twin, and the cost that was not per cell

### Fixed — the duplicate-name WARNING counted every document as its own twin (reported P1)

- 0.3.36 taught the resolver to refuse a name that means two documents, and then
  built a warning on top of it with a comparison that cannot work:

  ```python
  return [row for row in rows() if row["name"] == name and row["doc"] is not doc]
  ```

  `Documents.Item(i)` builds a **new Python wrapper** on every access, so `is` is
  false even for two handles to one document - measured on B30 and quoted by the
  reporter from B32: `Item(1) is Item(1) → False`, `Item(1) == Item(1) → True`.
  Every document therefore had one twin - itself - and one sentence was wrong
  twice: a part created under a name the session had never used was told
  *"Another open document is already called `SOLO36.CATPart` (1 of them)"*, and
  activating a unique name said *"2 open documents carry that name"* while
  `catia_list_documents` said 1 in the same reply.
- Identity now comes from one place, `CATIAConnection.same_document`, which
  compares through COM (`==`) and falls back to name+path only when the build
  refuses to compare. `_document_rows` marks the active row with it too - that
  line was already right, and having two definitions was the defect.
- Consequences: a warning appears only when there IS another document
  (`(1 of them)` for a pair), `catia_activate_document` counts the pair as 2, and
  a fresh name says nothing at all. Measured on B30 with a real pair
  (`v36_dup.CATPart`, one saved one unsaved): unique name → plain
  `Activated document: 'v36_part.CATPart'`; the pair → `2 open documents carry
  that name`; `catia_new_part('SOLO37B')` → no warning.

### Fixed — a read-only failure lost its `next_actions` (reported P2)

- `DOCUMENT_NAME_AMBIGUOUS` carries two next actions, and it is raised almost
  only by `catia_get_*` / `catia_measure_*` - which are read-only, and read-only
  failures publish no `recovery` block (0.3.34: a tool that cannot write has no
  model to repair). The block was doing double duty: it carried both the repair
  advice and the ARGUMENT advice, so suppressing it dropped the two sentences
  the CHANGELOG promised.
- When the block is suppressed and the raise site knows what to do, the actions
  now travel in `data.next_actions`, beside the `candidates` they describe, with
  `data.next_actions_note` saying why they are there. A tool that DOES write
  still gets them inside `recovery`; nothing is invented when the raise site said
  nothing.

### Fixed — `sum(overhead_breakdown.values())` over-counted (reported P3)

- 0.3.36 added `component_lookup_ms` INSIDE `overhead_breakdown` and said in the
  same release that *"the three terms still add up to `overhead_ms` exactly"* -
  true of the three, false of the dict, which now had four keys. A caller who
  followed 0.3.34's note and summed the block silently added 5.8 ms it should
  not have. Renaming the key would not have helped: `sum()` does not read names.
- It is published **beside** the block now (`reply["component_lookup_ms"]`, in the
  cost-first order), the block has exactly three keys again, and `overhead_note`
  says so in words: summing the dict is safe; the lookup is a part of
  `components_doc_ms`, so subtract it, do not add it.

### Fixed — one session's cold/warm multipliers were written as the fact (reported §6.1, third time)

- `cold_warm_note` quoted `1.30x on a 12-cell part, 15.10x on a 1207-cell part,
  measured in one session` - 0.3.35's run. The same model in the 0.3.36 run read
  1.15x-1.32x and 12.29x. Both versions and both ranges are in the note now, and
  it says out loud that any single multiplier is a reading, not a constant.
- This is the same failure as 0.3.34's `overhead_note` and 0.3.35's
  `elapsed_note`, in the release that fixed the second one. The rule that keeps
  being relearned: a number measured once is published with its version and its
  range, or not at all.

### Fixed — `catia_get_inertia`'s PART route had no cost account (reported §6.2)

- The assembly route has published `parts_ms` / `overhead_ms` /
  `overhead_breakdown` since 0.3.36; the part route published `elapsed_ms` and
  nothing else, which made "174.3 ms here, 2 117.3 ms there, same tool" a
  question the reply could not answer. Both part routes now publish
  `parts_ms`, `overhead_ms`, `measure_ms`, `tensor_ms` and an `overhead_note`,
  with `parts_ms + overhead_ms = elapsed_ms` closing exactly (measured on B30:
  173.4 = 173.4 for the active part).
- `tensor_ms` is the field that answers the report's question: on B30 a part
  reached by `document=` spent **10 210 ms of its 14 855 ms borrowing an
  activation that failed** - the cost was in the attempt, not the measurement.

### Fixed — `tensor_reason` accused an independent part of being a component (reported §6.3)

- One code, `no_own_window`, asserted *"this part is a component of an open
  assembly"* about every part whose tensor could not be read - including parts
  reached through `document=` that no assembly references, where
  `catia_list_documents` printed no `component_of` in the same breath. The
  advice was wrong in the same way: it sent the caller to look for an assembly
  that does not hold the part.
- Two codes now, decided by the reading the listing already prints
  (`CATIAConnection.component_usage`, one definition shared by both tools):
  - `nested_in_assembly`, naming the assemblies that DO hold the part;
  - `not_activated`, when none does - a focus refusal, fixed by
    `catia_activate_document`, not by looking for an assembly;
  - and `unreadable` when CATIA raised on the query itself.

### Fixed — the post-cold note cited a field that is not in the reply (reported §6.4)

- On the FIRST warm call after a cold pass the note ended *"compare it with the
  previous warm call quoted above"* - but that call is the session's first warm
  one, so the only numbers "above" were the other session's, which the same
  sentence had just told the reader not to use. Three cases now: no previous
  warm call at all (say so), a previous one that is not quoted here (say the
  number and that it is not a trend), or the steady state, where the delta IS
  quoted.

### Added — `walk_other_ms` is 50x smaller on the inertia route, and now says why (reported §6.5)

- Same field name, different job: `get_inertia` moves one centroid per instance,
  `get_bounding_box` moves each part's whole vertex cloud (9.4 ms against
  463-486 ms for one 70-instance assembly in the acceptance run). The inertia
  route's `overhead_note` says so, so the next reader does not conclude that one
  of them has been optimised.

### Performance — the regression was the name resolver, not the per-cell sweep (report 2, §3)

The report asked for a decision rather than a guess ("`k` went up is a fact, why
is unknown"), and gave three experiments. Two of them are done here.

- **The sweep is unchanged.** `git diff v0.3.35..v0.3.36` touches no line of the
  vertex-sweep path (the only measurement.py changes are notes, the narrowed
  lookup timer, and `_mesh_export`'s restore-by-object). A same-session A/B on
  B30 (`v36_part.CATPart`, alternating checkouts, 8 calls each, twice) measures
  the sweep itself at **38-40 ms on both** - identical within noise.
- **But `document=` calls DID get slower in 0.3.36, and by a fixed amount.** The
  duplicate-name check enumerates every open document and reads `Name` AND
  `FullName` for each. On B30 each late-bound property read costs 1.3-1.9 ms
  (`Item(i)` 0.82 ms, `+Name` 1.27 ms, `+FullName` 1.94 ms; 8 documents: full
  rows 28.5 ms, names alone 16.7 ms), so every `document=` call paid +28 ms on a
  call that used to take 56 ms. Measured A/B: 0.3.35 **56 ms**, 0.3.36 **80 ms**
  (+43 %), same part, same session.
- Fixed by reading the cheap thing first: `_document_rows(with_paths=False)`
  reads names only, and `document_matches` uses it unless the key looks like a
  path or the name is shared (both cases still get the full rows, and both are
  the rare answer). A/B after the change: **56 ms vs 66 ms** (+18 %), and the
  remaining 10 ms is one name-only enumeration - the price of never answering
  with the other model. The part routes pay it once per call, not per cell.
- Still unverified: the report's own +16.4 % warm figure. Its machine had 19 open
  documents (~40 ms of resolver per call), which cannot account for 3 259 ms.
  Their `f`/`k` separation stays unexplained, and this release does not claim to
  explain it.

- 705 tests pass (22 new in `tests/test_reported_issues_2026_10_28_v0337.py`),
  including one that pins the pywin32 behaviour the P1 came from: a collection
  that mints a fresh wrapper per access.

## [0.3.36] — 2026-10-28 · two documents can have one name, and a number you cannot tell apart is worse than an error

### Fixed — `document=` returned the OTHER model when two documents shared a name (reported P1)

- `_resolve_open_document` documented its own premise: *"A name is unambiguous"*.
  That premise was false, and this server is what breaks it - `catia_new_part` /
  `catia_new_product` set the part number, CATIA uses it as the document name, so
  calling either twice with one name leaves **two documents called
  `DUP35.CATPart`**. The resolver returned on the first hit, so
  `catia_get_inertia{document='DUP35.CATPart'}` answered **16000 mm³ for the
  24000 mm³ model that was in front** - no error, no hint, the name echoed back.
  The reporter nearly filed it as a regression of 0.3.28's sub-assembly work.
- Name resolution now lives on the connection, one definition for every tool
  (`_document_rows`, `document_matches`, `resolve_document`,
  `describe_matches`), and it **collects every match instead of stopping at the
  first**. Rules: a full-PATH match beats a name match (a saved document's
  `FullName` is the only thing that tells two same-named copies apart); one match
  is that match; anything else raises `DOCUMENT_NAME_AMBIGUOUS` with
  `data.candidates` (`document`, `path`, `active`, `saved`) and two next actions.
  Measured on B30: bare name → refused, listing both copies with paths;
  `document='F:\cat_bench\v36_dup.CATPart'` → 16000 (the saved one);
  `catia_get_inertia{}` right after → 24000 (still the document that was in front).
- `catia_measure_topology` had a second copy of the same loop; it calls the same
  resolver now and refuses with the same code.
- The tool that CREATES the duplicate says so: `catia_new_part` /
  `catia_new_product` compare the new document's name against every open document
  and warn at creation time, when renaming is still cheap. `catia_activate_document`
  says it activated the first of N same-named documents, and
  `catia_list_documents` flags each entry with `name_shared_by` plus a note
  pointing at the path.
- **Found while verifying the above, not reported:** `_tensor_for` and
  `_mesh_export` borrow the activation (the tensor only exists for the active
  document) and restored it **by name**. With a duplicate pair that restore
  activated the wrong twin - measured: the active document went from the unsaved
  `v36_dup.CATPart` to the saved copy after a named `catia_get_inertia`, so the
  NEXT no-argument call measured the other model. Both now keep the previous
  document as an OBJECT and re-activate that. `_tensor_for` also checks identity,
  not name, before believing that `Activate()` worked.

### Fixed — `after_cold` was a missing key, and its sentence pointed the wrong way (reported P2 ×2)

- The cold reply omitted `after_cold` entirely, so `reply["after_cold"]` raised
  KeyError on the one call where "which regime is this?" is the whole question -
  two fields away from the `poses_route` hole 0.3.35 had just closed, in the same
  object. It is three values now and always present: `null` (this call was
  cold), `true` (first call after a cold pass), `false` (steady state).
- The post-cold sentence asserted "read it as an upper bound" from one
  afternoon's numbers (0.3.34: +27.4 %; **0.3.35 measured -0.8 %, the opposite
  direction**). It now names both measurements, says the direction is session
  state rather than a property of this code, and stops calling it an upper bound.
  The quoted seconds carry their version, like `overhead_note` already did.
- The reply also publishes **this session's own difference**: the previous warm
  `elapsed_ms` for the same document, and the current call's delta against it.
  A number from another session is now a fallback, not the default.

### Fixed — per-part cold/warm ratio, because the aggregate is a weighted sum (reported observation)

- `parts[]` entries now carry `cold_ms` and `cold_warm_ratio` once both regimes
  have been seen, plus a note. On one unchanged model the aggregate read 4.65×
  (0.3.30), 7.6× (0.3.31), 7.24× (0.3.33), 9.11× (0.3.35) - not drift, a weighted
  sum: the acceptance run measured **1.30× on a 12-cell part and 15.10× on a
  1207-cell one in the same session**. The cold note says so and points at the
  terms instead of quoting the total as a property of CATIA.

### Fixed — the narrowed call's cost was labelled as "that one read" (reported P2)

- `components_doc_ms` for `component=…` was 303.3 ms on 70 instances - 33.5 % of
  the full sweep's placement cost for ONE pose, and 3× the 95-101 ms a single
  unfiltered read costs. The difference is matching the requested name against
  every component. `overhead_breakdown.component_lookup_ms` now times that
  separately (sub-bucket, so the three terms still add up to `overhead_ms`
  exactly), and the note says what it covers.

### Fixed — a customised failure no longer ends with the COM_ERROR sermon (reported P3)

- `SKETCH_NOT_FOUND` / `DOCUMENT_NOT_FOUND` / `NO_MATERIAL` got their own
  `next_actions`, but `message` still appended *"CATIA reports most automation
  failures as a bare 'method failed' … before reading that as a bad sketch, check
  the state above"* - directly below a line saying the sketch name does not
  exist, and for a document that is not open it pointed at a body the caller does
  not have. That paragraph is now only added when the failure came from COM, i.e.
  when the exception carries no code of its own. `Session state:` stays either
  way - it is a reading, not a diagnosis.

### Fixed — `recovery` on a read-only tool when it failed validation (reported P3)

- `is_read_only` was queried only in the `except` branch, so
  `catia_list_components{names_only: 'yes please'}` answered with a full recovery
  block ("inspect the CATIA model before retrying") while the same tool failing at
  execution answered with none. It is computed once, before the first failure
  path, and `UNKNOWN_TOOL` / `INVALID_ARGUMENT` publish no recovery block at all -
  nothing was opened, read or written.

### Fixed — an empty assembly named the wrong document, and the two routes disagreed (reported P2)

- `skipped[0].component` was `self._active_name()` - the ACTIVE WINDOW, so
  `EMPTY35.CATProduct` measured while `F35S.CATPart` was in front reported
  `skipped: [{component: 'F35S.CATPart'}]`, a document the call never mentioned.
  The placeholder is gone: an entry in `skipped` means "here is a component and
  here is why its box is missing", and an empty product has none. The reason
  lives in `empty_assembly` + `incomplete_because` (which names the MEASURED
  document) and `instances` is 0/0/0 on **both** routes - the box route used to
  say `total: 1` while `catia_get_inertia` said 0 for the same document in the
  same second. `instances.skipped == len(skipped)` now holds everywhere.
- `catia_list_documents` returns `[]` for an empty session instead of the string
  `"No documents open in CATIA"` - the same type-depends-on-data defect as the
  three listings fixed in 0.3.34.

### Fixed — three small fields (reported P3)

- `catia_list_features[].catia_type` was `shape.Type`, which answers `None` (no
  attribute) or a raw id (a hole came back as `0`) on this API - null on every
  feature of every part, forever. It now carries the host's own word for the
  feature (`凸台`, `盒体`, `Pocket`), i.e. the prefix the `type` mapping read, so
  the mapping can be audited instead of trusted.
- `sketches_note` described an `aliases` key that is absent from a reply with no
  rebuilt sketch; it now says the array appears only when a sketch has one.
- `catia_get_inertia` publishes `parts_ms`, `overhead_ms`, `overhead_breakdown`
  (`components_doc_ms` / `walk_other_ms` / `report_ms`) and `poses_route`, the same
  vocabulary as `catia_get_bounding_box`, so the two aggregators on one assembly
  can be compared. Measured on B30, 4 instances: `parts_ms 111.7 + overhead_ms
  136.9 = 248.6`, and `elapsed_ms 248.6`; the three buckets close to the digit.

### Changed

- `CodedError` carries `data`, and `failure()` merges it into the envelope's
  `data`, so a refusal can publish structure (`candidates`) instead of prose.
- `catia_list_documents` gains `name_shared_by` on a duplicated entry.

### Verified

- 683 tests pass (`pytest tests -q`), 30 of them new. (This line said 680
  when 0.3.36 shipped; the suite was already 3 tests further on.)
- On B30 2026-10-28, through the tool layer (`catia_probe/probe_v0336a`): the
  duplicate-name refusal and its path escape hatch, the creation warning, the
  empty product on both routes, `after_cold` null/true/false, the per-part ratio,
  the narrowed lookup, the inertia cost account, and the shortened message.

### Not fixed / not verified

- **The 70-instance `M113_asm_v21` model was not measured again.** Its `CATPart`
  files are B32 and this machine runs B30: `Documents.Open` raises CATIA's own
  file dialog and the assembly never loads. The report's 18.8-19.2 s warm timings
  are theirs, not reproduced here; the per-part ratios quoted above come from
  their run plus this machine's small assemblies.
- **`component_lookup_ms` is only measured on the narrowed path.** The unfiltered
  route does no name matching, so the field is absent there rather than 0 - and
  the three cost terms stay comparable across both.
- **`catia_type` is the localised prefix.** On a build whose UI language differs
  the string differs with it; `type` is the stable field, `catia_type` is the
  evidence behind it.

## [0.3.35] — 2026-10-28 · the third honest value, and a constant that read as the opposite

### Fixed — `poses_route` disappeared on the narrowed path (reported P1)

- `catia_get_bounding_box{component: 'H34P.6'}` shipped no `poses_route` at all:
  not `null`, not a value - the key was absent, so `reply["poses_route"]` raised
  KeyError on the one path that deliberately reads a single pose. And a caller
  that got `one_call` everywhere else would have read the narrowed call as
  "all seventy were batched", which is the opposite of what it did.
- It reads `"single"` now, with a note of its own ("this call was narrowed to one
  component, so exactly one placement was read … reading the rest is the
  per-instance entry fee 0.3.26 removed"). Three routes, three sentences:
  `one_call` / `per_component` / `single`. This is the rule 0.3.32 set for
  `sweep_route: mesh` - the third honest value, never a missing key.
- The route is captured right after the first component read, because a narrowed
  reply re-reads the component list (to find a nested twin) and that second read
  would have answered `one_call`.

### Fixed — `recovery.rollback_supported` was false next to `rolled_back: true` (reported P2)

- Both reviewers hit this. The field was a constant: no tool takes a rollback
  argument, so it was false in every reply, and next to `rolled_back: true` it
  read as "this failure could not be rolled back" - the opposite of what had just
  happened. A constant that carries no information and reads as a contradiction is
  worse than no field.
- It is `can_request_rollback` now (the name says what the constant means), and
  when there is a rollback story to tell (`rolled_back` is not null) the block
  carries `rollback_note`: "no tool in this server takes a rollback argument …
  'rolled_back' answers the different question of whether THIS failure already
  removed the feature it created".

### Fixed — `SKETCH_NOT_FOUND` still got the default "inspect the model" advice (reported P3)

- `catia_pad{sketch_name: '草图.99'}` answered `effects.modified: null` and
  "Inspect the CATIA model before retrying; model changes were not verified" for a
  call that only walked `Body.Sketches`. `NO_MATERIAL` had been given bespoke
  advice in 0.3.34; this one was missed.
- `CodedError` now carries `modified` and `next_actions` like `FeatureRejected`
  does, and both sketch lookups (`part_design`, `sketcher`) fill them:
  `modified: false` plus "pick a name from `Available` … this call created
  nothing, so nothing has to be undone".

### Fixed — a document that is not open is no longer `COM_ERROR` (reported P3, optional ask)

- `catia_get_bounding_box{document: 'NOPE.CATProduct'}` answers
  `DOCUMENT_NOT_FOUND`: "I typed the name wrong / it is not open" and "CATIA
  failed" lead to different next steps, and `COM_ERROR` said neither. Both raise
  sites carry it, and the phrase table knows it for anything that does not.

### Fixed — small items from the same report

- `catia_list_features` said `type: "unknown"` for `盒体.1` - CATIA's Chinese name
  for Shell was missing from the type table, so a shell built by `catia_shell`
  listed as nothing while the pad above it said `"pad"`.
- The `overhead_note` still said "90.5 % … five seconds" although
  `components_doc_ms` was 911.8 ms by then - the only sentence explaining where
  the overhead went described a number that no longer existed. It is version-tagged
  now ("in 0.3.33 and earlier …") and points at `poses_route` for this call.
- `elapsed_note` said warm calls "vary by a few percent" while the call right
  after a cold pass measured 23.6 % above the third one, on both 0.3.33 and
  0.3.34. The reply now carries `after_cold: true` on that call and says so:
  "read a single post-cold number as an upper bound, not as the warm cost".

### Changed — `error_type` in a plan step

- A failed step that this server refuses on purpose now reports
  `error_type: "CodedError"` (or `"FeatureRejected"`), not `"RuntimeError"`. Both
  are `RuntimeError` subclasses, so `except RuntimeError` keeps working, but a
  caller matching the string `error_type == "RuntimeError"` would stop. Called out
  here because the reporter asked for it.

### Measured, not fixed — the fixed/marginal split moved, and nothing in that path changed

- The report measured warm `parts[]` fixed cost `f` rising ~182 → ~243 ms while
  the marginal cost fell 3.2-4.2 → 2.7-3.7 ms/cell: small parts slower, big parts
  faster, net −5.6 %. Worth recording that **no code on the per-part reader
  changed between 0.3.33 and 0.3.35** (the sweep is byte-identical), so this is a
  session-shaped shift rather than a trade this release made. It is the same
  reason the docstrings no longer quote `f` and `k` as constants.

### Verified

- 651 tests pass (12 new in `tests/test_reported_issues_2026_10_28_v0335.py`).
- On B30 through the tool layer (`catia_probe/probe_v0335a`): narrowed call →
  `poses_route: single`; cold → no `after_cold`, first warm → `after_cold: true`,
  second warm → `false`; `SKETCH_NOT_FOUND` with `modified: false` and bespoke
  `next_actions`; `DOCUMENT_NOT_FOUND` on a missing document; `盒体.1` → `shell`;
  `overhead_note` free of "five seconds".
- Still not verifiable here: the reporter's B32 model (this machine runs B30 and
  cannot open those `CATPart`s), so the 20.0 s warm call remains their number.

## [0.3.34] — 2026-10-28 · one failure one code, and a listing that keeps its shape

### Fixed — a bare call flattened `NO_MATERIAL` into `COM_ERROR` (reported P0)

- 0.3.30 gave failures machine-readable codes so a caller could branch:
  `NO_MATERIAL` means change the offset or profile, `SKETCH_NOT_FOUND` means read
  `Available`. 0.3.33 kept them **inside `catia_run_plan`** and lost them
  everywhere else: the server derived the code from the Python exception type
  (`"UNSUPPORTED_CAPABILITY" if isinstance(error, AttributeError) else
  "COM_ERROR"`), while the planner read the message. One failed pad, two answers.
- There is one table now (`results.failure_code`), and the planner calls it. Order:
  the code the raise site declares, then the exception type, then the phrase table,
  then the caller's default (`COM_ERROR` on the bare path, `STEP_EXCEPTION` in a
  plan). The raise sites that know what they are refusing carry it:
  `FeatureRejected` for pad/pocket/hole no-material, `CodedError(...,
  code="SKETCH_NOT_FOUND")` for both sketch lookups.
- `tests/...v0334` asserts the two paths agree on the same exception object, which
  is the check the report asked for.

### Fixed — the `recovery` block contradicted the message (reported P1)

- `catia_pad` answering "so it was rolled back … Volume stayed 96750.0 mm3" shipped
  `recovery.rolled_back: null`, `rollback_supported: false` and "Inspect the CATIA
  model before retrying; model changes were not verified" in the same reply.
- `FeatureRejected` now carries `next_actions`, so the envelope says what the text
  says: `modified: false`, `rolled_back: true`, "change the sketch's offset or
  profile, then retry - retrying the same call IS the call that failed".
- A tool that cannot write no longer gets a recovery block at all: `is_read_only`
  covers `catia_get_*` / `catia_list_*` / `catia_measure_*` / `catia_diagnose` /
  `catia_export` plus six named exceptions, and their failures report
  `effects.modified: false` instead of asking a human to inspect a model nothing
  touched. `catia_get_bounding_box{document: '<not open>'}` was the example in the
  report - a lookup in the `Documents` collection that told the caller the model
  might be half-built.

### Fixed — a listing changed its TYPE when it was empty (reported P1)

- `catia_list_features` answered the bare string `"No features in the active body"`
  when a body held sketches and no feature - the exact moment its own description
  says to call it ("this is how to check whether a sketch still exists after a
  failed pad/pocket/hole"). `resp["sketches"]` raised instead of returning the
  caller's sketch.
- It always returns an object now (`count: 0`, `features: []`, the sketches listed,
  and a `note` explaining that an empty feature list is not a failure). Same for
  `catia_list_components` (the report called the pattern out) and for
  `catia_list_constraints`, which was worse: a bare LIST when full, a STRING when
  empty.

### Fixed — an empty product was described as "every component failed" (reported P2)

- `skipped: []` next to "each one's reason is in 'skipped'". An empty product now
  says "the active product holds no component, so there is nothing to measure -
  `complete: false` here means 'nothing was asked for'", and `skipped` carries one
  entry, the way `catia_get_inertia` already did for the same document.

### Fixed — the cost block did not add up (reported P3)

- `components_doc_ms + walk_other_ms + report_ms` was consistently 0.1-0.2 ms MORE
  than `overhead_ms` on every sample, in the one reply that promises to reconcile
  to the millisecond. `report_ms` is the remainder now, and if rounding pushed the
  sum past the total the excess comes off the largest bucket. Measured after the
  change: sum == `overhead_ms` exactly (0.0, three samples).

### Changed — `components_doc_ms`: 866 → 205-267 ms on 10 instances

- The report's P2 asked where the 5 seconds of a 70-instance call go, and the answer
  its own `overhead_breakdown` gave was "reading the component tree": 5 036 ms of
  5 563 ms. That is one `Evaluate` per instance for twelve numbers (measured here:
  86 ms each).
- `batch_positions` reads every child's 12 placement numbers in ONE script call,
  with a status byte per instance so a refused pose falls back to the
  per-component reader instead of reporting zeros. Measured on B30, 10 instances:
  `components_doc_ms` 866 / 851 / 868 → **267 / 253 / 205**. One instance is
  unchanged (95 → 101 ms): there is nothing to batch.
- The reply publishes `poses_route` (`one_call` / `per_component`) next to the cost,
  and `component=` narrowing still reads ONE pose - batching all of them would bring
  back the 5.8 s entry fee 0.3.26 removed.

### Changed — the docstring stopped quoting one afternoon

- `200 ms + 3.2 ms/cell` and `173.4 s against 22.9 s` were true when measured and
  wrong one release later (the same model measured 3.8-4.9 ms/cell and 183 / 25.9 s
  on 0.3.33), so a reader whose environment matched neither read the release as a
  regression. Both are ranges with version labels now, and the cold note quotes
  **this call's** `parts_ms` / `overhead_ms` / `elapsed_ms` instead of a constant.
- Warm `parts[]` was reported 12.6-17.3 % slower than 0.3.31. Not reproduced here:
  `git diff v0.3.31..v0.3.34` touches nothing in the per-part reader except
  publishing two fields, and on a 204-cell part the sweep route measures 1 348 ms
  against 2 398 ms for the per-cell route (one_call 1.78× faster, identical box) -
  so the ranking of the two routes did not flip at scale. Recorded as a measured
  "not reproduced" rather than fixed.

### Fixed — small items from the same reports

- `catia_run_plan` publishes `measurements_status` (`taken` / `not_taken` /
  `not_requested`); a stopping plan says why `measurements` is empty instead of
  leaving it silently `{}` (reported P3, the same hole `assertions_status` closed
  on 2026-10-27).
- `_remove_feature` restores `part.InWorkObject = body`. A pocket rollback left
  `in_work_object=Datum_xy_45`, which is the state this server's own error text
  warns makes the next Part Design call fail (reported P3). Fixing it in one place
  covers pad, pocket, hole, chamfer, draft and pattern.
- `catia_shell` reports the volume it added (`Volume 96000.0 -> 157500.0 mm3
  (delta +61500.0)`), like every other feature tool (reported P3).
- A rebuild in the caller's vocabulary now opens with the caller's name:
  "('草图.2' resolves to '草图.3', which CATIA just removed.)" - the third retry
  used to reply only about 草图.3 → 草图.4 (reported P4).

### Verified

- 638 tests pass (25 new in `tests/test_reported_issues_2026_10_28_v0334.py`).
- On B30 through the tool layer (`catia_probe/probe_v0334a/b/c`): 204-cell part,
  one_call vs per_cell with identical boxes; 10-instance product, the pose batching
  and the exact reconciliation of the cost block.
- Not verifiable here: the reporter's own model. Its `CATPart`s are B32 files and
  this machine runs B30, so `Documents.Open` raises CATIA's file dialog and the
  70-instance assembly cannot be loaded at all - the 5 s / 25 s figures remain
  theirs, and the batching gain above is measured on a 10-instance stand-in.

## [0.3.33] — 2026-10-28 · the remedies in the errors point at tools that exist

### Fixed — three error messages offered `catia_list_sketches`, which is not a tool

- This server serves 101 tools and `catia_list_sketches` is not one of them, yet
  three messages sent the caller there: `gsd.py` ("Run catia_list_sketches and use
  the name it reports"), and two in `part_design.py` (the delete-feature failure and
  the unknown loft section). The caller - an agent, most likely - followed the only
  remedy offered and got "unknown tool", in exactly the situation 0.3.31 had
  created: a sketch deleted by a rollback and rebuilt under a new name (reported
  2026-10-28).
- All three now name `catia_list_features`, whose `sketches[]` carries the rebuilt
  name and its aliases, and say which array to read.
- **There is a guard now**, because this class of defect is invisible to reading and
  to every earlier test: `tests/test_reported_issues_2026_10_28_v0333.py` scans every
  string constant in `catia_mcp/**/*.py` for `catia_*` and fails unless the name is
  a served tool (or a known structural name: the package, the VBScript entry points
  the sweep injects, the `catia_gsd_` / `catia_bbox_` prefix templates). Its first
  run found a fourth phantom nobody had reported: `catia_list_faces`' description
  told the caller to "use the same body_name in catia_drawing_add_view" - a tool this
  lite fork dropped. That sentence is gone.

### Fixed — GSD did not resolve a rebuilt sketch's old name

- `catia_gsd_spline(element='草图.2')` searched the tree verbatim
  (`hb.HybridSketches.Item(name)`, `bodies.Item(i).Sketches.Item(name)`), and
  `_sketch_exists` compared names literally, so after a rollback rebuilt 草图.2 as
  草图.4 it answered "No outline recorded for sketch '草图.2'" and then suggested
  three things that were all wrong: the sketch was in the tree, the record existed
  (under the new name), and nothing needed redrawing - one alias lookup was missing
  (reported 2026-10-28). This contradicted 0.3.32's own release note, which said
  every read path goes through the resolver.
- `_find_element`, `_sketch_exists` and `_outline_points` all resolve through
  `conn.sketch_alias` now. The outline record is looked up under both the old and
  the rebuilt name (a rebuild keeps the old record and copies it to the new key), and
  the success note says which name was used: "Corners taken from sketch '草图.2'
  (rebuilt as '草图.3')".
- Measured on B30 2026-10-28: `gsd_spline(element='草图.2')` → 4 points, spline
  created; then `pad` failed and rebuilt 草图.3; the **same call** then produced
  样条线.2 from "草图.2 (rebuilt as 草图.3)" instead of the "No outline recorded"
  error. `catia_sketch_get_geometry('草图.2')` answered `by alias '草图.3'` in the
  same state.
- The "no outline" message now also names the rebuild when there is one ("'草图.2'
  was re-created as '草图.3' after a rollback; no outline was recorded for either
  name"), instead of implying the caller never drew it.

- The guard found a second path while it was being written: `_section_reference`
  (a loft section) also looked names up verbatim. A rebuilt sketch is the same
  profile under a new name, so a section may now name it by either; the failure
  message names the rebuild when there is one.

### Verified

- 613 tests pass (`pytest tests -q`), 11 of them new - including the tool-name guard,
  which is the part that keeps this from coming back.
- On B30 2026-10-28, through the tool layer (`catia_probe/probe_v0333a`): the
  before/after of `gsd_spline(element='草图.2')` around a rollback, the alias read,
  the listing with aliases, and the error path with no phantom name.

### Not fixed

- **The guard reads string constants, not message templates.** A name assembled at
  runtime (`f"catia_{verb}_sketch"`) would slip through; none exists today (the only
  f-string prefixes are `catia_gsd_` / `catia_bbox_`, both allowlisted explicitly).
- **The loft-section path is covered by a unit test only** - the real-machine run
  exercised `gsd_spline(element=… )`, not `catia_loft` with a rebuilt section.

## [0.3.32] — 2026-10-28 · one meaning for a sketch name, and the cost no part owns

### Fixed — a rebuilt sketch's old name was only understood by Part Design

- 0.3.31 promised "'草图.2' now resolves to it". It resolved in `catia_pad` /
  `catia_pocket` / `catia_hole` and nowhere else: for the same live sketch,
  `catia_sketch_get_geometry` answered "No sketch named '草图.2' in the active
  body" — the one tool a caller reaches for after a rollback (reported 2026-10-27).
  There are now four methods on the connection — `sketch_alias`,
  `note_sketch_alias`, `sketch_aliases_of`, `sketch_scope` — and both tool groups
  delegate to them; `SketcherTools._locate_sketch` is the single entry every read
  and edit tool goes through, so "what does this name mean" has one answer.
- The table held **one hop**: `草图.2 -> 草图.3`. A second rollback deleted 草图.3,
  rebuilt it as 草图.4 and wrote `草图.3 -> 草图.4`, so the name the caller actually
  held stopped resolving on the third retry while the sketch was in the tree the
  whole time (reported: retry 1 and 2 answered NO_MATERIAL, retry 3 answered
  SKETCH_NOT_FOUND). A rebuild now re-points every entry that pointed at the lost
  name, so the caller's original name stays valid for the session *and* the table
  stays one level deep. Lookups still follow a chain when one exists (an older
  build's table, or a lost re-point), with cycle detection.
- Measured on B30 2026-10-28, three consecutive rollbacks, only ever naming
  草图.2: rebuilds 草图.3 → 草图.4 → 草图.5, all three retries resolved (each failed
  on the geometry, not on the name), and `catia_sketch_get_geometry('草图.2')`
  answered `found: "by alias '草图.5'"` with the profile's elements.

### Fixed — the rebuilt sketch says what it holds again

- 0.3.31 rebuilt the sketch from its record and then dropped the `It held: …`
  sentence that 0.3.30 printed on the way out, so the caller could not see the
  profile from the reply, could not read it from the sketch tools and could not
  find it in the listing (reported 2026-10-27). The rebuild path prints it now, and
  with a different tail — "the rebuilt sketch holds the same elements" instead of
  "catia_create_sketch + the same drawing calls rebuilds it", which told the caller
  to redo work this server had just done.

### Fixed — a name that works is a name that is listed

- `catia_list_features.sketches[]` entries carry `aliases: ["草图.2", …]`, and the
  `Available:` line of a `SKETCH_NOT_FOUND` prints `草图.6 (alias: 草图.2, 草图.3,
  草图.4, 草图.5)`. Until then the listing was the only authoritative place a caller
  could check "is my sketch still there", and a rebuilt sketch was missing from it
  while working perfectly (reported 2026-10-27).

### Fixed — `sweep_route` on the assembly route too

- `parts[]` published `points_route` (`batched` / `per_point`) and not
  `sweep_route`, although 0.3.31's changelog said both routes published it, so
  "read `sweep_route` to see which reader ran" was false exactly where it mattered
  (reported 2026-10-27). Every part entry now says `one_call`, `per_cell`, or
  `mesh` when no vertex sweep ran at all — the third honest value of the same
  field, rather than a missing key.

### Added — the cost that belongs to no part is published

- On the 70-instance assembly, `elapsed_ms` minus the sum of `parts[].elapsed_ms`
  was 5 394.6 ms — 23.6 % of a warm call, and larger than the most expensive part
  in the table — but it appeared nowhere, so a share-of-total analysis built from
  `parts` was short by that much (reported 2026-10-27). The reply now carries
  `parts_ms`, `overhead_ms` and `overhead_breakdown`
  (`components_doc_ms` / `walk_other_ms` / `report_ms`), all ahead of `components`.
- The breakdown answers the question the report could only guess at. On a 5-instance
  product measured here: `elapsed_ms` 1 308.1, `parts_ms` 856.8, `overhead_ms`
  451.3, of which **`components_doc_ms` 413.3** — the component-tree read, once per
  document, not per part. So the residual scales with the number of instances, and
  it is identical cold and warm for the same reason; it is one read, not 28 slices.

### Added — `warm_up`: pay the cold pass on purpose

- The first topology read of a part costs 4–7× the later ones, and it lands inside
  whichever call happens to come first — on the acceptance model 173.4 s against
  22.9 s for the identical call right after (reported 2026-10-27: "暖态砍 46.3 %，
  冷态只砍 12.3 %，冷热差拉到 7.6×"). `catia_get_bounding_box{warm_up: true}` runs
  the same measurement, throws the geometry away and returns what it cost:
  `parts_measured`, `cold_parts`, `elapsed_ms`, and per part `elapsed_ms` /
  `first_measure` / `cells_scanned` / `sweep_route`. Warm up after opening an
  assembly, then measure.
- Measured on B30: a 2-part / 5-instance product, `warm_up: true` → `cold_parts: 2`,
  1 345.9 ms; the next ordinary call → `cold_parts: 0`, 1 308.1 ms. On parts this
  small cold and warm are within 7 % of each other, so the saving shows on a real
  model, not on a toy one — stated here rather than claimed.
- The report also asked why the mesh box can be cached from `(size, mtime)` and the
  vertex box cannot. It cannot because the mesh box describes a **file** and is
  reused only for a part that is not open, while the vertex box describes the
  **live** part, whose state includes edits that were never saved; a file-keyed
  cache would answer for the part as it was on disk. That reasoning is in
  `warm_up_note`, in the tool description and in the README, so the next reader
  does not have to rediscover it.

### Changed — the numbers quoted in the cost notes are this release's

- The cold `elapsed_note` and the tool description said "194.7 s for the first call
  and 41.4 s for the next one". They now quote 173.4 s / 22.9 s, say the ratio
  **grew** from 4.65× because the warm side got twice as fast, and point at
  `warm_up=true` (reported 2026-10-27). The warm per-cell model is 3.2 ms/cell
  after 0.3.31's one-call sweep, not 9.5 ms.

### Verified

- 602 tests pass (`pytest tests -q`), 21 of them new in
  `tests/test_reported_issues_2026_10_28_v0332.py`.
- On B30 2026-10-28, through the tool layer (`catia_probe/probe_v0332a`): the three
  consecutive rollbacks above, the alias read, the alias listing, `It held:` on the
  rebuild path, `sweep_route` per part, `overhead_ms` reconciling to the millisecond
  (451.3 = 1 308.1 − 856.8) and `warm_up` turning `cold_parts: 2` into `0`.

### Not fixed

- **CATIA's first-read cost is still there.** 0.3.32 makes it schedulable and
  legible; it does not remove it, and the 173 s first call on the 28-part
  acceptance assembly would still be 173 s. What changed is that a caller can now
  ask for it separately.
- **`components_doc_ms` is attributed, not optimised.** It was 413 ms of the 451 ms
  overhead on a 5-instance product; cutting it is a separate job (it is one read of
  the product tree, and the reply now says so instead of calling it "per part").
- **The 70-instance `WB_CATIA_M113` assembly still cannot be measured on this
  machine** (all instances `part not loaded`), so the overhead figures quoted from
  that model are the report's, and the breakdown above comes from a product built
  here.

## [0.3.31] — 2026-10-27 · the bore goes where the profile is, and a lost sketch comes back

### Fixed — `catia_hole` drilled at the sketch origin, whatever the circle said

- CATIA's `Hole` feature takes the bore's axis from a **POINT in the sketch**, not
  from the circle's centre. A sketch holding one circle and nothing else was
  therefore drilled at (0, 0) - measured on B30 2026-10-27, circles at (0, 0) and
  at (10, 0) produced **bit-identical parts** (volume 15246.0178, COG
  (0, 0, −0.1236) in both), and a circle at (100, 100) on a 40 × 20 block drilled
  a bore through the middle of the block and reported success. The removed volume
  was exactly `pi*r^2*h` in every one of those cases, so nothing downstream could
  see it (reported 2026-10-27).
- When the sketch was drawn in this session and holds a circle with no point, a
  **construction point is added at the circle's centre** before the hole is built,
  and the reply says so. Measured after the fix: COG x −0.4945, removed-material
  centroid (10.000, 0.000, 2.500), `0.000 mm from the profile's circle`.
- A sketch that holds a point *away* from its circle - the shape that used to move
  the bore silently - is refused **before** anything is created: "holds a point at
  (0, 0) and a circle centred at (10, 0), 10.000 mm apart … Nothing was created."
- The bore's position is now **verified, not assumed**: the centroid of the removed
  material is recovered from the volume and centre-of-gravity readings taken before
  and after the cut (`V0·G0 − V1·G1 = v·g`), compared with the profile in the sketch
  plane, and a bore that missed is rolled back and reported. Amplification above
  500× (a nick in a large part) is arithmetic rather than geometry, so the check
  declines to accuse in that case instead of inventing a position error.
- The tool description no longer claims "The sketch supplies the bore's position."
  It states where the axis actually comes from, with the measurement behind it.

### Fixed — `depth` is measured from the sketch plane, and the reply converts it

- `depth` is applied through the hole's bottom limit, measured **from the sketch
  plane**. With the sketch on a datum plane offset away from the material - the
  route this server's own error messages recommend - `depth 5` in a 20 mm block
  drilled **15 mm**, and the reply said nothing (reported 2026-10-27). The reply
  now converts the removed volume into the length it drilled: "Bore length 15.00
  mm, not 5 mm: 'depth' is measured from the SKETCH PLANE, which sits 10.00 mm
  from the face the bore broke out of, so the two add up." The schema says it too.
- A bore that removes nothing on the far side of the sketch plane now lists that
  as a cause, and says `catia_hole` has **no reverse argument** because CATIA's
  `Hole` does not expose one (`DirectionOrientation` raises `Property
  'Item.DirectionOrientation' can not be set.` on B30), and points at `through_all`
  or a sketch on the face to be drilled.

### Fixed — a rollback that takes the input sketch rebuilds it from its record

- 0.3.30 disclosed that punch-back; the state after the error still differed from
  run to run, so `NO_MATERIAL` had two recoveries and a retry could answer
  `SKETCH_NOT_FOUND` (reported 2026-10-27). The sketch is now **re-created from
  this session's own drawing record** - same plane, same drawing calls, replayed
  through `body.Sketches.Add` - and the old name is aliased to it: "It has been
  re-created from its recorded definition as '草图.3' … and '草图.2' now resolves
  to it, so retrying the same call works." Measured on B30: after a failed pad,
  retrying with the **old** name answered `NO_MATERIAL` again, not
  `SKETCH_NOT_FOUND`; a second rollback rebuilt 草图.3 as 草图.4.
- The rebuild refuses out loud when the record cannot reproduce the sketch: no
  record (a sketch from a reopened file), a support that is not a datum plane, or
  an element whose start angle is not in the record (an arc). It never pretends.
- `catia_sketch_point` gained `construction` (default `false`), so a caller can
  supply the locating point itself and keep it out of the profile.

### Changed — one script call per part instead of one COM round trip per cell

- The vertex sweep now runs **inside CATIA**: one `SystemService.Evaluate` call
  enumerates the selection, applies the construction filter by NLS name and reads
  each survivor's coordinates, instead of paying a Python→COM round trip per
  topology cell. Measured on B30 2026-10-27, 60 cells with 28 holes:
  **827 ms one-call vs 1134 ms per-cell (−27 %)**, with identical boxes and the
  same 8 kept vertices. The per-cell reader stays as the fallback, and
  `sweep_route` (`one_call` / `per_cell`) publishes which one ran on both routes.
- A build that refuses CATScript still measures the same numbers: the script route
  falls back silently, and the tests assert both routes return the same box.

### Fixed — the cold/warm gap is now labelled on every route, and the cost is first

- A part's **first** topology read in a session costs 4–7× the later ones for the
  same work. That is CATIA building its internal representation, and 0.3.31 cannot
  remove it; it is now stated where the number is, on the single-part route as well
  as the assembly route: `first_measure: true` plus `elapsed_note: "this part's
  topology is being read for the FIRST time in this session …"`. Measured here:
  86.9 ms cold, 70.1 ms warm on the same block.
- The assembly reply is **ordered by cost**: `dimensions`, `min`, `max`,
  `complete`, `elapsed_ms`, `cold_parts`, `part_boxes_total`, `part_boxes`,
  `elapsed_note`, `document` … come before the 70 `components` entries, so
  truncation no longer eats the one field that explains the time.
- `nls_chars` travels with its counter-evidence on both routes (`nls_chars_note`:
  the 1.31× / 5.49× / r = 0.34 measurement that falsified "NLS length is the
  per-cell cost"), and the B30-specific constants are labelled as B30's rather than
  as properties of the model.
- The warm assembly note warns about jitter: "12 cells measured at 324 ms and then
  549 ms in one session", so one part's time is not read to three digits.

### Fixed — error codes come from the exception, not from the message

- `COMPONENT_NOT_FOUND` used to fire for `get_bounding_box` only, because the
  planner matched a phrase that one tool happened to use. A missing component now
  raises `ComponentNotFound` from all three raise sites (assembly, document,
  measurement) and the planner prefers `error.code`, so the same failure carries
  the same code whichever tool produced it.
- A plan that stops on the first error now says how much of it did not run: "1 of
  5 planned steps failed at step 2 (catia_pad); 3 step(s) were not run."

### Verified

- 581 tests pass (`pytest tests -q`), 30 of them new in
  `tests/test_reported_issues_2026_10_27_v0331.py`.
- On B30 2026-10-27: the hole position, the point-vs-circle refusal, the bore
  length sentence, the far-side sentence, the pad rollback rebuild and the retry
  with the old sketch name were all exercised through the tool layer
  (`catia_probe/probe_v0331d`, `v0331e`), and the sweep A/B and the single-part
  cost fields on real documents (`v0331i`, `v0331j`).

### Not fixed

- The 70-instance `WB_CATIA_M113` assembly could not be re-measured in this
  session: opening the product leaves all 70 instances unloaded
  (`part not loaded: ReferenceProduct failed`), so the box comes back
  `complete: false` with 70 `skipped` entries. That is the same on 0.3.29
  (`probe_v0329a`), i.e. a property of this machine's CATIA, not of this version -
  but it means the assembly-level cold/warm ratio in 0.3.31 is quoted from the
  report, not re-measured here.

## [0.3.30] — 2026-10-27 · the rollback took the sketch, so say it took the sketch

### Fixed — a failed pad/pocket/hole took the sketch, and one of them said it hadn't

- `catia_pad` answered "Your sketch '草图.7' is still in the tree - only the pad
  node was removed" while CATIA had removed the sketch together with the feature
  it was consumed by. The caller then read "changing the offset or the profile
  does" and had nothing left to change: the next call answered `No sketch named`,
  which reads like a typo in their own naming (reported 2026-10-27).
- The check behind that sentence had been answering True for every input since it
  was written: it walked `body.Parent.Bodies`, a Body has no `Parent` in the V5
  automation model, the fallback called `.Count` on a Python list, and the outer
  `except` returned "cannot tell, do not accuse" - as True. It reads `Part.Bodies`
  now and has three answers: present, gone, and *could not be read back*. Measured
  on B30: 5 of 5 rollbacks left no sketch, and 3 of 5 claimed otherwise; with the
  fix, 0 of 5 claim otherwise.
- When the sketch is gone the message says so, and echoes what it held from this
  session's own drawing record - `It held: rectangle (30, 15) .. (35, 25) on plane
  'xy' at offset 20 - catia_create_sketch + the same drawing calls +
  catia_close_sketch rebuilds it.` A rollback that cannot be prevented has to
  leave the caller something to act on.
- `catia_pocket` and `catia_hole` consume the sketch the same way and said nothing
  about it at all; all three now share one sentence (`_sketch_after_rollback`).

### Fixed — `component=` narrowing called a partial box complete

- `get_bounding_box(component='P26A.1')` matches TOP-LEVEL components only, and
  when the same part is ALSO installed inside a sub-assembly the reply carried
  `complete: true`, `instances: {total: 1}` and a `note_scope` saying "a
  sub-assembly is EXPANDED" - three statements, two of them false, in one reply
  (reported 2026-10-27). Same class as the 0.3.28 partial sum, one size smaller.
- The reply now finds the instances the narrowing left out: `complete: false`,
  `scope: "top_level_only"`, `also_nested: [{path, depth}]`, one `skipped` entry
  per nested instance, and `note_scope` that says the box covers the top-level
  instance only. The un-narrowed reply keeps the "expanded" wording, which is
  true there.

### Changed — the cost model has two regimes, and the reply says which one it ran in

- One acceptance run measured the same 70-instance assembly three times in one
  session: **194.7 s, then 41.4 s, then 41.5 s**, with `cells_scanned`,
  `vertices_used` and `nls_chars` IDENTICAL all three times. The work is the same;
  the first pass pays CATIA's own setup per part. Every per-part number published
  so far - including the "17-93 ms per cell" calibration of 0.3.27 - was a COLD
  number being read as a property of the part (reported 2026-10-27).
- `parts[].first_measure`, `part_boxes_total`, `cold_parts` and `elapsed_note` say
  which regime a call ran in. The single-part route publishes `first_measure` too.
  Warm the fit is `~200 ms + 9.5 ms per cell` (26 parts: 8.6-11 ms per cell); cold
  it is 16-90 ms per cell, and the description says both.

### Changed — `nls_chars` is a size, not a cost predictor

- 0.3.29 published it with "divide by cells_scanned … that is the filter's whole
  per-cell cost". The same report falsified that on 26 parts: `nls/cell` varies
  1.31x while `ms/cell` varies 5.49x (r = 0.34), and the part with the longest
  paths was the fastest. The claim is withdrawn; the field stays as a size
  indicator with the counter-evidence in its note.

### Added — a step error a script can branch on

- Every failed step in `run_plan` answered `code: "STEP_EXCEPTION"` with
  `error: "RuntimeError: …"` in front of a sentence that already explained itself,
  so "the sketch went with the rollback" and "the licence is missing" looked the
  same (reported 2026-10-27). The class name moved to `error_type`, and the
  failures this server raises on purpose get their own code:
  `SKETCH_NOT_FOUND`, `NO_MATERIAL`, `UNSUPPORTED_CAPABILITY`,
  `COMPONENT_NOT_FOUND`, `PART_NOT_LOADED`.

### Added — a failed plan reports the document's final state

- With `stop_on_error: false` and every step run, `measurements` was `{}` even
  when the later steps built and saved their part. It now measures the final state
  and says in `measurements_note` that `expect` was NOT evaluated against it.
  A plan that stopped at the failure still reports nothing - there is no final
  state to report.

### Added — `catia_list_features` lists the sketches

- 14 sketches in a body and `count: 3`, so "what is in the tree" could only be read
  side-on from the `Available:` list a failed pad prints - and that channel was the
  one lying about survival. Sketches now come back as `sketches` /
  `sketch_count`, not folded into `count` (they are not features).

### Changed — `get_inertia` publishes `elapsed_ms` on the part routes too

- The assembly route had it, the two part routes did not, so the field existed or
  not depending on which document was active (reported 2026-10-27). Same field,
  every exit.

### Fixed — the "Not covered" note in 0.3.29 named the wrong machine

- It said the 70-instance acceptance model "could not be re-measured here" because
  "this machine runs B30". True of the B30 dev box; the acceptance machine is B32
  and measured it completely. The B32 numbers are now recorded as the regression
  baseline they are: `get_inertia` 76 362 861.6798 mm3 / area 10 547 327.351 mm2 /
  COG (-419.2233, 41.6144, 275.4612) / `complete: true` / 70 of 70 instances, and
  the box `(-978.182, -210.526, -566.9) .. (141.608, 316.793, 1049.910)`.

## [0.3.29] — 2026-10-27 · a sub-assembly is not a hole in the box either

### Changed — the bounding box expands sub-assemblies too

- 0.3.28 taught `get_inertia` to expand a sub-assembly; `get_bounding_box` on the
  same document still listed it in `skipped` (`complete: false`, `kind:
  "sub-assembly"`). Honest, but it left a nested assembly unbounded and made the
  two aggregators differ in capability (reported 2026-10-27). The walk is
  recursive now: placements compose on the way down, every entry carries `path`
  (`ASM/PART.1`) and `depth`, and `assembly_depth_max` says how deep the reply
  went.
- A part is still bounded ONCE per identity, however many levels it appears at -
  the local box does not depend on the placement - so nesting adds no cost for a
  part that is used twice.
- Verified live on B30 with a two-level fixture: a plate at the origin plus a
  40x40x40 block inside a sub-assembly placed at (200,0,0) whose own instance sits
  at (150,0,0) returns `min (-50, -30, 0)` / `max (370, 30, 40)` - **bit-identical
  to the same block placed flat at (350,0,0)**, with `instances 2/2`, `part_boxes
  2`, `assembly_depth_max 2` and paths `Part1.1` / `Product1.1/Part2.1`.
- `skipped[]` entries now carry `path` and `depth` as well, so a branch that could
  not be expanded is named by where it sits, not just by its part number.
- `component=` still matches TOP-LEVEL components; the "no component matches"
  error now says that a nested instance has to be measured through its
  sub-assembly instead of reading as "this instance does not exist".
- Not covered: the acceptance model (`M113_asm_v21`, 70 instances) could not be
  re-measured here - it was written by CATIA **B32** (V5-6R2022) and this machine
  runs **B30** (V5-6R2020, release 30), which refuses its parts ("不能打开文件 …
  P12.CATPart"; the product opens, its 28 part links do not). The flat numbers are
  unaffected by construction - at depth 1 the composition is the identity, and
  `_mat_mul(I, A) = A` exactly - and the unit tests assert the depth-1 path.

## [0.3.28] — 2026-10-27 · a sub-assembly is not a part, and a partial sum is not a total

### Fixed — a sub-assembly was a partial sum wearing the assembly's name

- `get_inertia` on an assembly that holds a SUB-ASSEMBLY summed the components it
  could read and returned that as `volume_mm3` / `center_of_gravity_mm`: a
  two-level model holding 60 000 + 60 000 mm3 answered `60000.0` with the centroid
  at the wrong end, no `complete`, no count of what was left out, and a `note`
  that described the aggregation as if it were the whole. The same assembly's
  `get_bounding_box` said `complete: false`, so the two aggregators disagreed
  about what "complete" means (reported 2026-10-27).
- Root cause: `_components_doc` reached for `part_of(child.ReferenceProduct.Parent)`
  and a sub-assembly's parent is a ProductDocument. Fifth time this project read a
  Product as a Part (0.3.15 `add_component`, 0.3.20 `run_plan`, 0.3.25
  `list_components`, 0.3.27 top level) - this one was one level DOWN.
- The recursion composes the two placements and reports each leaf with a path, so
  `components` now adds up to the totals. Verified live on B30: a 100x60x10 plate
  (60 000 mm3, COG in its centre) plus the same plate inside a sub-assembly moved
  to x=100 returns `120000.0` and `center_of_gravity_mm.x = 50.0`
  (`assembly_depth_max: 2`, `instances: {2, 2, 0}`, paths `Part1.1` and
  `Product1.1/Part1.1`), identical through `document=` and through the no-argument
  route.
- `skipped[].error` for that case no longer says "part not loaded" - which sent the
  caller to `catia_open_document`, where opening the file changes nothing, because
  the component really is an assembly. It says "sub-assembly, not a part", names
  the two routes that work, and carries `kind`.

### Fixed — the totals now say whether they are the whole assembly

- `complete`, `instances: {total, measured, skipped}`, `incomplete_because` and
  `assembly_depth_max` on the inertia reply, the same vocabulary the bounding box
  uses. When anything is missing, `incomplete_because` says the number is a
  PARTIAL sum and names the alternatives; a 0 total is still `null` rather than 0.
- Surface area is aggregated too (`area_mm2` / `area_cm2`): it is additive for
  disjoint solids and it was missing from the assembly route entirely, although the
  tool description listed it as an output. Per component: `area_mm2` on each entry.
- One part is measured once, not once per instance: volume / area / COG are
  properties of the PART, so 70 instances of 28 parts pay 28 reads.

### Fixed — `get_inertia` published a different key set per call shape

- `volume_cm3`, `area_cm2`, `mass_g`, `density_kg_m3` and `mass_note` existed only
  on the active-part route; `document='<part>.CATPart'` returned the same numbers
  without them, so `r["area_cm2"]` worked in one call shape and raised KeyError in
  the other (reported 2026-10-27). Both routes now read through one function
  (`_measured_quantities`) and publish one key table.
- A quantity that cannot be read is `null` with `volume_error` / `area_error` /
  `center_of_gravity_error` beside it - never absent, and no longer the string
  `"unavailable: …"` in a field a caller compares as a number.

### Fixed — `catia_diagnose` now reports the session state messages point at

- Every `execute`-stage failure ends with "catia_diagnose reports both" and a
  `Session state:` line - while `catia_diagnose` returned six keys, none of them
  document, in_work_object, body or selection (reported 2026-10-27). It has a
  `session` block now: document, document kind, part, in_work_object, body, shape
  count, selection size, geometrical-set count, a one-line `line` form, and a
  `note` wherever a reading has no meaning (no document, or no Part open).
- Both presentations read the same function (`connection.session_snapshot`), so the
  line in an error message and the block in a diagnosis cannot drift apart.

### Changed — the mesh-cache claim states the condition it depends on

- "a part opened as its own document is exported in place every time" was not
  reproducible: an acceptance run opened a component, activated the assembly, then
  measured it and still got `(reused: …)` in 551 ms (reported 2026-10-27). The
  cause is that opening an ALREADY-LOADED component does not give it a window -
  `catia_open_document` activates it instead (0.3.26) - so the copy route, and its
  cache, still runs.
- The description now states the test the code actually makes (does `Activate()`
  change `ActiveDocument`), says that a part which does change it is exported in
  place and never cached, and says that `catia_open_document` cannot put a loaded
  component into that state. Same claim, one that can be checked.

### Added — `nls_chars`: what the construction filter actually costs

- `parts[]` and the single-part reply publish the total length of the NLS paths the
  construction filter read (one path per cell, free to collect - the string is
  already in hand). Two parts with 239 and 246 cells cost 66.4 and 30.5 ms per
  cell, so cell count does not explain the spread; path length is the candidate
  that does, and now the comparison needs no extra run (reported 2026-10-27, §3.4).
  The single-part route caps its sweep at 200 cells, so there it also publishes
  `nls_chars_cells` when the number is a sample.

### Note — two things this version does NOT do

- `get_bounding_box` still does not recurse into sub-assemblies. It keeps saying
  `complete: false` with `kind: "sub-assembly"` in `skipped` (honest, and unchanged
  behaviour), but the branch is still missing; `get_inertia` now does recurse, so
  the two aggregators differ in capability, not in honesty.
- P05 / P01 / P07 were not profiled here: the acceptance model they come from
  (`E:\WB_CATIA_M113\parts\`) is not on this machine. What was added instead is the
  number the comparison needs (`nls_chars`), and the fact that the three parts
  cost 66-92 ms per cell against 17-31 for the rest of the same batch is recorded
  in the README rather than explained away.

## [0.3.27] — 2026-10-27 · a component that was not looked for, and a cost that was not reported

### Fixed — `component=` was accepted and ignored when a Part was active

- `catia_get_bounding_box(component='P26B.1')` measured the ACTIVE PART and
  returned its box: no error, no warning, not even the name in the reply. A loop
  over instances produced N identical, plausible, wrong numbers — and the tool
  already refuses `method='mesh'` in Product mode because "a wrong number that
  looks plausible is worse than an error" (reported 2026-10-27).
- `component=` now looks the name up in the open assemblies when the active
  document is not one: one match is measured there and the reply says so
  (`measured_in: "'ASM.CATProduct' - the active document is not an assembly, so
  'X' was looked up in the open assembly that contains it"); two matches are named
  and refused rather than guessed; no match is refused with the active document's
  name and the two routes that work. Verified live: with a standalone part active,
  `component='drone body I001'` returned the drone body's 227×132×227 box (not the
  active part's 139.7×41.4×76.2) plus `measured_in`.

### Fixed — `elapsed_ms` on the single-part replies too

- The field existed only on assembly replies, so the route that costs 5.2 s for
  535 cells said nothing about its cost and a batch script could not find its slow
  part (reported 2026-10-27). All three part exits (named document, active part
  vertices, active part mesh) now stamp it: `elapsed_ms: 165.4` on a small part.

### Changed — `points_route` is present on every route

- The default single-part call (`auto` → mesh) published neither `points_route`
  nor any of the vertex counters, so "the batch worked" and "this build has no
  such field" were again indistinguishable - on the path most calls take. The mesh
  branches now report `points_route: "mesh"` with `mesh_vertices`, and every entry
  of `parts[]` carries `points_route` (`batched` / `batched_retried` /
  `per_point` for a vertex sweep, `mesh` for an STL extent).

### Fixed — `run_plan`: one grammar for failures, and a skipped `expect` says so

- A plan with ONE failed step reverted to "Step 6 (catia_fillet) failed" while a
  plan with two used "2 of 15 steps failed: …", so a script parsing the message
  had to handle both. One failure now reads "1 of 8 steps failed: step 6 (…)";
  a stopping plan keeps "Step 2 (…) failed", where the steps after it never ran
  (reported 2026-10-27).
- When a step failed, `expect` was skipped and the reply carried
  `measurements: {}` / `assertions: []` with nothing saying so — `assert
  r["assertions"][0]["pass"]` then raised IndexError, and `if r["assertions"]`
  read as "none were asked for". The reply now carries
  `assertions_status: "skipped"` with the reason, and the message ends
  "(assertions not evaluated: N step(s) ran, M failed)".

### Fixed — `get_inertia(document='<assembly>.CATProduct')`

- It reached for `.Part` on a Product and failed with "Active document 'X'
  exposes no 'Part'. Open or create a Part document first." — about a document the
  caller had just NAMED, with advice that is backwards for "measure the assembly"
  (reported 2026-10-27). A Product document is now measured through the same
  per-component path the no-argument call uses when the assembly is active.

### Note — the cost calibration was too optimistic

- The tool description said "about 15 ms per cell … a 500-cell part costs ~5 s".
  Measured on two acceptance models the per-cell cost rises with the part: 17 ms
  on a 24-cell plate, 41–49 ms on 220–246-cell parts, 93 ms on a 504-cell body
  (47 s), 70 ms on a 1 207-cell part (84 s, 38.8 % of a 70-instance call). The
  description and the README now give the range and the "cost concentrates in a
  few parts" structure that `elapsed_ms` makes visible, and the mesh-cache claim is
  scoped: it saves one export per curved part (≈10 s of a 204 s call when only 2
  of 28 parts are curved), not "the second call is much cheaper" in general.

## [0.3.26] — 2026-10-27 · a saved file's box does not change

### Added — the reply says what the box cost, and the docs say it before you wait

- `elapsed_ms` on the reply and on every entry of `parts[]`. The total says "this
  call was slow"; the per-part value says WHICH part - 1 207 cells and 24 cells
  differ by 40x, and the estimate alone does not say where the time went.
- The `catia_get_bounding_box` description and a README section ("What an exact box
  costs") now state the price: ~15 ms per topology cell on B30, so a 500-cell part
  is ~5 s, a 70-instance assembly is minutes (not seconds), and the server runs
  tools one at a time - nothing else runs meanwhile.

### Changed — a mesh box measured from a saved file is reused

- The copy route reads the SAVED file (it has to: CATIA refuses to export a part
  that is edited in context), so its answer is a function of that file. It is now
  cached under the file's identity - path, size, modified time to the nanosecond -
  and an unchanged file is not copied, opened, updated, exported and closed again.
  That lifecycle was the whole cost: 12-17 s per curved part on B30.
  Measured on the 24-instance drone assembly (5 curved parts): **129.5 s for the
  first call, 15.96 s for the second (8.1x)**, with `dims`, `min` and `max`
  bit-identical. Per part, the curved ones went 11.9-16.9 s -> 44-564 ms.
- Only the copy route is cached. An in-place export may include unsaved edits, and
  no file identity describes those, so that path is measured every time.
- The reply says which happened: a reused box carries "reused: the same file was
  already measured in this session and its size and modified time have not
  changed" in its `source_note`.
- Bounded and conservative: 48 entries, vertex clouds kept only up to 50 000
  points, and a truncated cloud is never stored. A cached entry that has no cloud
  cannot answer a world-box request - falling back to its two corners would be the
  diagonal bug of 0.3.23, so that request goes back to the file instead.

### Fixed — `open_document` on a file CATIA already holds

- `catia_open_document` called `Documents.Open` unconditionally. For a file that is
  already open CATIA raises "the document is already open, reopen?" - a modal on
  the automation thread, and its **Yes reloads the document, discarding unsaved
  edits**. Nobody answers a dialog raised there, so the call never returns
  (measured 2026-10-26: a probe sat on it until it was killed at 300 s). It now
  finds the open document by path (the helper `save_document` already used),
  activates it, and says so instead of asking the question.

## [0.3.25] — 2026-10-26 · a part is not an assembly

### Fixed — "Open assemblies" listed .CATPart files as assemblies

- `catia_list_components`'s refusal on a Part document offered the caller a list
  of "Open assemblies" built by probing `.Product` - which a `PartDocument`
  answers with a stub. On a session with 16 documents it printed **13 .CATPart
  files as assemblies**, and following that advice (`catia_activate_document`)
  gets refused again (reported 2026-10-26). It now uses `_is_product_document`,
  the type-based judgement `add_component` has used since 0.3.15 - the same
  question answered twice in one file, once right and once wrong.

### Fixed — `occurrence` meant two different numbers

- In a full listing `occurrence` was the component's position in the assembly
  (1..70), while `{"part_number": ..., "occurrence": n}` resolved against the
  ordinal within that part. The 3rd component overall was labelled occurrence 3,
  but resolving `P02_motor` with occurrence 3 found the THIRD motor, not the one
  listed (reported 2026-10-26). `occurrence` is now per part everywhere, `index`
  keeps the assembly position (which is what a bare index resolves against), and
  the reply states `occurrence_scope: "per_part"`. Verified on a 24-instance
  assembly: motors are 1..4, and occurrence 2 resolves to exactly the instance
  listed as occurrence 2.

### Changed — the per-part cost model is published per part

- `vertices_used` / `cells_scanned` are properties of the PART, and they were
  repeated on every instance entry: 70 entries carried 28 distinct values, 20 of
  them duplicated ten times over (reported 2026-10-26). They now live in
  `parts[]` (one entry per part, with `instances`, `local_dimensions`, `source`
  and, for curved parts, the mesh caveat); instance entries keep the geometry.
- `cells_scanned` also says what it counts: topology cells returned by the vertex
  search **before** construction geometry was filtered out (sketch-wire vertices,
  datum-plane corners). It is not a mesh grid count - an acceptance report read it
  as one, because the field name did not say.
- `points_route` is now published on the happy path (`"batched"`), not only when
  the reader degraded to `"per_point"`: "the batch worked" and "this build has no
  such field" used to look identical. It appears on the single-part branch too,
  next to `vertices_used` and `cells_scanned`.

### Fixed — `component=` no longer reads every placement to answer about one

- `catia_get_bounding_box(component='P24.1')` built the component list with all
  its placements before filtering, so a one-component question on a 70-instance
  assembly paid ~70 pose reads (~5 s on B30, and exactly the "5.8 s per part
  entry fee" a report measured on a 16-vertex block). `_components_doc` now
  filters by name/part number before the pose is read - the same rule
  `list_components` has followed since 0.3.22. Measured: one pose read instead of
  70.

### Note — where the remaining per-part time goes (measured, B30)

- A 535-cell / 335-vertex part: `_local_box` takes **5.2 s**, of which the vertex
  search is ~1 s, the construction filter ~4 s (`Selection.Item` 2.9 ms +
  `Value.Name` 9.6 ms + `Reference` 2.5 ms per cell) and the batched coordinate
  read 0.4 s. That cell loop is the "entry fee" the acceptance reports keep
  measuring; it is not the vertex count and it is not a mesh step. Cutting it
  further means either reading the NLS path only for cells near an extreme or
  bounding from the mesh - both change what "exact" means, so neither is done
  here.
- **Two calls on the same assembly are not the same measurement.** One 24-instance
  drone assembly: 130.0 s right after opening it, 94.4 s on an immediate second
  call (1.38x). Per part, the difference is concentrated: `drone body` (535 cells)
  36.1 s cold / 5.9 s warm (6.2x), the small parts 1.2-2.1x - the first sweep of a
  part pays for its update.
- **In an assembly with curved parts, the mesh fallback costs more than the vertex
  work.** Those parts are in context, so each is copied to a temp file, opened,
  updated, exported to STL and closed: 12-17 s per part, and warming does not help
  (5 parts = 74.2 s of that 94.4 s; the 6 vertex-bounded parts are 13.1 s
  together). `include_curved: false` is the cheap route when `complete: true` is
  not needed - it reports those instances in `skipped` instead of measuring them.

## [0.3.24] — 2026-10-26 · a diagonal is not a box

### Fixed — a mesh-bounded part was being rotated as a diagonal, not as a part

- The mesh fallback took the local box's **two opposite corners**, transformed
  those two points and used their extent as the instance's world box. A diagonal,
  rotated, is a line: its extent across the rotation is almost nothing, so a disc
  whose true world box is ±72.5 in x and y came back as **±100.29 / ±21.32** - and
  because one axis grew, the aggregate min/max still looked like a full cover with
  `complete: true`, `skipped: 0` and no warning (reported 2026-10-26). A part with
  an identity placement was unaffected, which is why it survived the previous
  round's checks.
- The mesh route now returns the **vertex cloud** (`_mesh_cloud`, capped at
  200 000 vertices per part, ~1.2 MB flat) and `_world_extent` transforms every
  vertex, per instance, without materialising points. On the 24-instance drone
  assembly one faceplate's world box shrank from a 26.57 mm to a 19.21 mm x-span -
  the previous number was 38 % too wide - while the assembly envelope stayed
  bit-identical (it is driven by vertex-bounded parts).

### Fixed — the STL export no longer leaves a modal "Save As" on the desktop

- `ExportData` is a save, so CATIA ran its file-alert path: every mesh
  measurement put up a modal "另存为 / 保存操作失败：导入的数据位于会话中…" and, with
  nobody to answer it on the automation thread, **blocked the call for 89.4 s**
  (reported 2026-10-26, with the raw-COM control: `DisplayFileAlerts = False` → 0
  dialogs, 0.00 s, same 16 071-byte STL). Since 0.3.23 the mesh route runs once per
  curved part of an assembly, so an occasional annoyance became a guaranteed one -
  in the reporter's words, "按默认参数调一次包围盒，CATIA 里就会留下一个没人点的框".
- Both export sites now go through `_export_stl`, which sets `DisplayFileAlerts =
  False` and restores it in `finally` (the same switch `add_component` has used
  since 0.3.9). The temporary STL also gets a unique name. Measured on B30:
  export 13.1 ms, zero #32770 windows before and after, flag still `True`
  afterwards. B30 does not raise the dialog at all - it refuses in place and writes
  0 bytes - so this fix is only observable on B32, where the report measured it.

### Changed — the assembly vertex route reads its points in one call, and says when it cannot

- `_local_box` was still on the per-vertex reader: 0.3.23's batch reached the
  single-part route but not the assembly one. Measured on B30, 335 vertices:
  **27.3 s one at a time, 424 ms batched** (60x, not the 6x that 8 vertices
  suggested).
- The batch is retried once before falling back, and the route is reported: an
  entry whose points came one-by-one carries `points_route: "per_point"`. The
  silent fallback is how a 60x slowdown once went unnoticed - 335 references took
  27.3 s right after an assembly opened and 424 ms on a warm session, with nothing
  in the reply to tell the two apart.
- Vertex entries now carry `cells_scanned`. The remaining per-part cost is the
  construction filter, not the vertices: on B30 each topology cell costs ~15 ms
  (`Selection.Item` 2.9 ms, `Value.Name` 9.6 ms to read the NLS path,
  `Reference` 2.5 ms), so a 535-cell body pays ~8 s before a single coordinate is
  read. That is the "6 s per part entry fee" the acceptance report measured on a
  16-vertex block. Whole assembly: 137.9 s → **106.8 s** for 24 instances
  (6 vertex-bounded parts, 5 mesh-bounded).

### Changed — route counts are numbers, and `mesh_fallback` counts parts

- `part_boxes_by_vertices` / `part_boxes_by_mesh` state the split that used to
  live only in the `bounds` sentence, so "this assembly should have 2 curved
  parts" is now an assertion instead of a parsing job.
- `mesh_fallback` lists **parts** (unique) and `mesh_fallback_instances` gives the
  instance count: it used to list instances while `warnings` listed parts, and
  6-versus-2 in one reply read like a defect.
- `tensor_partial_route` replaces the seventh line of `tensor_reason` prose with
  a `{code, tool, shape, answers, does_not_answer}` object - nine identical
  paragraphs in a nine-part sweep was the second-longest block in the reply.

### Fixed — `list_components` on a Part says no assembly is active

- A `PartDocument` has a `Product` (the part's own tree node) whose `Products`
  collection is empty, so asking it for components answered "No components in the
  active assembly" - which reads like "the assembly is empty". It now says which
  document is active, that a component list needs a Product document, and which
  assemblies ARE open (reported 2026-10-26).

### Note — `by_reason` keys need re-baselining

- 0.3.23 renamed one fall-through value: `construction_borderfvertex` →
  `construction_border_f_vertex` (CATIA's letters `BorderFVertex`, our missing
  separators). Every explicitly named reason is unchanged, but a stored comparison
  table keyed on the old string will not match (reported 2026-10-26 by an
  acceptance harness that keeps one).

## [0.3.23] — 2026-10-26 · a box that covers the curved parts

### Fixed — an assembly box now bounds curved parts instead of skipping them

- `catia_get_bounding_box(document='<assembly>.CATProduct')` took
  `include_curved` (default `true`): a part with no vertex at any extremum is
  bounded from its STL mesh instead of being left out, so `complete: true` now
  means every instance is inside the box. Measured on the 24-instance drone
  assembly (B30, 2026-10-26): `complete: true`, 11 of 24 instances came from
  the mesh, 137.9 s. Before this, those 11 instances were `skipped` and the box
  was a lower bound in six directions.
- A mesh-bounded instance says so in its own entry - `source: "mesh_fallback"`,
  `source_note`, and `mesh_vertices` - and the reply lists it in
  `mesh_fallback`. `bounds` distinguishes the two cases: "lower bound: each
  component's box came from its vertices…" or "mixed: N instance(s) are
  bounded from their mesh…".
- The mesh route returns two extent corners, so the entry used to publish
  `vertices_used: 2` for a 474-vertex part. `vertices_used` now exists only for
  a vertex-bounded entry; a mesh entry reports `mesh_vertices`.
- The "measured from a copy of the saved file" warning is repeated once per
  PART, not once per instance (11 identical sentences -> 5), and no longer
  starts with the `"; "` it was written to be appended with.
- `incomplete_because` and `method` no longer tell the caller to "place the box
  with `catia_get_component_transform`" - that tool READS a placement. Both now
  say to read the placement (`catia_list_components` /
  `catia_get_component_transform`) and apply the rotation/translation yourself.

### Changed — a vertex sweep is one CATScript call, not one per vertex

- `Measurable.GetPoint` only answers through `SystemService.Evaluate`, so a
  vertex sweep paid one round trip per vertex. `read_measurable_points` batches
  the whole list into a single script call: measured on B30 (2026-10-26), 8
  vertices took 605 ms one by one and 98 ms batched (6.2x, identical
  coordinates).

### Fixed — a plan that kept going now names every failed step

- `run_plan{stop_on_error: false}` marked each failed step in `data.steps` but
  the top-level `message` named only the last one, so a 5-step plan with two
  failures read as one failure wherever `message` is the only field that
  reaches a log. Measured after the change: "2 of 3 steps failed: step 2
  (catia_activate_document), step 3 (catia_activate_document)". A stopping plan
  keeps the plain "Step 2 (…) failed", because the steps after it never ran.

### Fixed — `names_only` no longer names a rotation it did not read

- `list_components{names_only: true}` put `"convention": "matrix"` on every
  entry next to a rotation that was deliberately skipped. The key now exists
  exactly when a rotation does, at both levels, and the reply carries a `read`
  line saying what was omitted. Measured on the 24-instance assembly: 94.4 ms,
  entries are `index / name / part_number / occurrence` only.

### Fixed — `by_reason` keeps CATIA's letters and adds the separators back

- `Selection_BorderFVertex` fell through to the generic branch and produced
  `construction_borderfvertex`, which a report read as a typo (the extra letter
  is CATIA's, the missing underscores were ours). The fall-through now splits
  camel-case: `construction_border_f_vertex`. Every explicitly named reason
  (`sketch_wire_edge`, `datum_plane_border_edge`, `datum_plane_corner`, …) is
  unchanged, so existing diffs keep working.

### Fixed — a deferred close says where the focus went

- `catia_close_document` on a part that an open assembly references returns
  `closed: false` and now adds `active_document_now: '<assembly>'` - "which
  document will the next tool act on" stopped being answerable from the reply
  once the close was deferred.
- `catia_activate_document`'s refusal no longer asserts "no window of its own"
  about a part that has one. Measured on B30 the same day: `Application.Windows`
  answers and its caption is the document name without the extension (`'Part1'`
  for `Part1.CATPart`), while `Document.Windows` raises `Add.Windows` - which is
  what an earlier probe had concluded "not exposed" from. The refusal now says
  either "HAS a window of its own, so this is a foreground/focus refusal" or "has
  no window of its own", and falls back to "CATIA reported no usable window
  list, so the cause cannot be told apart" when it cannot tell. Captions are
  compared whole, never as substrings (`'P1'` must not match `'P10_prop'`).

### Fixed — `new_part` says which name a plan has to assert on

- `catia_new_part(name=…, file_path=…)` now ends with "expect.document (and
  every document= argument) must use the FILE name '<file>.CATPart', not the
  part name '<part-number>'" - two round trips in a 2026-10-26 acceptance run
  went into discovering exactly that.
- `get_inertia`'s `tensor_reason` ("no own window") now also names the route
  that does work without opening anything: a plan's `expect` can name the
  document and borrows the activation itself, which answers volume, area and
  centroid (not the tensor).

## [0.3.22] — 2026-10-26 · a pattern that keeps its reversal

### Fixed — a reversed pattern direction is not a left-handed axis pair

- `catia_rect_pattern` decided handedness with
  `(x,y)|(y,z)|(z,x)` AND `reverse_dir1 == reverse_dir2`. Reversing ONE
  direction therefore looked left-handed, the tool swapped the two slots, the
  reversed axis landed in dir2, and `AddNewRectPattern` refused it at update -
  so the two recommendations the tool itself prints ("prefer dir1_axis",
  "set reverse_dir1 to flip that side") could not be combined. Reported
  2026-10-26 with a full matrix; reproduced on B30 the same day:
  `reverse_dir1` alone builds (-1 005.31 mm3), `dir1_axis:"x" + reverse_dir1`
  was rolled back, reversing BOTH sides built again.
- Only the axis pair now decides handedness; the swap stays for genuinely
  left-handed pairs, with counts, spacings and reversals travelling with their
  slot. Re-measured after the fix: `dir1_axis:"x" + reverse_dir1` -1 005.31 mm3,
  `dir1_axis:"z" + reverse_dir1` -502.65 mm3, `(x,z)` still swapped.

### Fixed — the topology counter can no longer answer two numbers for one part

- `catia_measure_topology` kept its own copy of the construction filter beside
  `catia_list_edges`. It now calls the same enumeration, so a count, an
  `Edge.N` and an edge fingerprint always describe the same set - and the
  exclusion breakdown is reported per reason (`by_reason`) instead of a single
  total.
- An enumeration that makes almost everything look like construction (1 of 20
  cells) is repeated and the larger answer kept, flagged as `edges_retried` with
  a sentence saying which reading to distrust. Reported 2026-10-26: the same
  untouched part answered `edges 1` and then `edges 12` in one session, which
  makes `expect.vertices` unusable as a gate. Verified after the change: five
  consecutive counts on one part, all `6 / 12 / 8`.
- A `Selection.Item` failure is retried once and then reported as what it is:
  "the SELECTION being unavailable, not the model having no edges: a modal
  dialog (often a '闭合' prompt raised while a sketch is still in edition) can
  hold it". It used to read like a broken model.

### Fixed — an assembly can be measured by name, and each part only once

- `catia_get_bounding_box(document='<assembly>.CATProduct')` works. The world box
  never needed the assembly to be active - it is arithmetic on each component's
  placement - so naming it is now the unambiguous route even when a second
  client changes `ActiveDocument` between two calls (reported 2026-10-26, twice:
  the failure surfaced as a bare `CATIAPart.MainBody failed` with the real
  document name only in the session line). `method='mesh'` is still refused for
  an assembly, because a mesh box is per part.
- The per-component box is computed once per PART, not once per instance: 70
  instances of 28 parts took 11 min 37 s at ~6 s per component. The reply now
  states `part_boxes` and `note_scope`, so the cost model is visible. Measured:
  5 instances of 2 parts -> `part_boxes: 2`.
- The part branch names the document it measured, in the reply and in the
  failure: "this measured 'V21_fillet.CATPart', which is not a part with a solid
  body ... If you meant the assembly, pass document='<assembly>.CATProduct'".

### Fixed — `list_components` no longer reads 70 placements to answer a filter

- `part_number` filters BEFORE the placement is read (a pose read costs ~70 ms,
  so "list just P24" out of 70 instances took 5 s), and `names_only: true`
  returns index / name / part_number / occurrence without any pose read at all.
  Measured on a 5-instance assembly: all+poses 370 ms, `part_number` 233 ms,
  `names_only` 32 ms.

### Fixed — the small ones from the same three reports

- `catia_pad`'s tool description now states the auto-flip, because an AI caller
  picks its arguments from `tools/list`, not from the runtime reply: a pad that
  adds nothing in the requested direction is flipped, kept, and reported as
  `direction auto-flipped to reach material`; only both-sides-empty rolls back.
- After a rolled-back pad, `part.InWorkObject` is put back to the body (on B32 it
  stayed on the sketch's datum plane, and the next Part Design call then failed
  with a bare "method failed" that reads like a bad sketch). `set_active_body`
  now reads `InWorkObject` back and says so when CATIA did not take it.
- `...or the profile does..` lost its second full stop, and a close of a part
  referenced by two assemblies says `'A', 'B' do.` instead of `does.`

## [0.3.21] — 2026-10-26 · a pad that flips, an assertion that cannot vanish

### Fixed — a failed pad no longer deletes its input sketch

- 0.3.20 diagnosed a pad that built nothing by creating a SECOND pad from the
  same sketch and deleting it again. That probe was built from a sketch the
  failed pad had already consumed, so (a) removing it took the caller's sketch
  out of the tree - the next call answered `No sketch named '草图.5'. Available:
  草图.1, 草图.4.`, which reads like a typo in the caller's own naming - and
  (b) it answered "nothing either way" for a direction that works, because the
  probe itself could not build. Reported 2026-10-26 with a 16-line reproduction.
- The diagnosis now flips the SAME pad node (`DirectionOrientation`) and
  re-measures, so nothing extra is created and nothing but that pad is ever
  removed. The reply states whether the sketch survived:
  `Your sketch '草图.5' is still in the tree - only the pad node was removed.`
  Verified: after a two-way failure the listing still answers
  `Available: 草图.1, 草图.2, 草图.3`.

### Fixed — a pad that adds nothing in the requested direction now flips itself

- `catia_pad` copies what `catia_pocket` has done since 0.3.4: when the requested
  side adds no material, the same feature is flipped and re-measured, and if that
  grows the body the feature is KEPT and reported as
  `Pad created: 40.000 mm (reverse) ... (direction auto-flipped to reach
  material: the requested side was empty, and the same feature keeps the material
  it was asked for on the other side)`. Measured on a 60x40x40 box with a
  10x10 sketch on its bottom face: +4 000.0 mm3, exactly 10 x 10 x 40.
- The two-way failure message now states a measured fact - both orientations were
  tried on the same node - and lists the cause that was missing: the sketch plane
  sits ON the material boundary and the sweep runs INTO the body, so the whole
  sweep is absorbed. It no longer says "the direction is not the problem", and no
  longer tells the caller that retrying cannot help: retrying with a different
  offset does.

### Fixed — an assertion that cannot be measured is a failure, not a silent pass

- `catia_run_plan` dropped an assertion whose value was missing, so
  `expect: {"document": "M113_asm", "volume_mm3": 1.0}` against a 76 million mm3
  assembly answered `PLAN_OK` with `assertions: []`. Every key that was asked for
  now produces a check; when the value is unavailable the check carries
  `pass: false` and an `unavailable` reason, and the failure line names the
  document: `A.CATProduct: volume_mm3: expected 1.0, got 800.0`.
- An assembly named in `expect` is measured by activating it for the measurement
  and restoring the previous active document, because `catia_get_inertia(
  document=…)` needs a Part. Verified: an assembly's volume now compares, instead
  of answering "exposes no 'Part'".
- `area_mm2` on a Product (`"n/a (Product document)"`) and a `cog` with a missing
  component are failures too, not skips.

### Fixed — a part an open assembly references closes with a state, not an error

- `catia_close_document` reported `CLOSE NOT CONFIRMED` when the document was a
  component of an assembly in the same session. That is CATIA's ordinary
  semantics - the caller did nothing wrong by opening the assembly first - so it
  is now a normal reply: `is STILL OPEN (closed: false). This is not a failure:
  CATIA keeps a document that an open assembly references, and
  'P00_Assembly.CATProduct' does.` The genuine anomaly (still open, and nothing
  references it) stays an error, and now says that is what it means.

### Fixed — frame sketches land in the body, datum points included

- 0.3.20 kept the frame plane's three datum points in a geometrical set. Measured
  on B30 2026-10-26: a pad on such a sketch built 400 mm3 and then REFUSED a
  R1 fillet on its own edge (`AddNewSolidEdgeFilletWithConstantRadius failed`),
  while the same pad with the points and the plane inserted into the body takes
  the fillet (`[applied Edge.1@R1]`). One tree, one update order.
- The B32 SP6 report of a frame pad that creates and never updates (faces 0,
  volume 0.0, `UpdateObject 失败`) is the same fault one step earlier, so this is
  the fix that addresses it directly rather than by refusing the feature on a
  version. Verified after the change on B30: frame sketch at
  O(10, -20, 20), u(0.970143, 0, -0.242536), v(0, 1, 0) -> pad 1 000.0 mm3, and
  a R1 fillet on Edge.1 applies with the anchor at 0.0000 mm.

### Added — edge anchors: every miss reported, and a mode to use the rest

- `catia_fillet` / `catia_chamfer` list ALL anchors that are beyond `anchor_tol`
  or ambiguous, with each one's nearest edge, its length and the measured
  distance - not just the first. Twenty anchors with one bad coordinate used to
  cost one round trip per bad anchor, and the report that hit it collapsed twenty
  to nine by hand.
- `on_anchor_miss: "fail" | "skip"` (default `fail`, unchanged semantics). With
  `skip`, the anchors that do match are filleted and the misses come back as
  `SKIPPED (on_anchor_miss='skip'): 1 anchor(s) beyond anchor_tol=0.5: [999, 999,
  999] is 1710.727 mm from the nearest edge (Edge.3, L20.0)`.

### Changed — a fillet refusal names a cause it can read

- The retry explanation blamed `catia_gsd_close_surface` for edges that come out
  of it. The reporter's four rejected edges were all bounded by `Sketch.N` and
  `Pad.N` - no GSD surface anywhere - while an existing R2.5 fillet met them at a
  corner. The fingerprint carries the bounding faces, so the reply now reads
  them: an edge bounded by `Fillet.N` / `Chamfer.N` is called out as a round
  meeting an existing one at a corner, and GSD surfaces are named only when the
  fingerprint actually contains them.

### Added — a purely outward shell

- `catia_shell` accepts `thickness: 0` together with `external_thickness > 0`,
  which is the only way to say "the wall grows outside the envelope and the
  original surface stays hollow". Measured on a 60x40x40 box (96 000 mm3) with
  one face removed: `thickness 0 / external 5` gives 61 500.0 mm3, exactly the
  analytic 70x50x45 minus 60x40x40. The 0.3.20 workaround was
  `thickness: 0.001`, which left a 10.4 mm3 inner membrane on a 10 400 mm2 wall.
- The reply names the side the wall grew on: `5 mm OUTWARD wall (the original
  surface stays hollow)` instead of `0.0 mm wall`, and
  `2.5 mm inward + 2.5 mm outward wall` for the symmetric case.

### Fixed — the small things the same reports listed

- `catia_activate_document` accepts `document` / `document_name` as well as
  `name`; a plan step that uses either validates (`BAD_ARGS` now understands a
  schema whose requirement is `anyOf: [{required: [name]}, …]` and names the
  shortest unsatisfied branch).
- `catia_get_inertia` adds `tensor_reason_code: "no_own_window"` beside
  `tensor_reason`, so nine parts in a row do not repeat nine identical sentences
  to say one word.
- `catia_close_sketch` warns when the sketch holds several closed loops that
  overlap: `this sketch holds 2 closed loops and 1 pair(s) of them overlap: loop
  1 (rectangle, 600.0 mm2) overlaps loop 2 (rectangle, 200.0 mm2) … Total loop
  area 800.0 mm2 is NOT the profile area when they overlap.` Two separate
  rectangles stay silent - that is a legitimate profile. `catia_sketch_rectangle`
  now says that ONE call draws the complete rectangle.
- `catia_run_plan` documents which tools cannot be nested
  (`catia_run_plan`, `catia_measure_topology`) and that each step is validated
  against its own schema before anything runs.
- Fixed a `//n` typo and a doubled full stop in the pad failure text.

## [0.3.20] — 2026-10-26 · a sketch on any plane, an edge by coordinate

### Added — sketch on an arbitrary plane (`{"type": "frame"}`)

- `catia_create_sketch` takes `{"type": "frame", "origin": [...], "u": [...],
  "v": [...]}` and builds a datum plane through that origin whose H axis is `u`
  and whose V axis is `v`, so a spec sheet's `(u, v)` numbers can be typed
  straight in. Ten steps in the 2026-10-25 report could not be modelled at all
  without it (eight 5.39°-tilted groove sketches on a track pad, two 14.04°
  inclined faces), because `plane: xy|yz|zx + offset` cannot express a normal
  that is not on a coordinate axis.
- Mechanism, measured on B30: `AddNewPlane3Points` (there is no `AddNewPlane`)
  on three points built from the frame, then `part.InWorkObject = body` before
  `body.InsertHybridShape(plane)` - without that assignment the call fails with
  E_FAIL. Verified: a 10 x 4 mm profile drawn in a frame at
  `(17.525, 185.862, 0)` with `u = (0.995584, -0.093875, 0)`, `v = -Z` pads to a
  box spanning x 17.525…27.6686, y 184.9233…187.8532, z -4…0 - i.e. exactly the
  frame, not an approximation of it.
- `catia_gsd_spline(element=...)` converts frame sketches back to global
  coordinates, so a frame profile can still become a 3D spline.

### Added — edges addressed by a coordinate that lies on them

- `catia_list_edges(coords=true)` adds `point_on_edge` and
  `distance_to_centre_mm` to every edge: a point ON the edge, taken as the
  closest point to the body's centre of gravity - the midpoint for a straight
  edge. Verified on a 60 x 40 x 40 box: all twelve edges come back exactly at
  their midpoints.
- `catia_fillet` / `catia_chamfer` take `edge_anchors: [[x, y, z], ...]` with
  `anchor_tol` (default 0.5 mm), and the reply states the matched edge name and
  the measured distance for each anchor. An anchor beyond the tolerance, or one
  equally close to two edges, is refused with the candidates listed - a fillet
  on the wrong edge is a legal feature nobody asked for. In the report, 22
  edges given as coordinates could be mapped to 4; the other 18 were given up
  because `catia_list_edges` returned no coordinates at all.
- How, measured: `Measurable.GetMinimumDistance` takes a POINT REFERENCE (it
  rejects a Measurable with "type mismatch"), `GetPoint`/`GetCOG`/
  `GetBoundingBox` all fail on an edge, and `GetPointsOnCurve` fails on solid
  edges too. The scratch points live in a temporary geometrical set that is
  deleted before the reply, so the model tree is left as it was.
- `catia_list_edges` now always answers one shape (`components`/`edges` inside
  an object, `convention` included) instead of returning a bare list in one case
  and an object in another.

### Added — arc from its two endpoints

- `catia_sketch_arc` accepts `p1`/`p2` plus `direction` instead of
  `start_angle`/`end_angle`. The source data that drove this gives "centre, R,
  (x1,y1) -> (x2,y2)" and no angles; of 236 arcs exactly one was a 240° major
  arc, and converting by hand is what turns it into a 120° one that puts the
  boss on the wrong side. Measured: `p1` at 10°, `p2` at 250° gives "swept
  240.0000° MAJOR arc" counter-clockwise and "swept 120.0000°" clockwise.
- Every arc reply now states the swept angle and the arc length, and a radius
  that does not fit its endpoints by more than 1 µm is called out - that is how
  a reverse-engineered profile acquires a gap.

### Added — batch assembly work

- `catia_add_component(count=N)` inserts N instances of one file. Each insert
  still runs as its own guarded process (the isolation is what keeps a CATIA
  dialog from blocking the session); a failure part-way reports how many landed
  rather than claiming success.
- `catia_set_component_transform(placements=[...])` places many components in
  one call with one display refresh, and reports each one's `max_deviation`.
  Measured: two instances, `worst_max_deviation` 0.0.
- `catia_new_part(file_path=...)` saves the new part immediately. A part with
  geometry and no path cannot be saved later and cannot be inserted into an
  assembly either - the caller only finds out when the insert cannot find the
  file.

### Added — plan steps are validated before the model changes

- `catia_run_plan` checks every step's `args` against that tool's own
  `inputSchema` and answers `BAD_ARGS` with `details: [{step, tool, missing,
  path, message, schema}]`. A step with a missing argument used to surface as
  `STEP_EXCEPTION / KeyError: 'name'` after the plan had already changed the
  model, while the same call made directly was rejected with a readable message.
- A step may carry `measure: true`; the report then holds that step's
  measurements. That is how one plan that builds several parts keeps one volume
  per part.
- `expect` may be a LIST, and an entry with `"document": "P06.CATPart"` is
  measured on that document - verified: PA.CATPart 800 mm3 and PB.CATPart
  1200 mm3 asserted in the same plan, both passing, each assertion tagged with
  its document.

### Fixed — a part is no longer described as a Product

- `catia_run_plan`'s measurements said `topology: "n/a (Product document)"` for
  every PART, because the test was "does `.Product` resolve" and a
  `PartDocument` carries a stub Product. Face/edge/vertex counts were therefore
  thrown away for parts (found 2026-10-25 while adding per-document assertions -
  the same trap that made `catia_add_component` fail on a Part document). The
  test is now the document type.
- On a real Product, `area_mm2` is `"n/a (Product document)"` instead of `null`,
  which read as "this assembly has no surface area".
- `catia_get_bounding_box` gains a `bounds` field beside `dimensions`: `exact`
  for a planar-faced solid, `lower bound` for a mesh sweep (measured 0.47-0.73 %
  small on curved faces) and for the assembly's vertex aggregation. It used to
  live only inside the prose of `method`, where a caller reading `dimensions`
  never sees it.

### Fixed — pad/pocket no longer blame the profile for a zero-volume sweep

- When a pad builds no material, the tool now pads the SAME sketch in the
  opposite direction before saying anything about the profile. Measured on B30:
  "THE PROFILE IS FINE: the same sketch padded in the opposite direction adds
  64.0 mm3 ... Pass direction='reverse'", or "It adds nothing in EITHER
  direction". Both of the report's real cases (a reversed direction, and a
  profile lying inside existing material) were previously answered with "the
  profile may be open - call this once more", and no number of retries can fix
  either.

### Fixed — the diagnosis names the server, even with no Part open

- `catia_diagnose` reports `mcp: {name, version, tool_count}` as its FIRST key.
  It previously sat behind the factory probe's early return, so a report written
  on an empty CATIA quoted no version at all (measured: `"mcp": null`).
  Verified: `{"name": "MCP_CATIA_MechDog", "version": "0.3.19",
  "tool_count": 101}`.

### Fixed — close_sketch names every breakpoint

- The warning lists ALL loose endpoints with the gap at each one, not just the
  closest pair: point lists rounded to 0.001 mm break at several joints at once.
  Verified with two gaps (0.0004 mm and 0.0006 mm): "4 loose endpoint(s) in 2
  break(s)". A profile that is open on purpose (a sweep guide) is still not
  nagged about.

### Fixed — sketch_delete_element keeps the sketch open

- The edition session is reopened on both paths, so a second delete does not
  need `catia_sketch_edit` first, and after a failed rebuild the sketch is open
  for the repair. The reply states the element count before and after, and says
  that every later index moved down by one. Index 1 remains the absolute axis.

### Fixed — the assembly bounding box says whether it covers everything

- The aggregated box carries `complete`, `instances: {total, measured, skipped}`
  and `incomplete_because`. `skipped` used to sit beside `min`/`max` as a
  sibling key, so a caller reading only the numbers never learned that six of
  seventy instances were excluded.
- `catia_shell` takes `external_thickness` - the third argument of
  `AddNewShell(face, internal, external)` is the only route to material OUTSIDE
  the surface. Measured on a 96 000 mm3 box with one face removed: internal 5
  gives 43 500 mm3, external 5 gives 61 500, and 2.5/2.5 gives 52 125, so the
  side has to be stated. A negative `thickness` is refused up front: CATIA
  answers it with a bare "UpdateObject failed", which reads like a bad feature
  name. A closed shell (no face removed) is refused with the measurement that
  `AddNewShell(None, ...)` builds an EMPTY feature (volume 0.0).

## [0.3.19] — 2026-10-25 · instances of one reference

### Added — `catia_copy_component`: instances of one reference, in one call

- A 24-instance assembly had to be built by calling `catia_add_component` once
  per instance: N isolated processes, and N peer components rather than N
  instances of one reference - which is a different thing in a BOM or a drawing
  (reported 2026-10-25).
- `Products.AddComponent(component.ReferenceProduct)` does it in one call.
  Measured on B30: the new component `INST2.2` has the same `ReferenceProduct` as
  `INST2.1`, so it is a true instance. `Selection.Copy` + `Paste` is NOT the way:
  Paste fails with E_FAIL and puts up CATIA's "this product does not have a valid
  type" dialog.
- The reply lists every instance of that part afterwards, so "which motor is
  number 3" is answered by the tool instead of by counting suffixes.

### Added — one instance can be addressed by part and occurrence

- `{"part_number": "P02_motor", "occurrence": 3}` is accepted wherever a
  `component_name` is taken (transform, rename, visibility, copy). The name
  suffix is CATIA-assigned: after one silent insert failure `P02_motor.3` is not
  the third motor anyone meant, and an assembly-wide index has to be counted by
  hand. Out-of-range names the instances that do exist.
- `catia_list_components` takes `part_number` to list just those, reports
  `occurrence`, and labels every entry `convention: "matrix"` - its `rotation` is
  the transpose of what `catia_set_component_transform` reads with
  `rotation_convention='basis'`, and a first comparison otherwise looks like a
  wrong placement (reported 2026-10-25).

### Fixed — the two Save As refusals now speak the same language

- The "an open document holds this file" branch reported neither size nor mtime,
  so a caller could not tell a finished part from a stale one, and could not tell
  which of the two guards it had hit - while the two guards lead to different
  next steps. Both now start with `already exists (<size>, last modified <time>)`,
  and the holder branch adds that `overwrite=true` does NOT help, because the
  file is locked by the open document.
- The success path says which case it was: `(new file - nothing was overwritten)`
  or `(replaced the existing file: …)`. Nobody should have to check the folder
  afterwards to know whether they just replaced something.

### Fixed — deleting "the last feature" has to be asked for

- A no-argument `catia_delete_feature` deleted whatever was created most
  recently, which on a long tree was the tip fillet the user had just added, not
  the test sketch they meant (reported 2026-10-25, one fillet lost). It now
  refuses and names the feature it would have deleted; pass `last=true` to mean
  it.

### Changed — a missing inertia tensor is a field, not a paragraph

- `get_inertia(document=…)` replied with five lines of prose in every one of the
  nine parts of a sweep. It now sets `"tensor": null` and `"tensor_reason": "…"`,
  and the prose shrinks to one sentence pointing at the field. A caller branches
  on the field instead of parsing a paragraph (reported 2026-10-25).

### Documented

- `measure_topology` adds `vertices_note` when the count is zero and cells were
  excluded: a revolved or fully curved solid has no corner to count, so
  "0 vertices, 20 excluded" is a correct answer, not a broken measurement.
- The 0.3.17 claim that thin sections can only be padded polygons is superseded -
  `catia_solid_loft` covers the lens-shaped case. No tool description still says
  "there is no solid loft"; the phrase survives only inside `catia_solid_loft`'s
  own history note and in a comment recording why diagnosis was rewritten.

## [0.3.18] — 2026-10-25 · there was a solid loft all along

### Added — `catia_solid_loft`: the solid loft this server said did not exist

- **`ShapeFactory.AddNewLoft()` exists and builds a closed solid in one update.**
  A user found it after `catia_diagnose` reported no such capability, and built a
  smooth propeller blade with it. The claim in 0.3.17's `thick_surface` text -
  "there is no solid loft to fall back on" - was wrong, and it was wrong because
  the probe checked a hand-written method list instead of the object.
- Reproduced here on B30 2026-10-25 with two circles (r8 at z=0, r16 at z=40):
  feature `多截面实体.1`, **volume 18 765.8 mm³**, area 4080.97 mm², CoG z=24.286.
  A Part Design solid, `list_features` shows it as `loft`.
- The case it exists for: a 2.30 mm lens section that refuses **every** thick
  surface offset (1.27/1.27 and 2.54/0 both fail) lofts to a **720.5 mm³** solid.
  Sections may be sketches, GSD curves or `Edge.N` / `Face.N`; the one-curve rule
  is the same as the surface loft, and a four-line sketch is still refused - with
  its own hint and a rollback that leaves the tree clean.

### Fixed — `catia_diagnose` reads the method table instead of a sample

- Both factories now report `typeinfo_addnew`: every `AddNew*` method read from
  the object's own type library (B30: 88 on `ShapeFactory`, 131 on
  `HybridShapeFactory`, including `AddNewLoft`, `AddNewRib`, `AddNewSplit`).
- The note says which list is which: the hand-written one is a licence and
  regression sample, **not** the capability list, so a method missing from it
  must not be read as "CATIA cannot do this".

### Fixed — Save As no longer replaces a file silently

- `catia_save_document(file_path=...)` **refuses by default** when the target
  already exists and is not this document's own file, and reports its size and
  last-modified time. `overwrite=true` replaces it and says what it replaced.
- This comes from a real loss: a verification run saved a new empty part onto a
  finished `P03_prop.CATPart`; the finished file was gone, and only a saved
  run_plan JSON made the rebuild possible (reported 2026-10-25).

### Fixed — a fingerprint collision is now solvable, not just reported

- Two edges can share length *and* both bounding faces at a fillet boundary: the
  fillet cuts a new edge beside the original one. The refusal now says the
  fingerprints are identical, names the `Edge.N` values as the disambiguator, and
  explains that they are still valid because nothing rebuilt in between.
- `{"index": N, "faces": [...]}` is accepted for that case, and the faces are
  cross-checked against the indexed edge - if they disagree the tree moved and
  the call stops instead of filleting the wrong edge.
- A batch fillet reply now lists the radius per edge: `[applied Edge.1@R3.81, …]`,
  after a run where two edges were rebuilt at the wrong radius because the reply
  named the edges without saying what had been applied to them.

### Documented

- **The spline bulge can be cancelled**, which the 0.3.16 text did not say:
  scale the control points toward the section centroid by
  `k = sqrt(target area / measured area)` and rebuild once. A nine-section blade
  at k=0.76 landed within 2 % of its 11 382 mm³ target, where the unscaled spline
  was +70 %. This is now in both the sketch spline and the GSD spline text.
- `catia_close_sketch` says what its `Profile:` echo covers: what this session
  drew in this document, nothing after a reopen - an empty echo is not an empty
  sketch.
- `thick_surface` points at `catia_solid_loft` instead of ending in "no solid
  loft exists".
- `pyproject.toml` sets `addopts = "-ra"`, so a run that skips tests says so;
  a B32 run reporting 329 against a B30 332 had no column explaining the gap.

## [0.3.17] — 2026-10-25 · every part has a 草图.1

### Fixed — the sketch memory was keyed by name, so every part shared one record

- **A fresh part's `close_sketch` echo listed the previous part's rectangle.**
  Reproduced on B30: part A draws a rectangle (echo "1 rectangle(s)"), part B
  draws only a circle and the echo says "1 circle(s), 1 rectangle(s)".
  `_drawn` was keyed by sketch name, and every part names its first sketch
  `草图.1`. Reported twice (2026-10-25) with independent traces, including a part
  whose first sketch echoed "1 rectangle(s), 1 spline(s)" it never drew.
- The outline record behind `catia_gsd_spline(element=...)` had the same key, so
  a reopened file could inherit **another document's corners**. Both are now
  keyed `document::sketch`, and creating a sketch clears its record.
- `element=` with no record now says so and names the document, instead of
  falling through: "No outline recorded for sketch '草图.3' in 'SAVED.CATPart' …
  reopening a file does not restore them". A record whose sketch has since been
  renamed or deleted is refused too. This is the case any reopened file hits —
  the nine parts of a rebuilt assembly all have a `草图.1`.

### Added — `catia_delete_body`, so an experiment can be cleaned up

- `catia_delete_feature` searches inside bodies and never sees one, so an
  experimental body could be emptied but never removed: one part ended up
  carrying nine empty bodies (reported 2026-10-25). The MainBody is refused, and
  a body that still holds features is refused unless `force` is true.
- Live: empty body deleted (3 → 2 bodies), body with a feature refused, forced
  deletion reports what went with it, MainBody refused by name.

### Added — `catia_measure_topology(document=...)`, and "there is no solid"

- Same argument `catia_get_inertia` and `catia_get_bounding_box` already take, so
  checking nine parts no longer costs nine open + activate pairs. An unknown name
  lists the open documents instead of a bare failure.
- An empty part now answers `solid: false` plus `note_empty` instead of three
  silent zeroes: "0 faces" and "this body holds no solid" are different verdicts
  and only the second means the counts cannot be used at all.

### Fixed — Save As onto a file that is already open fails in one step, not six

- With an assembly open that references `P03_prop.CATPart`, saving a new part
  onto that same path retried six times and failed after ~14 s. The reason is
  that the target file is held by the open document. The tool now finds the
  holder first and names it (plus the assembly that references it) before trying,
  and routes the "save onto its own path" case to a plain `Save`.

### Documented — the four geometry answers people needed and did not get

- **`thick_surface` has a real limit**: the total offset must stay below twice
  the surface's smallest radius of curvature. Measured on B30 2026-10-25 on a
  lens-shaped loft 2.30 mm thick: `1.27/1.27` failed **and `2.54/0` failed**, so
  it is not a two-sided problem — at the sharp corners the radius goes to zero
  and the offset self-intersects whichever side it goes. A 6 mm hexagonal
  section of the same shape thickens fine. Thin wing sections cannot be thickened
  at all; model them as padded polygons.
- **A spline is not a polyline, and the difference is the volume**: a six-point
  aerofoil-like outline padded +29.3 % as a closed spline against **+0.48 %** for
  the same six points joined with straight lines (reporter's measurement,
  2026-10-25; matches the +20 %/+79 % rectangle already in the loft text). So if
  the profile is a specification, draw lines and accept that it will not loft, or
  draw the spline and re-measure the envelope.
- **`pad`/`pocket` "built no material"** now names two causes beyond an open
  profile: a self-intersecting outline (check the signed area) and a closed
  outline CATIA simply failed to solve — which is intermittent, and the reporter's
  six-point polygon padded on the retry. The message says to call it once more
  before rebuilding the sketch.
- **A three-face edge has a second cause worth naming**: if it shares an endpoint
  with an edge you have already filleted, the fillet's own faces are the extra
  pair. Fillet the chain in ONE call (`edge_names=[...]`) instead of one by one.
- **`fill` failures** explain that boundaries must be closed and must meet each
  other, and that a session-sensitive failure is retried in a fresh session
  before falling back to a swept-and-thickened section.

## [0.3.16] — 2026-10-25 · a spline through four corners is not the four corners

### Corrected — the loft section rule I wrote in 0.3.15 was wrong

- 0.3.15 said a section must be "one continuous wire", and that a sketch profile
  from `catia_sketch_rectangle` **is** one wire and therefore works. Measured on
  B30 2026-10-25, section pairs of the same size:

  | section | result |
  |---|---|
  | `gsd_circle` ×2 | builds |
  | **sketch circle** ×2 | builds |
  | closed 3D spline through the same corners ×2 | builds |
  | `sketch_rectangle` ×2 | **fails** |
  | that rectangle → `gsd_extract` ×2 | **fails** |

  So "wire" was the wrong unit: a closed polyline is stored as four curves, and
  a loft section is ONE curve - CATIA does not associate a loop, and an Extract
  of the profile is still four cells. The description and the failure message
  now say that, and no longer recommend an Extract that does not work.
- This matters beyond wording: it is why a spec that gives corner coordinates
  ends up as a spline section (a spline through the corners is the only measured
  form that builds). A spline interpolates, so it does not reproduce a source
  part whose surface bulges past its sections - the P03 propeller envelope
  question, which is a modelling route and not a tool fix.

### Added — `catia_gsd_spline(element=...)`, so a polygon's corners need typing once

- A loft section is one curve and a polygon is four, so the way through is a
  curve through the same corners - which needs their coordinates, and a sketch
  cannot hand them back (a CATIA Point2D exposes no readable X/Y through
  automation on this build). So the server now remembers the outline it drew and
  where the sketch sits, and `element='草图.1'` turns that into a closed spline:
  corners in loop order, in global XYZ, converted through the plane the sketch
  was created on.
- The plane mapping is measured, not assumed (B30 2026-10-25, a 20 x 6 rectangle
  padded on each datum plane): `xy` -> `(u, v, offset)`, `yz` -> `(offset, u, v)`,
  `zx` -> `(v, offset, u)`. Verified end to end: a 4-line sketch on `zx` at
  offset 25 became a spline whose thickened solid measured x 0..6, y 25..27,
  z 0..20.
- Refused rather than guessed in three cases, each with its own sentence: a
  sketch this server never drew (or one from a reopened file), a sketch on a
  face (no readable frame), and a sketch holding a circle or a spline (already
  one curve - pass the sketch itself as the section).
- Closed lines are chained back into a loop, including segments drawn backwards;
  a chain that does not close names the point where it stops.

### Added — the loft description now carries the two routes that actually work

- **`catia_sketch_spline(closed=true)`** draws the same interpolating curve
  *inside* the sketch, so the sketch is one curve and lofts directly. Measured:
  such a sketch lofted through two sections and closed into a solid.
- **When the source bulges past its own sections** (a tangency with a tension
  factor, which CATIA's GSM loft does not expose), model the bulge instead of
  asking the loft for it: add sections where the source is thickest, with area
  and span read off measured cross-sections. Worked example included (a blade
  whose sections span x -4.604..16.256 while the part reaches x ±23.222: add
  z=30 / 78.0 mm² and z=70 / 106.4 mm², lands within 5 %; `ruled` came out 67 %
  short on that same part).
- **A spline is not the polygon.** Measured: a closed spline through the corners
  of a 20 x 12 rectangle swept 24.04 x 21.46 - +20 % in x and +79 % in y. The
  bulge is worst across the short side, so replacing a thin quadrilateral
  section with a spline thickens exactly the direction that had to stay thin.
  That is a substitution, and both descriptions now say so.

## [0.3.15] — 2026-10-25 · the box was 60 mm wide because of a sketch corner

Four reports against 0.3.14. Two of them reproduced exactly as written, and one
of those had a root cause nobody had named.

### Fixed — a sketch's wire corners were counted as vertices of the solid

- **A 40×20×10 pad measured 60×80×10 in an assembly.** Reproduced on B30, then
  traced: the vertex search returns 20 cells for that pad, and 4 of them are the
  corners of a sketch that is no longer part of the solid. `is_construction`
  knew about construction *edges* and *faces* but had **no vertex prefix at
  all** - `Selection_BorderREdge`, `Selection_WireREdge`, `Selection_BorderRSur`
  and nothing else - so every wire corner passed the filter and went into the
  box. `Selection_WireFVertex` is what CATIA calls them; a vertex of the solid
  is `Selection_FVertex`. The single-part box looked right only because the
  polluted corners happened to sit inside the solid.
- The rule is now "the leading type token carries `Wire` or `Border`" rather
  than an exhaustive list, so variants nobody has met yet are covered too, and
  it is checked against the head of the NLS path only (`Brp:(Sketch.1;...)`
  appears in every path and means nothing).
- Live check after the fix: that pad reports **8 vertices** in
  `catia_measure_topology` and **40×20×10** both on its own and per component.

### Fixed — the "activate the only open assembly for you" branch was dead code

- **`catia_add_component` failed with a Part Design diagnostic whenever a Part
  was active.** Every `PartDocument` answers `.Product` with a stub, so
  `get_active_product()` succeeded on a Part document, the `except` that was
  supposed to fall back never ran, and the insert then failed on the stub. The
  document is now identified by `Type` (`CATProduct`), and only then asked for
  its Product.
- Live check: with the part active and one assembly open, the insert now
  succeeds and the reply says so - "(activated 'ASMP.CATProduct' first: it was
  the only assembly open and a Part document was active)".
- `catia_add_new_part` had the same trap and got the same fix.

### Fixed — the failure envelope contradicted the message

- **`recovery.rollback_supported` was hard-coded `false` and `next_actions` said
  "recovery is manual" even when the tool had just removed the feature CATIA
  rejected.** Reported twice. A rolled-back failure now reports
  `effects.modified: false`, `recovery.rolled_back: true` and "Nothing to
  repair: ..."; a rollback that failed says which feature is poisoning the tree.
  `rollback_supported` stays `false` because there is still no rollback *request*
  a client can send - that is a different question from whether this failure
  already undid itself, and the new field answers it.
- The rollback state travels as an attribute on `FeatureRejected` (a
  `RuntimeError` subclass) instead of being re-parsed out of the message.
- Live check: `fillet R25` on the same block → `UpdateObject failed`,
  `effects.modified: false`, `recovery.rolled_back: true`, tree back to one
  feature, and `catia_update_part` succeeds immediately afterwards.

### Fixed — smaller things the reports named

- **`rect_pattern` reported a pattern that added no material as a plain
  success.** When the model's volume does not change and the grid is larger
  than 1×1, the reply now says so and names the cause: CATIA places copies only
  on the side each direction points to, so a seed at the far end of the part
  puts them all outside it. The direction line also carries its sign now
  (`axis x+ -> dir1`) instead of only saying "(reversed)".
- **`list_features` did not mention geometrical sets at all**, so the
  `PatternAxis` set every circular pattern leaves behind (a line plus its two
  points, which must stay - the pattern references them) was invisible in the
  listing while being obvious in CATIA's tree. It is now reported with its
  element count, and the payload says why it cannot be deleted.
- **A fillet's reply now says where the feature landed** ("feature 2 of 2 in
  '零件几何体'") and that CATIA exposes no reorder, so a fillet cannot be placed
  before an existing feature. Measured: `Part` and `Body` have no
  Insert / Reorder / Move at all.
- **An edge bounded by three or more faces is now explained instead of merely
  rejected.** A real edge has two; the extra pair is a coplanar seam left by a
  boolean union of two coplanar bosses, with no dihedral angle and no material
  to remove. Deliberately a hint, not a refusal: a part that legitimately ends
  up with three bounding faces should still be attemptable.
- **A multi-cell wire is the first thing a failing loft should blame.**
  `catia_gsd_multi_section_surface` now says, in both the description and the
  error, that a section must be ONE continuous wire - four separate sketch
  lines, a multi-cell Extract or a Join of disjoint curves all fail the same
  way, while `catia_sketch_rectangle` / `catia_sketch_circle` profiles work.
- **`measure_topology` publishes its counting rule** (`counting_rule`), so a
  stored `expect.vertices/faces/edges` that is no longer met reads as "the rule
  changed" rather than "the model is wrong". It changed twice: 0.3.11 excluded
  construction geometry, 0.3.15 excludes sketch-wire vertex corners.
- **`save_document` no longer claims Save As is impossible after modelling.**
  That was measured on B30 with the raw `Document.SaveAs`; this tool uses the
  FileSystem route and the reporter saved a 35-feature part on B32 SP6 in one
  call. Both are stated, and the order that always works is still recommended.
- **`_insert_blocked_hint` no longer asserts that an activated part blocks the
  insert.** It did not reproduce on B32, so the hint names the open document as
  the first thing to rule out and says the measurement was build-specific.

## [0.3.14] — 2026-10-24 · one rejected fillet used to kill the whole part

Five reports against 0.3.11. The first one is the serious one, and it reproduced
exactly as reported.

### Fixed — a failed feature no longer poisons the part

- **Single-feature failures left the dead node in the tree, and from then on every
  update of the part failed at it.** Reproduced on B30: `fillet R40` on a
  40×20×10 block → shape_count 1 → 2, `catia_update_part` fails, and the next
  `pad` fails too, until `倒圆角.1` is deleted. The batch paths rolled back since
  0.3.7; `fillet` (single), `mirror`, `shell`, `thickness` and `boolean` called
  `part.UpdateObject` raw. They all go through `_safe_update` now, whose reply
  says `rolled_back: true/false` - and when the removal itself fails it says the
  feature must be deleted by name before anything else.
- **`catia_update_part` now names the feature that blocks the update** instead of
  answering a bare "方法 Update 失败": it tries each feature on its own and reports
  the first that fails, with the `catia_delete_feature` call that fixes it.
- **`catia_list_features` publishes `count` (and `body`)**, so "did my feature get
  created, or is a dead one hanging around" is answerable from the success path -
  the failure state line already showed `shape_count`, which is how the reporter
  located this.

### Fixed — the rest of the reports

- **`catia_add_component` now checks the active document.** `AddComponentsFromFiles`
  is a method of `Products`, so it needs a Product to be active; after a
  `document=` measurement (or an explicit `activate_document`) a PART is active and
  the insert failed with a bare `AddComponentsFromFiles 失败` plus a diagnostic
  about Part Design factories that had nothing to do with it. It now activates the
  assembly when exactly one is open, and otherwise says which documents are open
  and what to do.
- **Inserting a file whose document was ACTIVATED fails, and now says why.**
  Measured on B30: the same insert works while the part is open but never
  activated, and fails right after `activate_document` on it - nothing in the COM
  error or in any listing distinguishes those two states. The message names the
  open document and the recovery step (save, close, insert again; existing
  components are untouched).
- **`catia_list_documents` now says which assemblies use each part**
  (`component_of`). A component's part IS listed and CANNOT be activated, so the
  listing and `activate_document`'s refusal used to contradict each other; one
  reporter wrote the wrong call order from it and wasted two round trips.
- **`get_bounding_box(document=..., mesh)` no longer exports a part edited in
  context.** On B32 that export puts up a modal dialog that nobody can answer,
  and the call still returns a correct box - so the size check could not tell, and
  six parts meant six dialogs. The state is detected first (activate + read back
  `ActiveDocument`, the same test `activate_document` uses) and the copy route
  runs instead. A box that came from the copy now carries a structured
  `warnings: [...]` entry, not just prose inside `method`.
- **`get_inertia` on an assembly where nothing is measurable returns a partial
  result** (`volume_mm3: null`, `components: []`, `skipped` with each reason),
  matching what the bounding box has done since 0.3.11. The tool that needed the
  diagnosis most was the one throwing it away.
- **`get_inertia(document=...)` now also returns the inertia tensor.** CATIA only
  computes it for the ACTIVE document, so this route used to need
  open + activate per part (18 calls for nine parts). It borrows the activation and
  restores the previous document; when the part has no window of its own it says
  so rather than returning a silently matrix-free payload. Side effect, measured
  and documented in the message above: a part activated this way cannot then be
  inserted as a component until it is closed.
- **Products of inertia below 1e-12 of the largest principal moment are reported as
  `0.0`** instead of -4.28e-50 dust.
- **`rect_pattern` takes `dir1_axis` / `dir2_axis` (x/y/z) and
  `reverse_dir1` / `reverse_dir2`.** An edge's direction cannot be read back from
  any listing, so two edges of equal length gave opposite directions and the only
  way to tell which one you got was the volume delta. Two things were found while
  verifying it: the no-`feature_name` path patterned the BODY (the trap
  `circ_pattern` had in 0.3.7) so every such call failed, and CATIA only accepts a
  RIGHT-handed direction pair - `(x,y)`, `(y,z)`, `(z,x)` build, `(x,z)`, `(z,y)`,
  `(y,x)` fail. A left-handed pair is now swapped internally (counts and spacings
  with it, so the geometry is unchanged) and the reply says so.
- **`list_bodies.faces` uses the same definition as `measure_topology`** -
  191 vs 186 on the same body was the same bug family as the old edge counts.
- **`draft` blames the direction before the neutral face.** A face cannot be
  drafted along its own normal and then no neutral face helps; the reporter tried
  all five and concluded the tool was broken.
- **`add_new_part(name=...)` reports when the name did not take** (tries `Name`
  then `PartNumber`, like `new_product`) instead of returning CATIA's name as if
  the argument had applied.
- **`run_plan` on a Product says `topology: "n/a (...)"`** instead of
  `topology_error` next to a perfectly good volume.
- **`new_product`'s reply no longer contradicts itself** ("Created 'ASMV.CATProduct'"
  followed by "an unsaved assembly is always 'Product1.CATProduct'").

### Metric semantics changed — re-baseline topology assertions

`faces` / `edges` / `vertices` are now counted by ONE rule everywhere
(`catia_measure_topology`, `catia_list_faces`, `catia_list_edges`,
`catia_list_bodies`): the NLS filter, excluding consumed sketch wires and the
datum planes' own borders. Consequences against 0.3.10 and earlier, measured by
the reporter on a 9-solid body: P02_motor faces 48 → 46, edges 109 → 101,
**vertices 72 → 96** (the old counter dropped 32 wire elements and now drops only
the datum borders). Any `expect.faces/edges/vertices` stored before 0.3.11 must be
re-baselined before it is used as an acceptance criterion again.

### Reported, not reproduced on B30

Both were reported as deterministic P1s on B32; neither reproduces here, so
nothing was changed for them - a workaround for a build we cannot reproduce is
just another silent branch:

- `circ_pattern(count=2, angular_spacing=90)` builds normally on B30 with a Pad
  seed (`2 instances, 1 added`, volume doubled) and with a Shaft seed.
- `mirror` about YZ on a 4×90° circular pattern builds normally.

If you hit either on B32, the session's `catia_list_features` count and the exact
`UpdateObject` text are what would let this be pinned down.

## [0.3.13] — 2026-10-24 · the loft does have a tangency condition; we said it did not

A user asked whether "keep tangency / tangent factor" was still missing. It was
missing from *this fork*, and the reason given for that was wrong: since 0.3.2 the
README and the tool description said CATIA's GSD loft exposes no tangent
condition, "verified by enumerating its type information". The enumeration covered
*properties*; the condition is a **method**.

Measured on V5-6R2020 B30, 2026-10-24, on three circles r=10/20/10 at z=0/50/100:

| call | result |
|---|---|
| `loft.SetStartSectionTangent(<circle ref>)` | accepted, then `UpdateObject failed` - a sketch curve is not a support |
| `loft.SetStartSectionTangent(<cylinder surface>)` | updates, area **10701.639 → 10174.914 mm²** (−4.9 %) |
| `loft.SetEndSectionTangent(<the same cylinder>)` | `UpdateObject failed` - that surface does not touch the far section |
| `MaintainTangency`, `TangentFactor`, `*SectionTangentFactor` | absent (property *and* method form) |

So: the entry point exists (one geometry each), the **factor does not**, and the
support has to actually touch the section - CATIA accepts the setter before it
looks at the geometry.

### Added

- `catia_gsd_multi_section_surface` takes **`start_tangent` / `end_tangent`**: the
  element name of a surface (or face) that section lies on. The reply names which
  end is tangent to what.
- A failed update while a tangency support is set now says the support rule,
  instead of only listing guides and section directions.
- `ruled` + a tangency condition is refused with an explanation: the ruled path is
  one blend per section pair, so there is no single loft feature to carry it.

### Corrected

- The claim "no tangent / curvature condition on a loft, verified by enumerating
  its type information" (0.3.2) was **wrong for the condition and right for the
  factor**. Both the tool description and the older changelog entry now say which
  is which: `HybridShapeLoft` exposes `SetStartSectionTangent` /
  `SetEndSectionTangent`, `SectionCoupling`, `Relimitation` and
  `CanonicalDetection`; it does not expose a tangent factor, a curvature
  condition's factor, or a `MaintainTangency` switch.

### Also measured, not a fix

- `CanonicalDetection` (0..2) changes nothing on this geometry: area stayed
  10701.639 mm² for 0, 1 and 2. It is not the "straight sides" switch that
  `ruled` provides.
- `AddNewLoft` numbers its sections and a following GSD feature makes the next
  `catia_create_sketch` start again at `草图.1`, which collides with the sketch the
  loft already references. Build all section sketches before the first GSD feature,
  or rename them - found the hard way here.

## [0.3.12] — 2026-10-24 · a dialog can no longer take the session hostage

`catia_add_component` was the one call that could still kill a session: a CATIA
file prompt on the automation thread blocks the serial STA worker, and the thread
that would answer it is the blocked one (one reporter sat there for **2 h 14
min**). The dialog watchdog answers `#32770` message boxes but deliberately leaves
CATIA's own `CATDlgDialog` panels alone, because a human may have opened one on
purpose - so widening it was not an option.

### Changed

- **`catia_add_component` now runs in a separate process** (`catia_mcp.guard`).
  CATIA is an out-of-process COM server, so a second client attaches to the same
  running instance, makes the one call, writes its result to a file and exits. If
  it does not come back within `CATIA_MCP_GUARD_TIMEOUT` (default 60 s) it is
  killed - which releases the server - and the recovery sweep cancels the dialog
  it left behind (the only place that clicks `CATDlgDialog`): the reply then says
  how many components the assembly holds, so you can tell whether the insert
  happened anyway, and points at `catia_list_components` before retrying.
  Measured on B30 with a child that never returns: the tool answered in **4.6 s**
  (3 s timeout + 1.6 s sweep), reported `0 more than before`, and the next tool
  call worked - the same failure used to be indistinguishable from a dead server.

- The child attaches with `GetActiveObject` only - a `Dispatch` there would launch
  a second CATIA when none is running - and gets `stdin`/`stdout` on `DEVNULL`,
  because a chatty child would corrupt the JSON-RPC framing of a stdio transport.

- The in-process path is kept, and is used automatically when the child cannot
  start or cannot attach; the reply says which path ran, since that is the
  difference between "protected" and "not protected" for the next call.

- Cost, measured on B30: a guarded call takes **0.46-0.53 s** more than an
  in-process one (Python startup plus the COM attach), so a 24-instance assembly
  pays about 12 s in total. That is the price of not being able to lose the
  session to a dialog.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CATIA_MCP_GUARD` | `1` | `0` puts the insert back in-process, with no protection |
| `CATIA_MCP_GUARD_TIMEOUT` | `60` | seconds before the isolated process is killed |
| `CATIA_MCP_GUARD_CMD` | - | override the child command (used by the tests) |

### Still open

- Killing the child frees the server, not CATIA: if CATIA itself is wedged behind
  a native dialog that no button answers, a human still has to look at it. What is
  fixed is that the *server* survives and says what it found.
- Only the insert is isolated. Everything else stays in-process, because a COM
  attach per call would cost seconds on every measurement.

## [0.3.11] — 2026-10-24 · numbers that were quietly wrong, and a route that only worked if you already knew the rule

Three reports against 0.3.10, all reproduced on V5-6R2020 B30 before they were
touched. The first one matters most: `document=` was added in 0.3.9 for exactly
one scenario - measuring a part while its assembly is open - and in that scenario
it either could not find the part at all or exported nothing.

### Fixed

- **`document=` now reaches a part that only exists inside an open product.**
  Components' part documents are not in the `Documents` collection (B30, measured:
  a product referencing two parts leaves Documents holding just the product), so
  the argument answered "No open document matches" in the case it was written for.
  It now locates the component by instance name, part number or file name and
  opens its file, and the reply says the session gained a document ("it stays
  open").

- **STL export no longer fails silently when the part is edited in context.**
  CATIA refuses `ExportData` with "编辑上下文时，无法另存为 stl" while the part is
  loaded inside an assembly with no window of its own; depending on the build that
  arrives as a modal dialog, as a swallowed failure, or as
  `[WinError 2] ... part.stl` - an error that names no cause. The export result is
  now checked by file size and a part that cannot be exported in place is
  re-exported from a temporary copy opened as its own document. The reply says so,
  including the cost: a copy reads the SAVED file, so unsaved edits are not in the
  box. When even that fails, the error names the cause and the route
  (`catia_open_document`) instead of `WinError 2`.

- **The inertia tensor was zero on every real part.** `round(x, 4)` on kg·m² is a
  resolution of 1e-4, and a 72 cm³ plate's principal moments are 2.1e-5 and 5.6e-5,
  so both came back `0.0` and a 73 cm³ loft came back all zeros - a number a caller
  compares against CAD and reads as "0 % error" (reported 2026-10-24). Six
  significant digits now, plus `inertia_matrix_unit` and `inertia_matrix_about`
  (component axes through the centre of gravity). Measured after the change on a
  40×20×10 block: `[[3.33e-7, 0, 0], [0, 1.13e-6, 0], [0, 0, 1.33e-6]]`, which is
  m/12·(b²+c²) for the material mass - the tensor uses the MATERIAL density, not
  the `density` argument, and `mass_note` now says so with that exact arithmetic.

- **`activate_document` claimed success without switching.** A part that exists
  only as a component of the open product is reachable as a document object and
  accepts `Activate()`, but `ActiveDocument` stays on the product - so the next
  `get_inertia` returned the assembly's volume (16000 mm³ for an 8000 mm³ part)
  with no error at all (reported twice on B32, 2026-10-24). The call now reads
  `ActiveDocument` back and raises with the two routes that work
  (`get_inertia(document=...)`, or `open_document` first).

- **`catia_measure_topology` and `catia_list_edges` count edges differently.**
  Third release running: 109 edges from one tool and 101 from the other on the same
  part. The counter filtered on the dynamic type (`Selection_Wire*`) while the
  listings filtered on the NLS, and the two rules differ by exactly the datum
  planes' own borders. Reproduced on B30: a block with a boss reported **18** edges
  in `measure_topology` and **14** in `list_edges`, and the four extra were
  `Selection_BorderREdge` on `Brp:(GSMPlane.N;k)`. One definition now (the
  listings'), with `edges_excluded` split by reason
  (`sketch_wire` / `datum_plane_borders`) so nothing disappears silently;
  `list_edges` reports the same breakdown as `excluded`.

- **`draft` without `neutral_face` picked the largest face, which is usually the
  base** - the one face that cannot work, because its normal is parallel to the
  pulling direction. The reported plate failed every time on a 2400 mm² base for a
  `zx` pull, while an explicit side wall worked. It now tries the four largest
  candidates in turn and keeps the first that updates (B30: Face.1 rejected,
  Face.4 accepted), and when all four fail the error lists what was tried.

- **An assembly where nothing is measurable failed the whole call.** It now returns
  a partial result - `dimensions: null`, `components: []`, and `skipped` with each
  component's reason - because "which part could not be measured and why" is the
  answer, not an exception.

- **`sketch_delete_element` now reports the volume delta** (measured: 10681.4 →
  10053.0965 mm³ on the reporter's Ø40+Ø5 part), so the reply proves the rebuild
  instead of asking for a second `get_inertia`.

### Corrected claims

These were wrong in a way that cost a caller a working tool:

- **`diagnose` said the measurement tools return `UNSUPPORTED_CAPABILITY` when
  Measurable lacks `GetBoundingBox` / `GetInertia`.** Nothing here uses either
  method - volume/area/COG come from `Measurable.Volume`/`.Area`/`GetCOG`, boxes
  from vertices or an STL, the tensor from `SPAWorkbench.Inertias`. The note now
  says missing them changes nothing, and only warns about real gaps.
- **`get_bounding_box`'s schema still said `auto = vertices if the shape has any`**
  and "the mesh method cannot be applied to a non-active document". `auto` has
  routed to the mesh since 0.3.10, and `document=` + mesh has measured
  non-active parts since 0.3.9. Reading the old text led straight to passing
  `method='vertices'`, i.e. to the under-reporting route. Both sentences rewritten.
- **The assembly box note said "a revolved part's diameter is under-reported".**
  A camera bracket's ARCH was under-reported by 8.13 mm / 19.6 % (33.2685 reported
  against 41.3974 true) and it is not revolved. The rule is "any part whose
  extremum lies on a curved face".
- **The GSD loft warning said the tool "fails on V5-6R2022 (B32)".** That came
  from one B32/SP6 session (2026-10-23, 7/7 configurations). Two other B32/SP6
  sessions then built both the smooth and the ruled loft and matched B30 to
  0.02 mm³ (2026-10-24, independently). It is session-sensitive, not
  build-determined, and the description says so instead of talking B32 users out
  of the tool.
- **An empty part was called a "surface-only part".** A brand-new part and a part
  made of GSD surfaces both have an empty main body but need different advice; the
  message now distinguishes them.
- **`new_product(name=...)` looked like a no-op** because `Product.Name` is
  read-only on the root. `PartNumber` is writable, so that is tried first and the
  reply says which attribute took (B30: `PartNumber set to 'ASMV'`).

### Known, not fixed

- `add_component` on a part whose document is already open: reported once as
  putting up the "same identifier in session" dialog. The path normalisation and
  the component-count check are in place, but nobody has reproduced that state
  since, so it is listed here rather than claimed fixed.
- `draft` / `fillet` / `chamfer` / `hole` still report no volume delta.
- The per-component world box in an assembly is a vertex sweep by construction;
  only `document=` + `mesh` gives the true per-part box.

## [0.3.10] — 2026-10-24 · measurements that can be trusted, crashes that say why

### Fixed
- A surface-only part no longer produces a payload full of `"unavailable: ..."`
  strings from `catia_get_inertia`. Volume, area and centroid are undefined
  without a solid, so it now says exactly that and names `catia_gsd_close_surface`
  as the way to get one. `run_plan` records it as `inertia_error` and carries on.
- `run_plan` treated `center_of_gravity_mm` as a dict even when `get_inertia` had
  reported it as a string, which is the shape of the reported
  `'str' object has no attribute 'get'` crash on a pure-surface plan. Guarded.
- `catia_new_product` said it had created a document named after the argument,
  while CATIA only renames the ROOT PRODUCT - the file stays `Product1.CATProduct`
  until saved. The reply now separates the two and points at
  `catia_save_document(file_path=...)`.

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

  > **Corrected in 0.3.13.** The condition DOES exist: it is a method
  > (`SetStartSectionTangent` / `SetEndSectionTangent`, one support geometry
  > each), which a property enumeration cannot see. Only the **factor** and the
  > `MaintainTangency` switch are absent. Measured on B30: a cylindrical support
  > through the first section changes the area 10701.639 → 10174.914 mm². The
  > parameters are now `start_tangent` / `end_tangent`.

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

## [Unreleased] (upstream, pre-0.2.2 — kept as history, never dated)

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
