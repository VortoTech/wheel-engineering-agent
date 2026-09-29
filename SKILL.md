---
name: wheel-engineering
description: Reconstruct a wheel from front and optional oblique photos plus known engineering specifications, preserving sources and unknowns while producing a recipe, visual preview, and verified STEP candidate. Use for WheelCAM wheel reconstruction and evidence review.
---

# Wheel Engineering Skill

Turn incomplete wheel references into a reviewable engineering **draft**. This Skill is the WheelCAM implementation of the Engineering Reconstruction Agent's Understand → Reason → Reconstruct → Verify → Report loop. Use deterministic geometry tools for construction and checking; a vision model may propose visible styling but cannot certify dimensions or manufacturing readiness.

## Understand and reason

Collect a straight-on front photo and, when available, an oblique photo. Record the source of known `diameter_in`, `width_in`, `pcd_mm`, `bolts`, `center_bore_mm`, and `et_mm` with `--spec-evidence`, such as `'{"pcd_mm":{"source":"drawing","reference":"drawing-01"}}'`. Omitted source stays `unspecified`; do not silently label it a user measurement. Catalog fitments are examples until linked to the photographed wheel. Do not read these millimeter values from an uncalibrated photo.

Run the recipe-only mode first when input completeness is uncertain. Read the `parameters`, `questions`, and `unknown` fields of `engineering_report.json` before building. The photo can supply spoke count and visible outlines as candidates; back geometry, material, wall thickness, pocket depth, and unprovided specifications remain questions or explicit design assumptions. If a required target dimension is missing, ask the user or continue only as a visual draft.

## Runtime and text input

This file is the single maintained instruction document for coding assistants. The application does **not** execute this Markdown. Its executable domain skill is implemented in `services/wheelcam/wheel_skill.py`, `wheel_skill_contract.py`, and the supporting modules. `.agents/skills/wheel-engineering/SKILL.md` and `.claude/skills/wheel-engineering/SKILL.md` are compatibility symlinks to this file, not separate implementations. Resolve documentation links from the repository root.

The workbench accepts text through `text_wheel.py`: the model proposes a supported style, deterministic review preserves explicit specifications and lists missing ones, then the recipe is built and checked. Start a text preview with:

```bash
PYTHONPATH=services .venv/bin/python scripts/demo_chain.py \
  --text 'Create a five-spoke wheel, 20 inch diameter, 10.5 inch width, ET15, 5x112, CB66.6' \
  --preview-only --out runs/text-example
```

In the workbench, `recipe_chat.py` applies allowed style changes and rebuilds the preview. Engineering changes require explicit confirmation through `workbench_revision.py`. A modified preview invalidates old delivery evidence; regenerate the delivery package. `scripts/demo_chain.py` orchestrates downstream `machining_step.py`, `manufacturing_demo.py`, and `drawing.py`. Machining STEP is a simplified separate artifact; it does not automatically contain the complete styled surface.

## Reconstruct

From the repository root with its installed `.venv`, use a **new or empty** output directory for each run:

```bash
PYTHONPATH=services .venv/bin/python -m wheelcam.wheel_skill \
  --front /path/to/front.jpg --oblique /path/to/oblique.jpg \
  --spec '{"diameter_in":20,"width_in":9.5,"pcd_mm":139.7,"bolts":6,"center_bore_mm":106.1,"et_mm":30}' \
  --spec-evidence '{"pcd_mm":{"source":"drawing","reference":"drawing-01"}}' \
  --no-build --out artifacts/wheel-skill/example-understanding
```

Use `--kernel mesh --visual-check` in a second directory for a GLB, numerical photo comparison and overlay. It stays at **L0**. Use `--kernel brep --visual-check` in a third directory when the user needs a STEP candidate; that path can take much longer and may fail on complex outlines. Do not silently substitute a mesh for a failed B-Rep. Visual scores are descriptive and never increase readiness.

Use `--style-agent` only to correct the preset styling after the initial recipe is reconstructed. The agent asks the visual model whether a feature exists; it searches style sizes by rendering candidates and comparing their edges with the photo. Every accepted edit passes the conversational style whitelist, then the model is rebuilt and checked again. Read `style_agent` and the saved `style/style_agent.json` for the changed fields, questions and scores. Style edits cannot modify engineering dimensions or increase readiness. Without `WHEELCAM_VLM_BASE_URL`, or if the agent fails, keep the original preset and review the question in `engineering_report.json`.

The optional visual styling model is **off by default**. Use `--use-vlm` only when the reference photos may be sent to the configured `WHEELCAM_VLM_URL`; check the endpoint before sending private images. Without it, `forged_y` styling is a default that needs review, not an observation.

## Verify and report

Read `recipe.json`, `engineering_report.json`, and `cad/report.json` after a build. Confirm the output kernel, artifact list and every check. Only a passing single-solid B-Rep **and** STEP round-trip can reach L1. L2 requires all six key specifications to have user, drawing or measurement provenance plus passing checks; catalog and unspecified values cannot advance it. The ET check currently uses the construction profile, not independent STEP metrology. L3 means only that the current geometry checks passed; it is not complete engineering, strength, or manufacturing validation. The recipe is regenerable; STEP itself does not carry native parametric feature history.

Present visible match separately from dimensional and geometry checks. Show what was observed, supplied, inferred, defaulted, and still unknown. Always retain `manufacturing_status=not_released`. Ask for the next necessary measurement if it blocks progress. No L4/L5, approved NC, or machining claim comes from this Skill.

For optimization or competition evidence, follow [evaluation.md](docs/skill-evaluation.md). For the current domain data flow and engineering limits, see [engineering-reconstruction-agent.md](docs/engineering-reconstruction-agent.md) and [rebuild-architecture.md](docs/rebuild-architecture.md).
