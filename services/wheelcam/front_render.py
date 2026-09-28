"""Front view of a wheel mesh without a browser or GPU: the part as a height field seen along -Z,
z-buffered in numpy and shaded by its normals. For the style agent (wheelcam.style_agent), which
shows it next to the order render; runs anywhere the mesh kernel runs (Spark has no display).
"""
import math

import numpy as np


def height_field(verts, tris, size=640, extent=None):
    """(z, mask, extent): the highest surface z over a size x size grid spanning +-extent mm."""
    v = np.asarray(verts, float)
    t = np.asarray(tris, np.int64)
    extent = extent or float(np.abs(v[:, :2]).max()) * 1.02
    px = (v[:, 0] + extent) / (2 * extent) * (size - 1)
    py = (extent - v[:, 1]) / (2 * extent) * (size - 1)          # image rows go down
    z = np.full(size * size, -np.inf)
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    x0, y0, x1, y1, x2, y2 = px[a], py[a], px[b], py[b], px[c], py[c]
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    keep = np.abs(den) > 1e-12                                     # walls seen edge on
    lo_x = np.floor(np.minimum.reduce([x0, x1, x2])).astype(int)
    lo_y = np.floor(np.minimum.reduce([y0, y1, y2])).astype(int)
    span = np.ceil(np.maximum(np.maximum.reduce([x0, x1, x2]) - lo_x, np.maximum.reduce([y0, y1, y2]) - lo_y)).astype(int) + 1
    for k in np.unique(span[keep]):                                # triangles grouped by pixel span
        sel = np.flatnonzero(keep & (span == k))
        for chunk in np.array_split(sel, max(1, len(sel) * k * k // 4_000_000 + 1)):
            gx, gy = np.meshgrid(np.arange(k), np.arange(k))
            X = lo_x[chunk, None] + gx.ravel()[None] + .5
            Y = lo_y[chunk, None] + gy.ravel()[None] + .5
            d = den[chunk, None]
            l0 = ((y1[chunk, None] - y2[chunk, None]) * (X - x2[chunk, None]) + (x2[chunk, None] - x1[chunk, None]) * (Y - y2[chunk, None])) / d
            l1 = ((y2[chunk, None] - y0[chunk, None]) * (X - x2[chunk, None]) + (x0[chunk, None] - x2[chunk, None]) * (Y - y2[chunk, None])) / d
            l2 = 1 - l0 - l1
            inside = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
            xi, yi = np.floor(X).astype(int), np.floor(Y).astype(int)
            inside &= (xi >= 0) & (xi < size) & (yi >= 0) & (yi < size)
            zz = l0 * v[a[chunk], 2][:, None] + l1 * v[b[chunk], 2][:, None] + l2 * v[c[chunk], 2][:, None]
            np.maximum.at(z, (yi * size + xi)[inside], zz[inside])
    z = z.reshape(size, size)
    return z, np.isfinite(z), extent


def shade(z, mask, extent, light=(-.45, .55, .70)):
    """Grey image (uint8): Lambert shading of the height field on white, dark where the surface
    is steep or deep, as a black wheel looks in a studio render."""
    size = z.shape[0]
    mm = 2 * extent / (size - 1)
    zf = np.where(mask, z, np.nan)
    fill = np.nanmin(zf) if np.isfinite(zf).any() else 0.0
    zf = np.where(mask, z, fill)
    gy, gx = np.gradient(zf, mm)
    n = np.stack([-gx, gy, np.ones_like(zf)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    lam = np.clip(n @ np.asarray(light) / np.linalg.norm(light), 0, 1)
    depth = (zf - fill) / max(float(np.ptp(zf[mask])) if mask.any() else 1.0, 1e-6)
    img = 40 + 150 * lam * (.55 + .45 * depth)
    img = np.where(mask, img, 255)
    return img.clip(0, 255).astype(np.uint8)


def render_front(body, size=640):
    """Front view (uint8 grey) of a manifold3d part, face towards +Z."""
    mesh = body.to_mesh()
    z, mask, extent = height_field(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), size)
    return shade(z, mask, extent)


def rotate_to_photo(img, degrees):
    """The render turned by `degrees` (counter-clockwise) about its centre, to face as the photo does."""
    from PIL import Image
    return np.asarray(Image.fromarray(img).rotate(degrees, resample=Image.BICUBIC, fillcolor=255))


if __name__ == "__main__":
    import json
    import sys

    from PIL import Image

    from . import mesh_build
    body, _ = mesh_build.build(json.loads(open(sys.argv[1]).read()))
    Image.fromarray(render_front(body)).save(sys.argv[2])
    print(sys.argv[2])
