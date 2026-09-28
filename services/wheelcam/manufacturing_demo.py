"""Review-only wheel process package and sampled 2.5D stock-removal study.

The NC file deliberately contains commented motion blocks. A machine-specific CAM
postprocessor, setup, feeds/speeds and engineering sign-off are required before NC.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, distance_transform_edt

from .forged_blank import face_z, hole_form, recipe_from_dict, z_back
from .mesh_build import blank_profile, outlines

GRID_MM = 1.5
TOOL_D_MM = 6.0
STEP_OVER_MM = 4.5
STEP_DOWN_MM = 6.0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def confirmed_recipe(recipe: dict, spec: dict) -> dict:
    """Copy a development recipe; the order's hole form is authoritative for this demo."""
    form = hole_form(spec.get("hole_form"))
    if not form:
        raise ValueError("No confirmed hole form; drilling and drawing cannot be generated")
    clean = {k: v for k, v in recipe.items() if not k.startswith("_")}
    for key, value in (("pcd", spec["spec"]["pcd_mm"]), ("bolts", spec["spec"]["bolts"]),
                       ("center_bore_r", spec["spec"]["center_bore_mm"] / 2)):
        if not math.isclose(float(clean[key]), float(value), abs_tol=0.01):
            raise ValueError(f"Recipe {key} differs from the confirmed order")
    clean.update(form)
    recipe_from_dict(clean)
    return clean


def window_grid(p, grid_mm=GRID_MM):
    """Sampled XY window target. This raster is a visualization, not CAD metrology."""
    extent = math.ceil((p.lip_r + 8) / grid_mm) * grid_mm
    axis = np.arange(-extent, extent + grid_mm / 2, grid_mm)
    bitmap = Image.new("1", (len(axis), len(axis)), 0)
    painter = ImageDraw.Draw(bitmap)
    for outline in outlines(p):
        painter.polygon([((x - axis[0]) / grid_mm, (y - axis[0]) / grid_mm)
                         for x, y in outline], fill=1)
    target = np.asarray(bitmap, dtype=bool).copy()
    xx, yy = np.meshgrid(axis, axis)
    target &= xx * xx + yy * yy <= p.window_r_out ** 2
    return axis, target


def raster_sweeps(target, grid_mm=GRID_MM, tool_d_mm=TOOL_D_MM, step_over_mm=STEP_OVER_MM):
    """Centreline raster kept inside the sampled target by one tool radius."""
    if tool_d_mm <= 0 or step_over_mm <= 0 or grid_mm <= 0:
        raise ValueError("Tool and raster spacing must be positive")
    clearance = distance_transform_edt(target) * grid_mm
    # A raster cell can be inside while the continuous cutter clips a spline.
    allowed = target & (clearance >= tool_d_mm / 2 + max(grid_mm, 2.5))
    stride = max(1, round(step_over_mm / grid_mm))
    centrelines = np.zeros_like(target)
    segments = []
    for y in range(0, target.shape[0], stride):
        row = allowed[y]
        changes = np.diff(np.pad(row.astype(np.int8), (1, 1)))
        for left, right in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]):
            if right - left < 2:
                continue
            centrelines[y, left:right] = True
            segments.append((int(y), int(left), int(right - 1)))
    radius_px = math.ceil(tool_d_mm / (2 * grid_mm))
    iy, ix = np.mgrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
    footprint = ix * ix + iy * iy <= (tool_d_mm / (2 * grid_mm)) ** 2
    swept = binary_dilation(centrelines, structure=footprint)
    overcut = swept & ~target
    if np.any(overcut):
        raise ValueError("Sampled cutter sweep leaves the target window")
    return segments, swept


def _tool_rows(p):
    return [
        ("T01", "turning", "profile tool", "nominal profile only", "turning setup and insert geometry unknown"),
        ("T02", "drilling", f"Ø{p.bolt_d:g} mm drill", f"{p.bolts} holes on PCD Ø{p.pcd:g}", "drill length and fixture unknown"),
        ("T03", "seat", f"Ø{p.seat_d:g} mm / {p.seat_cone_deg:g}° form tool", "conical bolt seats", "seat location and form tool require CAM review"),
        ("T04", "window roughing", f"Ø{TOOL_D_MM:g} mm end mill", "sampled 2.5D raster only", "access, holder clearance and remaining stock not validated"),
    ]


