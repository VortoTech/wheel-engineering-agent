"""Create an evidence-constrained LOCAL Y spoke, not a full wheel."""
import json
import math
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector, Matrix
from mathutils.geometry import tessellate_polygon
from bpy_extras.object_utils import world_to_camera_view

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/route-study/sector'
DATA=json.loads((OUT/'geometry.json').read_text())
POSE=DATA['pose']; R=Matrix(POSE['rotation'])


def lift(u,v,crown=0):
    qx=(u-POSE['cx'])/POSE['scale']; qy=(POSE['cy']-v)/POSE['scale']
    a,b=R[0][0],R[0][1]; c,d=R[1][0],R[1][1]; det=a*d-b*c
    def xy(z):
        q0=qx-R[0][2]*z; q1=qy-R[1][2]*z
        return ((d*q0-b*q1)/det,(-c*q0+a*q1)/det)
    lo,hi=-POSE['sag']-.02,.02
    for _ in range(45):
        z=(lo+hi)/2;x,y=xy(z)
        t=max(0,min(1,(math.hypot(x,y)-.16)/.72))
        expected=-POSE['sag']*(1-(3*t*t-2*t*t*t))+crown
        if z>expected:hi=z
        else:lo=z
    z=(lo+hi)/2
    return (*xy(z),z)


def mat(name,color,metallic=0.0,roughness=.4,micro=False):
    m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
    m.node_tree.nodes.clear();bs=m.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    bs.inputs['Base Color'].default_value=(*color,1)
    bs.inputs['Metallic'].default_value=metallic
    bs.inputs['Roughness'].default_value=roughness
    if micro:
        tex=m.node_tree.nodes.new('ShaderNodeTexNoise');tex.inputs['Scale'].default_value=180
        tex.inputs['Detail'].default_value=2;tex.inputs['Roughness'].default_value=.55
        bump=m.node_tree.nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.018
        bump.inputs['Distance'].default_value=.0007
        m.node_tree.links.new(tex.outputs['Fac'],bump.inputs['Height'])
        m.node_tree.links.new(bump.outputs['Normal'],bs.inputs['Normal'])
    out=m.node_tree.nodes.new('ShaderNodeOutputMaterial');m.node_tree.links.new(bs.outputs['BSDF'],out.inputs['Surface'])
    return m


def distance_to_edges(u,v,outline):
    result=1e9
    for a,b in zip(outline,outline[1:]+outline[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1]; t=max(0,min(1,((u-a[0])*dx+(v-a[1])*dy)/(dx*dx+dy*dy+1e-12)))
        result=min(result,math.hypot(u-a[0]-dx*t,v-a[1]-dy*t))
    return result


def face_relief(distance_px):
    """A real raised face with a broad edge ramp, in normalized rim units.

    The 7 px band is an appearance hypothesis derived from the visible dark edge;
    it is not a measured machining radius.  smoothstep avoids a tubular spoke.
    """
    t=max(0,min(1,distance_px/7))
    return .010*(t*t*(3-2*t))


def shell(name,outline,front,side,thickness=.04):
    vectors=[Vector((u,-v,0)) for u,v in outline]
    lookup={tuple(v):i for i,v in enumerate(vectors)}
    faces=[[v if isinstance(v,int) else lookup[tuple(v)] for v in tri] for tri in tessellate_polygon([vectors])]
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(vectors,[],faces);mesh.update()
    bm=bmesh.new();bm.from_mesh(mesh)
    bmesh.ops.subdivide_edges(bm,edges=list(bm.edges),cuts=2,use_grid_fill=True)
    bmesh.ops.triangulate(bm,faces=list(bm.faces))
    bm.verts.ensure_lookup_table();bm.verts.index_update()
    verts=[lift(v.co.x,-v.co.y,face_relief(distance_to_edges(v.co.x,-v.co.y,outline))) for v in bm.verts]
    faces=[[v.index for v in f.verts] for f in bm.faces]
    edges=[(e.verts[0].index,e.verts[1].index) for e in bm.edges if e.is_boundary]
    bm.free();n=len(verts)
    verts += [(x,y,z-thickness) for x,y,z in verts]
    topcount=len(faces)
    faces += [[i+n for i in reversed(f)] for f in faces.copy()]
    faces += [[a,b,b+n,a+n] for a,b in edges]
    result=bpy.data.meshes.new(name+' shell');result.from_pydata(verts,[],faces);result.update()
    obj=bpy.data.objects.new(name,result);bpy.context.collection.objects.link(obj)
    result.materials.append(front);result.materials.append(side)
    for i,p in enumerate(result.polygons):p.material_index=0 if i<topcount else 1;p.use_smooth=i<topcount
    bm=bmesh.new();bm.from_mesh(result);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces));bm.to_mesh(result);bm.free()
    obj['source']='manual original-photo front boundary; reference-view agreement is constructed'
    obj['thickness']='assumed normalized radius units; not measured'
    obj['front_relief']='7 source-pixel smoothstep to 0.010 normalized units; appearance hypothesis'
    bevel=obj.modifiers.new('Physical edge rounding — assumed','BEVEL')
    bevel.width=.006;bevel.segments=3;bevel.limit_method='ANGLE';bevel.angle_limit=math.radians(24)
    return obj


