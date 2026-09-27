"""Engineering drawing of a mesh-built wheel: front view, A-A section, key dimensions, title block.

An A3 landscape SVG (millimetres, scale 1:4 by default) for review, not a released drawing:
- front view: the part projected along the axis (windows show through), bolt circle, bore
- section A-A: the plane through the axis along spoke 0, so one half cuts a spoke and, with an
  odd spoke count, the other half a window; hatched, with the centre plane and the mounting face
- dimensions: lip OD, overall width, ET (mounting face to centre plane), centre bore, PCD, holes and
  seats from the recipe; the title block carries the order spec, material, blank and "not released"
Tolerances and geometric requirements are not defined by WheelCAM and the block says so.
"""
from __future__ import annotations

import datetime
import html
import math

import numpy as np

A3 = (420.0, 297.0)


def _polys_to_path(polys, tf) -> str:
    out = []
    for poly in polys:
        pts = [tf(*p) for p in np.asarray(poly, float)]
        if len(pts) < 2:
            continue
        out.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z")
    return " ".join(out)


def front_polygons(body):
    """Outline of the part seen along the axis (x, y), holes included."""
    return body.project().to_polygons()


def section_polygons(body, angle_deg=0.0):
    """Cut of the part by the plane through the axis at `angle_deg`, as (s, z) polygons: s is the
    signed distance from the axis along that direction."""
    turned = body.rotate([0, 0, -angle_deg]).rotate([90, 0, 0])      # that plane -> z' = 0, y' = -z
    return [np.column_stack([np.asarray(p)[:, 0], -np.asarray(p)[:, 1]]) for p in turned.slice(0.0).to_polygons()]


def _dim_h(x0, x1, y, text, ext_from=None):
    """Horizontal dimension line with arrows at y, extension lines from ext_from (y) if given."""
    parts = []
    if ext_from is not None:
        for x in (x0, x1):
            parts.append(f'<line x1="{x:.2f}" y1="{ext_from:.2f}" x2="{x:.2f}" y2="{y + (1.5 if y > ext_from else -1.5):.2f}" class="thin"/>')
    parts.append(f'<line x1="{x0:.2f}" y1="{y:.2f}" x2="{x1:.2f}" y2="{y:.2f}" class="dim" marker-start="url(#a)" marker-end="url(#a)"/>')
    parts.append(f'<text x="{(x0 + x1) / 2:.2f}" y="{y - 1.2:.2f}" class="t" text-anchor="middle">{html.escape(text)}</text>')
    return "\n".join(parts)


def _dim_v(x, y0, y1, text, ext_from=None):
    parts = []
    if ext_from is not None:
        for y in (y0, y1):
            parts.append(f'<line x1="{ext_from:.2f}" y1="{y:.2f}" x2="{x + (1.5 if x > ext_from else -1.5):.2f}" y2="{y:.2f}" class="thin"/>')
    parts.append(f'<line x1="{x:.2f}" y1="{y0:.2f}" x2="{x:.2f}" y2="{y1:.2f}" class="dim" marker-start="url(#a)" marker-end="url(#a)"/>')
    parts.append(f'<text x="{x - 1.2:.2f}" y="{(y0 + y1) / 2:.2f}" class="t" text-anchor="middle" '
                 f'transform="rotate(-90 {x - 1.2:.2f} {(y0 + y1) / 2:.2f})">{html.escape(text)}</text>')
    return "\n".join(parts)


