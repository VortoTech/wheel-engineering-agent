"""Manual photo evidence -> conditional camera -> constrained local Y surface.

No changes to the production database. Fitting the construction observations is
not counted as independent validation. Rim points are split before fitting.
"""
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import least_squares, brentq

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/route-study/sector'
ANNOTATION=Path(__file__).with_name('annotations.json')


def camera_fit(data):
    points=np.asarray(data['rim_points'],float)
    train,held=points[::2],points[1::2]
    def residual(p,pts):
        cx,cy,a,b,phi=p
        u=pts[:,0]-cx; v=cy-pts[:,1]
        x=np.cos(phi)*u+np.sin(phi)*v
        y=-np.sin(phi)*u+np.cos(phi)*v
        return (np.sqrt((x/b)**2+(y/a)**2)-1)*b
    fit=least_squares(lambda p:residual(p,train),[353,264,255,231,.08],
                      bounds=([320,245,240,200,-.5],[375,285,275,245,.5]),loss='soft_l1')
    cx,cy,a,b,phi=fit.x
    theta=np.arccos(b/a)
    # Camera basis: world x/y are wheel plane; z is front normal.
    r0=np.array([np.cos(theta)*np.cos(phi),-np.sin(phi),np.sin(theta)*np.cos(phi)])
    r1=np.array([np.cos(theta)*np.sin(phi),np.cos(phi),np.sin(theta)*np.sin(phi)])
    r2=np.cross(r0,r1)
    R=np.stack([r0,r1,r2])
    hub=np.array([data['hub_center'][0]-cx,cy-data['hub_center'][1]])/a
    z_hub=float(np.dot(hub,R[:2,2])/np.dot(R[:2,2],R[:2,2]))
    return dict(cx=float(cx),cy=float(cy),scale=float(a),rotation=R.tolist(),
                sag=float(-z_hub),rim_minor_px=float(b),
                ring_train_median_px=float(np.median(abs(residual(fit.x,train)))),
                ring_held_out_median_px=float(np.median(abs(residual(fit.x,held)))),
                note='Weak perspective conditional on a circular planar rim. Not unique physical camera calibration.')


def depth(r,pose):
    t=np.clip((np.asarray(r)-.16)/.72,0,1)
    return -pose['sag']*(1-(3*t*t-2*t*t*t))


def project(points,pose):
    q=np.asarray(points)@np.asarray(pose['rotation']).T
    return np.column_stack([pose['cx']+pose['scale']*q[:,0],pose['cy']-pose['scale']*q[:,1]])


def lift(pixels,pose):
    R=np.asarray(pose['rotation']); A=R[:2,:2]; d=R[:2,2]
    output=[]
    for u,v in np.asarray(pixels):
        q=np.array([(u-pose['cx'])/pose['scale'],(pose['cy']-v)/pose['scale']])
        def point(z): return np.linalg.solve(A,q-d*z)
        def f(z): return z-float(depth(np.linalg.norm(point(z)),pose))
        z=brentq(f,-pose['sag']-1e-8,1e-8)
        output.append([*point(z),z])
    return np.asarray(output)


def smooth_boundary(points,spacing=1.5):
    p=np.asarray([*points,points[0]],float)
    s=np.r_[0,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))]
    # Periodic interpolating curve; explicitly retains the manual input points.
    fn=CubicSpline(s,p,bc_type='periodic')
    t=np.unique(np.r_[np.arange(0,s[-1],spacing),s[:-1]])
    return fn(t)


def path(points,close=True):
    return 'M '+' L '.join(f'{x:.2f},{y:.2f}' for x,y in points)+(' Z' if close else '')


