# Forged-blank prototype

The geometry now lives in `services/wheelcam/forged_blank.py` as the **`forged-blank-v1`** template, alongside the main window-method template. `build.py` here is the experiment CLI (renders against the reference photo); `POST /api/projects/{id}/forged-builds` with `{"expected_revision", "recipe"}` queues the same build in the workbench (900 s worker limit instead of 300 s), producing `wheel.step`, `wheel.glb`, `stock.step`, `recipe.json` and a viewer-compatible `report.json`. Exported parts use the main template's coordinates (Z=0 at the rim-width mid-plane); the report derives ET from the mounting face (hub back) and runs the same caliper / supplier-stock / weight checks as the main template from the project's 准备 inputs (the template's own `stock.step` is not overwritten). The workbench has a 锻坯 tab for it.

Builds a wheel in forging/machining order from one `ForgedWheel` parameter set:

1. **Revolved forging blank**: hub plateau, concave face web (`z_top`/`z_back` profiles), lip face ring, barrel. One solid, so hub and spokes are never separate bodies.
2. **Face facets**: two sloped ruled-loft cuts per spoke segment leave a ridge and side facets that follow the face and fade out at the hub and ring (`facet_deg: 0` gives flat tops).
3. **Through windows**: 2D sketch = annulus minus spoke footprints, with only real corners filleted, extruded along the axis. Families: `y_split` (stem + two bowed arms), `single` (hub width → rim width), and `skeleton`: one spoke group as a graph (`nodes` in polar mm/deg, `edges` with start/end widths), footprint = tapered edge quads + round joints; tips placed near ±pitch/2 meet the neighbouring group, so trees close into a mesh. Skeleton centrelines are clipped to the window band before facets/grooves/pockets are cut. Window outlines are periodic splines, so each window has one smooth wall. `spoke_sweep_deg` twists spokes toward the rim (for directional wheels).
4. **Spoke grooves**: channels parallel to the finished top. `groove_offsets` are fractions of the half width (`[0]` = centre groove, `[0.62]` = one each side, `[]` = none).
5. **Back weight pockets** (`back_pocket_skin` > 0): a U-channel milled up under each spoke, leaving `back_pocket_wall` each side and the skin under the machined top; its width follows the real spoke width (tapered stems, widening spokes).
6. **Lip pockets** (optional) and **lug holes and seats**.

**Window-rim edge breaks are a CAM operation, not CAD geometry.** `edge_break` (mm, 45°) is written to the report's `cam_operations`; the B-Rep keeps sharp rims. Every way of modelling it failed on real wheels (2026-09-23/24): B-Rep fillet/chamfer (0/317 edges), pipe sweeps (zero volume / hung), per-sample ruled wedges (worked, but STEP 3–11× larger: 22–81 MB, with small steps), and single smooth ring tools per window (only the flat WORK wheel succeeded; on faceted/grooved spokes either that cut or the following groove cut returned null shapes). CAM chamfers sharp edges directly, which is also how a real drawing states it.

Facets, grooves and back pockets are lofted along each spoke's actual centreline (bowed arms, swept spokes), not the straight chord: on the bowed HF-6 arms the chord-following pockets had cut one wall down to 11–12 mm of a ~40 mm web.

## Recipes (all dimensions are eyeballed design assumptions, not measurements)

| Recipe | Photo | Family | What it tests |
|---|---|---|---|
| `recipes/hf6-y-split.json` (defaults) | …27214 | 6 × Y split, deep concave | facets, side grooves, 20 lip pockets |
| `recipes/wide6-centre-groove.json` | …27216 | 6 × single, parallel, 22" | centre groove, near-flat face, no lip pockets |
| `recipes/work6-tapered.json` | …27223 | 6 × single, widening, 16" | flat tops, big window fillets, small wheel, tapered back pockets |
| `recipes/v12-hub-fork.json` | …27218 | 6 × Y split at the hub = 12 spokes, 22" | directional sweep, deep concave, same Y family as HF-6 |
| `recipes/tree6-branching.json` | …27217 | skeleton: stem → 2 arms → 4 twigs, tips converge with neighbours | branching mesh, 24 windows |

All five build as one valid solid and pass `check_walls.py`. `wide6`, `work6` and `v12` needed a recipe JSON only; `tree6` needed the new skeleton family once and is itself only a recipe.


```sh
PYTHONPATH=services .venv/bin/python experiments/forged-blank/build.py \
  --recipe experiments/forged-blank/recipes/hf6-y-split.json --output artifacts/forged-blank-hf6
```

Recipe keys are `ForgedWheel` field names; unknown keys are rejected. `_photo` and `_view_x` are render hints only.
Outputs: `blank/part .step/.glb`, `recipe.json` (the full parameter set, needed to rebuild because STEP carries no history), `report.json` (per-operation removed volume, validity, STEP round trip, removal ratio), `comparison.png`, `blank-and-back.png`.

`check_walls.py <recipe> <build dir>` probes both side walls of every spoke with vertical rays and fails if the material there is thinner than 70% of the web (a pocket cut through the wall). Validated both ways: it fails the known-bad builds (rim-width pockets on the tapered WORK spokes, chord pockets on bowed arms) and passes the current four. An earlier version that only counted surface hits passed the bad builds.

Volumes use `wheelcam.mass_properties.volume` (Gauss–Kronrod). Plain `Shape.Volume()` reported 37 315 mm³ for an edge break whose Boolean difference was 1 874 mm³.

## Known gaps

- Not modelled: spoke side pockets, back weight pockets, centre-cap recess, window draft, real rim bead/drop-centre profile, multi-spoke/mesh families.
- The WORK spokes' slight lean is not matched: `spoke_sweep_deg: 10` made them look like a turbine, so its recipe leaves it at 0.
