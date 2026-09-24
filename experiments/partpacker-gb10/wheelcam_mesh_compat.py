"""Conservative ARM64 fallback for PartPacker's inference-only mesh cleanup."""
from __future__ import annotations

import numpy as np
import trimesh


def _mesh(vertices, faces):
    return trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )


def clean_mesh(vertices, faces, v_pct=1, min_f=0, min_d=0, repair=False,
               remesh=False, remesh_size=0.01, remesh_iters=3, verbose=True):
    """Remove invalid faces and tiny components without changing surface shape.

    PartPacker calls this with repair=False and remesh=False. Those are the only
    supported settings: silently approximating repair/remeshing would weaken the
    geometry evidence.
    """
    if repair or remesh:
        raise NotImplementedError("ARM64 fallback does not repair or remesh")
    mesh = _mesh(vertices, faces)
    before = (len(mesh.vertices), len(mesh.faces))
    if len(mesh.faces):
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.update_faces(mesh.unique_faces())
        mesh.remove_unreferenced_vertices()
    if len(mesh.faces) and (min_f > 0 or min_d > 0):
        full_diagonal = float(np.linalg.norm(mesh.bounds[1] - mesh.bounds[0]))
        kept = []
        for component in mesh.split(only_watertight=False):
            diagonal = float(np.linalg.norm(component.bounds[1] - component.bounds[0]))
            enough_faces = min_f <= 0 or len(component.faces) >= min_f
            enough_size = min_d <= 0 or full_diagonal == 0 or diagonal >= full_diagonal * min_d / 100
            if enough_faces and enough_size:
                kept.append(component)
        if kept:
            mesh = trimesh.util.concatenate(kept)
        else:
            mesh = _mesh(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64))
    after = (len(mesh.vertices), len(mesh.faces))
    if verbose:
        print(f"[WheelCAM ARM64] conservative mesh cleanup: {before} -> {after}")
    return np.asarray(mesh.vertices), np.asarray(mesh.faces)


def decimate_mesh(*_args, **_kwargs):
    raise NotImplementedError(
        "ARM64 fallback does not decimate; run PartPacker with --num_faces -1"
    )
