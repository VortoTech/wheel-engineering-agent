"""Run with Blender --background --python this-file. Makes five comparable gray studies."""
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/route-study'


def material():
    mat = bpy.data.materials.new('Uniform gray — no texture or metallic masking')
    mat.diffuse_color = (.42,.45,.49,1)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
    mat.node_tree.links.new(bsdf.outputs['BSDF'],output.inputs['Surface'])
    bsdf.inputs['Base Color'].default_value = (.18,.20,.23,1)
    bsdf.inputs['Roughness'].default_value = .42
    bsdf.inputs['Metallic'].default_value = .0
    return mat


def reset():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)


def tube(name, branch, angle):
    verts, faces = [], []
    # Extra close end rings preserve the ends under Catmull-Clark subdivision.
    def near(a,b):
        result=dict(a)
        result['center']=[x*.98+y*.02 for x,y in zip(a['center'],b['center'])]
        return result
    rings = [branch[0], near(branch[0],branch[1]), *branch[1:-1],
             near(branch[-1],branch[-2]), branch[-1]]
    for s in rings:
        c, tangent = Vector(s['center']), Vector(s['tangent'])
        for j in range(8):
            a = j*math.tau/8
            v = c + tangent*(s['width']/2*math.cos(a)) + Vector((0,0,s['height']/2*math.sin(a)))
            verts.append(tuple(v))
    for k in range(len(rings)-1):
        for j in range(8):
            a=k*8+j; b=k*8+(j+1)%8
            faces.append((a,b,b+8,a+8))
    faces += [tuple(reversed(range(8))),tuple((len(rings)-1)*8+j for j in range(8))]
    mesh=bpy.data.meshes.new(name); mesh.from_pydata(verts,[],faces); mesh.update()
    obj=bpy.data.objects.new(name,mesh); bpy.context.collection.objects.link(obj)
    obj.rotation_euler.z=angle
    mod=obj.modifiers.new('Editable curved blade subdivision','SUBSURF'); mod.levels=2; mod.render_levels=2
    for p in mesh.polygons: p.use_smooth=True


def ring(name, outer, inner, bottom, top, segments=160):
    verts=[]; faces=[]
    for r,z in [(outer,bottom),(outer,top),(inner,top),(inner,bottom)]:
        verts += [(r*math.cos(i*math.tau/segments),r*math.sin(i*math.tau/segments),z) for i in range(segments)]
    for row in range(4):
        for i in range(segments):
            j=(i+1)%segments; nxt=(row+1)%4
            faces.append((row*segments+i,row*segments+j,nxt*segments+j,nxt*segments+i))
    mesh=bpy.data.meshes.new(name); mesh.from_pydata(verts,[],faces); mesh.update()
    obj=bpy.data.objects.new(name,mesh); bpy.context.collection.objects.link(obj)
    bevel=obj.modifiers.new('Edge highlight radius','BEVEL'); bevel.width=1.2; bevel.segments=3
    for p in mesh.polygons: p.use_smooth=True


def new_subd():
    controls=json.loads((OUT/'controls.json').read_text())
    for group in range(5):
        for i,branch in enumerate(controls['branches']):
            tube(f'group-{group+1}-blade-{i+1}',branch,group*math.tau/5)
    ring('Outer lip',247,239,51,59)
    ring('Inner detail ring',222,215,51,59)
    ring('Barrel — assumed',246,241,-135,55)
    ring('Hub — assumed',55,29,-14,3)
    for i in range(15):
        a=i*math.tau/15
        bpy.ops.mesh.primitive_cube_add(size=1, location=(230*math.cos(a),230*math.sin(a),55))
        obj=bpy.context.object; obj.name=f'Decorative bridge {i+1}'
        obj.dimensions=(24,6,7); obj.rotation_euler.z=a
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        mod=obj.modifiers.new('Rounded bridge','BEVEL'); mod.width=1.2; mod.segments=3


