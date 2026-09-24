"""Controlled A/B evidence for the experimental semantic master-sector compiler.

The baseline and replay share identical assumed dimensions and input outlines.
This measures adapter equivalence and one local edit, NOT photo reconstruction,
provider quality, minimum wall thickness or manufacturing readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import cadquery as cq
import numpy as np

from wheelcam.geometry import _volume, _window_outline_wire, export_model, inspect_shape
from wheelcam.mass_properties import measure_volume, volume_method
from wheelcam.models import WheelSpec, default_sources
from wheelcam.sector_program import compile_sector, example_program, patch_feature, sample_feature
from wheelcam.template import TEMPLATE_VERSION
from wheelcam.windows import HUB_KEEP_MM, _closed, _point_segment, rotate
from scripts.sector_boolean import boolean_policy, compare_boolean


VOLUME_NOISE_MM3 = 0.01
OUTLINE_DEVIATION_LIMIT_MM = 0.1


class BooleanComparisonError(ValueError):
    """Reject acceptance but retain all measured residuals and operation policy."""

    def __init__(self, message, details):
        super().__init__(message)
        self.details = {**details, "passed": False}


def fixture_base():
    # Disable finishing operations to isolate the semantic opening edit. They
    # have separate coverage; no claim is made about fillet/draft edit locality.
    return WheelSpec(junction_fillet_mm=0, window_edge_fillet_mm=0,
                     pocket_depth_mm=0, valve_diameter_mm=0, center_bore_mm=66.6)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def read_verified_build(case: Path, spec: WheelSpec):
    """Bind a historical STEP to its exact requested/resolved recipe, read-only."""
    recipe = json.loads((case / "recipe.json").read_text())
    report = json.loads((case / "report.json").read_text())
    for name in ("recipe.json", "wheel.step"):
        actual = hashlib.sha256((case / name).read_bytes()).hexdigest()
        if actual != report["artifacts"][name]["sha256"]:
            raise ValueError(f"Existing {name} hash does not match its report: {case}")
    if recipe["spec"] != spec.model_dump(mode="json"):
        raise ValueError(f"Existing build recipe does not match the controlled input: {case}")
    if report.get("build_status") != "exact" or report.get("build_resolution", {}).get("resolved_recipe") != recipe["spec"]:
        raise ValueError(f"Existing build was adjusted or lacks matching resolved recipe: {case}")
    return report


def volume_stability(shape):
    """Do not trust roundtrip agreement when the same integrator is unstable.

    A tolerance sweep is a consistency check, not proof of absolute accuracy.
    Both original and reimported STEP can agree on the same erroneous integral.
    """
    measurements = []
    for epsilon in (1e-5, 1e-6, 1e-7):
        measurement = measure_volume(shape, epsilon=epsilon)
        measurements.append({"epsilon": epsilon, **measurement.to_dict()})
    volumes = [item["volume_mm3"] for item in measurements]
    spread = (max(volumes) - min(volumes)) / max(abs(min(volumes)), 1e-9)
    return {"measurements": measurements, "relative_spread": spread,
            "method": volume_method(),
            "relative_spread_limit": 5e-5,
            "passed": all(np.isfinite(value) and value > 0 for value in volumes) and spread <= 5e-5,
            "scope": "Numerical integration consistency, not independent dimensional certification"}


def sampled_curve_deviation(feature, outline):
    """Bidirectional point-to-polyline estimate, not a certified Hausdorff bound."""
    reference = np.asarray(sample_feature(feature, count=1024), dtype=float)
    edge = _window_outline_wire(outline).Edges()[0]
    actual = np.asarray([(point.x, point.y) for point in edge.positions(
        np.linspace(0, 1, 2000, endpoint=False))])
    distance = max(float(_point_segment(actual, *_closed(reference)).max()),
                   float(_point_segment(reference, *_closed(actual)).max()))
    return {"feature_id": feature.id, "sampled_bidirectional_outline_deviation_mm": distance,
            "reference_samples": len(reference), "native_curve_samples": len(actual),
            "passed": distance <= OUTLINE_DEVIATION_LIMIT_MM}


def shape_volume(shape):
    # A legitimate empty Boolean is an empty, valid Compound. A null or invalid
    # native result is an error, not evidence that two geometries are equal.
    if shape.isNull() or not shape.isValid():
        raise ValueError("Invalid/null Boolean result; cannot infer zero difference")
    volume = _volume(shape)
    if not np.isfinite(volume) or volume < -1e-8:
        raise ValueError("Invalid signed Boolean volume")
    return max(0.0, volume)


def difference(first, second):
    # CadQuery passes shared native subshapes to Boolean operations. Isolate
    # every operation and measure the pristine operands before any operation.
    before_volume, after_volume = shape_volume(first), shape_volume(second)
    removed, removed_operation = compare_boolean(first, second, operation="cut")
    added, added_operation = compare_boolean(second, first, operation="cut")
    common, common_operation = compare_boolean(first, second, operation="common")
    removed_volume, added_volume, common_volume = map(shape_volume, (removed, added, common))
    residuals = {"before_minus_common_minus_removed_mm3": abs(before_volume - common_volume - removed_volume),
                 "after_minus_common_minus_added_mm3": abs(after_volume - common_volume - added_volume),
                 "net_change_minus_removed_plus_added_mm3": abs(before_volume - after_volume - removed_volume + added_volume)}
    residual = max(residuals.values())
    values = {"before_mm3": before_volume, "after_mm3": after_volume, "common_mm3": common_volume,
              "removed_mm3": removed_volume, "added_mm3": added_volume,
              "conservation_residual_mm3": residual, "conservation_residuals": residuals,
              "volume_method": volume_method(),
              "operations": {"removed": removed_operation, "added": added_operation, "common": common_operation}}
    if residual > VOLUME_NOISE_MM3:
        raise BooleanComparisonError(f"Boolean volume conservation failed: {residual:g} mm3", values)
    return removed, added, values


def edit_locality(before, after, spec, feature_index):
    """Measure actual reopened STEP differences against the edited feature orbit."""
    removed, added, values = difference(before, after)
    outline = spec.window_outlines_mm[feature_index]
    tools = [cq.Solid.extrudeLinear(_window_outline_wire(rotate(outline,
                 spec.spoke_phase_deg + index * 360 / spec.spoke_count), -1000),
                 [], cq.Vector(0, 0, 2000)) for index in range(spec.spoke_count)]
    operations = values["operations"]

    def measure_boolean(name, first, others, operation):
        shape, record = compare_boolean(first, others, operation=operation)
        operations[name] = record
        return shape_volume(shape)

    outside = measure_boolean("outside_orbit", removed, tools, "cut")
    per_copy = [measure_boolean(f"orbit_{index}", removed, tool, "common")
                for index, tool in enumerate(tools)]
    hub = cq.Solid.makeCylinder(spec.hub_diameter_mm / 2 + HUB_KEEP_MM,
                                2000, cq.Vector(0, 0, -1000))
    hub_change = (measure_boolean("removed_in_hub", removed, hub, "common")
                  + measure_boolean("added_in_hub", added, hub, "common"))
    rim_start = spec.rim_diameter_in * 25.4 / 2 - spec.rim_wall_mm
    outer = cq.Solid.makeCylinder(spec.rim_diameter_in * 25.4, 2000, cq.Vector(0, 0, -1000))
    inner = cq.Solid.makeCylinder(rim_start, 2000, cq.Vector(0, 0, -1000))
    collar, operations["rim_collar"] = compare_boolean(outer, inner, operation="cut")
    rim_change = (measure_boolean("removed_in_rim", removed, collar, "common")
                  + measure_boolean("added_in_rim", added, collar, "common"))
    spread = max(per_copy) - min(per_copy)
    coverage_residual = abs(values["removed_mm3"] - outside - sum(per_copy))
    values.update(outside_edited_feature_orbit_mm3=outside,
                  protected_hub_change_mm3=hub_change, protected_outer_rim_change_mm3=rim_change,
                  removed_per_copy_mm3=per_copy, copy_volume_spread_mm3=spread,
                  orbit_coverage_residual_mm3=coverage_residual)
    values["passed"] = (values["removed_mm3"] > 1 and values["added_mm3"] <= VOLUME_NOISE_MM3
                         and outside <= VOLUME_NOISE_MM3 and hub_change <= VOLUME_NOISE_MM3
                         and rim_change <= VOLUME_NOISE_MM3 and min(per_copy) > 0
                         and spread <= VOLUME_NOISE_MM3 and coverage_residual <= VOLUME_NOISE_MM3)
    return values


def render_comparison(shapes, output, before_spec, after_spec, feature_index):
    """Orthographic projections of actual STEP tessellation; no generated imagery."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    fig, axes = plt.subplots(2, 3, figsize=(15, 9), facecolor="white")
    titles = ["A / sampled-outline baseline", "B / semantic program replay", "C / root slot width +2 mm"]
    target = np.asarray(rotate(after_spec.window_outlines_mm[feature_index], after_spec.spoke_phase_deg))
    bounds = (target.min(0) - 8, target.max(0) + 8)
    for column, (shape, title) in enumerate(zip(shapes, titles)):
        vertices, faces = shape.tessellate(.25, .1)
        triangles = np.array([v.toTuple() for v in vertices])[np.array(faces)]
        normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
        light = np.array([-.25, -.35, .9028])
        intensity = .43 + .42 * np.maximum(0, normal @ light)
        order = np.argsort(triangles.mean(1)[:, 2])
        colors = np.repeat(intensity[:, None], 3, axis=1)
        for row in range(2):
            ax = axes[row, column]
            ax.add_collection(PolyCollection(triangles[order, :, :2], facecolors=colors[order],
                                            edgecolors="none", antialiaseds=False))
            ax.set_aspect("equal")
            if row == 0:
                ax.set(xlim=(-250, 250), ylim=(-250, 250), title=title)
                ax.axis("off")
            else:
                ax.set(xlim=(bounds[0][0], bounds[1][0]), ylim=(bounds[0][1], bounds[1][1]),
                       title="Edited feature / actual STEP close-up", xlabel="X (mm)", ylabel="Y (mm)")
        old = np.asarray(rotate(before_spec.window_outlines_mm[feature_index], before_spec.spoke_phase_deg))
        if column == 2:
            axes[1, column].plot(*np.vstack([old, old[0]]).T, color="#c77a00", linewidth=1,
                                 linestyle="--", label="previous input boundary")
            axes[1, column].legend(fontsize=8, loc="upper left")
    fig.suptitle("Synthetic control experiment - not a photo fit; dimensions assumed; NOT RELEASED")
    fig.tight_layout()
    fig.savefig(output / "comparison.png", dpi=160)
    plt.close(fig)