def write_svg(data,boundary,predicted,pose):
    actual=np.asarray(data['validation']['points'])
    header='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 620 591"><image href="source.jpg" width="620" height="591"/>'
    svg=header+f'<path d="{path(boundary)}" fill="#16c9db" fill-opacity=".12" stroke="#00eaff" stroke-width="1.2"/>'
    for x,y in data['master']['boundary']:
        svg+=f'<circle cx="{x}" cy="{y}" r="1.6" fill="#00eaff"/>'
    for poly in data['uncertain_pockets']:
        svg+=f'<path d="{path(poly)}" fill="none" stroke="#ffbf52" stroke-width="1.2" stroke-dasharray="3 2"/>'
    for seat in data['bolt_seats']:
        x,y=seat['center'];svg+=f'<ellipse cx="{x}" cy="{y}" rx="{seat["rx"]}" ry="{seat["ry"]}" fill="none" stroke="#ffbf52" stroke-width="1" stroke-dasharray="3 2"/>'
    (OUT/'annotation.svg').write_text(svg+'</svg>')
    validation=header+f'<path d="{path(predicted)}" fill="none" stroke="#ff5aaa" stroke-width="1.2"/>'
    for x,y in actual:validation+=f'<circle cx="{x}" cy="{y}" r="2" fill="#00eaff"/>'
    (OUT/'validation.svg').write_text(validation+'</svg>')
    structure=header
    for i,(x,y) in enumerate(data['structure']['root_centers'],1):
        structure+=f'<circle cx="{x}" cy="{y}" r="6" fill="#00eaff"/><text x="{x+7}" y="{y+4}" font-size="12" fill="#00eaff" stroke="#111" stroke-width=".3">Y{i}</text>'
    for x,y in data['structure']['lug_centers']:
        structure+=f'<circle cx="{x}" cy="{y}" r="12" fill="none" stroke="#ffbf52" stroke-width="1.5"/>'
    (OUT/'structure.svg').write_text(structure+'</svg>')


def main():
    data=json.loads(ANNOTATION.read_text()); OUT.mkdir(parents=True,exist_ok=True)
    source=ROOT/data['source_image']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==data['source_sha256']
    pose=camera_fit(data)
    boundary=smooth_boundary(data['master']['boundary'])
    xyz=lift(boundary,pose)
    rotation=np.deg2rad(data['validation']['group_rotation_deg'])
    rot=np.array([[np.cos(rotation),-np.sin(rotation),0],[np.sin(rotation),np.cos(rotation),0],[0,0,1]])
    predicted=project(xyz@rot.T,pose)
    actual=np.asarray(data['validation']['points'])
    error=np.min(np.linalg.norm(actual[:,None]-predicted[None],axis=2),axis=1)
    report={'camera':pose,'master_construction_roundtrip_max_px':float(np.max(np.linalg.norm(project(xyz,pose)-boundary,axis=1))),
        'construction_roundtrip_is_accuracy':False,'validation':{'median_px':float(np.median(error)),
        'max_px':float(error.max()),'per_point_px':error.tolist(),'count':len(error),
        'used_for_optimization':False,'used_for_topology_diagnosis':True,
        'is_untouched_holdout':False,'gate':'median <= 5px AND max <= 12px; heuristic, not engineering tolerance',
        'passed':bool(np.median(error)<=5 and error.max()<=12)},
        'limitations':data['uncertainties'],'engineering_approved':False}
    report['topology_comparison']=[]
    for groups in [5,6]:
        a=2*np.pi/groups;rr=np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1]])
        pp=project(xyz@rr.T,pose);ee=np.min(np.linalg.norm(actual[:,None]-pp[None],axis=2),axis=1)
        report['topology_comparison'].append({'groups':groups,'median_px':float(np.median(ee)),
                                              'max_px':float(ee.max()),'diagnostic_only':True})
    payload={'annotation':data,'pose':pose,'boundary':boundary.tolist(),'boundary_xyz':xyz.tolist(),
             'rim_arc':smooth_boundary(data['rim_arc']).tolist()}
    (OUT/'geometry.json').write_text(json.dumps(payload,indent=2))
    (OUT/'report.json').write_text(json.dumps(report,indent=2))
    shutil.copyfile(source,OUT/'source.jpg')
    shutil.copyfile(ANNOTATION,OUT/'annotations.json')
    write_svg(data,boundary,predicted,pose)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