def normalize(objects, ai=False):
    # Uniform scaling only. AI pose heuristic is recorded; it is not calibrated.
    if ai:
        coords=[o.matrix_world@Vector(v) for o in objects for v in o.bound_box]
        spans=[max(v[k] for v in coords)-min(v[k] for v in coords) for k in range(3)]
        axis=min(range(3),key=lambda k:spans[k])
        if axis != 2:
            from mathutils import Matrix
            rot=Matrix.Rotation(math.pi/2,4,'X' if axis==1 else 'Y')
            for o in objects: o.matrix_world=rot@o.matrix_world
    bpy.context.view_layer.update()
    pts=[o.matrix_world@Vector(v) for o in objects for v in o.bound_box]
    lo=Vector(tuple(min(v[k] for v in pts) for k in range(3)))
    hi=Vector(tuple(max(v[k] for v in pts) for k in range(3)))
    mid=(lo+hi)/2; scale=4.94/max(hi.x-lo.x,hi.y-lo.y)
    from mathutils import Matrix
    transform=Matrix.Scale(scale,4)@Matrix.Translation(-mid)
    for o in objects: o.matrix_world=transform@o.matrix_world
    return {'raw_bounds_min':list(lo),'raw_bounds_max':list(hi),'uniform_scale':scale,
            'ai_pose':'smallest bounding-box axis aligned to Z; not photo calibrated' if ai else None}


def studio():
    scene=bpy.context.scene
    scene.render.engine='CYCLES'; scene.cycles.device='CPU'; scene.cycles.samples=16
    scene.cycles.use_denoising=True
    scene.render.resolution_x=720; scene.render.resolution_y=720; scene.render.resolution_percentage=100
    scene.world.use_nodes=True
    bg = next(n for n in scene.world.node_tree.nodes if n.type=='BACKGROUND')
    bg.inputs[0].default_value=(.7,.7,.7,1)
    bg.inputs[1].default_value=.45
    scene.view_settings.view_transform='AgX'
    for loc,power,size in [((-4,-3,7),500,5),((4,1,5),325,4),((0,5,3),400,3)]:
        bpy.ops.object.light_add(type='AREA',location=loc)
        light=bpy.context.object; light.data.energy=power; light.data.shape='DISK'; light.data.size=size
        light.rotation_euler=(-light.location).to_track_quat('-Z','Y').to_euler()
    bpy.ops.object.camera_add(location=(0,0,10))
    camera=bpy.context.object; camera.data.type='ORTHO'; camera.data.ortho_scale=6.3
    scene.camera=camera
    return scene,camera


def main():
    report={'blender_version':bpy.app.version_string,
            'comparison_scope':'representation-only; not calibrated photo reconstruction',
            'new_inference':False}
    for route in ['baseline','baseline-smoothed','cad-loft','blender-subd','sf3d-existing']:
        reset()
        if route=='blender-subd': new_subd()
        elif route=='sf3d-existing':
            bpy.ops.import_scene.gltf(filepath=str(ROOT/'data/reconstructions/cd68c8c258144e8ca6391d732a67738f/reference.glb'))
        else:
            path=OUT/('cad-loft.stl' if route=='cad-loft' else 'baseline.stl')
            bpy.ops.wm.stl_import(filepath=str(path))
        objects=[o for o in bpy.context.scene.objects if o.type=='MESH']
        if route=='baseline-smoothed':
            for obj in objects:
                # Deliberately conservative: normals improve shading, not silhouette.
                for p in obj.data.polygons: p.use_smooth=True
                mod=obj.modifiers.new('Conservative Laplacian smoothing','LAPLACIANSMOOTH')
                mod.iterations=4; mod.lambda_factor=.15; mod.use_volume_preserve=True
        mat=material()
        for obj in objects: obj.data.materials.clear(); obj.data.materials.append(mat)
        report[route]=normalize(objects,ai=route=='sf3d-existing')
        report[route]['mesh_objects']=len(objects)
        report[route]['input_polygon_count']=sum(len(o.data.polygons) for o in objects)
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects: obj.select_set(True)
        bpy.context.view_layer.objects.active=objects[0]
        bpy.ops.export_scene.gltf(filepath=str(OUT/f'{route}.glb'),use_selection=True,export_apply=True)
        scene,camera=studio()
        for view,loc in [('front',(0,0,10)),('oblique',(-5,-2,9))]:
            camera.location=loc; camera.rotation_euler=(-camera.location).to_track_quat('-Z','Y').to_euler()
            scene.render.filepath=str(OUT/f'{route}-{view}.png')
            bpy.ops.render.render(write_still=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT/f'{route}.blend'))
        print('COMPLETED',route,flush=True)
    (OUT/'render-report.json').write_text(json.dumps(report,indent=2))


main()
