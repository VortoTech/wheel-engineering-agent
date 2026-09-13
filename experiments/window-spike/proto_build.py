"""Prototype 'turned body − windows' wheel: v9 rim/lip/hub, a revolved spoke blank with v9's
front/back curves, window cutters from windows.json (optionally drafted), filleted window edges.
Writes proto.step, proto_mask.png (projected with the saved camera), proto_report.json."""
import json, math, sys, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import cadquery as cq
from scipy.interpolate import splprep, splev
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'services'))
from wheelcam.models import WheelSpec
from wheelcam.template import layout, HUB_EDGE_FILLET
from wheelcam.geometry import _wire, _fuse_with_fillet, _cutters, _volume
from wheelcam.photo_pose import project
S = Path.cwd()   # the work directory holding context.json, masks and outputs
ctx = json.load(open(S/'context.json')); spec = WheelSpec(**ctx['spec']); pose = ctx['pose']
win = json.load(open(S/__import__('os').environ.get('WINDOWS', 'windows.json')))
EDGE_FRONT, EDGE_BACK = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0, 1.0
DRAFT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
t0 = time.time(); lay = layout(spec)

rim = cq.Solid.revolve(_wire(*zip(*lay['rim_polygon']), lambda p: (p[0], 0.0, p[1])), [], 360, cq.Vector(0,0,0), cq.Vector(0,0,1))
if lay['front_lip']:
    lip = lay['front_lip']
    rim = rim.fuse(cq.Solid.revolve(_wire(lip['polygon'], lip['radii'], lambda p: (p[0], 0.0, p[1])), [], 360,
                                    cq.Vector(0,0,0), cq.Vector(0,0,1))).clean()
hub = (cq.Workplane('XY', origin=(0, 0, spec.offset_et_mm)).circle(lay['hub_radius'])
       .extrude(spec.hub_thickness_mm).edges('>Z').fillet(HUB_EDGE_FILLET).val())
# Spoke blank: same quadratic front/back as the v9 explicit-profile spoke, revolved (a turning op).
f0, f1 = lay['sections'][0], lay['sections'][-1]; r0, r1 = f0['r'], f1['r']; delta = f1['front']-f0['front']
top = [(r0, f0['front']), ((r0+r1)/2, f0['front']+delta*(1-spec.face_curve)/2), (r1, f1['front'])]
bottom = [(r, z-d) for (r, z), d in zip(top, [f0['depth'], (f0['depth']+f1['depth'])/2, f1['depth']])]
V = lambda p: cq.Vector(p[0], 0, p[1])
profile = cq.Wire.assembleEdges([cq.Edge.makeBezier([V(p) for p in top]), cq.Edge.makeLine(V(top[-1]), V(bottom[-1])),
                                 cq.Edge.makeBezier([V(p) for p in bottom[::-1]]), cq.Edge.makeLine(V(bottom[0]), V(top[0]))])
blank = cq.Solid.revolve(profile, [], 360, cq.Vector(0,0,0), cq.Vector(0,0,1))
z_lo = min(z for _, z in bottom) - 3; z_hi = max(z for _, z in top) + 3

def smooth_closed(poly, n=120):
    p = np.array(poly); p = np.vstack([p, p[:1]])
    keep = np.r_[True, np.hypot(*np.diff(p, axis=0).T) > 1e-6]; p = p[keep]
    tck, _ = splprep([p[:, 0], p[:, 1]], s=len(p)*0.25, per=1)
    x, y = splev(np.linspace(0, 1, n, endpoint=False), tck)
    return list(zip(x, y))
def cutter(poly, angle):
    pts = smooth_closed(poly); c, s = math.cos(angle), math.sin(angle)
    pts = [(x*c-y*s, x*s+y*c) for x, y in pts]
    wire = cq.Wire.makePolygon([cq.Vector(x, y, z_lo) for x, y in pts], close=True) if False else \
        cq.Wire.assembleEdges([cq.Edge.makeSpline([cq.Vector(x, y, z_lo) for x, y in pts], periodic=True)])
    if DRAFT:
        # Wider at the back, the true front outline at the front: offset the base outward by the taper run.
        base = wire.offset2D((z_hi-z_lo)*math.tan(math.radians(DRAFT)), 'arc')[0]
        return cq.Solid.extrudeLinear(base, [], cq.Vector(0, 0, z_hi-z_lo), taper=DRAFT)
    return cq.Solid.extrudeLinear(wire, [], cq.Vector(0, 0, z_hi-z_lo))
