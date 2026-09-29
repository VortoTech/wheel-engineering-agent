# Evaluation protocol

Freeze input files, supplied dimensions, recipe/template revision, model settings, hardware and scoring code before comparing variants. Record source commit, input hashes, output directories, actual execution host, elapsed time and failures. Use a fresh output directory per attempt and keep failures in the denominator.

Separate visual metrics (front IoU, edge error, reference overlays) from engineering checks (solid validity, STEP round-trip, dimensional evidence and missing geometry). A better same-image fit is not better manufacturing precision.

Cases used to tune templates or thresholds are calibration cases. The project's 13 real-order cases participated in calibration and cannot establish unseen-design generalization. Seek authorized new cases for independent evaluation; if unavailable, label frozen repeats as regression evidence.

Compare only equivalent acceptance criteria. Report incomplete generic-model outputs as failures under that specific protocol, not proof that all models without this skill fail. Preserve unknown dimensions, skipped operations and model-call failures in the evidence.
