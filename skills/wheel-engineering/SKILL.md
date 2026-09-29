---
name: wheel-engineering
description: Reconstruct or revise automotive wheel drafts from photos, text and known specifications using the Wheel Engineering runtime. Use for wheel recipes, visual previews, STEP candidates and evidence review; preserve unknown dimensions and manufacturing limits.
---

# Wheel Engineering

Convert incomplete references into a reviewable engineering draft: Understand → Reason → Reconstruct → Verify → Report. Use deterministic construction and checks; vision proposes styling, not certified dimensions.

## Prepare the runtime

This directory is a portable skill package. It contains instructions and a launcher, not the CAD engine or model weights. Read [setup.md](references/setup.md) for installation. Run `python3 <skill-dir>/scripts/run.py doctor` before the first build. If it fails, resolve the reported dependency or path issue; do not invent results. No automatic installation, model deployment or remote upload occurs in the launcher.

## Choose a workflow

Use the installed skill's absolute directory in these commands. Input and output paths remain relative to the caller's working directory.

- **Photo understanding:** `python3 <skill-dir>/scripts/run.py photo --front /path/to/front.jpg --spec '{"diameter_in":20,"width_in":10.5,"pcd_mm":112,"bolts":5,"center_bore_mm":66.6,"et_mm":15}' --no-build --out runs/understanding`
- **Photo preview:** omit `--no-build`, add `--kernel mesh --visual-check`, and use a new output directory. For optional oblique evidence add `--oblique /path/to/oblique.jpg`.
- **Text preview:** `python3 <skill-dir>/scripts/run.py text 'Five-spoke wheel, 20x10.5, ET15, 5x112, CB66.6' --out runs/text-preview`. Requires a configured chat model; supported template styling only.
- **Full styled STEP candidate:** use photo mode with `--kernel brep`. This can be slow or fail. Do not replace a failed B-Rep with a mesh and report STEP success.
- **Delivery preparation:** use `chain /path/to/case --out runs/delivery`, or `chain --snapshot /path/to/snapshot.json --out runs/revised-delivery`. Read [contracts.md](references/contracts.md) before using the simplified machining STEP route.

Run `photo --help`, `text --help` or `chain --help` for the installed runtime's options. Always choose a new or empty output directory.

## Reason and preserve evidence

For known specifications, supply photo mode's `--spec-evidence` map, for example `'{"pcd_mm":{"source":"drawing","reference":"drawing-01"}}'`. Unspecified provenance is not a measurement. Never estimate uncalibrated engineering dimensions from a photograph. Read the report's parameters, questions and unknowns before claiming completion.

A photo can suggest spoke count and visible outlines. Back geometry, wall thickness, material and missing dimensions remain unknown or explicit template assumptions. Ask for missing measurements if they block the requested result; otherwise clearly label a visual draft.

Optional `--style-agent` corrects preset styling through vision questions and render comparison; it cannot change engineering dimensions or raise readiness. Verify the configured endpoint and input-sharing authorization before sending photos. Missing configuration or failed calls must remain visible in the report. Legacy `--use-vlm` has a separate endpoint configuration; see setup.

Workbench conversation uses `recipe_chat.py` for allowed style edits and rebuilds. Engineering changes require explicit confirmation. After changes, old photo scores and delivery files do not validate the new recipe. Generate and verify a new delivery version.

## Verify the actual result

Read `recipe.json`, `engineering_report.json`, available CAD reports and downstream manifests. Use [contracts.md](references/contracts.md) to interpret artifacts and readiness. Report failures and skipped style operations. Keep visual fit separate from geometry and dimension checks.

GLB stays L0. A STEP candidate needs valid solid and round-trip evidence; dimensions need confirmed provenance and checks. L3 does not certify strength, fatigue or manufacturing. Preserve `manufacturing_status=not_released`. No approved NC or L4/L5 follows from this skill.

For quality comparisons, read [evaluation.md](references/evaluation.md). Report the actual execution device; a local CAD job calling a Spark model is not a Spark full-chain run.
