"""Versioned, per-image evidence used by the experimental sector workflow.

The reconstruction code consumes geometry evidence; it must not know about a
particular product-photo SHA.  Learned or deterministic detectors can create a
candidate bundle through the same interface as a human annotation tool.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

from .storage import now, uid


SCHEMA = "wheelcam-sector-evidence-v1"
CANDIDATE_ALGORITHM = "paired-trace-sector-candidate-v1"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _points(value, label, minimum=1):
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{label}至少需要 {minimum} 个点。")
    result = []
    for point in value:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(isinstance(item, (int, float)) and math.isfinite(item) for item in point)):
            raise ValueError(f"{label}必须是有限的二维坐标。")
        result.append([float(point[0]), float(point[1])])
    return result


def validate_bundle(annotations, holdout, source_sha256, image_size):
    """Validate provenance and the common evidence contract before geometry."""
    if not isinstance(annotations, dict) or not isinstance(holdout, dict):
        raise ValueError("扇区证据和观察记录必须是对象。")
    if annotations.get("source_sha256") != source_sha256 or holdout.get("source_sha256") != source_sha256:
        raise ValueError("扇区证据、观察记录与当前原图摘要不一致。")
    width, height = image_size
    if list(annotations.get("image_size", [])) != [width, height]:
        raise ValueError("扇区证据的图像尺寸与当前原图不一致。")
    structure, master = annotations.get("structure"), annotations.get("master")
    if not isinstance(structure, dict) or not isinstance(master, dict):
        raise ValueError("扇区证据缺少结构或母扇区。")
    groups = structure.get("spoke_groups")
    if not isinstance(groups, int) or not 2 <= groups <= 40:
        raise ValueError("周期组数必须是 2–40 的整数。")
    _points(annotations.get("rim_points"), "外圈", 8)
    _points([annotations.get("hub_center")], "中心", 1)
    _points(master.get("boundary"), "母扇区外缘", 12)
    _points(master.get("panel_boundary"), "孔区整体外缘", 12)
    openings = annotations.get("uncertain_pockets", [])
    if not isinstance(openings, list) or len(openings) > 8:
        raise ValueError("孔轮廓数量必须为 0–8。")
    for index, loop in enumerate(openings):
        _points(loop, f"第 {index+1} 个孔轮廓", 3)
    validation = annotations.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("扇区证据缺少诊断观察。")
    _points(validation.get("points"), "诊断观察", 1)
    _points(holdout.get("points"), "固定观察", 1)
    for name, document in (("扇区证据", annotations), ("固定观察", holdout)):
        for point in _all_points(document):
            x, y = point
            if not (0 <= x <= width and 0 <= y <= height):
                raise ValueError(f"{name}坐标必须位于当前原图内。")


def _all_points(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _all_points(item)
    elif isinstance(value, list):
        if len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
            yield value
        else:
            for item in value:
                yield from _all_points(item)


def _root(data_root: Path, source_sha256: str) -> Path:
    return data_root / "sector-evidence" / source_sha256


def _group_rows(analysis, group):
    """Return accepted four-edge cross sections in normalized radial order."""
    traces = [trace for trace in analysis.get("traces", []) if trace.get("group") == group]
    if len(traces) != 2:
        return []
    cx, cy = analysis["ellipse"]["cx"], analysis["ellipse"]["cy"]
    rx, ry = analysis["ellipse"]["rx"], analysis["ellipse"]["ry"]
    angle = math.radians(analysis["spokes"]["image_phase_deg"] + group * 360 / analysis["spokes"]["groups"])
    rows = []
    for first, second in zip(traces[0].get("samples", []), traces[1].get("samples", [])):
        radius = (float(first.get("radius_ratio", 0)) + float(second.get("radius_ratio", 0))) / 2
        if not (.36 <= radius <= .86 and first.get("accepted") and second.get("accepted")):
            continue
        points = [*first.get("points", []), *second.get("points", [])]
        if len(points) != 4:
            continue
        ordered = sorted(points, key=lambda p: -(p[0]-cx)/rx*math.sin(angle) + (p[1]-cy)/ry*math.cos(angle))
        rows.append((radius, ordered))
    return sorted(rows, reverse=True)


def _sample_rows(rows, count=12):
    if len(rows) < count:
        return rows
    indexes = np.unique(np.rint(np.linspace(0, len(rows)-1, count)).astype(int))
    return [rows[index] for index in indexes]


def _observations(rows, scale_x, scale_y, count=14):
    chosen = _sample_rows(rows, max(4, math.ceil(count/4)))
    points = [point for _, edge in chosen for point in edge]
    return [[float(x)*scale_x, float(y)*scale_y] for x, y in points[:count]]


def candidate_from_analysis(analysis, source_sha256, image_size):
    """Build a reviewable Y-sector draft from independent detected blade traces.

    This is deliberately a candidate generator.  It neither infers openings nor
    turns monocular pixels into measured thickness or rear geometry.
    """
    if analysis.get("image_sha256") != source_sha256:
        raise ValueError("识图记录与当前主参考图不一致，请重新识图。")
    spokes = analysis.get("spokes") or {}
    groups = spokes.get("groups")
    if analysis.get("status") != "candidates" or not isinstance(groups, int) or groups < 3:
        raise ValueError("当前识图没有可靠周期候选，请先在照片页重新提取。")
    aw, ah = analysis.get("image_size", [0, 0])
    if not aw or not ah:
        raise ValueError("识图记录缺少图像尺寸。")
    sx, sy = image_size[0]/aw, image_size[1]/ah
    rows = [_group_rows(analysis, group) for group in range(groups)]
    eligible = [index for index, values in enumerate(rows)
                if len(values) >= 12 and values[0][0]-values[-1][0] >= .30]
    if len(eligible) < 3:
        raise ValueError("至少需要三组具有连续双辐边缘的观察，当前证据不足，不能生成母扇区。")
    master_group = max(eligible, key=lambda index: len(rows[index]))
    sampled = _sample_rows(rows[master_group])
    # Rows are outer -> inner and each row is ordered across the two blades.
    edge = [[[float(x)*sx, float(y)*sy] for x, y in points] for _, points in sampled]
    # Follow the outside and inside of each arm.  The bridge between the two
    # inner runs is an explicit root connection, not a filled sector template.
    boundary = ([points[0] for points in reversed(edge)]
                + [points[1] for points in edge]
                + [points[2] for points in reversed(edge)]
                + [points[3] for points in edge])
    inner, outer = edge[-1], edge[0]
    ridge_guides = [
        [[(points[a][0]+points[b][0])/2, (points[a][1]+points[b][1])/2] for points in reversed(edge)]
        for a, b in ((0, 1), (2, 3))
    ]
    alternatives = sorted((index for index in eligible if index != master_group),
                          key=lambda index: len(rows[index]), reverse=True)
    diagnostic_group, holdout_group = alternatives[:2]
    period = 360/groups
    relative = lambda other: ((other-master_group+groups//2) % groups-groups//2) * period
    ellipse = analysis["ellipse"]
    rim = analysis.get("outer_points") or [
        [ellipse["cx"]+ellipse["rx"]*math.cos(t), ellipse["cy"]+ellipse["ry"]*math.sin(t)]
        for t in np.linspace(0, 2*math.pi, 24, endpoint=False)
    ]
    annotations = {
        "schema": SCHEMA, "source_sha256": source_sha256, "image_size": list(image_size),
        "method": CANDIDATE_ALGORITHM,
        "units": "normalized reference pixels; 3D uses outer rim radius = 1, not millimetres",
        "structure": {"spoke_groups": groups, "group_angle_deg": period, "lug_centers": [],
                      "master_group": master_group,
                      "note": "周期来自本地识图；孔位尚未检测。"},
        "master": {"name": f"auto trace group {master_group+1}", "boundary": boundary,
                   "panel_boundary": boundary, "root_seam": [inner[3], inner[0]],
                   "tip_seams": [[outer[0], outer[1]], [outer[2], outer[3]]],
                   "ridge_guides": ridge_guides, "groove_guides": [],
                   "note": "逐支臂边缘组合的可编辑候选；根部连接与辐端切口仍需人工核对。"},
        "rim_points": [[float(x)*sx, float(y)*sy] for x, y in rim],
        "hub_center": [float(ellipse["cx"])*sx, float(ellipse["cy"])*sy],
        "uncertain_pockets": [],
        "validation": {"group_rotation_deg": relative(diagnostic_group),
                       "points": _observations(rows[diagnostic_group], sx, sy),
                       "used_for_optimization": False, "used_for_topology_diagnosis": True,
                       "note": "另一组自动边缘观察，仅用于诊断；不是人工复核或独立盲测。"},
        "uncertainties": ["母扇区来自局部边缘轨迹，需人工校正", "未检测根部小孔",
                          "背面、厚度、连接半径和毫米尺寸均未恢复"],
        "candidate": {"algorithm": CANDIDATE_ALGORITHM, "master_group": master_group,
                      "eligible_groups": eligible, "sample_rows": len(sampled),
                      "source_analysis_id": analysis.get("id")},
    }
    holdout = {"schema": "wheelcam-sector-holdout-v1", "source_sha256": source_sha256,
               "name": f"auto trace group {holdout_group+1}",
               "group_rotation_deg": relative(holdout_group),
               "points": _observations(rows[holdout_group], sx, sy), "created_at": now(),
               "method": CANDIDATE_ALGORITHM, "reviewed": False, "used_for_optimization": False,
               "used_for_camera": False,
               "note": "自动轨迹留出组；用于暴露不一致，不是人工独立验收。"}
    return annotations, holdout


def load(data_root: Path, source_sha256: str, fallback_dir: Path | None = None):
    root = _root(data_root, source_sha256)
    pointer = root / "current.json"
    if pointer.is_file():
        current = json.loads(pointer.read_text())
        evidence_id = current["id"]
        annotations_path = root / f"{evidence_id}.annotations.json"
        holdout_path = root / f"{evidence_id}.holdout.json"
        if not annotations_path.is_file() or not holdout_path.is_file():
            raise ValueError("当前扇区证据版本不完整，请恢复或重新保存。")
        return (json.loads(annotations_path.read_text()), json.loads(holdout_path.read_text()),
                {**current, "origin": "per-image"}, annotations_path.read_bytes(), holdout_path.read_bytes())
    if fallback_dir:
        annotations_path, holdout_path = fallback_dir / "annotations.json", fallback_dir / "holdout.json"
        if annotations_path.is_file() and holdout_path.is_file():
            annotation_bytes, holdout_bytes = annotations_path.read_bytes(), holdout_path.read_bytes()
            annotations, holdout = json.loads(annotation_bytes), json.loads(holdout_bytes)
            if annotations.get("source_sha256") == source_sha256 and holdout.get("source_sha256") == source_sha256:
                return annotations, holdout, {"id": "builtin-sample", "origin": "builtin-sample"}, annotation_bytes, holdout_bytes
    raise FileNotFoundError(source_sha256)


def save(data_root: Path, source_sha256: str, annotations, holdout, method: str):
    root = _root(data_root, source_sha256)
    root.mkdir(parents=True, exist_ok=True)
    evidence_id, created_at = uid(), now()
    annotation_bytes = json.dumps(annotations, ensure_ascii=False, indent=2).encode()
    holdout_bytes = json.dumps(holdout, ensure_ascii=False, indent=2).encode()
    manifest = {"schema": SCHEMA, "id": evidence_id, "created_at": created_at,
                "source_sha256": source_sha256, "method": method,
                "annotations_sha256": digest(annotation_bytes), "holdout_sha256": digest(holdout_bytes)}
    files = {f"{evidence_id}.annotations.json": annotation_bytes,
             f"{evidence_id}.holdout.json": holdout_bytes,
             f"{evidence_id}.manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode()}
    for name, content in files.items():
        staged = root / f".{evidence_id}.{name}.tmp"
        staged.write_bytes(content)
        os.replace(staged, root / name)
    pointer = root / f".{evidence_id}.current.json.tmp"
    pointer.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    os.replace(pointer, root / "current.json")
    return manifest