def drawing_svg(body, p, *, spec=None, order=None, title="锻造单片轮毂 初稿", drawing_no="", scale=4.0,
                source="渲染图重建 + 订单确认单规格", date=None) -> str:
    """The A3 SVG of `body` (manifold) built from recipe `p` (ForgedWheel)."""
    spec, order = spec or {}, order or {}
    W, H = A3
    k = 1 / scale
    lo, hi = np.asarray(body.bounding_box()[:3]), np.asarray(body.bounding_box()[3:])
    lip_od = float(hi[0] - lo[0])
    width = float(hi[2] - lo[2])
    # front view on the left half, centre (cx, cy)
    cx, cy = 20 + lip_od * k / 2 + 8, 30 + lip_od * k / 2 + 6
    tf_front = lambda x, y: (cx + x * k, cy - y * k)
    front = _polys_to_path(front_polygons(body), tf_front)
    # section A-A on the right: z across (front of the part to the right), s up
    sx0 = cx + lip_od * k / 2 + 45                                   # x of the back face (z = -width)
    tf_sec = lambda s, z: (sx0 + (z + width) * k, cy - s * k)
    section = _polys_to_path(section_polygons(body), tf_sec)
    # mounting face (as the ET check measures it) and the centre plane
    import manifold3d as m3
    r_out = max(p.center_bore_r + 4, min(p.hub_r, p.pcd / 2 - p.seat_d / 2 - 1))
    ring = (m3.Manifold.cylinder(p.width + 10, r_out, r_out, 96).translate([0, 0, -p.width - 5])
            - m3.Manifold.cylinder(p.width + 12, p.center_bore_r + 1, p.center_bore_r + 1, 96).translate([0, 0, -p.width - 6]))
    hub = body ^ ring
    z_mount = float(hub.bounding_box()[2]) if not hub.is_empty() else float("nan")
    z_centre = float(lo[2]) + width / 2
    et = z_mount - z_centre
    xm, _ = tf_sec(0, z_mount)
    xc, _ = tf_sec(0, z_centre)
    top, bot = cy - lip_od * k / 2 - 6, cy + lip_od * k / 2 + 6
    el = []
    el.append(f'<path d="{front}" class="part" fill-rule="evenodd"/>')
    el.append(f'<path d="{section}" class="cut" fill-rule="evenodd"/>')
    # centre lines
    el.append(f'<line x1="{cx - lip_od * k / 2 - 5:.2f}" y1="{cy:.2f}" x2="{cx + lip_od * k / 2 + 5:.2f}" y2="{cy:.2f}" class="centre"/>')
    el.append(f'<line x1="{cx:.2f}" y1="{top:.2f}" x2="{cx:.2f}" y2="{bot:.2f}" class="centre"/>')
    el.append(f'<line x1="{sx0 - 6:.2f}" y1="{cy:.2f}" x2="{sx0 + width * k + 6:.2f}" y2="{cy:.2f}" class="centre"/>')
    el.append(f'<line x1="{xc:.2f}" y1="{top:.2f}" x2="{xc:.2f}" y2="{bot:.2f}" class="centre"/>')
    el.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{p.pcd / 2 * k:.2f}" class="centre" fill="none"/>')
    # section marks on the front view (spoke 0 direction)
    for sgn in (1, -1):
        x = cx + sgn * (lip_od * k / 2 + 3)
        el.append(f'<text x="{x:.2f}" y="{cy - 2:.2f}" class="tb" text-anchor="middle">A</text>')
    el.append(f'<text x="{sx0 + width * k / 2:.2f}" y="{top - 4:.2f}" class="tb" text-anchor="middle">A-A</text>')
    # dimensions
    el.append(_dim_h(cx - lip_od * k / 2, cx + lip_od * k / 2, bot + 6, f"Ø{lip_od:.1f}", ext_from=cy))
    el.append(_dim_h(sx0, sx0 + width * k, bot + 6, f"{width:.1f}", ext_from=cy + lip_od * k / 2))
    y_et = cy - (p.pcd / 2 + 30) * k                                 # above the hub, clear of the title
    el.append(_dim_h(min(xc, xm) - 0.0, max(xc, xm), y_et, "", ext_from=cy))
    el.append(f'<text x="{max(xc, xm) + 2:.2f}" y="{y_et + 1:.2f}" class="t">ET {et:+.1f}（安装面 → 中心平面）</text>')
    # holes drawn true from the recipe over the projection (a mesh projection rounds them unevenly)
    for i in range(p.bolts):
        a = math.radians(180 / p.spokes + i * 360 / p.bolts)
        hx, hy = tf_front(p.pcd / 2 * math.cos(a), p.pcd / 2 * math.sin(a))
        el.append(f'<circle cx="{hx:.2f}" cy="{hy:.2f}" r="{p.seat_d / 2 * k:.2f}" class="thin" fill="none"/>')
        el.append(f'<circle cx="{hx:.2f}" cy="{hy:.2f}" r="{p.bolt_d / 2 * k:.2f}" class="hole"/>')
    el.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{p.center_bore_r * k:.2f}" class="hole"/>')
    bx0, by0 = cx - p.center_bore_r * k * .7, cy - p.center_bore_r * k * .7
    el.append(f'<line x1="{bx0:.2f}" y1="{by0:.2f}" x2="{bx0 - 18:.2f}" y2="{by0 - 20:.2f}" class="thin"/>')
    el.append(f'<text x="{bx0 - 19:.2f}" y="{by0 - 21:.2f}" class="t" text-anchor="end">中心孔 Ø{2 * p.center_bore_r:.1f}</text>')
    holes = f"{p.bolts}×Ø{p.bolt_d:g} 通孔"
    if p.seat_cone_deg > 0:
        holes += f"，Ø{p.seat_d:g}×{p.seat_cone_deg:g}° 锥座"
    else:
        holes += f"，Ø{p.seat_d:g} 沉孔"
    lx, ly = cx + p.pcd / 2 * k * .7, cy + p.pcd / 2 * k * .7
    el.append(f'<line x1="{lx:.2f}" y1="{ly:.2f}" x2="{lx + 22:.2f}" y2="{ly + 16:.2f}" class="thin"/>')
    el.append(f'<text x="{lx + 23:.2f}" y="{ly + 17:.2f}" class="t">PCD Ø{p.pcd:g}，{html.escape(holes)}，均布</text>')
    # title block
    size = f'{spec.get("diameter_in", "")}×{spec.get("width_in", "")}J' if spec else ""
    rows = [("名称", title), ("图号", drawing_no), ("规格", f'{size} ET{spec.get("et_mm", round(et, 1)):g} PCD {p.bolts}×{p.pcd:g} CB{2 * p.center_bore_r:g}'),
            ("材料", "6061-T6 锻造"), ("毛坯", order.get("blank", "未指定")), ("比例 / 单位", f"1:{scale:g} / mm"),
            ("来源", source), ("公差", "未定义：按工厂标准由工程师补注"), ("日期", (date or datetime.date.today()).isoformat()),
            ("状态", "未发布 NOT RELEASED · 需工程师审核")]
    bx, by, bw, rh = W - 10 - 150, H - 10 - len(rows) * 6.5, 150, 6.5
    el.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{rh * len(rows)}" class="frame"/>')
    for i, (key, val) in enumerate(rows):
        y = by + i * rh
        el.append(f'<line x1="{bx}" y1="{y}" x2="{bx + bw}" y2="{y}" class="thin"/>')
        el.append(f'<text x="{bx + 2}" y="{y + 4.6}" class="t">{html.escape(key)}</text>')
        cls = "tr" if key == "状态" else "t"
        el.append(f'<text x="{bx + 30}" y="{y + 4.6}" class="{cls}">{html.escape(str(val))}</text>')
    el.append(f'<line x1="{bx + 28}" y1="{by}" x2="{bx + 28}" y2="{by + rh * len(rows)}" class="thin"/>')
    notes = ["技术要求：", "1. 未注圆角、倒角按锻造与加工工艺由工程师确定。",
             "2. 辐条曲面精加工由工厂 CAM 编程；本图只标注回转与孔系尺寸。",
             "3. ET 为安装面到轮辋中心平面的距离，正值偏向外侧。"]
    for i, line in enumerate(notes):
        el.append(f'<text x="14" y="{H - 10 - (len(notes) - 1 - i) * 5.5:.1f}" class="t">{html.escape(line)}</text>')
    style = """
  .part { fill: #eef1f5; stroke: #111; stroke-width: .25; }
  .hole { fill: white; stroke: #111; stroke-width: .25; }
  .cut { fill: url(#hatch); stroke: #111; stroke-width: .35; }
  .centre { stroke: #b03030; stroke-width: .18; stroke-dasharray: 6 1.5 1 1.5; fill: none; }
  .dim { stroke: #1a4fa0; stroke-width: .18; }
  .thin { stroke: #555; stroke-width: .15; }
  .frame { fill: none; stroke: #111; stroke-width: .35; }
  .t { font: 3.2px "PingFang SC", "Noto Sans CJK SC", sans-serif; fill: #111; }
  .tb { font: bold 4.2px "PingFang SC", "Noto Sans CJK SC", sans-serif; fill: #111; }
  .tr { font: bold 3.2px "PingFang SC", "Noto Sans CJK SC", sans-serif; fill: #b00000; }"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">
<defs>
  <marker id="a" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
    <path d="M0,2 L10,5 L0,8 z" fill="#1a4fa0"/></marker>
  <pattern id="hatch" width="2" height="2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect width="2" height="2" fill="#f4f4f4"/><line x1="0" y1="0" x2="0" y2="2" stroke="#333" stroke-width=".25"/></pattern>
  <style>{style}</style>
</defs>
<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>
<rect x="5" y="5" width="{W - 10}" height="{H - 10}" class="frame"/>
{chr(10).join(el)}
</svg>
"""
