"""Constrained camera candidates; depth stays at the current CAD assumption.

A single image does not calibrate a unique camera. Alternate spoke groups are
held out, and a pose is adopted only when those groups improve too.
"""
import math
import json
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
from scipy.optimize import least_squares

from .template import layout


def rotation(ax, ay, az):
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return np.array([[cz*cy, cz*sy*sx-sz*cx, cz*sy*cx+sz*sx],
                     [sz*cy, sz*sy*sx+cz*cx, sz*sy*cx-cz*sx], [-sy, cy*sx, cy*cx]])


def project(points, pose):
    p = np.asarray(points, dtype=float)
    q = (p-np.array([0, 0, pose['reference_z_mm']])) / pose['radius_mm']
    q = q @ np.asarray(pose['rotation']).T
    denominator = 1-q[..., 2]/pose['distance_radii']
    return np.stack([pose['cx']+pose['scale_px']*q[..., 0]/denominator,
                     pose['cy']-pose['scale_px']*q[..., 1]/denominator], axis=-1)


@lru_cache(maxsize=128)
def _front_bounds(spec_json):
    lay = layout(SimpleNamespace(**json.loads(spec_json)))
    first,last = lay['sections'][0],lay['sections'][-1]
    return first['r'],last['r'],first['front'],last['front']


def front_z(spec, radial):
    r0,r1,z0,z1 = _front_bounds(spec.model_dump_json())
    t = np.clip((np.asarray(radial)-r0)/(r1-r0), 0, 1)
    return z0+(z1-z0)*((1-spec.face_curve)*t+spec.face_curve*t*t)


def unproject_front(points, pose, spec, theta=None):
    """Intersect pixel rays with the assumed spoke front, without changing depth."""
    pixels = np.asarray(points, dtype=float)
    d, scale = pose['distance_radii'], pose['scale_px']
    # q = origin + t*direction, where t=1 on the unrotated reference plane.
    inv = np.asarray(pose['rotation'])
    origin = np.array([0, 0, d]) @ inv
    direction = np.column_stack([(pixels[:, 0]-pose['cx'])/scale,
                                  -(pixels[:, 1]-pose['cy'])/scale, np.full(len(pixels), -d)]) @ inv
    z = np.zeros(len(pixels))
    for _ in range(12):
        t = (z-origin[2])/direction[:, 2]
        p = origin + t[:, None]*direction
        radial = (np.hypot(p[:, 0], p[:, 1]) if theta is None else p[:,0]*math.cos(theta)-p[:,1]*math.sin(theta))*pose['radius_mm']
        z = (front_z(spec, radial)-pose['reference_z_mm'])/pose['radius_mm']
    result = p*pose['radius_mm']+np.array([0,0,pose['reference_z_mm']])
    if not np.all(np.isfinite(result)):
        raise ValueError('点位不能投影到当前轮辐正面。')
    return result


def fit_pose(analysis, spec):
    e = analysis['ellipse']; radius = (spec.rim_diameter_in*25.4+35)/2
    scale = (e['rx']+e['ry'])/2
    base = {'cx':e['cx'], 'cy':e['cy'], 'scale_px':scale, 'distance_radii':1e6,
            'rotation':np.eye(3).tolist(), 'radius_mm':radius,
            'reference_z_mm':spec.rim_width_in*25.4/2, 'angles_deg':[0,0,0]}
    count = analysis['spokes']['groups']; observations = []
    for group in range(count):
        negative, positive = analysis['traces'][group*2:group*2+2]
        theta = math.radians(analysis['spokes']['image_phase_deg']+group*360/count)
        for r in (.5,.62,.82):
            pts = [np.mean(a['points']+b['points'], axis=0) for a,b in zip(negative['samples'],positive['samples'])
                   if a['accepted'] and b['accepted'] and abs(a['radius_ratio']-r) < .016]
            if len(pts) >= 3:
                observations.append((group, r, theta, np.median(pts, axis=0)))
    train = [o for o in observations if o[0]%2 == 0]
    held = [o for o in observations if o[0]%2 == 1]
    result = {'status':'insufficient', 'pose':base, 'train_groups':sorted({o[0] for o in train}),
              'held_out_groups':sorted({o[0] for o in held}),
              'note':'相机候选基于当前凹深假设；不能由单张照片唯一恢复焦距、距离或真实深度'}
    if len({o[0] for o in train}) < 3 or len({o[0] for o in held}) < 2:
        return result
    outer = np.array(analysis['outer_points'])
    def make_pose(v, distance):
        return {**base, 'rotation':rotation(*v[:3]).tolist(), 'angles_deg':np.rad2deg(v[:3]).tolist(),
                'cx':float(v[3]),'cy':float(v[4]),'scale_px':float(v[5]),'distance_radii':distance}
    def center_errors(pose, obs):
        errors = []
        for _,r,t,point in obs:
            rs = np.array([r-.003,r,r+.003])*radius
            xyz = np.column_stack([rs*math.cos(t),-rs*math.sin(t),front_z(spec,rs)])
            screen = project(xyz,pose)
            tangent = screen[2]-screen[0]; tangent /= np.linalg.norm(tangent)
            errors.append(np.dot(point-screen[1],[-tangent[1],tangent[0]]))
        return np.array(errors)
    def ring_errors(pose):
        r = np.array(pose['rotation']); d = pose['distance_radii']; s = pose['scale_px']
        den = np.array([-r[2,0]/d,-r[2,1]/d,1])
        h = np.stack([s*np.array([r[0,0],r[0,1],0])+pose['cx']*den,
                       -s*np.array([r[1,0],r[1,1],0])+pose['cy']*den, den])
        xy = np.column_stack([outer,np.ones(len(outer))]) @ np.linalg.inv(h).T
        return (np.hypot(xy[:,0],xy[:,1])/np.abs(xy[:,2])-1)*scale
    before_train = float(np.median(np.abs(center_errors(base,train))))
    before_held = float(np.median(np.abs(center_errors(base,held))))
    choices = []
    initial = [0,0,0,e['cx'],e['cy'],scale]
    low = [-.45,-.45,-.10,e['cx']-12,e['cy']-12,scale*.9]
    high = [.45,.45,.10,e['cx']+12,e['cy']+12,scale*1.1]
    for distance in (6.,12.,1e6):
        def residual(v):
            pose = make_pose(v,distance)
            return np.r_[center_errors(pose,train),ring_errors(pose)*.35,np.array(v[:3])*.4]
        for tilt in (.1,-.1):
            fitted = least_squares(residual,[tilt,tilt,*initial[2:]],bounds=(low,high),loss='soft_l1',f_scale=1.5,max_nfev=100)
            pose = make_pose(fitted.x,distance)
            training = float(np.median(np.abs(center_errors(pose,train))))
            validation = float(np.median(np.abs(center_errors(pose,held))))
            ring = float(np.median(np.abs(ring_errors(pose))))
            if ring < 2:
                choices.append((training,validation,ring,pose))
    # Select using training loss only; held-out groups gate acceptance, not tuning.
    chosen = min(choices,key=lambda c:c[0]) if choices else None
    if chosen and not (chosen[0] < before_train*.95 and chosen[1] < before_held*.95):
        chosen = None
    result.update(status='fitted' if chosen else 'no_improvement', before_train_px=before_train,
                  before_held_out_px=before_held, after_train_px=chosen[0] if chosen else before_train,
                  after_held_out_px=chosen[1] if chosen else before_held,
                  ring_median_px=chosen[2] if chosen else float(np.median(np.abs(ring_errors(base)))))
    if chosen:
        result['pose'] = chosen[3]
    return result
