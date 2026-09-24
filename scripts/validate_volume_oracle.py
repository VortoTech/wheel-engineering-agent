"""Independent local volume oracle for a tightly bounded root-slot width edit.

This integrates the analytic capsule against the uncut blank's known thickness,
without importing STEP or invoking the CAD kernel. It is independent of OCCT's
mass integrator, not of the assumed CAD design. The native cutter is a sampled
periodic spline, so exact equality to this analytic target is not expected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from wheelcam.models import WheelSpec
from wheelcam.sector_program import RootSlot, SectorProgram, compile_sector
from wheelcam.template import layout, window_blank_profile
from wheelcam.windows import HUB_KEEP_MM


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def capsule_integral(slot: RootSlot, thickness, order: int) -> float:
    """Integrate thickness(hypot(x, y)) over an exact capsule in its local frame.

    Rectangle plus two polar semicircles have disjoint interiors. Polar cap
    quadrature avoids square-root singularities at Cartesian circle endpoints.
    The slot angle is immaterial because the blank thickness is radial.
    """
    if isinstance(order, bool) or not isinstance(order, int) or not 8 <= order <= 512:
        raise ValueError("Quadrature order must be an integer from 8 to 512.")
    nodes, weights = np.polynomial.legendre.leggauss(order)
    radius = slot.width_mm / 2
    half_straight = (slot.length_mm - slot.width_mm) / 2
    x = slot.center_radius_mm + half_straight * nodes[:, None]
    y = radius * nodes[None, :]
    grid_weights = weights[:, None] * weights[None, :]
    total = np.sum(thickness(np.hypot(x, y)) * grid_weights) * half_straight * radius
    rho = radius * (nodes[:, None] + 1) / 2
    for sign in (-1, 1):
        angle = math.pi * nodes[None, :] / 2
        x = slot.center_radius_mm + sign * (half_straight + rho * np.cos(angle))
        y = rho * np.sin(angle)
        total += np.sum(thickness(np.hypot(x, y)) * rho * grid_weights) * radius * math.pi / 4
    return float(total)


def slot_edit_oracle(before_program, after_program, base_spec, orders=(96, 192)):
    """Return expected removal, or reject edits outside the oracle's domain.

    Exactly one width may increase, with fixed total length, centre and angle.
    Such capsules are nested, hence their union is the wider capsule and the
    set-difference integral equals the difference of their separate integrals.
    """
    before, after = map(SectorProgram.model_validate, (before_program, after_program))
    base = WheelSpec.model_validate(base_spec)
    left, right = before.model_dump(), after.model_dump()
    if {k: v for k, v in left.items() if k != "features"} != {k: v for k, v in right.items() if k != "features"}:
        raise ValueError("Oracle requires identical topology, phase and group count.")
    if len(before.features) != len(after.features):
        raise ValueError("Oracle requires the same feature list.")
    changed = [(a, b) for a, b in zip(before.features, after.features) if a != b]
    if len(changed) != 1 or not all(isinstance(item, RootSlot) for item in changed[0]):
        raise ValueError("Oracle supports exactly one root-slot width edit.")
    old, new = changed[0]
    if {k: v for k, v in old.model_dump().items() if k != "width_mm"} != {
            k: v for k, v in new.model_dump().items() if k != "width_mm"} or new.width_mm <= old.width_mm:
        raise ValueError("Oracle requires only a width increase, with fixed slot length and placement.")
    disabled = ("junction_fillet_mm", "window_edge_fillet_mm", "window_face_relief_mm",
                "window_spoke_ridge_mm", "window_side_draft_deg", "pocket_depth_mm",
                "lip_extension_mm", "rim_pocket_count", "valve_diameter_mm")
    if any(getattr(base, key) != 0 for key in disabled):
        raise ValueError("Oracle excludes fillets, draft, relief, ridges, pockets, lip and valve features.")
    before_spec, before_manifest = compile_sector(before, base)
    after_spec, after_manifest = compile_sector(after, base)
    profile = window_blank_profile(before_spec)
    top, bottom = np.array(profile["top_rz"]), np.array(profile["bottom_rz"])
    r0, r1 = top[0, 0], top[-1, 0]
    if not np.allclose(top[:, 0], bottom[:, 0], rtol=0, atol=1e-12) or abs(top[1, 0] - (r0 + r1) / 2) > 1e-12:
        raise ValueError("Oracle requires matching, linear-radius quadratic blank curves.")
    depths = top[:, 1] - bottom[:, 1]
    if np.any(depths <= 0):
        raise ValueError("Blank thickness controls must all be positive.")
    radial_min, radial_max = new.center_radius_mm - new.length_mm / 2, new.center_radius_mm + new.length_mm / 2
    lay = layout(before_spec)
    rim_min = min(point[0] for point, _ in lay["rim_polygon"]) - max(radius for _, radius in lay["rim_polygon"])
    hub_keep = before_spec.hub_diameter_mm / 2 + HUB_KEEP_MM
    lug_max = before_spec.bolt_circle_mm / 2 + max(lay["lug_pocket_diameter"], lay["lug_cone_diameter"],
                                                 before_spec.bolt_diameter_mm) / 2
    if not (max(r0, hub_keep, lug_max, before_spec.center_bore_mm / 2) < radial_min < radial_max < min(r1, rim_min)):
        raise ValueError("Capsule must lie wholly inside the unmodified blank, outside hub/lugs and inside the rim.")

    def thickness(radial):
        t = (radial - r0) / (r1 - r0)
        return depths[0] * (1 - t) ** 2 + 2 * depths[1] * (1 - t) * t + depths[2] * t ** 2

    if len(orders) != 2 or orders[0] >= orders[1]:
        raise ValueError("Supply two strictly increasing quadrature orders.")
    measurements = []
    for order in orders:
        old_volume, new_volume = capsule_integral(old, thickness, order), capsule_integral(new, thickness, order)
        measurements.append({"order": order, "before_slot_mm3": old_volume, "after_slot_mm3": new_volume,
                             "per_copy_removed_mm3": new_volume - old_volume,
                             "expected_removed_mm3": (new_volume - old_volume) * before.groups})
    delta = abs(measurements[-1]["expected_removed_mm3"] - measurements[0]["expected_removed_mm3"])
    return {
        "schema": "wheel-slot-volume-oracle-v1", "feature_id": old.id, "groups": before.groups,
        "width_before_mm": old.width_mm, "width_after_mm": new.width_mm,
        "measurements": measurements, "expected_removed_mm3": measurements[-1]["expected_removed_mm3"],
        "convergence_delta_mm3": delta, "convergence_limit_mm3": 1e-6,
        "converged": delta <= 1e-6,
        "applicability": {"single_nested_capsule_edit": True, "finishing_operations_disabled": True,
                          "capsule_radial_bounds_mm": [radial_min, radial_max],
                          "blank_radial_bounds_mm": [r0, r1], "hub_keep_radius_mm": hub_keep,
                          "lug_outer_radius_mm": lug_max, "conservative_rim_inner_radius_mm": rim_min,
                          "compiler_checks_passed": True,
                          "compiler_min_web_mm": [before_manifest["sampled_outline_checks"]["min_web_mm"],
                                                  after_manifest["sampled_outline_checks"]["min_web_mm"]]},
        "before_compiled_spec_sha256": canonical_hash(before_spec.model_dump(mode="json")),
        "after_compiled_spec_sha256": canonical_hash(after_spec.model_dump(mode="json")),
        "blank_profile": profile,
        "scope": "Independent of OCCT volume integration; uses the same assumed blank design, not physical measurements.",
        "limitations": ["Compiler collision/web checks concern sampled 2D boundaries, not certified native splines.",
                        "Native periodic-spline cutter differs slightly from the analytic capsule.",
                        "Quadrature convergence does not bound representation error or establish engineering accuracy."],
        "photo_fidelity": "not_measured", "manufacturing_status": "not_released",
    }


def frozen_inputs(source: Path):
    """Read-only provenance verification; legacy report mass values are unused."""
    inputs = []
    for case in ("b-program-replay", "c-slot-edit"):
        folder = source / case
        recipe = json.loads((folder / "recipe.json").read_text())
        report = json.loads((folder / "report.json").read_text())
        program = json.loads((folder / "sector-program.json").read_text())
        hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                  for name in ("recipe.json", "report.json", "sector-program.json", "wheel.step")}
        for name in ("wheel.step", "recipe.json"):
            if hashes[name] != report["artifacts"][name]["sha256"]:
                raise ValueError(f"{name} does not match frozen report hash: {case}")
        if recipe.get("sector_program") != program:
            raise ValueError(f"Program does not match frozen recipe: {case}")
        if report.get("build_status") != "exact":
            raise ValueError(f"Oracle requires an exact build without fallback: {case}")
        if report.get("build_resolution", {}).get("resolved_recipe") != recipe["spec"]:
            raise ValueError(f"Resolved build recipe differs from original input: {case}")
        compiled, _ = compile_sector(program, recipe["base_spec"])
        if compiled.model_dump(mode="json") != recipe["spec"]:
            raise ValueError(f"Recompiled program differs from frozen recipe: {case}")
        inputs.append({"case": case, "recipe": recipe, "program": program, "sha256": hashes})
    if inputs[0]["recipe"]["base_spec"] != inputs[1]["recipe"]["base_spec"]:
        raise ValueError("B/C base recipes differ.")
    return inputs


def compare_measurements(oracle, inputs, report, allowance_mm3=.5):
    """Compare hash-bound measurements; no kernel calls or Boolean operations."""
    if not math.isfinite(allowance_mm3) or allowance_mm3 < 0:
        raise ValueError("Comparison allowance must be finite and nonnegative.")
    if report.get("schema") != "wheel-volume-measurements-v1":
        raise ValueError("Unknown mass measurement report schema.")
    rows = report.get("measurements", [])
    if len(rows) != 2 or len({row["case"] for row in rows}) != 2:
        raise ValueError("Mass report must contain exactly one B and one C measurement.")
    indexed = {row["case"]: row for row in rows}
    values = []
    for item in inputs:
        row = indexed.get(item["case"], {})
        value = row.get("volume_mm3")
        if row.get("step_sha256") != item["sha256"]["wheel.step"] or not row.get("method"):
            raise ValueError("Mass measurements must identify their method and frozen STEP hash.")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Mass measurements must be finite positive numbers.")
        values.append(value)
    actual, expected = values[0] - values[1], oracle["expected_removed_mm3"]
    return {"measured_wheel_volume_delta_mm3": actual, "oracle_removed_mm3": expected,
            "absolute_error_mm3": abs(actual - expected), "comparison_allowance_mm3": allowance_mm3,
            "passed": bool(oracle["converged"] and abs(actual - expected) <= allowance_mm3),
            "allowance_scope": "Experimental numerical/spline-representation allowance; not a manufacturing tolerance.",
            "measurements": rows}


def run(source: Path, output: Path, measurements: Path | None = None, allowance_mm3=.5):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"Refusing to overwrite existing evidence: {output}")
    inputs = frozen_inputs(source)
    oracle = slot_edit_oracle(inputs[0]["program"], inputs[1]["program"], inputs[0]["recipe"]["base_spec"])
    oracle["source_directory"] = str(source.resolve())
    oracle["input_hashes"] = [{"case": item["case"], **item["sha256"]} for item in inputs]
    oracle["source_sha256"] = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in (Path(__file__), Path(__file__).resolve().parents[1] / "services/wheelcam/template.py",
                                            Path(__file__).resolve().parents[1] / "services/wheelcam/sector_program.py")}
    if measurements:
        oracle["comparison"] = compare_measurements(oracle, inputs, json.loads(measurements.read_text()), allowance_mm3)
        oracle["measurement_report_sha256"] = hashlib.sha256(measurements.read_bytes()).hexdigest()
    else:
        oracle["comparison"] = {"status": "not_measured", "reason": "No hash-bound kernel measurement report supplied."}
    output.mkdir(parents=True, exist_ok=True)
    (output / "oracle.json").write_text(json.dumps(oracle, ensure_ascii=False, indent=2, allow_nan=False))
    return oracle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("artifacts/sector-program-five-v2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measurements", type=Path)
    parser.add_argument("--allowance-mm3", type=float, default=.5)
    args = parser.parse_args()
    result = run(args.source, args.output, args.measurements, args.allowance_mm3)
    print(json.dumps({"expected_removed_mm3": result["expected_removed_mm3"],
                      "converged": result["converged"], "comparison": result["comparison"]}, indent=2))
    raise SystemExit(0 if result["converged"] and result["comparison"].get("passed", True) else 1)
