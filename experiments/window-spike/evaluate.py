"""Material IoU inside the spoke/window annulus, per alternating spoke groups.
Usage: evaluate.py target.png candidate.png [...]"""
import json, math, sys
from pathlib import Path
import numpy as np
from PIL import Image
S = Path.cwd()   # the work directory holding context.json, masks and outputs
ctx = json.load(open(S/'context.json'))
e = ctx['ellipse']; w, h = ctx['image_size']
PHASE = 39.5; GROUPS = 8; ZONE = (0.30, 0.76)
yy, xx = np.mgrid[0:h, 0:w]
u, v = (xx-e['cx'])/e['rx'], (yy-e['cy'])/e['ry']
rho = np.hypot(u, v); ang = np.degrees(np.arctan2(v, u))
zone = (rho >= ZONE[0]) & (rho <= ZONE[1])
sector = np.floor(((ang-PHASE+180/GROUPS) % 360)/(360/GROUPS)).astype(int)
def load(p): return np.asarray(Image.open(p).convert('L')) > 127
def iou(a, b, m): return float((a & b & m).sum() / max(1, ((a | b) & m).sum()))
def scores(target, cand):
    even, odd = zone & (sector % 2 == 0), zone & (sector % 2 == 1)
    tp = (target & cand & zone).sum()
    return {'iou': round(iou(target, cand, zone), 4), 'iou_even_groups': round(iou(target, cand, even), 4),
            'iou_odd_groups': round(iou(target, cand, odd), 4),
            'material_precision': round(float(tp / max(1, (cand & zone).sum())), 4),
            'material_recall': round(float(tp / max(1, (target & zone).sum())), 4),
            'per_group': [round(iou(target, cand, zone & (sector == k)), 3) for k in range(GROUPS)]}
if __name__ == '__main__':
    target = load(sys.argv[1])
    for p in sys.argv[2:]:
        print(Path(p).name, json.dumps(scores(target, load(p))))