def seat(seat,front,side):
    x,y=seat['center'];rx,ry=seat['rx'],seat['ry'];N=64
    verts=[]
    for shrink,dz in [(1,0),(.6,0),(.6,-.025),(1,-.025)]:
        for i in range(N):
            angle=i*math.tau/N;p=lift(x+rx*shrink*math.cos(angle),y+ry*shrink*math.sin(angle))
            verts.append((p[0],p[1],p[2]+dz))
    faces=[]
    for row in range(4):
        for i in range(N):faces.append((row*N+i,row*N+(i+1)%N,((row+1)%4)*N+(i+1)%N,((row+1)%4)*N+i))
    mesh=bpy.data.meshes.new('Unmeasured bolt-seat context');mesh.from_pydata(verts,[],faces);mesh.update()
    obj=bpy.data.objects.new('Bolt-seat context — unmeasured, separate part',mesh);bpy.context.collection.objects.link(obj)
    mesh.materials.append(front);mesh.materials.append(side)
    for i,p in enumerate(mesh.polygons):p.material_index=0 if i<N else 1;p.use_smooth=True
    obj['status']='context only, not a fitted circular bore or connected engineering seat'


def camera():
    scene=bpy.context.scene;bpy.ops.object.camera_add();cam=bpy.context.object
    cam.data.type='ORTHO';cam.data.sensor_fit='HORIZONTAL';cam.data.ortho_scale=620/POSE['scale']
    offset=R[0]*((310-POSE['cx'])/POSE['scale'])+R[1]*((POSE['cy']-295.5)/POSE['scale'])
    cam.location=offset+R[2]*6;cam.rotation_euler=R.transposed().to_euler();scene.camera=cam
    scene.render.resolution_x=620;scene.render.resolution_y=591;scene.render.resolution_percentage=100
    bpy.context.view_layer.update()
    errors=[]
    for u,v in DATA['annotation']['master']['boundary']:
        p=world_to_camera_view(scene,cam,Vector(lift(u,v)))
        errors.append(math.hypot(p.x*620-u,(1-p.y)*591-v))
    if max(errors)>.05:raise RuntimeError(f'Camera raster mismatch: {max(errors)} px')
    cam.data.show_background_images=True;bg=cam.data.background_images.new()
    bg.image=bpy.data.images.load(str(OUT/'source.jpg'));bg.image.pack();bg.alpha=.55;bg.display_depth='BACK'
    return cam,max(errors)


