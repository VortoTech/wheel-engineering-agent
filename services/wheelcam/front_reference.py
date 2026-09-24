"""Deterministic near-front reference views derived from a fitted rim ellipse.

These images are annotation aids.  They do not infer hidden depth, the rear face,
or any geometry that is not visible in the source photograph.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import map_coordinates


def estimate_tilt_deg(ellipse: dict) -> float:
    """Return the orthographic tilt implied by an ellipse's minor/major ratio."""
    rx, ry = abs(float(ellipse["rx"])), abs(float(ellipse["ry"]))
    if not rx or not ry:
        raise ValueError("外圈椭圆半径必须大于零。")
    ratio = min(rx, ry) / max(rx, ry)
    return math.degrees(math.acos(max(0.0, min(1.0, ratio))))


def rectify_front(
    source: Image.Image,
    ellipse: dict,
    analysis_size: tuple[int, int] | list[int] | None = None,
    output_size: int | None = None,
) -> Image.Image:
    """Map the fitted ellipse to a circle and mask everything outside the face.

    ``ellipse`` may use coordinates from a resized analysis image.  Supplying
    ``analysis_size`` scales it back to the source image before resampling.
    """
    image = source.convert("RGB")
    source_w, source_h = image.size
    analysis_w, analysis_h = analysis_size or image.size
    if analysis_w <= 0 or analysis_h <= 0:
        raise ValueError("识图尺寸无效。")
    scale_x, scale_y = source_w / analysis_w, source_h / analysis_h
    cx = float(ellipse["cx"]) * scale_x
    cy = float(ellipse["cy"]) * scale_y
    rx = float(ellipse["rx"]) * scale_x
    ry = float(ellipse["ry"]) * scale_y
    if rx <= 0 or ry <= 0:
        raise ValueError("外圈椭圆半径必须大于零。")

    side = int(output_size or max(source_w, source_h))
    if side < 64:
        raise ValueError("正视参考图尺寸过小。")
    center = side / 2
    radius = side * 0.46
    angle = math.radians(float(ellipse.get("angle_deg", 0.0)))
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    # Pillow expects an inverse affine transform: destination pixel -> source.
    ax, bx = cos_a * rx / radius, -sin_a * ry / radius
    ay, by = sin_a * rx / radius, cos_a * ry / radius
    coeffs = (
        ax,
        bx,
        cx - ax * center - bx * center,
        ay,
        by,
        cy - ay * center - by * center,
    )
    face = image.transform(
        (side, side),
        Image.Transform.AFFINE,
        coeffs,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(255, 255, 255),
    ).convert("RGBA")
    mask = Image.new("L", (side, side), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        (center - radius, center - radius, center + radius, center + radius),
        fill=255,
    )
    face.putalpha(mask)
    return face


def symmetry_guide(rectified: Image.Image, groups: int = 5, phase_deg: float = -90.0) -> Image.Image:
    """Repeat one canonical sector as a clearly derived, exactly symmetric guide.

    Averaging every sector creates ghosts when the source has parallax.  Instead,
    this samples one full sector centered on ``phase_deg`` and copies that same
    evidence around the axis.  It is intentionally a tracing aid, never a new
    observation of the object.
    """
    if groups < 2:
        raise ValueError("对称组数至少为 2。")
    rgba = np.asarray(rectified.convert("RGBA"), dtype=np.float32)
    height, width = rgba.shape[:2]
    yy, xx = np.indices((height, width), dtype=np.float32)
    cx, cy = (width - 1) / 2, (height - 1) / 2
    dx, dy = xx - cx, yy - cy
    radius = np.hypot(dx, dy)
    theta = np.arctan2(dy, dx)
    phase = math.radians(phase_deg)
    period = 2 * math.pi / groups
    canonical = phase + (theta - phase + period / 2) % period - period / 2
    source_x = cx + radius * np.cos(canonical)
    source_y = cy + radius * np.sin(canonical)
    channels = [map_coordinates(rgba[..., channel], [source_y, source_x], order=1, mode="constant", cval=0)
                for channel in range(3)]
    channels.append(rgba[..., 3])
    return Image.fromarray(np.stack(channels, axis=-1).clip(0, 255).astype(np.uint8), "RGBA")


def composite_on_white(image: Image.Image) -> Image.Image:
    """Return an RGB preview while preserving transparent PNGs at the API boundary."""
    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, "white")
    return Image.alpha_composite(white, rgba).convert("RGB")
