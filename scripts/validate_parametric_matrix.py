"""Build a deterministic WheelCAM P1 acceptance matrix.

Every selected case is exported to STEP and read back by export_model().  The
Geometry pass counts stay separate from exact-request acceptance: a stable STEP
obtained by reducing a requested fillet is a degraded build, not an exact pass.
The legacy suite varies dimensions; the operations suite covers construction
paths with deterministic synthetic sketches, not fitted photographs. Neither is
photo-reconstruction accuracy, structural validation, or product approval.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

from wheelcam.geometry import export_model
from wheelcam.models import WheelSpec, default_sources
from wheelcam.mass_properties import volume_method


def matrix(count=60):
    diameters = [17, 18, 19, 20, 21, 22]
    widths = [7.0, 7.5, 8.5, 9.5, 10.5, 11.0]
    spokes = [5, 6, 7, 8, 9, 10]
    offsets = [-5, 10, 25, 35]
    sweeps = [-16, -8, 0, 8, 16]
    bores = [57.1, 60.1, 66.6, 72.6]
    for index in range(count):
        spoke_count = spokes[index % len(spokes)]
        bolt_count = 4+index % 3
        pcd = [108.0, 112.0, 120.0][index % 3]
        yield WheelSpec(
            rim_diameter_in=diameters[index % len(diameters)],
            rim_width_in=widths[(index // len(diameters)) % len(widths)],
            offset_et_mm=offsets[(index // 3) % len(offsets)],
            rim_wall_mm=5.0+(index % 3)*.75,
            hub_diameter_mm=180,
            hub_thickness_mm=44+(index % 3)*4,
            center_bore_mm=bores[index % len(bores)],
            bolt_count=bolt_count,
            bolt_circle_mm=pcd,
            bolt_diameter_mm=12+(index % 3),
            spoke_count=spoke_count,
            spoke_width_hub_mm=28 if spoke_count >= 9 else 34,
            spoke_width_rim_mm=22+(index % 3)*2,
            spoke_thickness_mm=20+(index % 4)*3,
            spoke_crown_mm=index % 4,
            face_curve=(index % 3)/2,
            sweep_deg=sweeps[(index // 2) % len(sweeps)],
            pocket_depth_mm=[0, 4, 6, 8][index % 4],
            junction_fillet_mm=[2, 3, 4, 5][index % 4],
        )


def _synthetic_window(center=36.0, r0=100.0, r1=200.0, h0=10.0, h1=20.0):
    """A tapered radial opening; dimensions are explicit design assumptions."""
    radii = [r0 + (r1 - r0) * index / 11 for index in range(12)]
    widths = [h0 + (h1 - h0) * (radius - r0) / (r1 - r0) for radius in radii]

    def point(radius, angle):
        return radius * math.cos(math.radians(angle)), radius * math.sin(math.radians(angle))

    return ([point(radius, center + width) for radius, width in zip(radii, widths)] +
            [point(radius, center - width) for radius, width in zip(reversed(radii), reversed(widths))])


def operation_matrix():
    """Small construction-path suite; these are not independent photo test cases."""
    common = {"spoke_count": 5, "junction_fillet_mm": 0, "pocket_depth_mm": 0}
    window = {**common, "spoke_method": "window", "window_edge_fillet_mm": 0,
              "window_outlines_mm": [_synthetic_window()]}
    three_windows = [_synthetic_window(center, 100, 190, 5, 7) for center in (14, 36, 58)]
    cases = [
        ("loft-single", common),
        ("loft-paired", {**common, "spoke_style": "paired", "sweep_deg": 0,
                         "spoke_width_hub_mm": 50, "spoke_fillet_mm": 1.5}),
        ("window-base", window),
        ("window-relief", {**window, "window_face_relief_mm": 1.0}),
        ("window-ridge", {**window, "window_outlines_mm": three_windows, "window_spoke_ridge_mm": 1.0}),
        ("window-draft", {**window, "window_side_draft_deg": 4.0}),
        ("window-combined", {**window, "window_outlines_mm": three_windows,
                             "window_face_relief_mm": 1.0, "window_spoke_ridge_mm": 1.0,
                             "window_side_draft_deg": 4.0}),
        ("window-rim-pockets", {**window, "rim_pocket_count": 15}),
    ]
    for name, parameters in cases:
        yield name, WheelSpec(**parameters)


def coverage_labels(spec: WheelSpec):
    """Requested active operations, not evidence of dimensional correctness."""
    features = ["rim", "hub", "center_bore", "bolt_holes"]
    if spec.spoke_method == "window":
        features.append("window_cut")
        style = "three_window" if len(spec.window_outlines_mm) == 3 else "single_window"
        for key, feature in (("window_face_relief_mm", "face_relief"),
                             ("window_spoke_ridge_mm", "spoke_ridge"),
                             ("window_side_draft_deg", "side_draft"),
                             ("window_edge_fillet_mm", "window_edge_fillet")):
            if getattr(spec, key) > 0:
                features.append(feature)
    else:
        style = spec.spoke_style
        features.append("spoke_loft")
        if style == "paired":
            features.append("paired_slot")
        if spec.pocket_depth_mm > 0:
            features.append("back_pocket")
    if spec.junction_fillet_mm > 0:
        features.append("junction_fillet")
    if spec.rim_pocket_count:
        features.append("rim_pockets")
    return {"method": spec.spoke_method, "style": style, "features": features}


def _requested_applied_differences(report, path=""):
    """Read legacy build evidence without treating fallback as an exact success."""
    differences = []
    for key, requested in report.items():
        location = f"{path}.{key}" if path else key
        if isinstance(requested, dict):
            differences.extend(_requested_applied_differences(requested, location))
        elif key.endswith("requested_mm"):
            applied_key = key.removesuffix("requested_mm") + "applied_mm"
            applied = report.get(applied_key)
            if isinstance(requested, (int, float)) and isinstance(applied, (int, float)):
                if not math.isclose(requested, applied, rel_tol=1e-9, abs_tol=1e-9):
                    differences.append({"field": location, "requested": requested, "applied": applied})
    return differences


def build_acceptance(report):
    """Exact/degraded/unknown acceptance is additional to legacy geometry checks."""
    checks = report.get("checks", {})
    geometry_passed = bool(checks) and all(value is True for value in checks.values()) and \
        report.get("step_solid_count") == 1 and checks.get("step_roundtrip") is True
    differences = _requested_applied_differences(report)
    fallback = report.get("step_stability", {}).get("fallback_used")
    partial_edges = (report.get("window_edge_fillet_requested_mm", 0) > 0 and
                     report.get("window_edges_rounded", 0) < report.get("window_edges_total", 0))
    claimed = report.get("build_status")
    if not geometry_passed:
        status = "failed"
    elif claimed == "degraded" or fallback is True or differences or partial_edges:
        status = "degraded"
    elif claimed == "exact":
        status = "exact_requested"
    else:
        # Equal legacy junction radii cannot establish the outcomes of all other
        # operations. Historical reports remain unverified unless rebuilt.
        status = "unverified"
    return {"geometry_passed": geometry_passed, "build_status": status,
            "exact_requested": status == "exact_requested", "fallback_used": fallback,
            "requested_applied_differences": differences}


def summarize_coverage(results):
    coverage = {"by_method": {}, "by_style": {}, "by_feature": {}}
    for item in results:
        labels = item["coverage"]
        for group, values in (("by_method", [labels["method"]]), ("by_style", [labels["style"]]),
                              ("by_feature", labels["features"])):
            for label in values:
                bucket = coverage[group].setdefault(label, {"selected": 0, "geometry_passed": 0,
                    "exact_requested": 0, "degraded": 0, "unverified": 0, "failed": 0})
                bucket["selected"] += 1
                bucket["geometry_passed"] += int(item["geometry_passed"])
                bucket[item["build_status"]] += 1
    return coverage


def run(output: Path, limit: int | None = None, suite: str = "legacy"):
    if suite not in {"legacy", "operations"}:
        raise ValueError(f"Unknown suite: {suite}")
    limit = (60 if suite == "legacy" else 8) if limit is None else limit
    if not 1 <= limit <= (100 if suite == "legacy" else 8):
        raise ValueError("Limit must be 1..100 for legacy or 1..8 for operations")
    selected = ([(f"legacy-{index:03}", spec) for index, spec in enumerate(matrix(limit), 1)]
                if suite == "legacy" else list(operation_matrix())[:limit])
    # A repeat run must use a new directory. Prior evidence is never recursively removed.
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"Refusing to overwrite existing evidence: {output}; choose a new --output")
    output.mkdir(parents=True, exist_ok=True)
    results, started = [], time.monotonic()
    for index, (case_id, spec) in enumerate(selected, 1):
        case = output/f"case-{index:03}"
        before = time.monotonic()
        result = {"case": index, "case_id": case_id, "spec": spec.model_dump(),
                  "coverage": coverage_labels(spec)}
        try:
            report = export_model(spec, case, snapshot={
                "name": f"P1 {suite} {case_id}", "spec": spec.model_dump(),
                "sources": default_sources(), "preparation": {},
                "template_version": "matrix-current",
                "input_provenance": "synthetic_parameter_fixture_not_photograph",
            })
            result.update(build_acceptance(report))
            result.update(checks=report.get("checks", {}),
                          step_volume_relative_delta=report.get("step_volume_relative_delta"),
                          volume_method=report.get("step_volume_method"))
        except Exception as error:
            result.update(geometry_passed=False, exact_requested=False, build_status="failed",
                          error=f"{type(error).__name__}: {error}")
        result.update(passed=result["geometry_passed"], seconds=round(time.monotonic()-before, 3))
        results.append(result)
    summary = {
        "schema": "wheelcam-p1-parametric-matrix-v2", "suite": suite, "requested": limit,
        # Preserve old count consumers, but explicitly document what 'passed' means.
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "passed_semantics": "geometry_checks_only_not_exact_request_acceptance",
        "geometry_passed": sum(item["geometry_passed"] for item in results),
        "exact_requested": sum(item["exact_requested"] for item in results),
        "degraded": sum(item["build_status"] == "degraded" for item in results),
        "unverified": sum(item["build_status"] == "unverified" for item in results),
        "acceptance_passed": all(item["exact_requested"] for item in results),
        "coverage": summarize_coverage(results),
        "scope": "Selected synthetic construction paths; not unseen-photo generalization, dimensional accuracy, or engineering approval",
        "elapsed_seconds": round(time.monotonic()-started, 3),
        "engineering_approved": False, "manufacturing_status": "not_released",
        "volume_method": volume_method(),
        "cases": results,
    }
    (output/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", "--profile", choices=("legacy", "operations"), default="legacy")
    parser.add_argument("--limit", type=int, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--output", type=Path,
                        help="New or empty output directory; existing evidence is never replaced")
    arguments = parser.parse_args()
    output = arguments.output or Path(f"artifacts/parametric-matrix-{arguments.suite}-{time.time_ns()}")
    result = run(output, arguments.limit, arguments.suite)
    print(json.dumps({key: result[key] for key in (
        "schema", "suite", "requested", "passed", "failed", "exact_requested", "degraded",
        "unverified", "acceptance_passed", "elapsed_seconds",
        "engineering_approved", "manufacturing_status")}, ensure_ascii=False, indent=2))
    print(f"Evidence: {output / 'summary.json'}")
    raise SystemExit(0 if result["acceptance_passed"] else 1)
