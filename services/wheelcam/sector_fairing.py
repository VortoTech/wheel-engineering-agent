"""Seam-aware local relief and boundary-constrained thin-plate fairing.

Image-plane regularization is a styling prior, not recovered surface depth.
"""
import numpy as np
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import spsolve


def attachment_edges(boundary, seams):
    """Identify the shorter boundary arc between each annotated cut's endpoints.

    The cut need not be a straight chord (the existing root has a rounded end).
    Reject detached/stale annotations instead of silently removing a free edge.
    """
    p = np.asarray(boundary)
    lengths = np.linalg.norm(np.roll(p, -1, axis=0)-p, axis=1)
    mask = np.zeros(len(p), dtype=bool)
    for seam in seams:
        ends = np.asarray(seam, float)
        if ends.shape != (2, 2) or np.linalg.norm(ends[1]-ends[0]) < 1e-6:
            raise ValueError("连接切口必须标注两个不同的端点。")
        distances = np.linalg.norm(p[:, None]-ends, axis=2)
        start, stop = distances.argmin(axis=0)
        tolerance = max(2., np.linalg.norm(ends[1]-ends[0])*.3)
        if np.any(distances.min(axis=0) > tolerance) or start == stop:
            raise ValueError("连接切口端点已偏离边界；请恢复边界或切换为旧版封口。")
        forward = np.arange(start, stop if stop > start else stop+len(p)) % len(p)
        reverse = np.setdiff1d(np.arange(len(p)), forward)
        arc = forward if lengths[forward].sum() <= lengths[reverse].sum() else reverse
        if lengths[arc].sum() > 3*np.linalg.norm(ends[1]-ends[0]):
            raise ValueError("连接切口跨越过长边界，请复核标注。")
        mask[arc] = True
    if mask.all():
        raise ValueError("至少需要一段真实外缘。")
    return mask


def thin_plate_operator(points, faces):
    """Linear FEM stiffness and lumped mass in source-pixel coordinates."""
    p, f = np.asarray(points), np.asarray(faces)
    a, b = p[f[:, 1]]-p[f[:, 0]], p[f[:, 2]]-p[f[:, 0]]
    twice_area = abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0])
    if np.any(twice_area < 1e-12):
        raise ValueError("平顺网格存在退化三角形。")
    mass = np.zeros(len(p))
    rows, columns, values = [], [], []
    for i in range(3):
        np.add.at(mass, f[:, i], twice_area/6)
        j, k = (i+1) % 3, (i+2) % 3
        u, v = p[f[:, j]]-p[f[:, i]], p[f[:, k]]-p[f[:, i]]
        weight = (u*v).sum(axis=1)/(2*twice_area)
        # Opposite edge receives half the cotangent at this corner.
        for x, y, sign in ((j, j, 1), (k, k, 1), (j, k, -1), (k, j, -1)):
            rows.extend(f[:, x]); columns.extend(f[:, y]); values.extend(sign*weight)
    stiffness = coo_matrix((values, (rows, columns)), shape=(len(p), len(p))).tocsr()
    return stiffness, mass


def fair_relief(points, faces, target, boundary_count, width_px):
    """Minimize mass-weighted displacement + width^4 * thin-plate energy.

    Every boundary height is held EXACTLY, including attachment cuts. No
    silhouette observations, normals from metal highlights, or validation points.
    """
    target = np.asarray(target)
    stiffness, mass = thin_plate_operator(points, faces)
    penalty = stiffness.T @ diags(1/mass) @ stiffness
    fixed, free = np.arange(boundary_count), np.arange(boundary_count, len(points))
    result = target.copy()
    if width_px > 0 and len(free):
        system = diags(mass)+width_px**4*penalty
        rhs = mass*target
        result[free] = spsolve(system[free][:, free], rhs[free]-system[free][:, fixed] @ target[fixed])
    if not np.isfinite(result).all():
        raise ValueError("曲面平顺求解未得到有限结果。")
    before, after = float(target @ penalty @ target), float(result @ penalty @ result)
    deviation = result-target
    return result, {"method": "boundary-constrained-thin-plate", "width_px": width_px,
                    "energy_before": before, "energy_after": after,
                    "rms_displacement_R": float(np.sqrt(np.dot(mass, deviation**2)/mass.sum())),
                    "max_displacement_R": float(abs(deviation).max()),
                    "boundary_max_displacement_R": float(abs(deviation[fixed]).max()),
                    "scope": "relief smoothness only; not reconstruction accuracy or G2 proof"}
