"""Rotational window-template benchmark against human labels.

For each usable label: rectify the photo with the outer-rim ellipse (weak-perspective assumption:
ellipse → circle), sample the labelled material in polar coordinates, fold by the labelled group
count, build a template from the EVEN sectors only, and score it on each ODD sector.

The score is an upper bound for any "one window shape × N rotations" model on this photo under this
rectification: losses come from non-symmetric design, concavity parallax that an ellipse cannot
remove, and label noise. It is not a CAD result; candidate CAD masks are scored separately with
labels_to_mask.py.

Usage: benchmark.py [ANNOTATION_DIR] [--all] [--json OUT.json]
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

from labels_to_mask import render

ROOT = Path(__file__).resolve().parents[2]
RADIAL, PER_SECTOR, RENDER_MAX = 200, 180, 1200


def hub_ratio(label):
    """Mean normalised rim radius of the hub ellipse points, i.e. where the scored zone starts."""
    hub, rim = label.get("hub"), label["rim"]
    if not hub:
        return 0.30
    t = math.radians(rim["angle_deg"])
    radii = []
    for x, y in hub["points"]:
        dx, dy = x - rim["cx"], y - rim["cy"]
        u, v = dx * math.cos(t) + dy * math.sin(t), -dx * math.sin(t) + dy * math.cos(t)
        radii.append(math.hypot(u / rim["a"], v / rim["b"]))
    return float(np.mean(radii))


def polar(label, groups, outer=0.97):
    """Material and validity sampled on (rho, phi) with PER_SECTOR columns per group."""
    w, h = label["image"]["width"], label["image"]["height"]
    k = min(1.0, RENDER_MAX / max(w, h))
    size = (max(1, round(w * k)), max(1, round(h * k)))
    material, valid = render(label, size, outer=1.0, inner=0.0)
    rim = label["rim"]; t = math.radians(rim["angle_deg"])
    rho = np.linspace(hub_ratio(label) + 0.01, outer, RADIAL)
    phi = np.linspace(0, 2 * math.pi, groups * PER_SECTOR, endpoint=False)
    R, P = np.meshgrid(rho, phi, indexing="ij")
    u, v = R * np.cos(P) * rim["a"], R * np.sin(P) * rim["b"]
    x = (rim["cx"] + u * math.cos(t) - v * math.sin(t)) * size[0] / w
    y = (rim["cy"] + u * math.sin(t) + v * math.cos(t)) * size[1] / h
    sample = lambda m: map_coordinates(m.astype(float), [y - 0.5, x - 0.5], order=0, mode="constant", cval=0) > 0.5
    return rho, sample(material), sample(valid)


def evaluate(label):
    groups = label["meta"].get("groups")
    if not groups or groups < 2:
        raise ValueError("缺少辐条组数（≥2），无法折叠")
    rho, material, valid = polar(label, groups)
    sectors = [slice(k * PER_SECTOR, (k + 1) * PER_SECTOR) for k in range(groups)]
    area = np.repeat(rho[:, None], PER_SECTOR, axis=1)          # polar cell area ∝ rho
    even = [s for k, s in enumerate(sectors) if k % 2 == 0]
    odd = [s for k, s in enumerate(sectors) if k % 2 == 1]

    def template_from(chosen):
        stack = np.stack([material[:, s] for s in chosen]).astype(float)
        weight = np.stack([valid[:, s] for s in chosen]).astype(float)
        return (stack * weight).sum(0) / np.maximum(weight.sum(0), 1e-9) > 0.5

    def iou(s, template):
        m, ok = material[:, s], valid[:, s]
        union = (((m | template) & ok) * area).sum()
        return float((((m & template) & ok) * area).sum() / union) if union else float("nan")
    template = template_from(even)
    # Even sectors are part of this template, so their scores are in-fit, not held out.
    per_sector = [round(iou(s, template), 4) for s in sectors]
    held = [per_sector[k] for k in range(groups) if k % 2 == 1]
    # Leave-one-out: every sector scored against the template of all the others — use this to
    # locate a deviating group; per_sector_iou favours the even (in-fit) sectors.
    loo = [round(iou(s, template_from([t for t in sectors if t is not s])), 4) for s in sectors]
    return {"groups": groups, "zone_rho": [round(float(rho[0]), 3), round(float(rho[-1]), 3)],
            "heldout_iou_mean": round(float(np.nanmean(held)), 4), "heldout_iou_min": round(float(np.nanmin(held)), 4),
            "loo_iou_mean": round(float(np.nanmean(loo)), 4), "loo_iou_min": round(float(np.nanmin(loo)), 4),
            "per_sector_loo": loo, "per_sector_iou": per_sector, "windows": len(label["windows"]),
            "rim_fit_rms_px": round(label["rim"]["rms_px"], 2), "rim_axis_ratio": round(min(label["rim"]["a"], label["rim"]["b"]) / max(label["rim"]["a"], label["rim"]["b"]), 3),
            "odd_sectors_used": len(odd)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, nargs="?", default=ROOT / "data" / "annotations")
    parser.add_argument("--all", action="store_true", help="包括未勾选“可用于评价”的标注")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.folder.glob("*.json")):
        label = json.loads(path.read_text())
        meta = label.get("meta", {})
        if not args.all and not meta.get("usable"):
            continue
        row = {"name": label["image"]["name"], "sha256": label["image"]["sha256"][:12], **{k: meta.get(k) for k in ("structure", "view", "style")}}
        try:
            row.update(evaluate(label))
        except (ValueError, KeyError) as exc:
            row["error"] = str(exc)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    if not rows:
        print("没有可评价的标注（需要勾选“可用于评价”，或加 --all）")


if __name__ == "__main__":
    main()