def run(output: Path, groups=5, reuse_builds: Path | None = None):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"Refusing to overwrite existing evidence: {output}")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    program, base = example_program(family="split_y", groups=groups), fixture_base()
    baseline, manifest = compile_sector(program, base)
    target_index = next(index for index, feature in enumerate(program.features)
                        if hasattr(feature, "width_mm"))
    feature = program.features[target_index]
    candidate_program = patch_feature(program, feature.id, {"width_mm": feature.width_mm + 2})
    candidate, candidate_manifest = compile_sector(candidate_program, base)
    replay, replay_manifest = compile_sector(type(program).model_validate_json(program.model_dump_json()), base)
    write_json(output / "protocol.json", {
        "schema": "wheel-sector-comparison-protocol-v1", "base_spec": base.model_dump(mode="json"),
        "base_spec_sha256": canonical_hash(base.model_dump(mode="json")),
        "program": program.model_dump(mode="json"), "compilation": manifest,
        "candidate_program": candidate_program.model_dump(mode="json"),
        "candidate_compilation": candidate_manifest,
        "all_dimensions_source": "synthetic_design_assumption",
        "reused_builds": str(reuse_builds.resolve()) if reuse_builds else None,
        "cadquery_version": cq.__version__, "template_version": TEMPLATE_VERSION,
        "boolean_policy": boolean_policy(),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path(__file__), Path(__file__).resolve().parents[1] / "services/wheelcam/sector_program.py",
            Path(__file__).resolve().parents[1] / "services/wheelcam/geometry.py",
            Path(__file__).resolve().parents[1] / "services/wheelcam/mass_properties.py",
            Path(__file__).resolve().parent / "sector_boolean.py")}})
    unchanged = [old.id for old, new, old_outline, new_outline in zip(
        program.features, candidate_program.features, baseline.window_outlines_mm, candidate.window_outlines_mm)
        if old.id != feature.id and old == new and old_outline == new_outline]
    unchanged_parameters = {key: value for key, value in baseline.model_dump().items()
                            if key != "window_outlines_mm"}
    parameters_equal = unchanged_parameters == {key: value for key, value in candidate.model_dump().items()
                                               if key != "window_outlines_mm"}
    curve_checks = {name: [sampled_curve_deviation(item, outline) for item, outline in zip(
        source.features, spec.window_outlines_mm)] for name, source, spec in
        [("baseline", program, baseline), ("candidate", candidate_program, candidate)]}
    write_json(output / "curve-checks.json", curve_checks)
    if not all(item["passed"] for values in curve_checks.values() for item in values):
        write_json(output / "summary.json", {"schema": "wheel-sector-comparison-v1", "experiment_passed": False,
            "failed_gate": "native_curve_sampled_deviation", "curve_checks": curve_checks,
            "curve_deviation_limit_mm": OUTLINE_DEVIATION_LIMIT_MM, "photo_fidelity": "not_measured",
            "ai_provider_changed": False, "engineering_approved": False, "manufacturing_status": "not_released"})
        raise ValueError("Native cutter curve deviates beyond experimental gate; see curve-checks.json")
    records, shapes = [], []
    for name, spec, source, compiled in [
        ("a-outline-baseline", baseline, None, None),
        ("b-program-replay", replay, program, replay_manifest),
        ("c-slot-edit", candidate, candidate_program, candidate_manifest),
    ]:
        case = (reuse_builds or output) / name
        snapshot = {"name": name, "spec": spec.model_dump(mode="json"), "sources": default_sources(),
                    "base_spec": base.model_dump(mode="json"),
                    "base_spec_sha256": canonical_hash(base.model_dump(mode="json")),
                    "compiled_spec_sha256": canonical_hash(spec.model_dump(mode="json")),
                    "template_version": TEMPLATE_VERSION, "preparation": {},
                    "input_provenance": "synthetic_design_assumptions_not_photograph",
                    "sector_program": source.model_dump(mode="json") if source else None,
                    "sector_compilation": compiled}
        before = time.monotonic()
        if reuse_builds:
            report = read_verified_build(case, spec)
        else:
            case.mkdir()
            if source:
                write_json(case / "sector-program.json", source.model_dump(mode="json"))
                write_json(case / "compilation.json", compiled)
            report = export_model(spec, case, snapshot=snapshot)
        shapes.append(cq.importers.importStep(str(case / "wheel.step")).val())
        current = inspect_shape(shapes[-1], spec)
        records.append({"case": name, "checks": current["checks"], "build_status": report["build_status"],
                        "build_artifacts": str(case.resolve()),
                        "step_sha256": hashlib.sha256((case / "wheel.step").read_bytes()).hexdigest(),
                        "volume_mm3": current["volume_mm3"], "volume_measurement": current["volume_measurement"],
                        "build_report_evidence": {"historical": bool(reuse_builds), "checks": report["checks"],
                            "volume_mm3": report["volume_mm3"], "volume_measurement": report.get("volume_measurement"),
                            "step_volume_relative_delta": report["step_volume_relative_delta"]},
                        "seconds": round(time.monotonic() - before, 3)})
        print(f"{name}: STEP inspected with {current['volume_measurement']['method']}", flush=True)
    write_json(output / "volume-measurements.json", {"schema": "wheel-volume-measurements-v1",
        "measurements": [{"case": item["case"], "step_sha256": item["step_sha256"],
                          **item["volume_measurement"]} for item in records[1:]]})
    # Save a view even when a subsequent geometry/evidence gate fails.
    render_comparison(shapes, output, baseline, candidate, target_index)
    stability = []
    for index, shape in enumerate(shapes):
        stability.append(volume_stability(shape))
        print(f"{records[index]['case']}: integration stability {stability[-1]['passed']}", flush=True)
    write_json(output / "volume-stability.json", stability)
    numerical_stability_passed = all(item["passed"] for item in stability)
    equivalence, locality = {"passed": False}, {"passed": False}
    comparison_errors = []
    if not numerical_stability_passed:
        comparison_errors.append("Volume integration is tolerance-sensitive; volume-only Boolean acceptance is blocked")
    else:
        try:
            # Independent copies avoid OCCT updates to shared subshapes leaking
            # from one Boolean measurement into the next.
            _, _, equivalence = difference(shapes[0].copy(), shapes[1].copy())
            equivalence["passed"] = max(equivalence[key] for key in (
                "removed_mm3", "added_mm3", "conservation_residual_mm3")) <= VOLUME_NOISE_MM3
            write_json(output / "baseline-equivalence.json", equivalence)
            print(f"Baseline replay equivalence: {equivalence['passed']}", flush=True)
            locality = edit_locality(shapes[1].copy(), shapes[2].copy(), candidate, target_index)
        except BooleanComparisonError as error:
            if equivalence["passed"]:
                locality = error.details
            else:
                equivalence = error.details
            comparison_errors.append(str(error))
        except ValueError as error:
            comparison_errors.append(str(error))
    gates = {"reopened_step_geometry_and_tracked_operations": all(
        all(value is True for value in item["checks"].values()) and item["build_status"] == "exact" for item in records),
        "deterministic_compilation": baseline == replay and manifest == replay_manifest,
        "volume_integration_stable": numerical_stability_passed,
        "native_curve_sampled_deviation": all(item["passed"] for values in curve_checks.values() for item in values),
        "baseline_replay_equivalent": equivalence["passed"], "local_material_removal": locality["passed"],
        "other_features_unchanged": len(unchanged) == len(program.features) - 1,
        "other_recipe_parameters_unchanged": parameters_equal}
    summary = {"schema": "wheel-sector-comparison-v1", "groups": groups,
        "input_provenance": "synthetic_controlled_fixture_not_photograph",
        "scope": "Same kernel and assumed dimensions: sampled-outline baseline vs semantic replay vs single feature edit",
        "edit": {"feature_id": feature.id, "parameter": "width_mm", "from": feature.width_mm,
                 "to": feature.width_mm + 2, "unchanged_feature_ids": unchanged},
        "cases": records, "baseline_equivalence": equivalence, "edit_locality": locality,
        "volume_method": volume_method(), "boolean_policy": boolean_policy(), "builds_reused": bool(reuse_builds),
        "current_export_roundtrip_rerun": not bool(reuse_builds),
        "volume_stability": stability, "comparison_errors": comparison_errors,
        "curve_checks": curve_checks, "curve_deviation_limit_mm": OUTLINE_DEVIATION_LIMIT_MM,
        "curve_check_scope": "Finite sampled estimate, not certified Hausdorff bound or minimum wall thickness",
        "boolean_volume_noise_limit_mm3": VOLUME_NOISE_MM3, "gates": gates,
        "experiment_passed": all(gates.values()), "photo_fidelity": "not_measured",
        "ai_provider_comparison": "not_run", "ai_provider_changed": False,
        "decision": "keep_current_provider_and_kernel; semantic_IR_remains_opt_in_experiment",
        "engineering_approved": False, "manufacturing_status": "not_released",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "limitations": ["Synthetic opening geometry, not reconstruction of the supplied product photo",
            "Only one slot width edit; arbitrary edits and other wheel families not certified",
            "Finishing features disabled to isolate this experiment",
            "Base surface, rim, hub, bolt seats and rear geometry retain existing template assumptions",
            "No independent photo benchmark, CAE, CAM or dimensional certification",
            "No production project was replaced and no live LLM was called"]}
    write_json(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", type=int, choices=range(5, 11), default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reuse-builds", type=Path,
                        help="Read/hash-check matching existing A/B/C STEP builds; write a new comparison directory")
    args = parser.parse_args()
    output = args.output or Path(f"artifacts/sector-program-{time.time_ns()}")
    preexisting_evidence = output.exists() and (not output.is_dir() or any(output.iterdir()))
    try:
        result = run(output, args.groups, reuse_builds=args.reuse_builds)
    except Exception as error:
        if not preexisting_evidence and output.is_dir() and not (output / "summary.json").exists():
            write_json(output / "failure.json", {"schema": "wheel-sector-comparison-failure-v1",
                "experiment_passed": False, "error_type": type(error).__name__, "error": str(error),
                "volume_method": volume_method(), "manufacturing_status": "not_released"})
        raise
    print(json.dumps({key: result[key] for key in ("experiment_passed", "gates", "edit_locality", "elapsed_seconds")}, indent=2))
    print(f"Evidence: {output / 'summary.json'}")
    raise SystemExit(0 if result["experiment_passed"] else 1)
