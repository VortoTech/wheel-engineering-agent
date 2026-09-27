"""Visual check: how closely a built wheel matches its reference photo, as numbers.

The CAD is rendered orthographically in the photo's frame (rim circle -> lip radius) and compared
in millimetres on the wheel face:

  window_iou     see-through area (photo background inside the lip) vs. CAD see-through area
  edge_mm        mean distance from photo edges to CAD crease edges and back (spoke flanks, ridges,
                 window walls); lower is better, lighting makes it never zero
  phase_deg      spoke rotation that best aligns the two (the photo's clocking is arbitrary)

Only the band hub_r .. BAND_OUT lip_r is scored: the centre carries a logo cap that is not part of the
CAD, and near the lip a real (perspective) photo sees the barrel wall through the windows where an
orthographic render sees through. The numbers are for comparing versions of the same wheel against
the same photo, not absolute accuracy.
"""
import math

import numpy as np
from scipy import ndimage

CREASE_DEG = 20.0            # normal change that counts as an edge in the CAD render
BAND_OUT = .78               # outer edge of the scored band, as a share of lip_r (see module doc)


def mesh(shape, tol=.3):
    """Triangles (n, 3, 3) and a B-Rep face id per triangle (a triangle id for a mesh-build manifold)."""
    if hasattr(shape, "to_mesh"):
        m = shape.to_mesh()
        tri = np.asarray(m.vert_properties, float)[:, :3][np.asarray(m.tri_verts)]
        keep = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) > 1e-3
        return tri[keep], np.arange(len(tri))[keep]
    tris, ids = [], []
    for k, face in enumerate(shape.Faces()):
        vs, fs = face.tessellate(tol, .3)
        if fs:
            v = np.array([x.toTuple() for x in vs])
            tris.append(v[np.array(fs)])
            ids.append(np.full(len(fs), k))
    tri, fid = np.concatenate(tris), np.concatenate(ids)
    keep = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) > 1e-3   # needle triangles: no usable normal
    return tri[keep], fid[keep]


def _normals(tri, eye):
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    return fn * np.where(fn @ eye < 0, -1.0, 1.0)[:, None]


