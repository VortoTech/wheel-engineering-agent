"""Fit the two window shapes (in-pair slot, between-group window) from a target material mask.
Samples the mask on the assumed v9 front surface through the saved camera, folds the FIT groups
(even groups only; odd groups stay held out), mirror-symmetrises, and reads each window as a
per-radius half-width. Output: windows.json (closed XY polylines in mm, group 0 frame)."""
import json, math, sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates, gaussian_filter1d
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'services'))
from wheelcam.models import WheelSpec
from wheelcam.photo_pose import project, front_z
S = Path.cwd()   # the work directory holding context.json, masks and outputs
ctx = json.load(open(S/'context.json')); pose = ctx['pose']; spec = WheelSpec(**ctx['spec'])
target = np.asarray(Image.open(sys.argv[1]).convert('L')) > 127
FIT = [0, 2, 4, 6]; PERIOD = 360/spec.spoke_count
rs = np.arange(70, 252, 1.0)                     # mm, hub edge → ring
phis = np.arange(-PERIOD/2, PERIOD/2+1e-9, 0.2)  # deg around a group centreline
def sample(group):
    base = spec.spoke_phase_deg + group*PERIOD
    R, P = np.meshgrid(rs, phis, indexing='ij'); a = np.radians(base + P)
    xyz = np.stack([R*np.cos(a), R*np.sin(a), front_z(spec, R)], -1)
    uv = project(xyz.reshape(-1, 3), pose).reshape(R.shape + (2,))
    return map_coordinates(target.astype(float), [uv[..., 1], uv[..., 0]], order=1)
folded = np.mean([sample(k) for k in FIT], 0)
folded = (folded + folded[:, ::-1]) / 2          # the design is mirror-symmetric about the group axis
material = folded > 0.5
# The turned hub disc is material by construction; the arm-only mask does not cover it.
material[rs < spec.hub_diameter_mm/2 + 2, :] = True
# Window half-widths per radius: slot = gap around phi=0, big = gap at both period edges.
def half_width(center_mask_row_order):
    out = []
    for row in center_mask_row_order:
        n = 0
        while n < len(row) and not row[n]: n += 1
        out.append(n)
    return np.array(out, float)
mid = len(phis)//2
slot_n = half_width(material[:, mid:])           # open cells from phi=0 outward
big_n = half_width(material[:, ::-1][:, :mid+1])  # open cells from +PERIOD/2 inward (symmetric)
step = phis[1]-phis[0]
def window(n, centre_deg):
    hw = np.radians(np.clip(n*step - step/2, 0, None))
    hw = gaussian_filter1d(hw, 2.0)
    open_rows = np.nonzero(hw > np.radians(0.6))[0]
    if len(open_rows) < 5: return None
    # Keep the longest contiguous open run (the window body).
    runs = np.split(open_rows, np.nonzero(np.diff(open_rows) > 1)[0]+1); run = max(runs, key=len)
    r, h = rs[run], hw[run]
    c = math.radians(centre_deg)
    left = [(ri*math.cos(c+hi), ri*math.sin(c+hi)) for ri, hi in zip(r, h)]
    right = [(ri*math.cos(c-hi), ri*math.sin(c-hi)) for ri, hi in zip(r[::-1], h[::-1])]
    lateral = r*np.sin(h)
    return {'polygon': left + right, 'r_min': float(r[0]), 'r_max': float(r[-1]),
            'max_lateral_half_mm': float(lateral.max()), 'half_width_deg': np.degrees(h).round(3).tolist(),
            'radii': r.tolist()}
slot, big = window(slot_n, 0.0), window(big_n, PERIOD/2)
json.dump({'source_mask': sys.argv[1], 'fit_groups': FIT, 'slot': slot, 'big': big,
           'phase_deg': spec.spoke_phase_deg}, open(S/'windows.json', 'w'))
Image.fromarray((material*255).astype('uint8')).resize((len(phis)*3, len(rs)*2)).save(S/'folded.png')
for k, wdw in (('slot', slot), ('big', big)):
    print(k, None if wdw is None else {x: round(wdw[x], 1) for x in ('r_min', 'r_max', 'max_lateral_half_mm')})
