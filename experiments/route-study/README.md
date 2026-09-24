# Free local route study

This is a controlled **representation study**, not a benchmark of reconstruction
accuracy. It does not update the production project, its draft, or model history.

## Reproduce

From the WheelCAM repository root:

```sh
.venv/bin/python experiments/route-study/test_controls.py
.venv/bin/python experiments/route-study/generate.py
/path/to/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python experiments/route-study/blender_study.py
cp experiments/route-study/index.html artifacts/route-study/index.html
cp data/images/4ba1b11f7d354ff1a3b51d5b58f3a40f.jpg artifacts/route-study/source.jpg
.venv/bin/python -m http.server 18766 --bind 127.0.0.1 --directory artifacts/route-study
```

Open `http://127.0.0.1:18766`. No external scripts, paid API, or CDN are used.

## Experimental controls

- A: existing model `857056f66bfe4548ab2444668852b117`, STEP tessellated to STL.
- B: same tessellation, conservative Laplacian smoothing and smooth normals.
- C: hand-authored paired blade curves, elliptical CAD loft sections.
- D: the SAME section controls as C, eight-vertex rings with Blender Subdivision.
- E: existing SF3D result `cd68c8c258144e8ca6391d732a67738f`, no fresh inference.
- All variants have the same neutral material, studio lighting, camera positions,
  image dimensions, and normalized diameter. SF3D uses a bounding-box orientation
  heuristic, not a solved camera. Inspect its pose before interpreting comparisons.

## What this does not establish

- The new curves are a hand-authored hypothesis; no new image-fitting optimizer
  or independent annotation has been implemented in this study.
- C/D omit bolt pockets and detailed rim transitions. They compare blade surface
  construction, not complete wheel design quality.
- C has 29 separate potentially overlapping solids, not a fused production BRep.
- D is a visual mesh assembly. Smooth shading is not curvature-continuity proof.
- The render camera is NOT calibrated to the source product photo. No photo IoU,
  dimensional fidelity, manufacturing, strength, or vehicle-fit claim is made.
- The old baseline and historical SF3D output are reused, not retrained.
- Blender is free software; SF3D remains under its own model/license conditions.

## Follow-up gate

If C/D make the desired blade language more controllable, first fit a single
group's boundary against independently marked ORIGINAL-photo points and calibrate
the camera. Review root and outer junctions at enlarged scale before implementing
whole-wheel generation. Do not choose a method just because its render is smooth.

## Observed results (2026-09-20)

Blender 4.5.9 LTS ran on this Apple Silicon Mac, CPU Cycles, 16 samples,
720 x 720, front and oblique renders. The downloaded macOS ARM64 DMG SHA256 was
`e3a3d7aac381fb4e4d05197f99cd8899484d7e8bc4497c134066e6733f372238`, matching the
official release checksum. It was mounted read-only under
`/private/tmp/wheelcam-blender-trial/mount`, not installed over an application.

- A/B: smoothing softens shading/edges but retains the inappropriate plate-like
  spoke layout. Not an adequate reconstruction fix.
- C/D: both yield controllable narrow curved blades; D has softer lip/bridge
  transitions. Their overall silhouettes are very similar because controls are
  shared. Current controls are too slender and omit the reference's complex root
  and bolt-seat structure. Neither is an accepted reproduction of the photo.
- E: existing SF3D gray geometry is lumpy, with irregular openings and artifacts;
  removing texture reveals these issues. This says nothing about untested models.
- No best photo-reconstruction model can be declared from this experiment.
  C/D are useful candidate representations for a corrected, photo-constrained
  master-sector model. B is a cosmetic treatment, not the main route.
- TRELLIS.2, TripoSG, PyTorch3D fitting and fresh SF3D inference were NOT executed.
  No paid API or cloud GPU was used.

Validation: control symmetry/positive section unittest passed; generator and
renderer completed; all five GLBs, five Blender files, ten PNGs and CAD STEP
were produced. CAD reports valid geometry with 29 separate solids. Browser
confirmed six loaded images (reference plus five variants) and functioning view
switch. Production tests were not rerun because production source was untouched.
