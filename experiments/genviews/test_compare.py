"""Synthetic check of compare.py with a known 8-group reference.

A 'generated' silhouette drawn from the same polar pattern, rotated 17° and mirrored, must align
back with IoU ≈ 1 and report the same harmonics; a 7-group variant must be caught by harmonics.
"""
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import NPHI, RHO, align, generated, harmonics  # noqa: E402


def pattern(groups, rho, phi):
    """Paired arms: two thin blades per group, narrowing outward."""
    period = 2 * math.pi / groups
    local = (phi % period) - period / 2
    half_gap, blade = 0.05 + 0.02 * rho, 0.035
    return (np.abs(local) > half_gap) & (np.abs(local) < half_gap + blade / np.maximum(rho, 0.2))


def reference_polar(groups):
    R, P = np.meshgrid(RHO, np.linspace(0, 2 * math.pi, NPHI, endpoint=False), indexing="ij")
    return pattern(groups, R, P)


def silhouette(groups, rotation_deg, mirrored, path, size=900):
    yy, xx = np.mgrid[0:size, 0:size]
    c, r = size / 2 + 7.5, size * 0.44
    dx, dy = (xx - c) / r, (yy - c) / r
    rho = np.hypot(dx, dy)
    phi = np.arctan2(dy, dx)
    phi = (-phi if mirrored else phi) - math.radians(rotation_deg)
    solid = (rho <= 1) & ((rho > 0.88) | (rho < 0.28) | pattern(groups, rho, phi % (2 * math.pi)))
    image = Image.fromarray(np.where(solid, 20, 240).astype("uint8")).filter(ImageFilter.GaussianBlur(1.2))
    image.save(path)


def test_rotated_mirrored_copy_aligns(tmp_path=Path("/tmp")):
    path = tmp_path / "gen_same.png"
    silhouette(8, 17, True, path)
    gen, geometry = generated(path)
    result = align(reference_polar(8), gen)
    # The paired pattern is mirror-symmetric, so "mirrored + 17°" and "plain −17° ≡ 28° (mod 45°)"
    # are the same image; accept either reading.
    assert result["iou"] > 0.9, result
    expected = 17.0 if result["mirrored"] else (-17.0) % 45
    assert abs(result["rotation_deg"] % 45 - expected) < 1.0, result
    assert harmonics(gen)[0] == harmonics(reference_polar(8))[0], (harmonics(gen), harmonics(reference_polar(8)))
    assert abs(geometry["radius_px"] - 900 * 0.44) < 5 and geometry["roundness"] > 0.98, geometry


def test_wrong_group_count_is_detected(tmp_path=Path("/tmp")):
    path = tmp_path / "gen_seven.png"
    silhouette(7, 0, False, path)
    gen, _ = generated(path)
    assert harmonics(gen)[0] != harmonics(reference_polar(8))[0]
    assert align(reference_polar(8), gen)["iou"] < 0.6


if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    test_rotated_mirrored_copy_aligns(d); test_wrong_group_count_is_detected(d)
    print("compare synthetic tests passed")