def splat(px, py, d, shape, rng=None):
    """Z-buffer of projected triangles (vertex pixel coords px, py and depth d toward the viewer, each
    (n, 3)) by dense random splats. Returns (triangle index per pixel, -1 = see-through; depth)."""
    rng = rng or np.random.default_rng(0)
    h, w = shape
    area = .5 * np.abs((px[:, 1] - px[:, 0]) * (py[:, 2] - py[:, 0]) - (px[:, 2] - px[:, 0]) * (py[:, 1] - py[:, 0]))
    counts = np.ceil(area * 12 + 4).astype(int)       # ~e^-12 chance a pixel gets no sample
    depth = np.full(h * w, -np.inf)
    face = np.full(h * w, -1)
    start, chunk = 0, 2_000_000
    ends = np.searchsorted(np.cumsum(counts), np.arange(1, counts.sum() // chunk + 2) * chunk)
    for stop in list(ends) + [len(px)]:
        stop = min(stop + 1, len(px))
        if stop <= start:
            continue
        idx = np.repeat(np.arange(start, stop), counts[start:stop])
        start = stop
        a, b = rng.random(len(idx)), rng.random(len(idx))
        flip = a + b > 1
        a[flip], b[flip] = 1 - a[flip], 1 - b[flip]
        wt = np.stack([1 - a - b, a, b], 1)
        sx, sy, sd = (wt * px[idx]).sum(1), (wt * py[idx]).sum(1), (wt * d[idx]).sum(1)
        ok = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        pix, sd, tri_id = sy[ok].astype(int) * w + sx[ok].astype(int), sd[ok], idx[ok]
        order = np.argsort(sd)                              # nearest written last
        pix, sd, tri_id = pix[order], sd[order], tri_id[order]
        win = sd > depth[pix]
        depth[pix[win]] = sd[win]
        face[pix[win]] = tri_id[win]
    face, depth = face.reshape(h, w), depth.reshape(h, w)
    # Random splats leave pinholes: see-through regions of a few pixels are filled.
    hit = face >= 0
    labels, n = ndimage.label(~hit)
    sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, n + 1))
    hit |= np.isin(labels, 1 + np.nonzero(sizes <= 8)[0])
    # A pixel no front sample reached shows a surface behind: take the nearest neighbour when most
    # neighbours are well in front of it.
    ahead = sum((np.roll(np.roll(depth, dy, 0), dx, 1) > depth + 1.0).astype(int)
                for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
    speck = (ahead >= 6) & (face >= 0)
    if speck.any():
        iy, ix = np.nonzero(speck)
        best = np.argmax(np.stack([np.roll(np.roll(depth, -dy, 0), -dx, 1)[iy, ix] for dy in (-1, 0, 1) for dx in (-1, 0, 1)]), 0)
        dy, dx = best // 3 - 1, best % 3 - 1
        jy, jx = np.clip(iy + dy, 0, h - 1), np.clip(ix + dx, 0, w - 1)
        face[iy, ix], depth[iy, ix] = face[jy, jx], depth[jy, jx]
    near = ndimage.distance_transform_edt(face < 0, return_distances=False, return_indices=True)
    return np.where(hit, face[near[0], near[1]], -1), np.where(hit, depth[near[0], near[1]], np.nan)


def raster(tri, eye, extent, size=500, up=(0, 1, 0)):
    """Orthographic render: depth (nan = see-through) and unit normal per pixel.

    Pixel (i, j) covers x = (j + .5) / size * 2 extent - extent, y = extent - (i + .5) / size * 2 extent.
    """
    eye = np.asarray(eye, float) / np.linalg.norm(eye)
    right = np.cross(up, eye)
    right /= np.linalg.norm(right)
    upv = np.cross(eye, right)
    P = tri.reshape(-1, 3)
    px = (((P @ right) / extent * .5 + .5) * size).reshape(-1, 3)
    py = ((.5 - (P @ upv) / extent * .5) * size).reshape(-1, 3)
    face, depth = splat(px, py, (P @ eye).reshape(-1, 3), (size, size))
    normal = np.zeros((size, size, 3))
    normal[face >= 0] = _normals(tri, eye)[face[face >= 0]]
    return depth, normal


def crease_edges(depth, normal, step_mm_px):
    """Pixels where the surface normal turns by more than CREASE_DEG, or the depth jumps (silhouettes)."""
    hit = np.isfinite(depth)
    edge = np.zeros_like(hit)
    cos_lim = math.cos(math.radians(CREASE_DEG))
    for axis in (0, 1):
        n2 = np.roll(normal, -1, axis=axis)
        h2 = np.roll(hit, -1, axis=axis)
        d2 = np.roll(depth, -1, axis=axis)
        both = hit & h2
        turn = both & ((normal * n2).sum(-1) < cos_lim)
        jump = both & (np.abs(np.nan_to_num(depth - d2)) > 3 * step_mm_px)
        edge |= turn | jump | (hit ^ h2)
    return edge


def photo_on_grid(image, centre, radius_px, lip_r, extent, size, rot_deg=0.0):
    """Resample the photo onto the render grid (mm), rotating the photo by rot_deg about the rim centre."""
    c = (np.arange(size) + .5) / size * 2 * extent - extent
    x, y = np.meshgrid(c, -c)
    t = math.radians(rot_deg)
    xr, yr = x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)
    scale = radius_px / lip_r
    cols, rows = centre[0] + xr * scale, centre[1] - yr * scale
    grey = image[..., :3].mean(axis=2)
    bright = image[..., :3].min(axis=2)
    sample = lambda im: ndimage.map_coordinates(im, [rows, cols], order=1, mode='nearest')
    return sample(grey), sample(bright)


def photo_edges(grey, band, pct=85):
    g = ndimage.gaussian_filter(grey, 1.0)
    mag = np.hypot(ndimage.sobel(g, 0), ndimage.sobel(g, 1))
    thr = max(np.percentile(mag[band], pct), .05)          # a flat region has no edges, not its top 15 %
    return (mag > thr) & band


