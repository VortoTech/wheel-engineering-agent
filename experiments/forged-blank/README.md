# Forged-blank prototype

Builds a wheel in forging/machining order from one `ForgedWheel` parameter set:

1. **Revolved forging blank**: hub plateau, concave face web (`z_top`/`z_back` profiles), lip face ring, barrel. One solid, so hub and spokes are never separate bodies.
2. **Face facets**: two sloped ruled-loft cuts per spoke segment leave a ridge and side facets that follow the face and fade out at the hub and ring (`facet_deg: 0` gives flat tops).
3. **Through windows**: 2D sketch = annulus minus spoke footprints, with only real corners filleted, extruded along the axis. Families: `y_split` (stem + two bowed arms) and `single` (hub width → rim width).
4. **Spoke grooves**: channels parallel to the finished top. `groove_offsets` are fractions of the half width (`[0]` = centre groove, `[0.62]` = one each side, `[]` = none).
5. **Lip pockets** (optional) and **lug holes and seats**.

## Recipes (all dimensions are eyeballed design assumptions, not measurements)

| Recipe | Photo | Family | What it tests |
|---|---|---|---|
| `recipes/hf6-y-split.json` (defaults) | …27214 | 6 × Y split, deep concave | facets, side grooves, 20 lip pockets |
| `recipes/wide6-centre-groove.json` | …27216 | 6 × single, parallel, 22" | centre groove, near-flat face, no lip pockets |
| `recipes/work6-tapered.json` | …27223 | 6 × single, widening, 16" | flat tops, big window fillets, small wheel |

The last two were produced from a recipe JSON only, with no code changes.

```sh
PYTHONPATH=services .venv/bin/python experiments/forged-blank/build.py \
  --recipe experiments/forged-blank/recipes/hf6-y-split.json --output artifacts/forged-blank-hf6
```

Recipe keys are `ForgedWheel` field names; unknown keys are rejected. `_photo` and `_view_x` are render hints only.
Outputs: `blank/part .step/.glb`, `recipe.json` (the full parameter set, needed to rebuild because STEP carries no history), `report.json` (per-operation removed volume, validity, STEP round trip, removal ratio), `comparison.png`, `blank-and-back.png`.

## Known gaps

- **Top-edge fillets on window rims**: the OCC fillet refused every window loop where the faceted top meets the walls (0/317 edges, 2026-09-23). Next approach: model the edge break in the cutter, not as a B-Rep fillet.
- Not modelled: spoke side pockets, back weight pockets, centre-cap recess, window draft, real rim bead/drop-centre profile, swept or asymmetric spokes (the WORK spokes lean slightly), multi-spoke/mesh families.
