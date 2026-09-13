"""Reviewable root-cap candidates and image-point corrections."""
import math

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

from .models import WheelSpec
from .photo_pose import front_z, project, unproject_front
from .template import layout


def radial_world(r, u, theta, spec):
    r,u = np.broadcast_arrays(r,u)
    return np.column_stack([r*math.cos(theta)-u*math.sin(theta),
                           -r*math.sin(theta)-u*math.cos(theta), front_z(spec,r)])


def landmarks(spec, analysis, pose, group):
    slot = layout(spec)['paired_slot']; a,c,w = slot['start_r_mm'],slot['corner_radius_mm'],slot['radius_mm']
    theta = math.radians(analysis['spokes']['image_phase_deg']+group*360/spec.spoke_count)
    return project(radial_world([a,a+c,a+c],[0,-w,w],theta,spec),pose).tolist()


def detect_root(photo, analysis, spec, pose):
    with Image.open(photo) as original:
        im = original.convert('L'); im.thumbnail((1440,1440))
    gray = gaussian_filter(np.asarray(im,dtype=float)/255,.65)
    sx,sy = im.width/analysis['image_size'][0],im.height/analysis['image_size'][1]
    def evidence(candidate):
        slot = layout(candidate)['paired_slot']; a,c,w = slot['start_r_mm'],slot['corner_radius_mm'],slot['radius_mm']
        ts = np.linspace(.08,math.pi/2,9)
        r = np.r_[np.full(7,a),a+c-c*np.cos(ts),a+c-c*np.cos(ts)]
        u = np.r_[np.linspace(-w+c,w-c,7),w-c+c*np.sin(ts),-w+c-c*np.sin(ts)]
        nr = np.r_[np.ones(7),np.cos(ts),np.cos(ts)]
        nu = np.r_[np.zeros(7),-np.sin(ts),np.sin(ts)]
        scores = []
        for group in range(spec.spoke_count):
            theta = math.radians(analysis['spokes']['image_phase_deg']+group*360/spec.spoke_count)
            sides = []
            for sign in (-1,1):
                pts = project(radial_world(r+sign*nr*1.8,u+sign*nu*1.8,theta,candidate),pose)
                sides.append(map_coordinates(gray,[pts[:,1]*sy,pts[:,0]*sx],order=1,mode='nearest'))
            delta = np.clip(sides[1]-sides[0],-.2,.3)
            scores.append(float(np.mean(delta)))
        return float(np.median(scores)),scores
    baseline,_ = evidence(spec)
    choices = []
    for start in np.arange(0,21,2):
        for corner in np.arange(2,23,2):
            proposed = {'paired_split_start_mm':float(start),'paired_root_round_mm':float(corner)}
            try:
                candidate = WheelSpec.model_validate({**spec.model_dump(),**proposed})
            except ValueError:
                continue
            score,groups = evidence(candidate)
            support = sum(v>.025 for v in groups)
            if support >= max(3,math.ceil(spec.spoke_count/2)):
                choices.append((score,proposed,support,groups))
    best = max(choices,key=lambda x:x[0]) if choices else None
    accepted = best is not None and best[0] > max(.025,baseline+.01)
    parameters = best[1] if accepted else {}
    candidate = WheelSpec.model_validate({**spec.model_dump(),**parameters})
    group = round((270-analysis['spokes']['image_phase_deg'])/(360/spec.spoke_count))%spec.spoke_count
    return {'status':'candidate' if accepted else 'needs_review','parameters':parameters,
            'before_contrast':baseline,'after_contrast':best[0] if accepted else baseline,
            'support_groups':best[2] if accepted else 0,'group':group,
            'points':landmarks(candidate,analysis,pose,group),
            'all_points':[landmarks(candidate,analysis,pose,g) for g in range(spec.spoke_count)],
            'note':'底部及左右转接点可拖动修正；按整轮对称约束生成。圆角和尺寸仍为照片拟合。'}


def correct_root(analysis, spec, group, points):
    if group >= spec.spoke_count:
        raise ValueError('分叉组号超出当前轮毂范围。')
    w,h = analysis['image_size']
    if any(not (0<=x<=w and 0<=y<=h) for x,y in points):
        raise ValueError('修正点必须位于参考图片内。')
    pose = analysis['camera_fit']['pose']
    theta = math.radians(analysis['spokes']['image_phase_deg']+group*360/spec.spoke_count)
    xyz = unproject_front(points,pose,spec,theta)
    radial = xyz[:,0]*math.cos(theta)-xyz[:,1]*math.sin(theta)
    tangent = -xyz[:,0]*math.sin(theta)-xyz[:,1]*math.cos(theta)
    if abs(tangent[0]) > 5 or abs(tangent[1]+tangent[2])/2 > 5:
        raise ValueError('点位偏离本组中心线过多，请选对分叉组并沿对称轮廓修正。')
    if tangent[1] >= 0 or tangent[2] <= 0:
        raise ValueError('左右转接点不可交叉，请分别放在分叉两侧。')
    a = radial[0]; c = np.mean(radial[1:])-a; gap = tangent[2]-tangent[1]
    values = {'paired_split_start_mm':round(float(a-spec.hub_diameter_mm/2),2),
              'paired_root_round_mm':round(float(c),2),
              'paired_gap_flare_mm':round(float(gap-spec.paired_gap_mm),2)}
    # No silent clamps: incompatible image geometry must be reviewed explicitly.
    fitted = WheelSpec.model_validate({**spec.model_dump(),**values})
    return values,landmarks(fitted,analysis,pose,group)