def compare_front(shape, image, rim_centre, rim_radius_px, p, size=500, phase_step=.5):
    """Scores of a built wheel against a straight-on photo. Returns (scores, overlay RGB uint8)."""
    extent = p.lip_r * 1.02
    tri, _ = mesh(shape)
    depth, normal = raster(tri, (0, 0, 1), extent, size)
    px_mm = 2 * extent / size
    c = (np.arange(size) + .5) / size * 2 * extent - extent
    x, y = np.meshgrid(c, -c)
    r = np.hypot(x, y)
    band = (r > p.hub_r) & (r < BAND_OUT * p.lip_r)
    cad_open = ~np.isfinite(depth)
    cad_edge = crease_edges(depth, normal, px_mm) & band
    pitch = 360 / p.spokes
    best = None
    for rot in np.arange(-pitch / 2, pitch / 2, phase_step):
        _, bright = photo_on_grid(image, rim_centre, rim_radius_px, p.lip_r, extent, size, rot)
        ph_open = bright > .85
        inter, union = (ph_open & cad_open & band).sum(), ((ph_open | cad_open) & band).sum()
        iou = inter / max(union, 1)
        if best is None or iou > best[0]:
            best = (iou, rot)
    iou, rot = best
    grey, bright = photo_on_grid(image, rim_centre, rim_radius_px, p.lip_r, extent, size, rot)
    ph_open = bright > .85
    ph_edge = photo_edges(grey, band & ~ph_open) | (ph_open ^ ndimage.binary_erosion(ph_open)) & band
    to_cad = ndimage.distance_transform_edt(~cad_edge) * px_mm
    to_photo = ndimage.distance_transform_edt(~ph_edge) * px_mm
    edge_mm = .5 * (to_cad[ph_edge].mean() + to_photo[cad_edge].mean())
    scores = {"window_iou": round(float(iou), 3), "edge_mm": round(float(edge_mm), 2),
              "edge_photo_to_cad_mm": round(float(to_cad[ph_edge].mean()), 2),
              "edge_cad_to_photo_mm": round(float(to_photo[cad_edge].mean()), 2),
              "phase_deg": round(float(rot), 2), "missing_open_mm2": round(float((ph_open & ~cad_open & band).sum() * px_mm ** 2)),
              "extra_open_mm2": round(float((cad_open & ~ph_open & band).sum() * px_mm ** 2))}
    overlay = np.stack([grey] * 3, -1) * .45 + .45
    overlay[ph_open & ~cad_open & band] = [.95, .55, .1]     # photo open, CAD solid: CAD too wide
    overlay[cad_open & ~ph_open & band] = [.2, .5, .95]      # CAD open, photo solid: CAD too thin
    overlay[cad_edge] = [.1, .9, .3]
    return scores, (np.clip(overlay, 0, 1) * 255).astype(np.uint8)


def _project_oblique(tri, e, tilt, centre, k, psi, mirror, scale=1.0):
    """Weak perspective of forged_photo.fit_depth: spoke phase psi, handedness mirror, tilt about the
    lip ellipse's major axis. Returns pixel coords and depth toward the camera, each (n, 3)."""
    ang = math.radians(e["angle_deg"]) + (0 if e["a"] < e["b"] else math.pi / 2)
    minor = np.array([math.cos(ang), math.sin(ang)])
    if minor @ centre[1] < 0:
        minor = -minor
    major = np.array([-minor[1], minor[0]])
    P = tri.reshape(-1, 3)
    x, y, z = P[:, 0], mirror * P[:, 1], P[:, 2]
    u = x * math.cos(psi) - y * math.sin(psi)
    v = x * math.sin(psi) + y * math.cos(psi)
    m = v * math.cos(tilt) - z * math.sin(tilt)
    img = centre[0] + k * (np.outer(u, major) + np.outer(m, minor))
    d = v * math.sin(tilt) + z * math.cos(tilt)
    return (img[:, 0] * scale).reshape(-1, 3), (img[:, 1] * scale).reshape(-1, 3), d.reshape(-1, 3)


