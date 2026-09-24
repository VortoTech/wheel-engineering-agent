"""Render a neutral oblique QA image from a local-sector GLB in Blender."""
import sys
from pathlib import Path

import bpy
from mathutils import Vector


args = sys.argv[sys.argv.index("--")+1:]
source, output = map(Path, args)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(source))
objects = [item for item in bpy.context.scene.objects if item.type == "MESH"]
material = bpy.data.materials.new("Neutral geometry review")
material.diffuse_color = (.34, .37, .41, 1)
material.use_nodes = True
bsdf = material.node_tree.nodes.get("Principled BSDF")
bsdf.inputs["Base Color"].default_value = (.22, .25, .29, 1)
bsdf.inputs["Roughness"].default_value = .5
bsdf.inputs["Metallic"].default_value = 0
for item in objects:
    item.data.materials.clear()
    item.data.materials.append(material)
    for polygon in item.data.polygons:
        polygon.use_smooth = True

corners = [item.matrix_world @ Vector(corner) for item in objects for corner in item.bound_box]
low = Vector(tuple(min(point[i] for point in corners) for i in range(3)))
high = Vector(tuple(max(point[i] for point in corners) for i in range(3)))
center, extent = (low+high)/2, max(high-low)

bpy.ops.object.camera_add(location=center+Vector((1.25, -1.7, 1.15))*extent)
camera = bpy.context.object
camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
camera.data.lens = 62
bpy.context.scene.camera = camera
for location, energy, size in [((-.8, -1.0, 2.2), 700, 3.0), ((1.8, .8, 1.2), 420, 2.5)]:
    bpy.ops.object.light_add(type="AREA", location=center+Vector(location)*extent)
    light = bpy.context.object
    light.data.energy, light.data.shape, light.data.size = energy, "DISK", size*extent
    light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 900, 720, 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
scene.world.color = (.012, .016, .022)
scene.render.filepath = str(output)
bpy.ops.render.render(write_still=True)
