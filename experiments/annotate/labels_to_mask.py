"""Render window labels to masks at any size, and score candidate masks against them.

material = inside the outer-rim ellipse − window polygons
valid    = evaluation zone (between hub ellipse and rim ellipse × --outer) − ignore polygons

Scores are material IoU / precision / recall over valid pixels. They measure only the annotated
front-face openings in this photo, not dimensional accuracy.

Usage:
  labels_to_mask.py LABEL.json --size W H [--out DIR] [--candidate MASK.png ...]
  (W H = pixel size of the candidate masks, e.g. the 720 px detection frame; labels are rescaled.)
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def ellipse_mask(e, size, scale_xy, factor=1.0):
    w, h = size
    sx, sy = scale_xy
    yy, xx = np.mgrid[0:h, 0:w]
    # Pixel centres back to label coordinates, then into the ellipse frame.
    dx, dy = (xx + 0.5) / sx - e["cx"], (yy + 0.5) / sy - e["cy"]
    t = math.radians(e["angle_deg"])
    u, v = dx * math.cos(t) + dy * math.sin(t), -dx * math.sin(t) + dy * math.cos(t)
    return (u / e["a"]) ** 2 + (v / e["b"]) ** 2 <= factor ** 2


def polygon_mask(polygons, size, scale_xy):
    canvas = Image.new("L", size)
    draw = ImageDraw.Draw(canvas)
    for polygon in polygons:
        draw.polygon([(x * scale_xy[0], y * scale_xy[1]) for x, y in polygon["points"]], fill=255)
    return np.asarray(canvas) > 0


def render(label, size, outer=0.97, inner=0.30):
    """Returns (material, valid) boolean arrays of shape (H, W)."""
    if not label.get("rim"):
        raise ValueError("标注缺少外圈椭圆，无法定义评价区域")
    image = label["image"]
    scale = (size[0] / image["width"], size[1] / image["height"])
    wheel = ellipse_mask(label["rim"], size, scale)
    windows = polygon_mask(label["windows"], size, scale)
    ignore = polygon_mask(label["ignore"], size, scale)
    hub = ellipse_mask(label["hub"], size, scale) if label.get("hub") else ellipse_mask(label["rim"], size, scale, inner)
    zone = ellipse_mask(label["rim"], size, scale, outer) & ~hub
    return wheel & ~windows, zone & ~ignore


def score(material, valid, candidate):
    union = ((material | candidate) & valid).sum()
    both = (material & candidate & valid).sum()
    return {"iou": round(float(both / max(1, union)), 4),
            "material_precision": round(float(both / max(1, (candidate & valid).sum())), 4),
            "material_recall": round(float(both / max(1, (material & valid).sum())), 4),
            "valid_pixels": int(valid.sum())}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("label", type=Path)
    parser.add_argument("--size", type=int, nargs=2, metavar=("W", "H"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--candidate", type=Path, nargs="*", default=[])
    parser.add_argument("--outer", type=float, default=0.97, help="评价区外边界 = 外圈椭圆 × 该系数")
    args = parser.parse_args()
    label = json.loads(args.label.read_text())
    size = tuple(args.size) if args.size else (label["image"]["width"], label["image"]["height"])
    material, valid = render(label, size, args.outer)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        Image.fromarray((material * 255).astype("uint8")).save(args.out / "label_material.png")
        Image.fromarray((valid * 255).astype("uint8")).save(args.out / "label_valid.png")
    print("label", json.dumps({"windows": len(label["windows"]), "ignore": len(label["ignore"]),
                               "valid_pixels": int(valid.sum()), "size": size}))
    for path in args.candidate:
        candidate = np.asarray(Image.open(path).convert("L").resize(size)) > 127
        print(path.name, json.dumps(score(material, valid, candidate)))


if __name__ == "__main__":
    main()
