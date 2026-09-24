"""Render a deterministic geometry-only PNG directly from a WheelCAM GLB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
import numpy as np


def load_glb(path: Path):
    data = path.read_bytes()
    json_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20+json_size])
    binary_offset = 28+json_size
    meshes = []
    for mesh in document["meshes"]:
        primitive = mesh["primitives"][0]
        position_accessor = document["accessors"][primitive["attributes"]["POSITION"]]
        index_accessor = document["accessors"][primitive["indices"]]
        position_view = document["bufferViews"][position_accessor["bufferView"]]
        index_view = document["bufferViews"][index_accessor["bufferView"]]
        positions = np.frombuffer(
            data, "<f4", position_accessor["count"]*3,
            binary_offset+position_view.get("byteOffset", 0),
        ).reshape(-1, 3).copy()
        faces = np.frombuffer(
            data, "<u4", index_accessor["count"],
            binary_offset+index_view.get("byteOffset", 0),
        ).reshape(-1, 3).copy()
        meshes.append((positions, faces, primitive.get("material", 0)))
    return document, meshes


def quaternion_matrix(value):
    x, y, z, w = value
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])


def render(source: Path, output: Path, view: str):
    document, meshes = load_glb(source)
    triangles, material_ids = [], []
    for node_index in document["scenes"][document.get("scene", 0)]["nodes"]:
        node = document["nodes"][node_index]
        if "mesh" not in node:
            continue
        positions, faces, material = meshes[node["mesh"]]
        rotation = quaternion_matrix(node.get("rotation", [0, 0, 0, 1]))
        translated = positions @ rotation.T+np.asarray(node.get("translation", [0, 0, 0]))
        triangles.append(translated[faces])
        material_ids.extend([material]*len(faces))
    triangles = np.concatenate(triangles)
    material_ids = np.asarray(material_ids)

    eye = np.array([0., 1., 0.]) if view == "front" else np.array([1.05, 1.8, .9])
    eye /= np.linalg.norm(eye)
    up_hint = np.array([0., 0., 1.])
    right = np.cross(eye, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, eye)
    projected = np.stack([triangles @ right, triangles @ up], axis=-1)
    depth = triangles.mean(axis=1) @ eye
    order = np.argsort(depth)

    normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    light = np.array([-.35, .8, .48])
    light /= np.linalg.norm(light)
    intensity = .34+.58*np.abs(normal @ light)
    bases = np.array([[.50, .53, .57], [.31, .34, .38], [.08, .10, .12]])
    colors = np.clip(bases[np.clip(material_ids, 0, 2)]*intensity[:, None], 0, 1)

    figure, axis = plt.subplots(figsize=(9, 9), dpi=150)
    figure.patch.set_facecolor("#10141a")
    axis.set_facecolor("#10141a")
    axis.add_collection(PolyCollection(projected[order], facecolors=colors[order],
                                       edgecolors="none", rasterized=True))
    values = projected.reshape(-1, 2)
    center = (values.min(axis=0)+values.max(axis=0))/2
    radius = np.max(values.max(axis=0)-values.min(axis=0))*.54
    axis.set_xlim(center[0]-radius, center[0]+radius)
    axis.set_ylim(center[1]-radius, center[1]+radius)
    axis.set_aspect("equal")
    axis.axis("off")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, bbox_inches="tight", pad_inches=.08, facecolor=figure.get_facecolor())
    plt.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--view", choices=("front", "oblique"), default="front")
    arguments = parser.parse_args()
    render(arguments.source, arguments.output, arguments.view)
