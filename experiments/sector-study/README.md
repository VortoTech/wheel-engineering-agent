# Original-photo-constrained local Y-spoke study

## Correction discovered in this study

The original photo shows **six Y-shaped spoke groups and six lug positions**.
The earlier five-group / ten independent slender blade assumption was wrong.
`annotations.json` records all six observed root and lug locations. Original
SHA256 is checked before preparation; the `.source` file is used, not a recompressed
preview. The new annotations do not reuse the old repeated five-sector polygons.

This experiment does NOT rewrite the current production project or its history.

## Reproduce

```sh
.venv/bin/python experiments/sector-study/test_prepare.py
.venv/bin/python experiments/sector-study/prepare.py
/path/to/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python experiments/sector-study/blender_sector.py
cp experiments/sector-study/index.html artifacts/route-study/sector/index.html
```

Serve `artifacts/route-study` on localhost and open `/sector/`. The preceding route
study server uses `http://127.0.0.1:18766/sector/`.

## Method and evidence boundary

1. Manually annotate the right group's visible front skin at original image pixel
   coordinates, after 4x browser inspection. This annotation still needs review.
2. Fit a projected circular rim using alternating rim observations; report errors
   on the unused alternating rim points. This is a weak-perspective hypothesis,
   NOT unique physical camera calibration.
3. Infer a conditional hub recession from the projected hub offset. Use an
   assumed radial depth profile. All world coordinates are normalized to rim
   radius = 1. No millimetre dimensions have been recovered.
4. Lift front boundary pixels onto that assumed surface. This construction
   necessarily projects back to its input points. Near-zero round-trip error is
   a software check, NOT reconstruction accuracy.
5. Tessellate/refine the front patch and add an assumed sidewall. Make separate
   rim arc and bolt-seat context pieces. Small uncertain pockets are drawn in
   yellow but not invented as through-holes. Parts/junctions remain disconnected.
6. Rotate the same master by 60 degrees and compare with the upper-right group.
   These observations helped diagnose the wrong five-group topology; they are
   NOT a pristine held-out test set. Do not market this as generalization proof.

## Current results

- Five targeted tests pass: six-group evidence structure, camera orthonormality,
  conditional ray lifting, diagnostic points not fitting the camera, and retaining
  original annotated points in the interpolated curve.
- Blender 4.5.9 renders and exports `local-sector.blend` and `local-sector.glb`.
- The master patch now has a 7 source-pixel smoothstep relief band rising to
  0.010 normalized units plus a 0.006-unit three-segment physical bevel. These
  values are editable appearance hypotheses, not measured dimensions.
- Geometry-only and gunmetal renders use the identical evaluated mesh. The
  gunmetal is procedural (metallic 0.90, roughness 0.30, subtle micro bump), so
  no photographed highlight is baked into a color texture.
- Four separate closed mesh objects; zero nonmanifold edges per object. This does
  not verify shape accuracy, solid union, self-intersection, or manufacturing.
- Blender camera-to-pixel agreement is about 0.000056 px: only camera convention
  consistency, not an accuracy estimate.
- Adjacent-sector diagnostic, same final master and camera: five groups median
  19.7 px / maximum 35.7 px; six groups median 2.7 px / maximum 17.2 px.
- Our preliminary gate (median <= 5 px AND maximum <= 12 px) is NOT passed.
  The boundary points are manual and correspondence/visibility remain uncertain.
- No full wheel is generated. The original project's five-group model remains
  unchanged and should not be mistaken for the corrected study.

## Remaining work

Review root/branch boundaries and attachment seams; independently annotate an
unused sector before further fitting; test perspective/depth alternatives rather
than tuning this diagnostic until it passes. Then construct the real branch-root
and bolt-seat transitions, and only then consider full sixfold replication.
No safety, fitment, engineering, STEP-solid or CAM claim is made.
