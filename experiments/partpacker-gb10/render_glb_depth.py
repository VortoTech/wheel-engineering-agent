"""Render deterministic depth projections for a large GLB without OpenGL."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import trimesh


source = Path(sys.argv[1])
output = Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
scene = trimesh.load(source, force="scene")

views = {
    "axis-z": np.array([0.0, 0.0, 1.0]),
    "axis-y": np.array([0.0, -1.0, 0.0]),
    "axis-x": np.array([1.0, 0.0, 0.0]),
    "oblique": np.array([1.0, -1.0, 0.7]),
}
size = 1024
panels = []

for name, view in views.items():
    view = view / np.linalg.norm(view)
    up_hint = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(view, up_hint))) > 0.95:
        up_hint = np.array([0.0, 1.0, 0.0])
    right = np.cross(up_hint, view)
    right /= np.linalg.norm(right)
    up = np.cross(view, right)

    corners = trimesh.bounds.corners(scene.bounds)
    corner_x = corners @ right
    corner_y = corners @ up
    low_x, high_x = float(corner_x.min()), float(corner_x.max())
    low_y, high_y = float(corner_y.min()), float(corner_y.max())
    pad_x = max((high_x - low_x) * 0.04, 1e-6)
    pad_y = max((high_y - low_y) * 0.04, 1e-6)
    low_x, high_x = low_x - pad_x, high_x + pad_x
    low_y, high_y = low_y - pad_y, high_y + pad_y

    zbuffer = np.full((size, size), -np.inf, dtype=np.float32)
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        vertices = trimesh.transform_points(scene.geometry[geometry_name].vertices, transform).astype(np.float32)
        x = vertices @ right
        y = vertices @ up
        z = vertices @ view
        ix = np.rint((x - low_x) / (high_x - low_x) * (size - 1)).astype(np.int32)
        iy = np.rint((high_y - y) / (high_y - low_y) * (size - 1)).astype(np.int32)
        valid = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
        np.maximum.at(zbuffer, (iy[valid], ix[valid]), z[valid])

    mask = np.isfinite(zbuffer)
    image = np.full((size, size), 18, dtype=np.uint8)
    if mask.any():
        values = zbuffer[mask]
        lo, hi = np.percentile(values, [1, 99])
        normalized = np.clip((values - lo) / max(hi - lo, 1e-6), 0, 1)
        image[mask] = (50 + normalized * 205).astype(np.uint8)
    colored = cv2.applyColorMap(image, cv2.COLORMAP_BONE)
    colored[~mask] = (12, 12, 12)
    cv2.putText(colored, name, (28, 54), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (64, 220, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(output / f"{name}.png"), colored)
    panels.append(colored)

contact = np.vstack((np.hstack(panels[:2]), np.hstack(panels[2:])))
cv2.imwrite(str(output / "contact-sheet.png"), contact)
print(f"rendered {len(scene.geometry)} geometries to {output}")
