"""Check a generated orthographic front view against the original photo.

Reference: the photo's material mask plus its rim ellipse — either a human label JSON
(experiments/annotate) or a mask + window-spike context.json.
Generated: dark pixels = material on a light background (the front_silhouette prompt).

Both are sampled on the same rectified polar grid (rho 0.30–0.76 of the outer radius), then:
  * dominant angular harmonics  → did the generator keep the group / arm count?
  * best rotation (and mirror)  → align, since the generator may rotate or flip the design
  * area-weighted IoU            → how much of the window shape survived
The IoU compares front-face planform only; it says nothing about depth or section.

Usage:
  compare.py GENERATED.png --ref-label LABEL.json
  compare.py GENERATED.png --ref-mask sam_arms.png --ref-context context.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy.ndimage import binary_fill_holes, label as components, map_coordinates

RHO = np.linspace(0.30, 0.76, 120)
NPHI = 1440


def polar(mask, cx, cy, rx, ry, angle_deg=0.0):
    t = math.radians(angle_deg)
    R, P = np.meshgrid(RHO, np.linspace(0, 2 * math.pi, NPHI, endpoint=False), indexing="ij")
    u, v = R * np.cos(P) * rx, R * np.sin(P) * ry
    x, y = cx + u * math.cos(t) - v * math.sin(t), cy + u * math.sin(t) + v * math.cos(t)
    return map_coordinates(mask.astype(float), [y, x], order=1, mode="constant") > 0.5


def reference(args):
    if args.ref_label:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "annotate"))
        from labels_to_mask import render
        label = json.loads(args.ref_label.read_text())
        w, h = label["image"]["width"], label["image"]["height"]
        material, _ = render(label, (w, h), outer=1.0, inner=0.0)
        e = label["rim"]
        return polar(material, e["cx"], e["cy"], e["a"], e["b"], e["angle_deg"])
    mask = np.asarray(Image.open(args.ref_mask).convert("L")) > 127
    e = json.loads(args.ref_context.read_text())["ellipse"]
    return polar(mask, e["cx"], e["cy"], e["rx"], e["ry"])


def generated(path, scale=1.0):
    """Polar grid of the generated view; scale multiplies the detected outer radius."""
    material, geometry = segment(path)
    cx, cy = geometry["center_px"]
    radius = geometry["radius_px"] * scale
    return polar(material, cx, cy, radius, radius), geometry


def segment(path):
    """Segment the silhouette, then take centre and radius from the filled wheel disc."""
    with Image.open(path) as image:
        gray = np.asarray(ImageOps.exif_transpose(image).convert("L"), dtype=float)
    hist, edges = np.histogram(gray, bins=256, range=(0, 256))
    # Otsu threshold: the prompt asks for pure black on pure white, but generators add grey.
    p = hist / hist.sum(); omega = np.cumsum(p); mu = np.cumsum(p * np.arange(256))
    between = (mu[-1] * omega - mu) ** 2 / np.maximum(omega * (1 - omega), 1e-12)
    material = gray < int(np.argmax(between))
    labels, count = components(material)
    if not count:
        raise ValueError("生成图里没有深色实体，无法分割")
    largest = labels == (np.argmax(np.bincount(labels.ravel())[1:]) + 1)
    disc = binary_fill_holes(largest)
    ys, xs = np.nonzero(disc)
    cy, cx = ys.mean(), xs.mean()
    cov = np.cov(np.vstack([xs - cx, ys - cy]))
    axes = np.sqrt(np.linalg.eigvalsh(cov)) * 2          # uniform disc: radius = 2·sqrt(variance)
    radius = math.sqrt(disc.sum() / math.pi)
    return material, {"center_px": [round(cx, 1), round(cy, 1)], "radius_px": round(radius, 1),
                      "roundness": round(float(axes[0] / axes[1]), 3)}


def harmonics(m, top=3):
    weights = RHO[:, None]
    spectrum = np.abs(np.fft.rfft((m - m.mean(axis=1, keepdims=True)) * weights, axis=1)).sum(0)
    order = [k for k in np.argsort(spectrum)[::-1] if 3 <= k <= 40][:top]
    return [int(k) for k in order]


def align(ref, gen):
    weights = RHO[:, None]
    a, best = (2 * ref - 1.0) * weights, None
    for mirrored in (False, True):
        g = gen[:, ::-1] if mirrored else gen
        b = (2 * g - 1.0) * weights
        score = np.fft.irfft(np.fft.rfft(a, axis=1).conj() * np.fft.rfft(b, axis=1), n=NPHI, axis=1).sum(0)
        shift = int(np.argmax(score))
        if best is None or score[shift] > best[0]:
            best = (score[shift], shift, mirrored, np.roll(g, -shift, axis=1))
    _, shift, mirrored, aligned = best
    union = ((ref | aligned) * weights).sum()
    return {"rotation_deg": round(shift * 360 / NPHI, 2), "mirrored": mirrored,
            "iou": round(float(((ref & aligned) * weights).sum() / union), 4) if union else None,
            "material_fraction_ref": round(float((ref * weights).sum() / (weights.sum() * NPHI)), 4),
            "material_fraction_gen": round(float((aligned * weights).sum() / (weights.sum() * NPHI)), 4)}


def compare(ref_polar, gen_path, scales=np.linspace(0.85, 1.15, 31)):
    """Report IoU at the detected radius and at the best radial scale: the generator may redraw the
    lip/barrel proportions, which shifts every radius and would otherwise hide a correct spoke shape."""
    material, geometry = segment(gen_path)
    cx, cy = geometry["center_px"]

    def at(scale):
        r = geometry["radius_px"] * scale
        return polar(material, cx, cy, r, r)

    base = at(1.0)
    best = max(((s, align(ref_polar, at(s))) for s in scales), key=lambda item: item[1]["iou"] or 0)
    return {"generated": str(gen_path), **geometry,
            "harmonics_ref": harmonics(ref_polar), "harmonics_gen": harmonics(base),
            "iou_at_detected_radius": align(ref_polar, base)["iou"],
            "best_scale": round(float(best[0]), 3), **best[1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("generated", type=Path, nargs="+")
    parser.add_argument("--ref-label", type=Path)
    parser.add_argument("--ref-mask", type=Path)
    parser.add_argument("--ref-context", type=Path)
    args = parser.parse_args()
    if not args.ref_label and not (args.ref_mask and args.ref_context):
        parser.error("需要 --ref-label，或 --ref-mask 与 --ref-context")
    ref = reference(args)
    for path in args.generated:
        print(json.dumps(compare(ref, path), ensure_ascii=False))


if __name__ == "__main__":
    main()
