"""Read/rebuild only the failed combined-operation fixture; never relax gates.

Stores native B-Rep before measurement, then compares frozen STEP / native /
fresh STEP over explicit integrator settings and independent triangle volumes.
This diagnostic does not replace the acceptance report or approve engineering.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time

import cadquery as cq
import numpy as np
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.GProp import GProp_GProps

from wheelcam.geometry import _build_wheel, STEP_ROUNDTRIP_RELATIVE_LIMIT
from wheelcam.models import WheelSpec


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def topology(shape):
    states = []
    for solid in shape.Solids():
        classifier = BRepClass3d_SolidClassifier(solid.wrapped)
        classifier.PerformInfinitePoint(1e-7)
        states.append(str(classifier.State()))
    box = shape.BoundingBox()
    return {"valid": shape.isValid(), "solid_count": len(shape.Solids()),
            "face_count": len(shape.Faces()), "edge_count": len(shape.Edges()),
            "vertex_count": len(shape.Vertices()), "infinity_states": states,
            "face_types": dict(Counter(face.geomType() for face in shape.Faces())),
            "bbox": [getattr(box, name) for name in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")]}


def integrals(shape):
    measurements = []
    for epsilon in (1e-6, 1e-7, 1e-8, 1e-9):
        properties = GProp_GProps()
        started = time.monotonic()
        error = BRepGProp.VolumePropertiesGK_s(shape.copy().wrapped, properties, epsilon, True, True, False, False, False)
        value = float(properties.Mass())
        row = {"method": "GK_with_spans", "epsilon": epsilon, "volume_mm3": value,
               "reported_error": float(error), "finite": math.isfinite(value) and math.isfinite(error),
               "seconds": time.monotonic() - started}
        measurements.append(row)
        print(json.dumps(row), flush=True)
    properties = GProp_GProps()
    started = time.monotonic()
    error = BRepGProp.VolumeProperties_s(shape.copy().wrapped, properties, 1e-8, True)
    measurements.append({"method": "legacy_Gauss_negative_control", "epsilon": 1e-8,
                         "volume_mm3": float(properties.Mass()), "reported_error": float(error),
                         "seconds": time.monotonic() - started})
    return measurements


def mesh_volume(shape, tolerance, angular):
    # Clear triangulation on a private B-Rep copy so each resolution is explicit.
    work = shape.copy()
    BRepTools.Clean_s(work.wrapped)
    started = time.monotonic()
    vertices, faces = work.tessellate(tolerance, angular)
    points = np.array([point.toTuple() for point in vertices])
    triangles = points[np.array(faces)]
    signed = float(np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6)
    # Position quantization is only for the independent manifold diagnostic;
    # the signed-volume sum above uses the original unmodified coordinates.
    _, merged = np.unique(np.round(points, decimals=7), axis=0, return_inverse=True)
    mapped = merged[np.array(faces)]
    noncollapsed = (mapped[:, 0] != mapped[:, 1]) & (mapped[:, 1] != mapped[:, 2]) & (mapped[:, 2] != mapped[:, 0])
    good = mapped[noncollapsed]
    edges = np.vstack((good[:, [0, 1]], good[:, [1, 2]], good[:, [2, 0]]))
    _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
    orientation = np.bincount(inverse, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1))
    return {"linear_deflection_mm": tolerance, "angular_deflection_rad": angular,
            "signed_triangle_volume_mm3": signed, "watertight_after_position_merge": bool(np.all(counts == 2)),
            "winding_consistent": bool(np.all(orientation == 0)), "triangle_count": len(faces),
            "position_quantization_decimals": 7, "collapsed_triangles_for_topology_check": int((~noncollapsed).sum()),
            "seconds": time.monotonic() - started,
            "scope": "Approximate independent triangle integration; not a certified error bound."}


def run(source: Path, output: Path, native_input: Path | None = None, mesh_only=False):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"Refusing to replace diagnostic evidence: {output}")
    recipe_path, step_path = source / "recipe.json", source / "wheel.step"
    recipe = json.loads(recipe_path.read_text())
    spec = WheelSpec.model_validate(recipe["spec"])
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"schema": "wheel-combined-volume-diagnostic-v1", "source": str(source.resolve()),
                "source_hashes": {name: digest(source / name) for name in ("recipe.json", "wheel.step")},
                "script_sha256": digest(Path(__file__)), "spec": spec.model_dump(mode="json"),
                "mesh_only": mesh_only,
                "step_relative_limit_unchanged": STEP_ROUNDTRIP_RELATIVE_LIMIT,
                "manufacturing_status": "not_released", "shapes": {}}
    write(output / "diagnostic.json", evidence)
    started = time.monotonic()
    if native_input:
        native = cq.Shape.importBrep(str(native_input))
        evidence["native_input"] = {"path": str(native_input.resolve()), "sha256": digest(native_input)}
    else:
        wheel, info, _ = _build_wheel(spec)
        native = wheel.val()
        evidence["build_info"] = info
        evidence["build_seconds"] = time.monotonic() - started
    native.exportBrep(str(output / "native.brep"))
    cq.exporters.export(native, str(output / "rebuilt.step"))
    evidence["native_brep_sha256"] = digest(output / "native.brep")
    evidence["rebuilt_step_sha256"] = digest(output / "rebuilt.step")
    write(output / "diagnostic.json", evidence)
    shapes = {"native": native, "frozen_step": cq.importers.importStep(str(step_path)).val(),
              "rebuilt_step": cq.importers.importStep(str(output / "rebuilt.step")).val()}
    for name, shape in shapes.items():
        print(name, flush=True)
        record = {"topology": topology(shape), "integrals": [] if mesh_only else integrals(shape)}
        evidence["shapes"][name] = record
        write(output / "diagnostic.json", evidence)
    for name, shape in shapes.items():
        evidence["shapes"][name]["meshes"] = []
        for tolerance, angular in ((.2, .08), (.05, .03), (.01, .01)):
            row = mesh_volume(shape, tolerance, angular)
            evidence["shapes"][name]["meshes"].append(row)
            print(name, json.dumps(row), flush=True)
            write(output / "diagnostic.json", evidence)
    evidence["elapsed_seconds"] = time.monotonic() - started
    write(output / "diagnostic.json", evidence)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("artifacts/parametric-operations-gk-v1/case-007"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-input", type=Path)
    parser.add_argument("--mesh-only", action="store_true")
    args = parser.parse_args()
    run(args.source, args.output, args.native_input, args.mesh_only)
