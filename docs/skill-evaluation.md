# Wheel Skill evaluation

Compare a baseline and candidate on the same fixed photos, supplied specifications, hardware, kernel, and scoring code. Record source revision, input hashes, recipe, output path, elapsed time, errors, and visual overlays. Use separate output directories and keep every failure in the denominator. Do not retune on the evaluation set after viewing its scores.

The 10-wheel manifest is `experiments/forged-blank/evalset.json`; product photos are **not in the repository**. Listed fitments are examples, not confirmed dimensions of each photographed wheel. Run `experiments/forged-blank/evalset.py --photos DIR --out NEW_DIR --kernel mesh` for visual and build comparison. Run `--kernel brep` separately for STEP behavior. The HF6-4 benchmark is a development case; judge generalization on other wheels.

For the user's private engineering-design files, follow [asset-evaluation-plan.md](asset-evaluation-plan.md). Run `scripts/asset_eval_intake.py` first to detect duplicate CAD truths; the original renders are composite images and must be paired to the correct size variant. M61 was used to tune front detection and symmetry in September 2026, so it is a development case rather than an independent holdout. The `.x_t` references are not yet part of the scoring script.

Report builds/attempts, per-wheel front window IoU, front edge error, oblique scores, time, failures, B-Rep valid single solid, STEP round-trip, and supplied dimension checks. Report visual and engineering outcomes separately. A higher mesh IoU cannot be described as better STEP validity or manufacturing readiness. Compare representative overlays, not only a mean score.

Current boundary: `docs/rebuild-architecture.md` records an initial 52/60 P1 parameter matrix with no complete re-run. The default mesh path outputs GLB, not STEP. `experiments/partpacker-gb10/RESULTS.md` records a GB10 visual mesh with separated depth layers. These do not prove real wheel dimensions, strength, CAM suitability, or an approved machining program.
