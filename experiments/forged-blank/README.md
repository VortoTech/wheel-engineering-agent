# Forged-blank prototype

Builds a wheel in forging/machining order from one `ForgedWheel` parameter set:

1. **Revolved forging blank**: hub plateau, concave face web (`z_top`/`z_back` profiles), lip face ring, barrel. One solid, so hub and spokes are never separate bodies.
2. **Face facets**: two sloped ruled-loft cuts per spoke segment leave a ridge and side facets that follow the face and fade out at the hub and ring (`facet_deg: 0` gives flat tops).
3. **Through windows**: 2D sketch = annulus minus spoke footprints, with only real corners filleted, extruded along the axis. Families: `y_split` (stem + two bowed arms) and `single` (hub width → rim width). Window outlines are periodic splines, so each window has one smooth wall. `spoke_sweep_deg` twists spokes toward the rim (for directional wheels).
4. **Window rim edge break**: 45° chamfer (`edge_break`, mm) along every rim, from sections normal to the rim whose heights are ray-cast on the real machined top, so it follows facets, hub and ring. Loose ~1 mm³ chips left at tight window tips are dropped and counted in the report; the stage is flagged `suspect` if the removed volume disagrees with rim length × c²/2 (widened for facets) or the body is not one solid.
5. **Spoke grooves**: channels parallel to the finished top. `groove_offsets` are fractions of the half width (`[0]` = centre groove, `[0.62]` = one each side, `[]` = none).
6. **Back weight pockets** (`back_pocket_skin` > 0): a U-channel milled up under each spoke, leaving `back_pocket_wall` each side and the skin under the machined top; its width follows the real spoke width (tapered stems, widening spokes).
7. **Lip pockets** (optional) and **lug holes and seats**.

Facets, grooves and back pockets are lofted along each spoke's actual centreline (bowed arms, swept spokes), not the straight chord: on the bowed HF-6 arms the chord-following pockets had cut one wall down to 11–12 mm of a ~40 mm web.

## Recipes (all dimensions are eyeballed design assumptions, not measurements)

| Recipe | Photo | Family | What it tests |
|---|---|---|---|
| `recipes/hf6-y-split.json` (defaults) | …27214 | 6 × Y split, deep concave | facets, side grooves, 20 lip pockets |
| `recipes/wide6-centre-groove.json` | …27216 | 6 × single, parallel, 22" | centre groove, near-flat face, no lip pockets |
| `recipes/work6-tapered.json` | …27223 | 6 × single, widening, 16" | flat tops, big window fillets, small wheel, tapered back pockets |
| `recipes/v12-hub-fork.json` | …27218 | 6 × Y split at the hub = 12 spokes, 22" | directional sweep, deep concave, same Y family as HF-6 |

All four build as one valid solid; every recipe except `v12-hub-fork` has back pockets. The last three needed a recipe JSON only.


```sh
PYTHONPATH=services .venv/bin/python experiments/forged-blank/build.py \
  --recipe experiments/forged-blank/recipes/hf6-y-split.json --output artifacts/forged-blank-hf6
```

Recipe keys are `ForgedWheel` field names; unknown keys are rejected. `_photo` and `_view_x` are render hints only.
Outputs: `blank/part .step/.glb`, `recipe.json` (the full parameter set, needed to rebuild because STEP carries no history), `report.json` (per-operation removed volume, validity, STEP round trip, removal ratio), `comparison.png`, `blank-and-back.png`.

`check_walls.py <recipe> <build dir>` probes both side walls of every spoke with vertical rays and fails if the material there is thinner than 70% of the web (a pocket cut through the wall). Validated both ways: it fails the known-bad builds (rim-width pockets on the tapered WORK spokes, chord pockets on bowed arms) and passes the current four. An earlier version that only counted surface hits passed the bad builds.

Volumes use `wheelcam.mass_properties.volume` (Gauss–Kronrod). Plain `Shape.Volume()` reported 37 315 mm³ for an edge break whose Boolean difference was 1 874 mm³.

## Known gaps

- **Edge break is faceted**: one ruled wedge per rim sample, so the chamfer has small steps (not visible at normal render scale; not CAM-ready). Smooth versions all failed on 2026-09-23: B-Rep fillet/chamfer on faceted rims (0/317 edges), a pipe sweep (fixed-Z binormal gives zero volume; corrected Frenet hung), and chunked smooth lofts (invalid Boolean/`clean`). A wedge corner lying exactly on the window wall made the combined cut silently remove nothing, so the 45° face now runs 0.5 mm into the window.
- Not modelled: spoke side pockets, back weight pockets, centre-cap recess, window draft, real rim bead/drop-centre profile, multi-spoke/mesh families.
- The WORK spokes' slight lean is not matched: `spoke_sweep_deg: 10` made them look like a turbine, so its recipe leaves it at 0.