def compare_oblique(shape, image, rim_points, hub_point, p, coarse_step=1.0):
    """Scores of a built wheel against an oblique (20-45 deg) photo of the same wheel.

    silhouette_iou  wheel outline incl. the barrel (rim width, lip)
    window_iou      see-through inside the lip ellipse (dish depth moves the windows in this view)
    edge_mm         as in compare_front, in mm on the lip plane, inside the lip ellipse
    Returns (scores, overlay RGB uint8).
    """
    from .forged_photo import _oblique_camera
    from .window_fit import fit_ellipse
    e, tilt, _ = _oblique_camera(p, rim_points, hub_point)
    a_px = max(e["a"], e["b"])
    k = a_px / p.lip_r
    c = np.array([e["cx"], e["cy"]])
    centre = (c, np.asarray(hub_point, float) - c)
    h, w = image.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    phi = math.radians(e["angle_deg"])
    uu = (xx - e["cx"]) * math.cos(phi) + (yy - e["cy"]) * math.sin(phi)
    vv = -(xx - e["cx"]) * math.sin(phi) + (yy - e["cy"]) * math.cos(phi)
    inside = (uu / e["a"]) ** 2 + (vv / e["b"]) ** 2 <= .9 ** 2
    bright = image[..., :3].min(axis=2)
    ph_fg, ph_open = bright < .9, (bright > .85) & inside
    tri, _ = mesh(shape)
    tri[..., 2] -= tri[..., 2].max()           # recipe frame: lip front at z = 0 (exported STEPs are centred)
    small = .5
    hs, ws = int(h * small), int(w * small)
    ph_open_s = ph_open[::2, ::2][:hs, :ws]
    inside_s = inside[::2, ::2][:hs, :ws]
    pitch = 2 * math.pi / p.spokes

    def score(psi, mirror, sc, shape_hw, open_mask, region):
        face, _ = splat(*_project_oblique(tri, e, tilt, centre, k, psi, mirror, sc), shape_hw)
        cad_open = (face < 0) & region
        return ((cad_open & open_mask).sum() / max((cad_open | open_mask).sum(), 1)), face

    best = max(((score(psi, m, small, (hs, ws), ph_open_s, inside_s)[0], psi, m)
                for m in (1, -1) for psi in np.arange(0, pitch, math.radians(coarse_step))), key=lambda t: t[0])
    _, psi0, mirror = best
    best = max(((score(psi, mirror, 1.0, (h, w), ph_open, inside)[0], psi)
                for psi in psi0 + np.radians(np.arange(-1, 1.01, .25))), key=lambda t: t[0])
    iou, psi = best
    px, py, d = _project_oblique(tri, e, tilt, centre, k, psi, mirror)
    face, depth = splat(px, py, d, (h, w))
    hit = face >= 0
    cad_open = ~hit & inside
    sil = (hit & ph_fg).sum() / max((hit | ph_fg).sum(), 1)
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    normal = np.zeros((h, w, 3))
    normal[hit] = fn[face[hit]]
    cad_edge = np.zeros((h, w), bool)
    for axis in (0, 1):
        n2, h2 = np.roll(normal, -1, axis=axis), np.roll(hit, -1, axis=axis)
        cad_edge |= (hit & h2 & (np.abs((normal * n2).sum(-1)) < math.cos(math.radians(CREASE_DEG)))) | (hit ^ h2)
    cad_edge &= inside
    grey = image[..., :3].mean(axis=2)
    ph_edge = photo_edges(grey, inside & ~ph_open) | ((ph_open ^ ndimage.binary_erosion(ph_open)) & inside)
    mm = 1 / k
    to_cad = ndimage.distance_transform_edt(~cad_edge) * mm
    to_photo = ndimage.distance_transform_edt(~ph_edge) * mm
    scores = {"silhouette_iou": round(float(sil), 3), "window_iou": round(float(iou), 3),
              "edge_mm": round(float(.5 * (to_cad[ph_edge].mean() + to_photo[cad_edge].mean())), 2),
              "tilt_deg": round(math.degrees(tilt), 1), "phase_deg": round(math.degrees(psi), 2), "mirror": int(mirror)}
    overlay = np.stack([grey] * 3, -1) * .45 + .45
    overlay[ph_fg & ~hit] = [.95, .55, .1]
    overlay[hit & ~ph_fg] = [.2, .5, .95]
    overlay[ph_open & ~cad_open] = [.95, .55, .1]
    overlay[cad_open & ~ph_open] = [.2, .5, .95]
    overlay[cad_edge] = [.1, .9, .3]
    return scores, (np.clip(overlay, 0, 1) * 255).astype(np.uint8)
