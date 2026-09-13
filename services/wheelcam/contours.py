"""Continuous dark-blade traces and conservative section fitting.

Image evidence is kept per blade. A radial consensus is used only for fitting;
unsupported/occluded samples are never drawn as if they were detected edges.
"""
import math

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

from .models import WheelSpec
from .template import layout, slot_half_width


def trace_blades(photo, ellipse, image_size, candidate):
    with Image.open(photo) as original:
        im = original.convert('L')
        im.thumbnail((1440, 1440))
    gray = gaussian_filter(np.asarray(im, dtype=float) / 255, .7)
    sx, sy = im.width / image_size[0], im.height / image_size[1]
    cx, cy, rx, ry = [ellipse[k] for k in ('cx', 'cy', 'rx', 'ry')]
    rs = np.linspace(.86, .36, 101)
    widths = np.arange(.012, .102, .003)
    traces = []
    for group in range(candidate['groups']):
        theta = math.radians(candidate['image_phase_deg'] + group * 360 / candidate['groups'])
        for sign in (-1, 1):
            centers = np.arange(sign * .08 - .08, sign * .08 + .081, .002)
            c, width = np.meshgrid(centers, widths, indexing='ij')
            def sample(u):
                return map_coordinates(gray, [sy*(cy + ry*(rs[:, None, None]*math.sin(theta) + u[None]*math.cos(theta))),
                    sx*(cx + rx*(rs[:, None, None]*math.cos(theta) - u[None]*math.sin(theta)))], order=1, mode='nearest')
            inside = (sample(c-width*.25) + sample(c) + sample(c+width*.25)) / 6 + .5*np.maximum(sample(c-width*.4), sample(c+width*.4))
            left, right = sample(c-width*.5-.005), sample(c+width*.5+.005)
            contrast = np.minimum(left, right) - inside
            score = np.clip(contrast, -.2, .4) + .35*np.clip((left+right)/2-inside, -.2, .4) - .2*inside
            # Do not jump to a neighbouring group or a broad patch beyond the tip.
            limit = rs[:, None, None] * math.tan(math.pi/candidate['groups']) * .96
            legal = (np.abs(c)[None]+width[None]/2 < limit) & (sign*c[None] > width[None]/2)
            legal &= (rs[:, None, None] < .78) | (width[None] <= .03)
            score = np.where(legal, score, -1e6)
            dp = score[0] - 2*np.abs(c-sign*candidate['offset_ratio'])
            history, shifts = [], [(dc, dw) for dc in range(-2, 3) for dw in range(-2, 3)]
            for row in range(1, len(rs)):
                best, choice = np.full_like(dp, -1e9), np.zeros(dp.shape, dtype=np.uint8)
                for index, (dc, dw) in enumerate(shifts):
                    dst = (slice(max(0, dc), min(len(centers), len(centers)+dc)), slice(max(0, dw), min(len(widths), len(widths)+dw)))
                    src = (slice(max(0, -dc), min(len(centers), len(centers)-dc)), slice(max(0, -dw), min(len(widths), len(widths)-dw)))
                    value = dp[src] - .015*dc**2 - .008*dw**2
                    better = value > best[dst]
                    best[dst] = np.maximum(best[dst], value)
                    choice[dst] = np.where(better, index, choice[dst])
                dp = best + score[row]
                history.append(choice)
            k, j = np.unravel_index(dp.argmax(), dp.shape)
            samples = []
            for row in range(len(rs)-1, -1, -1):
                lo, hi = float(centers[k]-widths[j]/2), float(centers[k]+widths[j]/2)
                quality = float(contrast[row, k, j])
                samples.append({'radius_ratio': float(rs[row]), 'edges_ratio': [lo, hi],
                    'contrast': round(quality, 4), 'accepted': bool(quality > .055 and score[row, k, j] > .035),
                    'points': [[cx+rx*(rs[row]*math.cos(theta)-u*math.sin(theta)),
                                cy+ry*(rs[row]*math.sin(theta)+u*math.cos(theta))] for u in (lo, hi)]})
                if row:
                    dc, dw = shifts[history[row-1][k, j]]
                    k, j = k-dc, j-dw
            traces.append({'group': group, 'side': sign, 'samples': samples[::-1]})
    return traces