def _reference_nc(p, axis, segments, z_top, z_bottom, *, z_shift=0.0):
    """G-code-looking motion *comments*, never an executable controller program."""
    blocks = [
        "(WHEELCAM REFERENCE NC - NOT RELEASED - ALL MOTION BLOCKS ARE COMMENTS)",
        "(NO WORK OFFSET, FIXTURE, POSTPROCESSOR, FEED, SPEED OR COLLISION VALIDATION)",
        "(DO NOT LOAD ON A MACHINE; ENGINEER MUST PROGRAM IN THE FACTORY CAM)",
        "(TURNING: X IS DIAMETER, Z IS WHEEL AXIS; COORDINATE ORIGIN IS A DESIGN ASSUMPTION)",
    ]
    # Revolved profile is a geometric contour, not a tested cutting sequence.
    profile = blank_profile(p, samples=24)
    for r, z in profile[::max(1, len(profile) // 65)]:
        blocks.append(f"(G01 X{2 * r:.3f} Z{z + z_shift:.3f}  ; TURN CONTOUR REFERENCE)")
    # The front face at the bolt circle is the drill entry side (+Z); the
    # mounting face is the back of the hub. Neither is a machine work offset.
    drill_r = face_z(p, p.pcd / 2) + z_shift + 5.0
    drill_z = z_back(p, p.hub_r) + z_shift - 3.0
    blocks.append("(DRILLING: XY LOCATIONS; R = BOLT ENTRY FACE +5 MM; Z = MOUNTING FACE -3 MM)")
    for i in range(p.bolts):
        angle = math.radians(180 / p.spokes + i * 360 / p.bolts)
        x, y = p.pcd / 2 * math.cos(angle), p.pcd / 2 * math.sin(angle)
        blocks.append(f"(G81 X{x:.3f} Y{y:.3f} Z{drill_z:.3f} R{drill_r:.3f}  ; HOLE {i + 1})")
    blocks.append(f"(WINDOW ROUGHING: T04 Ø{TOOL_D_MM:g}, STEPOVER {STEP_OVER_MM:g}, STEP DOWN {STEP_DOWN_MM:g} MM)")
    depth = max(0, z_top - z_bottom)
    layers = max(1, math.ceil(depth / STEP_DOWN_MM))
    for layer in range(1, layers + 1):
        z = max(z_bottom, z_top - layer * STEP_DOWN_MM)
        blocks.append(f"(LAYER {layer}/{layers} Z{z:.3f} ; FULL SAMPLED RASTER, STILL NOT MACHINE CODE)")
        for y, left, right in segments:
            blocks.append(f"(G01 X{axis[left]:.3f} Y{axis[y]:.3f} Z{z:.3f})")
            blocks.append(f"(G01 X{axis[right]:.3f} Y{axis[y]:.3f} Z{z:.3f})")
    blocks.append("(CURVED SPOKE FINISHING IS NOT PROGRAMMED; FACTORY CAM REQUIRED)")
    return "\n".join(blocks) + "\n", layers


def _continuous_cutter(axis, segments, tool_d_mm=TOOL_D_MM):
    """Union the actual XY swept capsules for the 3D mesh Boolean study."""
    import manifold3d as m3

    capsules = []
    for y, left, right in segments:
        x0, x1, yc = float(axis[left]), float(axis[right]), float(axis[y])
        line = np.array([(x0, yc - .05), (x1, yc - .05),
                         (x1, yc + .05), (x0, yc + .05)])
        capsules.append(m3.CrossSection([line]).offset(tool_d_mm / 2, m3.JoinType.Round))
    return m3.CrossSection.batch_boolean(capsules, m3.OpType.Add)


def _render_3d_comparison(stock, rough, design, path: Path, gouge_mm3: float, *,
                          center_z=-145.0, titles=None):
    """Simple shaded orthographic snapshots of the exact mesh Boolean results."""
    width, height = 480, 450
    canvas = Image.new("RGB", (3 * width + 80, height + 90), "white")
    painter = ImageDraw.Draw(canvas)
    camera = np.array([1.0, -.8, 1.15])
    camera /= np.linalg.norm(camera)
    up_axis = np.array([0.0, 0.0, 1.0])
    right = np.cross(up_axis, camera)
    right /= np.linalg.norm(right)
    up = np.cross(camera, right)
    light = np.array([.4, -.4, 1.0])
    light /= np.linalg.norm(light)
    # The same projection and scale on each panel makes removed stock visible.
    center = np.array([0.0, 0.0, center_z])
    scale = min(width, height) / 610.0
    palette = ((155, 166, 177), (78, 135, 194), (172, 143, 102))
    for index, (body, title, base) in enumerate(zip(
            (stock, rough, design),
            titles or ("MODEL BLANK (ASSUMED)", "AFTER 2.5D SWEEP", "RECONSTRUCTED DESIGN"), palette)):
        mesh = body.to_mesh()
        verts = np.asarray(mesh.vert_properties)[:, :3].astype(float) - center
        faces = np.asarray(mesh.tri_verts, dtype=np.int64)
        points = verts[faces]
        normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
        norm = np.linalg.norm(normals, axis=1)
        visible = (norm > 1e-9) & ((normals @ camera) > 0)
        points, normals, norm = points[visible], normals[visible], norm[visible]
        shade = np.clip(.45 + .5 * (normals @ light) / norm, .3, 1)
        order = np.argsort(np.mean(points @ camera, axis=1))
        projected = np.stack((points @ right, points @ up), axis=-1) * scale
        projected[:, :, 0] += 20 + index * (width + 20) + width / 2
        projected[:, :, 1] = 43 + height / 2 - projected[:, :, 1]
        for i in order:
            tone = float(shade[i])
            color = tuple(int(min(255, channel * tone + 20)) for channel in base)
            painter.polygon([tuple(point) for point in projected[i]], fill=color)
        left = 20 + index * (width + 20)
        painter.rectangle((left, 43, left + width, 43 + height), outline="#666666", width=2)
        painter.text((left + 7, 43 + height + 8), title, fill="#111111")
    status = "GOUGE DETECTED - REPLAN" if gouge_mm3 > 1.0 else "NO SAMPLED GOUGE"
    painter.text((22, 16), f"3D STOCK REMOVAL STUDY | {status} | NOT RELEASED", fill="#aa1111")
    canvas.save(path)


def create_package(recipe: dict, spec: dict, output: Path, *, blank_code: str | None = None,
                   design_body=None) -> dict:
    """Create metadata, disabled NC reference and a sampled static simulation figure."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    confirmed = confirmed_recipe(recipe, spec)
    p = recipe_from_dict(confirmed)
    (output / "recipe.json").write_text(json.dumps(confirmed, ensure_ascii=False, indent=2) + "\n")
    axis, target = window_grid(p)
    segments, swept = raster_sweeps(target)
    if not segments:
        raise ValueError("No reachable Ø6 mm window raster; no package produced")
    # The sampled study treats the window as a constant-depth 2.5D pocket.
    # A real wheel has varying face/back surfaces, so this is never a collision check.
    z_top = max(0.0, float(p.ring_z)) + 15.0
    z_bottom = float(p.hub_z - p.window_pocket_depth)
    nc, layers = _reference_nc(p, axis, segments, z_top, z_bottom)
    (output / "reference.nc").write_text(nc)
    with (output / "tool_list.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("tool", "operation", "type", "intent", "pending_review"))
        writer.writerows(_tool_rows(p))
    plan = {
        "schema": "wheelcam-manufacturing-demo-v1", "status": "not_released",
        "source": "confirmed order hole form + reconstructed recipe; geometry remains a draft",
        "stock": {"code": blank_code, "geometry_status": "not_provided", "setup_status": "not_validated"},
        "operations": [
            {"id": "OP10", "type": "turning", "geometry": "revolved blank profile", "status": "reference_only"},
            {"id": "OP20", "type": "drilling", "cycle": "G81 reference", "hole_count": p.bolts,
             "hole_d_mm": p.bolt_d, "pcd_mm": p.pcd,
             "retract_plane_z_mm": round(face_z(p, p.pcd / 2) + 5.0, 3),
             "drill_end_z_mm": round(z_back(p, p.hub_r) - 3.0, 3),
             "status": "reference_only"},
            {"id": "OP30", "type": "conical_seat", "top_d_mm": p.seat_d,
             "included_angle_deg": p.seat_cone_deg, "status": "reference_only"},
            {"id": "OP40", "type": "window_2_5d_roughing", "tool_d_mm": TOOL_D_MM,
             "step_over_mm": STEP_OVER_MM, "step_down_mm": STEP_DOWN_MM,
             "sampled_sweeps": len(segments), "depth_layers": layers, "status": "reference_only"},
            {"id": "OP50", "type": "spoke_surface_finishing", "status": "factory_cam_required"},
        ],
        "simulation": {"type": "sampled_2_5d_xy_cutter_sweep", "grid_mm": GRID_MM,
                       "target_window_area_mm2": round(float(target.sum()) * GRID_MM ** 2, 1),
                       "swept_area_mm2": round(float(swept.sum()) * GRID_MM ** 2, 1),
                       "remaining_target_area_mm2": round(float((target & ~swept).sum()) * GRID_MM ** 2, 1),
                       "sampled_overcut_area_mm2": round(float((swept & ~target).sum()) * GRID_MM ** 2, 1)},
        "limits": ["Reference NC contains only commented motion blocks and is not machine executable.",
                   "Stock supplier geometry, fixture, tool holder, feeds, speeds, coordinate setup and postprocessor are unknown.",
                   "3D Boolean stock removal uses the model's assumed blank, not the factory forging or a machine collision model.",
                   "The 2.5D study does not validate curved spoke surfaces or remaining 3D material."],
    }
    if design_body is not None:
        import manifold3d as m3
        from .mesh_build import export_glb, revolve

        stock = revolve(blank_profile(p))  # model's own pre-cut blank, not the factory blank
        section = _continuous_cutter(axis, segments)
        radial_limit = m3.CrossSection.circle(p.window_r_out, 256)
        section = section ^ radial_limit
        target_section = (m3.CrossSection([np.asarray(poly) for poly in outlines(p)],
                                          m3.FillRule.EvenOdd) ^ radial_limit)
        cutter = section.extrude(z_top - z_bottom).translate([0, 0, z_bottom])
        rough = stock - cutter
        if rough.status() != m3.Error.NoError:
            raise ValueError(f"3D sampled stock Boolean failed: {rough.status()}")
        gouge = max(0.0, (design_body - rough).volume())
        residue = max(0.0, (rough - design_body).volume())
        export_glb(stock, output / "stock_assumed.glb")
        export_glb(rough, output / "stock_after_roughing.glb")
        _render_3d_comparison(stock, rough, design_body, output / "simulation_3d.png", gouge)
        plan["simulation_3d"] = {
            "stock_type": "revolved_pre_cut_model_blank; not factory stock",
            "continuous_xy_swept_area_mm2": round(section.area(), 2),
            "continuous_xy_outside_window_mm2": round((section - target_section).area(), 2),
            "stock_volume_mm3": round(stock.volume(), 1),
            "after_roughing_volume_mm3": round(rough.volume(), 1),
            "design_volume_mm3": round(design_body.volume(), 1),
            "gouge_vs_design_mm3": round(gouge, 1),
            "remaining_vs_design_mm3": round(residue, 1),
            "status": "requires_replanning" if gouge > 1.0 else "sampled_no_gouge",
            "conclusion": (f"窗口粗加工无过切（相对当前重建网格的采样仿真）；余量 {residue / 1e6:.3f} L 交工厂 CAM 曲面精加工。"
                           if gouge <= 1.0 else
                           f"发现采样过切 {gouge / 1e6:.3f} L；需重新规划窗口粗加工。"),
            "note": "Constant-depth sweep is compared with the reconstructed mesh, not factory CAD. "
                    "Remaining volume includes uncut holes, seats and styling, not only curved spoke surfaces. "
                    "It has no machine, fixture, holder or true forging stock geometry.",
        }
        plan["limits"].append("A positive gouge volume means the sampled path intersects the design; it must not be released.")
    (output / "process_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")

    panel = 560
    canvas = Image.new("RGB", (panel * 3 + 80, panel + 110), "white")
    painter = ImageDraw.Draw(canvas)
    painter.text((24, 12), "REFERENCE 2.5D STUDY  |  NOT RELEASED  |  NO 3D COLLISION CHECK", fill="#111111")
    labels = ("TARGET WINDOW FOOTPRINT", "SAMPLED 6 MM CUTTER SWEEP", "REMAINING TARGET AREA")
    maps = (np.where(target, 30, 248), np.where(swept, 40, 248),
            np.where(target & ~swept, 40, np.where(target, 150, 248)))
    for index, values in enumerate(maps):
        if index == 0:
            rgb = np.stack([values] * 3, axis=-1).astype(np.uint8)
        elif index == 1:
            rgb = np.stack([values, values, np.full_like(values, 248)], axis=-1).astype(np.uint8)
        else:
            rgb = np.stack([np.full_like(values, 248), values, values], axis=-1).astype(np.uint8)
        tile = Image.fromarray(np.flipud(rgb), "RGB").resize((panel, panel), Image.Resampling.NEAREST)
        left = 20 + index * (panel + 20)
        canvas.paste(tile, (left, 45))
        painter.rectangle((left, 45, left + panel - 1, 45 + panel - 1), outline="#333333", width=2)
        painter.text((left + 4, 45 + panel + 8), labels[index], fill="#111111")
        painter.text((left + 4, 45 + panel + 29), f"X, Y: {axis[0]:g} TO {axis[-1]:g} MM", fill="#444444")
    canvas.save(output / "simulation.png")
    names = ["recipe.json", "reference.nc", "tool_list.csv", "simulation.png"]
    if design_body is not None:
        names += ["stock_assumed.glb", "stock_after_roughing.glb", "simulation_3d.png"]
    plan["artifacts"] = {name: {"sha256": _sha(output / name), "bytes": (output / name).stat().st_size}
                         for name in names}
    (output / "process_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return plan