def main():
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    neutral=mat('Geometry check gray',(.32,.34,.36),roughness=.52)
    neutral_side=mat('Geometry check side',(.14,.15,.16),roughness=.58)
    front=mat('Gunmetal face — hypothesis',(.075,.085,.095),metallic=.90,roughness=.30,micro=True)
    side=mat('Gunmetal recessed side — hypothesis',(.025,.032,.040),metallic=.78,roughness=.38,micro=True)
    context=mat('Context gunmetal — not accepted geometry',(.10,.11,.12),metallic=.84,roughness=.34,micro=True)
    shell('Sixfold master Y — original photo front contour',DATA['boundary'],front,side)
    shell('Rim attachment arc — approximate context',DATA['rim_arc'],context,side,.035)
    for s in DATA['annotation']['bolt_seats']:seat(s,context,side)
    objects=[o for o in bpy.context.scene.objects if o.type=='MESH']
    stats={}
    for o in objects:
        bm=bmesh.new();bm.from_mesh(o.data)
        stats[o.name]={'vertices':len(bm.verts),'faces':len(bm.faces),'nonmanifold_edges':sum(not e.is_manifold for e in bm.edges)};bm.free()
    scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=48;scene.cycles.device='CPU';scene.cycles.use_denoising=True
    scene.render.film_transparent=True;scene.render.image_settings.color_mode='RGBA'
    scene.world.use_nodes=True
    bg=next(n for n in scene.world.node_tree.nodes if n.type=='BACKGROUND');bg.inputs[0].default_value=(.018,.022,.028,1);bg.inputs[1].default_value=.16
    for loc,power,size in [((-3,2,4),650,3.5),((3,-1,3),360,2.4),((0,-4,2),240,2.8)]:
        bpy.ops.object.light_add(type='AREA',location=loc);light=bpy.context.object
        light.data.energy=power;light.data.size=size;light.rotation_euler=(-light.location).to_track_quat('-Z','Y').to_euler()
    cam,err=camera()
    # Geometry-only evidence: identical mesh/camera/lights, deliberately non-metallic.
    saved=[]
    for obj in objects:
        saved.append([m for m in obj.data.materials])
        obj.data.materials.clear();obj.data.materials.append(neutral);obj.data.materials.append(neutral_side)
    scene.render.filepath=str(OUT/'geometry-reference.png');bpy.ops.render.render(write_still=True)
    for obj,mats in zip(objects,saved):
        obj.data.materials.clear()
        for material in mats:obj.data.materials.append(material)
    scene.render.filepath=str(OUT/'metal-reference.png');bpy.ops.render.render(write_still=True)
    # Backward-compatible name now represents geometry evidence, not a texture result.
    scene.render.filepath=str(OUT/'reference-render.png')
    for obj in objects:
        obj.data.materials.clear();obj.data.materials.append(neutral);obj.data.materials.append(neutral_side)
    bpy.ops.render.render(write_still=True)
    for obj,mats in zip(objects,saved):
        obj.data.materials.clear()
        for material in mats:obj.data.materials.append(material)
    bpy.ops.object.select_all(action='DESELECT')
    for o in objects:o.select_set(True)
    bpy.context.view_layer.objects.active=objects[0]
    bpy.ops.export_scene.gltf(filepath=str(OUT/'local-sector.glb'),use_selection=True,export_apply=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'local-sector.blend'))
    points=[o.matrix_world@Vector(v) for o in objects for v in o.bound_box]
    center=Vector(tuple((min(p[k] for p in points)+max(p[k] for p in points))/2 for k in range(3)))
    cam.location=center+Vector((-.75,-.55,2));cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler()
    cam.data.ortho_scale=1.75;scene.render.resolution_x=900;scene.render.resolution_y=720
    for obj in objects:
        obj.data.materials.clear();obj.data.materials.append(neutral);obj.data.materials.append(neutral_side)
    scene.render.filepath=str(OUT/'geometry-oblique.png');bpy.ops.render.render(write_still=True)
    for obj,mats in zip(objects,saved):
        obj.data.materials.clear()
        for material in mats:obj.data.materials.append(material)
    scene.render.filepath=str(OUT/'metal-oblique.png');bpy.ops.render.render(write_still=True)
    scene.render.filepath=str(OUT/'local-oblique.png');bpy.ops.render.render(write_still=True)
    depsgraph=bpy.context.evaluated_depsgraph_get();evaluated={}
    for obj in objects:
        mesh=bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph),preserve_all_data_layers=True,depsgraph=depsgraph)
        bm=bmesh.new();bm.from_mesh(mesh)
        evaluated[obj.name]={'vertices':len(bm.verts),'faces':len(bm.faces),
            'nonmanifold_edges':sum(not e.is_manifold for e in bm.edges)}
        bm.free();bpy.data.meshes.remove(mesh)
    (OUT/'mesh-report.json').write_text(json.dumps({'camera_raster_max_px':err,'base_objects':stats,
        'evaluated_after_relief_and_bevel':evaluated,'geometry':{'front_relief_height_normalized':.010,
        'front_relief_width_source_px':7,'edge_rounding_normalized':.006,'dimensions_measured':False},
        'materials':{'geometry_check':'nonmetallic gray','gunmetal':{'metallic':.90,'roughness':.30,
        'micro_bump_distance_normalized':.0007,'photometrically_measured':False}},
        'mesh_integrity_is_photo_accuracy':False,'full_wheel_generated':False,'engineering_approved':False},indent=2))
    print('LOCAL_SECTOR_DONE',flush=True)


main()