def fit_sections(traces, spec, count):
    """Fit visible section widths, with equal votes per group and CAD validation.

    Residuals are section-width residuals in the rectified image, not full silhouette
    accuracy, camera calibration, or manufacturing tolerances.
    """
    outer_r = (spec.rim_diameter_in*25.4+35)/2
    if spec.paired_window_root_mm:
        return {"status":"manual_window_active", "stations":[], "parameters":{},
                "note":"大窗口曲线已启用，保留当前截面控制；原五截面宽度求解不适用于此骨架。"}
    sections = layout(spec)['sections']
    requested = [sections[1]['r']/outer_r, sections[2]['r']/outer_r, .83]
    stations = []
    for radius in requested:
        observations = []
        for group in range(count):
            negative, positive = traces[group*2:group*2+2]
            rows = []
            for a, b in zip(negative['samples'], positive['samples']):
                if abs(a['radius_ratio']-radius) > .04 or not a['accepted'] or not b['accepted']:
                    continue
                lo, li = a['edges_ratio']; ri, ro = b['edges_ratio']
                gap, width = ri-li, ro-lo
                if 0 < gap < width:
                    rows.append([gap, width])
            if len(rows) >= 3:
                observations.append(np.median(rows, axis=0))
        if len(observations) < max(3, math.ceil(count*.5)):
            continue
        values = np.array(observations)
        med = np.median(values, axis=0)
        mad = np.median(np.abs(values-med), axis=0)
        # The median tolerates isolated background tracks; reject dispersed evidence
        # rather than pruning down to a convenient minority of spokes.
        if float(np.max(mad)) > .025:
            continue
        stations.append({'radius_ratio': float(radius), 'gap_ratio': float(med[0]),
            'width_ratio': float(med[1]), 'support_groups': len(values),
            'spread_ratio': float(np.max(np.median(np.abs(values-med), axis=0)))})
    result = {'status': 'insufficient', 'stations': stations, 'parameters': {},
              'basis': '同一径向截面的净间隙与整组宽度；各组等权，非整圈轮廓精度'}
    if len(stations) != 3:
        return result
    tip = stations[-1]
    gap = tip['gap_ratio']*outer_r
    blade = (tip['width_ratio']-tip['gap_ratio'])*outer_r/2
    minimum_blade = max(4, spec.spoke_thickness_mm*math.tan(math.radians(6))+2.5, 3*spec.spoke_fillet_mm)
    if not (16 <= gap <= 50 and minimum_blade <= blade <= 14):
        result['status'] = 'outside_constraints'
        return result
    # End-width and shoulder/middle controls all enter the actual CAD loft.
    tip_width = gap+2*blade
    root = spec.spoke_width_hub_mm
    proposed = {'paired_gap_mm': round(gap, 2), 'paired_tip_width_mm': round(blade, 2),
                'paired_shoulder_mm': round(stations[0]['width_ratio']*outer_r-(root*.75+tip_width*.25), 2),
                'paired_gap_flare_mm': round(max(0, (stations[0]['gap_ratio']-tip['gap_ratio'])*outer_r), 2),
                'paired_mid_mm': round(stations[1]['width_ratio']*outer_r-(root*.5+tip_width*.5), 2)}
    # Search back toward the existing shape if a detected width would close a window.
    for fraction in (1, .9, .8, .7, .6, .5, .4):
        trial = {k: round(getattr(spec, k)+(v-getattr(spec, k))*fraction, 2) for k, v in proposed.items()}
        try:
            fitted = WheelSpec.model_validate({**spec.model_dump(), **trial})
        except ValueError:
            continue
        def errors(s):
            fs = layout(s)['sections']
            widths = [fs[1]['width'], fs[2]['width'], s.paired_gap_mm+2*s.paired_tip_width_mm]
            return np.array([[2*slot_half_width(layout(s)['paired_slot'], v['radius_ratio']*outer_r)/outer_r-v['gap_ratio'], width/outer_r-v['width_ratio']]
                             for width, v in zip(widths, stations)])
        before, after = errors(spec), errors(fitted)
        if np.mean(after**2) >= np.mean(before**2)*.95:
            result['status'] = 'no_improvement'
            return result
        result.update(status='fitted', parameters=trial, constraint_fraction=fraction,
                      before_rms_ratio=float(np.sqrt(np.mean(before**2))),
                      after_rms_ratio=float(np.sqrt(np.mean(after**2))))
        return result
    result['status'] = 'outside_constraints'
    return result
