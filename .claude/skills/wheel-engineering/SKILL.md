---
name: wheel-engineering
description: Reconstruct an automotive (forged) wheel as verified, editable CAD from product photos plus known specs. Use when the user wants a wheel modelled from a photo, asks what can or cannot be determined about a wheel from images, or needs a wheel STEP with a verification report. Do not use for general CAD parts.
---

# Wheel Engineering Skill

Turns a straight-on photo, an optional 20–45° oblique photo and a few specs into a parametric wheel
recipe, a STEP solid and an engineering report. The rule of this skill: **measure what the photos
show, take dimensions from the user or a spec, and name everything else as unknown. Never guess
silently.**

## What each input can and cannot give

| Input | Gives | Cannot give |
|---|---|---|
| Straight-on front photo | spoke group count, window outlines, stem slots / small holes (±1–2 mm after scaling) | any depth, scale |
| Oblique photo (20–45°) | dish depth over the window band (camera tilt from the lip ellipse fixes mm per pixel of parallax) | spoke chamfer depth, back side |
| User / spec | diameter, width (J), PCD, bolt count, centre bore, ET | — |
| Nothing | back pockets, wall thickness, chamfer depth, material | these stay template defaults and are listed under `unknown` |

Vision-language models are not used for counting. On 8 product photos (2 runs each, 2026-09-25),
Step3-VL-10B on the DGX Spark got the spoke group count right on 2 of 7 and the lug count on 2 of 8,
and looped without an answer in 5 of 16 runs; MiMo v2.6 Pro got the group count right on at most 2
of 7, said 5 lugs on every 6-lug wheel, and returned nothing in 7 of 16 runs. StepFun
step-3.7-flash and step-5-preview answered "5 groups, 5 lugs" on every photo they answered (right
only on the one 5-spoke wheel, never on lugs) and returned nothing in 11 and 6 of 16 runs. Counts come from the
sector-symmetry measurement, which reports its own confidence and was right on every photo tested.

## Workflow

1. **Collect inputs.** Ask for a straight-on front photo. Ask for the six key specs (`diameter_in`,
   `width_in`, `pcd_mm`, `bolts`, `center_bore_mm`, `et_mm`). If the user has no oblique photo, say
   the dish depth will be a default and ask whether one is available.
2. **Dry run** (seconds, no CAD):
   ```bash
   .venv/bin/python -m wheelcam.wheel_skill --front FRONT.jpg [--oblique ANGLE.jpg] \
       --spec '{"diameter_in": 20, "width_in": 9.5, "pcd_mm": 139.7, "bolts": 6, "center_bore_mm": 106.1, "et_mm": 30}' \
       --out runs/NAME --no-build
   ```
   Read `runs/NAME/engineering_report.json`: `questions` (ask the user these before building),
   `parameters` (value, source, confidence per parameter), `unknown`.
3. **Resolve questions with the user.** Do not invent a missing spec. A photo confidence below 0.3
   means: show the user the measurement and ask whether to use it.
4. **Build and verify** (5–15 min for a traced wheel): the same command without `--no-build`. Run
   long builds in the background with a memory cap; a runaway OCC boolean once used ~96 GB.
5. **Report** to the user: the readiness level, `readiness_limits`, the checks table, the unknowns
   and the estimated mass. Never describe the result as manufacturing-ready.

## Readiness levels

| Level | Meaning | Needs |
|---|---|---|
| L0 | visual only | — |
| L1 | parametric: one valid STEP solid from a recipe | build succeeded |
| L2 | dimensioned: key dimensions from user/spec and verified on the solid | all six specs; diameter, width, ET, bolt pattern checks pass |
| L3 | validated: L2 + geometry integrity, bolt pattern, rotational symmetry, no sliver faces | all checks pass |
| L4/L5 | simulation / manufacturing review | outside this skill; never assigned |

## Checks performed on the solid

`single_valid_solid`, `outer_diameter`, `overall_width`, `offset_et`, `bolt_pattern` (count, PCD,
even spacing), `rotational_symmetry` (sector volume spread < 1 %), `no_sliver_faces` (none under
5 mm², they break CAM). The build123d MCP server, when configured, gives an independent second
check: `import_cad_file`, `validate`, `find_hole_patterns`, `measure(material="6061")`.

## Limits to state every time

- Depth from one oblique photo is ±5 mm on real product photos (retouched, lit for marketing).
- Spoke chamfer depth, back pockets and wall thickness are not measured.
- Mass is an estimate with 6061; no strength, fatigue or impact validation (SAE J2530 / JWL / VIA).
- Reproducing a branded commercial design for sale may infringe design patents and trademarks;
  use branded wheels only as accuracy benchmarks.