cutters = []
for k in range(spec.spoke_count):
    a = math.radians(spec.spoke_phase_deg + k*360/spec.spoke_count)
    for key in ('slot', 'big'):
        if win.get(key): cutters.append(cutter(win[key]['polygon'], a))
# Turn hub + spoke blank as one centre body first (coaxial revolves fuse robustly), then mill the
# windows; clip the cutters so a smoothed slot bottom never nicks the hub side.
hub_keep = cq.Solid.makeCylinder(lay['hub_radius'] + 1.5, 2000, cq.Vector(0, 0, -1000))
cutters = [c.cut(hub_keep) for c in cutters]
spokes = hub.fuse(blank).clean().cut(*cutters).clean()
print('centre body solids', len(spokes.Solids()))
# Round the window edges: every non-axisymmetric edge of the cut blank is a window rim edge.
fr, bk = [], []
for edge in spokes.Edges():
    # Window rims are the spline-wall ∩ face curves; circles and Bezier seams belong to the turned blank.
    if edge.geomType() != 'BSPLINE': continue
    # Hub-side seams (clip arc, hub ∩ blank) are turning edges, not window rims.
    if math.hypot(edge.Center().x, edge.Center().y) < lay['hub_radius'] + 6: continue
    (fr if edge.Center().z > np.interp(math.hypot(edge.Center().x, edge.Center().y), [r0, r1], [top[0][1], top[-1][1]]) - 4 else bk).append(edge)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
def try_fillet(shape, edges):
    maker = BRepFilletAPI_MakeFillet(shape.wrapped)
    try:
        for e, r in edges: maker.Add(r, e.wrapped)   # raises for edges not bounded by exactly 2 faces
    except Exception:
        return None
    try:
        maker.Build()
        if not maker.IsDone():   # Shape() of a failed fillet is null; casting it segfaults in OCC
            return None
        cand = cq.Shape.cast(maker.Shape())
        return cand if cand.isValid() and len(cand.Solids()) == 1 else None
    except Exception:
        return None
edge_applied, rounded = 0.0, 0
for radius in (EDGE_FRONT, EDGE_FRONT*0.5):
    wanted = [(e, radius) for e in fr] + [(e, min(EDGE_BACK, radius)) for e in bk]
    cand = try_fillet(spokes, wanted)
    if cand is None:
        # Drop only the edges that cannot be rounded on their own; report the count, never hide it.
        wanted = [pair for pair in wanted if try_fillet(spokes, [pair]) is not None]
        cand = try_fillet(spokes, wanted) if wanted else None
    if cand is not None:
        spokes, edge_applied, rounded = cand, radius, len(wanted); break
print('window edges front/back', len(fr), len(bk), 'applied', edge_applied, 'rounded', rounded)
split = (lay['hub_radius'] + lay['well_radius'])/2
try:
    body, applied = _fuse_with_fillet([rim, spokes], spec.junction_fillet_mm, split)
except Exception as exc:   # the shared helper does not guard Add(); report the fallback instead of failing
    print('junction fillet raised:', exc)
    body, applied = _fuse_with_fillet([rim, spokes], 0, split)
result = body.cut(*_cutters(spec, lay)).clean()
cq.exporters.export(cq.Workplane(obj=result), str(S/'proto.step'))
back = cq.importers.importStep(str(S/'proto.step')).val()
verts, tris = result.tessellate(0.3, 0.3)
xyz = np.array([[v.x, v.y, v.z] for v in verts]); uv = project(xyz, pose)
w, h = ctx['image_size']; canvas = Image.new('L', (w, h)); d = ImageDraw.Draw(canvas)
for t in tris: d.polygon([tuple(uv[i]) for i in t], fill=255)
canvas.save(S/'proto_mask.png')
report = {'valid': result.isValid(), 'solids': len(result.Solids()), 'faces': len(result.Faces()),
          'volume_mm3': _volume(result), 'step_rel_delta': abs(_volume(back)-_volume(result))/_volume(result),
          'window_edges_front': len(fr), 'window_edge_fillet_requested': EDGE_FRONT, 'window_edge_fillet_applied': edge_applied,
          'draft_deg': DRAFT, 'junction_applied': applied, 'junction_requested': spec.junction_fillet_mm,
          'build_s': round(time.time()-t0, 1)}
json.dump(report, open(S/'proto_report.json', 'w'), indent=1); print(report)
