"""Persist isolated Boolean operands/results and diagnose GK volume rejection.

Diagnostic only: raw rejected estimates are retained as evidence, never accepted
by relaxing the shared policy. Translation is a separate experiment, not an
implicit production integration fallback. No production source is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import cadquery as cq
from OCP.BRepGProp import BRepGProp
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.GProp import GProp_GProps
from OCP.TopTools import TopTools_ListOfShape
from OCP.gp import gp_Pnt

from wheelcam.mass_properties import _solid_leaves, volume_method


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def topology(shape):
    box = shape.BoundingBox() if shape.Vertices() else None
    return {
        "valid": shape.isValid(), "null": shape.isNull(), "kind": shape.ShapeType(),
        "solids": len(shape.Solids()), "faces": len(shape.Faces()),
        "bounds": [box.xmin, box.xmax, box.ymin, box.ymax, box.zmin, box.zmax] if box else None,
    }


def classify_saved(output):
    result = {"schema_version": "wheel-volume-classification-debug-v1", "shapes": {},
              "script_sha256": sha(Path(__file__)),
              "scope": "Native infinity and point classification of saved evidence plus isolated copies"}
    for name in ("b", "c", "removed", "added", "common"):
        path = output / f"{name}.brep"
        if not path.exists():
            continue
        shape = cq.Shape.importBrep(str(path))
        result["shapes"][name] = {"sha256": sha(path), "variants": {}}
        for tag, candidate in (("original", shape), ("copy", shape.copy())):
            records = []
            for solid in candidate.Solids():
                classifier = BRepClass3d_SolidClassifier(solid.wrapped)
                box = solid.BoundingBox()
                classifier.PerformInfinitePoint(1e-7)
                record = {"infinity_state": str(classifier.State()),
                          "solid_orientation": str(solid.wrapped.Orientation()),
                          "shell_orientations": [str(shell.wrapped.Orientation()) for shell in solid.Shells()],
                          "points": {}}
                for label, point in (("far", (1000, 1000, 1000)), ("barrel", (230, 0, -50)),
                                     ("center_bore", (0, 0, 60)), ("bore_off_axis", (10, 5, 60)),
                                     ("bore_off_axis_high", (1, 2, 70))):
                    classifier.Perform(gp_Pnt(*point), 1e-7)
                    record["points"][label] = {"point_mm": point, "native_state": str(classifier.State()),
                        "fresh_native_state": str(BRepClass3d_SolidClassifier(solid.wrapped, gp_Pnt(*point), 1e-7).State()),
                        "cadquery_is_inside": solid.isInside(point),
                        "bbox_contains": (box.xmin <= point[0] <= box.xmax and box.ymin <= point[1] <= box.ymax
                                          and box.zmin <= point[2] <= box.zmax)}
                records.append(record)
            result["shapes"][name]["variants"][tag] = records
    write_json(output / "classification.json", result)
    print(json.dumps(result, indent=2), flush=True)


def explicit_boolean(first, second, *, common, fuzzy):
    builder = BRepAlgoAPI_Common() if common else BRepAlgoAPI_Cut()
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(first.copy().wrapped)
    tools.Append(second.copy().wrapped)
    builder.SetArguments(arguments)
    builder.SetTools(tools)
    builder.SetNonDestructive(True)
    builder.SetRunParallel(False)
    builder.SetFuzzyValue(fuzzy)
    builder.Build()
    if not builder.IsDone():
        raise ValueError("Explicit deterministic Boolean did not finish")
    if hasattr(builder, "HasErrors") and builder.HasErrors():
        raise ValueError("Explicit deterministic Boolean reported errors")
    return cq.Shape.cast(builder.Shape())


def prepare(source: Path, output: Path, *, deterministic=False, fuzzy=0.0):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite diagnostics: {output}")
    output.mkdir(parents=True, exist_ok=True)
    inputs = {key: source / case / "wheel.step" for key, case in (
        ("b", "b-program-replay"), ("c", "c-slot-edit"))}
    manifest = {
        "schema_version": "wheel-sector-volume-debug-v1",
        "scope": "Diagnostic only; no changed geometry, no relaxed acceptance, NOT RELEASED",
        "inputs": {key: {"path": str(path.resolve()), "sha256": sha(path)} for key, path in inputs.items()},
        "source_sha256": {str(path): sha(path) for path in (
            Path(__file__), Path(__file__).resolve().parents[1] / "services/wheelcam/mass_properties.py")},
        "method": volume_method(), "shapes": {},
        "boolean_policy": {"explicit_ocp": deterministic, "non_destructive": True if deterministic else None,
                           "run_parallel": False if deterministic else None, "fuzzy_mm": fuzzy if deterministic else None,
                           "error_api_exposed": hasattr(BRepAlgoAPI_Cut(), "HasErrors"),
                           "scope": "Fuzzy tolerance is Boolean evaluation tolerance, not a model change"},
    }
    write_json(output / "manifest.json", manifest)
    # Keep pristine reimports for operands and copy them for every Boolean.
    b, c = (cq.importers.importStep(str(inputs[key])).val() for key in ("b", "c"))
    for name, shape in (("b", b), ("c", c)):
        shape.exportBrep(str(output / f"{name}.brep"))
        manifest["shapes"][name] = {**topology(shape), "sha256": sha(output / f"{name}.brep")}
    write_json(output / "manifest.json", manifest)
    operations = ({
        "removed": lambda: explicit_boolean(b, c, common=False, fuzzy=fuzzy),
        "added": lambda: explicit_boolean(c, b, common=False, fuzzy=fuzzy),
        "common": lambda: explicit_boolean(b, c, common=True, fuzzy=fuzzy),
    } if deterministic else {
        "removed": lambda: b.copy().cut(c.copy()),
        "added": lambda: c.copy().cut(b.copy()),
        "common": lambda: b.copy().intersect(c.copy()),
    })
    for name, operation in operations.items():
        started = time.monotonic()
        print(f"Boolean {name} started", flush=True)
        shape = operation()
        shape.exportBrep(str(output / f"{name}.brep"))
        manifest["shapes"][name] = {**topology(shape), "sha256": sha(output / f"{name}.brep"),
                                    "seconds": time.monotonic() - started}
        write_json(output / "manifest.json", manifest)
        print(f"Boolean {name} saved: {manifest['shapes'][name]}", flush=True)


def finite_or_none(value):
    return value if math.isfinite(value) else None


def raw_measure(solid, epsilon, centered):
    started = time.monotonic()
    box = solid.BoundingBox()
    shift = (-(box.xmin + box.xmax) / 2, -(box.ymin + box.ymax) / 2, -(box.zmin + box.zmax) / 2)
    measured = solid.translate(shift) if centered else solid
    props = GProp_GProps()
    try:
        error = float(BRepGProp.VolumePropertiesGK_s(
            measured.wrapped, props, epsilon, True, True, False, False, False,
        ))
        mass = float(props.Mass())
        policy = volume_method(epsilon=epsilon)
        accepted = (measured.isValid() and math.isfinite(mass) and mass > 0 and math.isfinite(error)
                    and 0 <= error <= policy["reported_error_limit"])
        return {"volume_mm3": finite_or_none(mass), "reported_relative_error": finite_or_none(error),
                "reported_error_limit": policy["reported_error_limit"], "accepted_by_numerical_guard": accepted,
                "scope": "Raw numerical guard only; native infinity/full shared policy not evaluated here",
                "epsilon": epsilon, "centered": centered, "translation_mm": shift if centered else (0, 0, 0),
                "seconds": time.monotonic() - started}
    except Exception as exc:
        return {"accepted_by_numerical_guard": False, "error": str(exc), "epsilon": epsilon, "centered": centered,
                "scope": "Raw numerical guard only; native infinity/full shared policy not evaluated here",
                "seconds": time.monotonic() - started}


def numerical_pass(record):
    # Historical diagnostics used an overbroad label. Preserve their numerical
    # facts, but never interpret that old field as the full production policy.
    return record.get("accepted_by_numerical_guard", record.get("accepted_by_policy", False))


def aggregate(records):
    if not records:
        return {"volume_mm3": 0.0, "accepted_by_numerical_guard": True, "empty": True}
    masses = [item.get("volume_mm3") for item in records]
    return {"volume_mm3": math.fsum(masses) if all(value is not None for value in masses) else None,
            "accepted_by_numerical_guard": all(numerical_pass(item) for item in records), "empty": False}


def assess_saved(output):
    """New interpretation report; never rewrites historical numerical records."""
    measurements = json.loads((output / "measurements.json").read_text())
    classification = json.loads((output / "classification.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    result = {
        "schema_version": "wheel-volume-diagnostic-assessment-v1", "shapes": {}, "conservation": {},
        "historical_field_notice": "accepted_by_policy and all_volumes_accepted in measurements.json checked numerical error/mass only, NOT the full shared volume service policy.",
        "scope": "Derived assessment of separately recorded numeric and native classification evidence; no new integration or acceptance threshold changes.",
        "evidence_sha256": {name: sha(output / name) for name in ("manifest.json", "measurements.json", "classification.json")},
        "current_shared_policy": volume_method(),
    }
    for name, saved in measurements["shapes"].items():
        evidence = classification["shapes"][name]
        if evidence["sha256"] != sha(output / f"{name}.brep") or evidence["sha256"] != manifest["shapes"][name]["sha256"]:
            raise ValueError(f"Classification is not bound to saved {name} BRep")
        classifications = evidence["variants"]["original"]
        if len(classifications) != len(saved["components"]):
            raise ValueError(f"Classification component count mismatch for {name}")
        finite = all(part["infinity_state"] == "TopAbs_State.TopAbs_OUT" for part in classifications)
        anomalies = [
            {"component": index, "label": label, **point}
            for index, part in enumerate(classifications) for label, point in part["points"].items()
            if point.get("fresh_native_state", point["native_state"]) == "TopAbs_State.TopAbs_IN" and not point["bbox_contains"]
        ]
        shape_result = {
            "native_infinity_guard_passed": finite,
            "infinity_states": [part["infinity_state"] for part in classifications],
            "point_classifier_bbox_contradictions": anomalies,
            "measurements_by_epsilon": {},
        }
        for epsilon, numeric in saved.get("aggregate_original", {}).items():
            passed = numerical_pass(numeric)
            shape_result["measurements_by_epsilon"][epsilon] = {
                "volume_mm3": numeric["volume_mm3"], "numerical_guard_passed": passed,
                "full_guard_evidence_passed": finite and passed and saved["topology"]["valid"],
                "scope": "Derived from recorded component validity, numerical guards, and native infinity; not a rerun of measure_volume or a geometry correctness claim",
            }
        result["shapes"][name] = shape_result
    for epsilon, old in measurements["conservation"].items():
        guards = [value["measurements_by_epsilon"].get(epsilon, {}).get("full_guard_evidence_passed", False)
                  for value in result["shapes"].values()]
        result["conservation"][epsilon] = {
            "residual_mm3": old["residual_mm3"], "limit_mm3": old["limit_mm3"],
            "all_full_guard_evidence_passed": all(guards),
            "passed": all(guards) and old["residual_mm3"] <= old["limit_mm3"],
        }
    result["point_classification_consistent_with_bounding_boxes"] = not any(
        value["point_classifier_bbox_contradictions"] for value in result["shapes"].values())
    result["comparison_accepted"] = False
    result["acceptance_note"] = "Diagnostic variants remain unaccepted; strict conservation failed and/or native classification is inconsistent. Further epsilon variants were stopped; no acceptance gate was relaxed."
    write_json(output / "assessment.json", result)
    print(json.dumps({"conservation": result["conservation"],
                      "point_classification_consistent_with_bounding_boxes": result["point_classification_consistent_with_bounding_boxes"],
                      "comparison_accepted": False}, indent=2), flush=True)


def diagnose(output: Path, *, whole_epsilon=None, refine_failures=True):
    manifest = json.loads((output / "manifest.json").read_text())
    results_path = output / "measurements.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {
        "schema_version": "wheel-sector-volume-debug-measurements-v1", "shapes": {}, "conservation": {},
        "scope": "Raw rejected estimates are diagnostics, not accepted volumes. No engineering validation.",
    }
    for name in ("removed", "added", "common", "b", "c"):
        path = output / f"{name}.brep"
        if sha(path) != manifest["shapes"][name]["sha256"]:
            raise ValueError(f"Saved BRep hash mismatch: {path}")
        shape = cq.Shape.importBrep(str(path))
        components = [cq.Shape.cast(item) for item in _solid_leaves(shape.wrapped)]
        saved = results["shapes"].setdefault(name, {"topology": topology(shape), "components": []})
        for index, solid in enumerate(components):
            if index == len(saved["components"]):
                saved["components"].append({"index": index, "topology": topology(solid), "measurements": []})
            component = saved["components"][index]
            pending = [(1e-7, False)]
            if whole_epsilon is not None:
                pending.append((whole_epsilon, False))
            # First identify the failing native component; only those get an
            # origin-shift/tighter-request matrix in the default bounded run.
            for epsilon, centered in pending:
                if any(item["epsilon"] == epsilon and item["centered"] == centered for item in component["measurements"]):
                    continue
                print(f"Measure {name}/{index} eps={epsilon} centered={centered}", flush=True)
                record = raw_measure(solid, epsilon, centered)
                component["measurements"].append(record)
                write_json(results_path, results)
                print(record, flush=True)
            default = next(item for item in component["measurements"] if item["epsilon"] == 1e-7 and not item["centered"])
            if refine_failures and not numerical_pass(default):
                for epsilon, centered in ((1e-8, False), (1e-9, False), (1e-7, True), (1e-8, True), (1e-9, True)):
                    if any(item["epsilon"] == epsilon and item["centered"] == centered for item in component["measurements"]):
                        continue
                    print(f"Refine {name}/{index} eps={epsilon} centered={centered}", flush=True)
                    record = raw_measure(solid, epsilon, centered)
                    component["measurements"].append(record)
                    write_json(results_path, results)
                    print(record, flush=True)
        for epsilon in (1e-7, whole_epsilon):
            if epsilon is None:
                continue
            records = [next((item for item in part["measurements"] if item["epsilon"] == epsilon and not item["centered"]), None)
                       for part in saved["components"]]
            if all(item is not None for item in records):
                saved.setdefault("aggregate_original", {})[str(epsilon)] = aggregate(records)
        write_json(results_path, results)
    for epsilon in (1e-7, whole_epsilon):
        if epsilon is None:
            continue
        values = {name: results["shapes"][name].get("aggregate_original", {}).get(str(epsilon))
                  for name in ("b", "c", "removed", "added", "common")}
        if all(value is not None and value["volume_mm3"] is not None for value in values.values()):
            b, c, removed, added, common = (values[key]["volume_mm3"] for key in values)
            residual = max(abs(b - common - removed), abs(c - common - added), abs(b - c - removed + added))
            accepted = all(numerical_pass(value) for value in values.values())
            results["conservation"][str(epsilon)] = {
                "residual_mm3": residual, "limit_mm3": .01, "all_numerical_guards_accepted": accepted,
                "passed": accepted and residual <= .01,
                "scope": "Numerical Boolean conservation only; not native infinity/full service policy or geometry/engineering certification",
            }
    write_json(results_path, results)
    print(json.dumps(results["conservation"], indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("artifacts/sector-program-five-v2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-booleans", action="store_true")
    parser.add_argument("--whole-epsilon", type=float)
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--fuzzy", type=float, default=0.0)
    parser.add_argument("--no-refinements", action="store_true")
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument("--assess-only", action="store_true")
    args = parser.parse_args()
    if args.assess_only:
        assess_saved(args.output)
    elif args.classify_only:
        classify_saved(args.output)
    else:
        if not args.reuse_booleans:
            prepare(args.source, args.output, deterministic=args.deterministic, fuzzy=args.fuzzy)
        diagnose(args.output, whole_epsilon=args.whole_epsilon, refine_failures=not args.no_refinements)
