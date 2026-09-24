"""Presentation assets. Decorative parts never enter the engineering shape or checks."""
import math
import cadquery as cq

from .preparation import checked_volume
from .mass_properties import volume_method
from .template import layout


def export_previews(wheel, rim, spec, output):
    silver, black = cq.Color(0.65, 0.67, 0.70), cq.Color(0.07, 0.075, 0.085)
    if spec.spoke_style != 'paired':
        # Photo-fitted window bodies use a neutral gunmetal preview so their fillets and draft are
        # legible against white product photos. This changes only GLB appearance, never STEP geometry.
        colour = cq.Color(0.14, 0.15, 0.16) if spec.spoke_method == 'window' else silver
        cq.Assembly(wheel, name='wheel-body', color=colour).export(str(output / 'wheel.glb'), tolerance=0.15, angularTolerance=0.1)
        return None
    shape = wheel.val()
    # Partition the finished shape by the original rim, so colors do not create duplicate material.
    rim_part = shape.copy().intersect(rim.copy())
    face_part = shape.copy().cut(rim.copy())
    rim_volume, face_volume, body_volume = map(checked_volume, (rim_part, face_part, shape))
    total = rim_volume + face_volume
    if total <= 0 or abs(total - body_volume) / total > 1e-5:
        raise ValueError('分色预览的实体分区体积不一致。')
    assembly = cq.Assembly(name='wheel-body')
    assembly.add(rim_part, name='rim-silver', color=silver)
    assembly.add(face_part, name='paired-center-black', color=black)
    assembly.export(str(output / 'wheel.glb'), tolerance=0.15, angularTolerance=0.1)

    lay = layout(spec)
    cap_radius = min(spec.center_bore_mm / 2 + 5, spec.bolt_circle_mm / 2 - lay['lug_pocket_diameter'] / 2 - 3)
    front = lay['hub_front_z']
    cap = cq.Workplane('XY', origin=(0, 0, front + 0.2)).circle(cap_radius).extrude(3).edges('>Z').fillet(0.8)
    refined = bool(lay['front_lip'])
    if refined:
        cap = cq.Workplane('XY', origin=(0, 0, front + 0.2)).circle(spec.hub_diameter_mm / 2 - 4).extrude(1.8)
        holes = [(spec.bolt_circle_mm / 2 * math.cos(2 * math.pi * i / spec.bolt_count),
                  spec.bolt_circle_mm / 2 * math.sin(2 * math.pi * i / spec.bolt_count)) for i in range(spec.bolt_count)]
        cap = cap.faces('>Z').workplane().pushPoints(holes).circle(lay['lug_pocket_diameter'] / 2 + 1.5).cutThruAll()
        cap_radius = spec.center_bore_mm * 0.4
        for i, (x, y) in enumerate(holes):
            insert = (cq.Workplane('XY', origin=(x, y, front - 2)).circle(spec.bolt_diameter_mm / 2 + 3)
                      .circle(spec.bolt_diameter_mm / 2).extrude(1))
            assembly.add(insert, name=f'display-only-lug-insert-{i + 1}', color=silver)
    assembly.add(cap, name='display-only-center-cap', color=black)
    cap_trim = cq.Workplane('XY', origin=(0, 0, front + 3.2)).circle(cap_radius - 2).circle(cap_radius - 2.6).extrude(0.3)
    assembly.add(cap_trim, name='display-only-cap-trim', color=silver)
    radius = spec.rim_diameter_in * 25.4 / 2 - 24
    if refined:
        radius = lay['front_lip']['inner_radius_mm'] - 11
    z = min(f['back'] for f in lay['sections'] if f['r'] >= radius - 12) - 8
    if refined:
        for offset, label in [(9, 'outer'), (-9, 'inner')]:
            bead = (cq.Workplane('XY', origin=(0, 0, z)).circle(radius + offset + 1.5)
                    .circle(radius + offset - 1.5).extrude(5))
            assembly.add(bead, name=f'display-only-{label}-ring-bead', color=black)
    ring = cq.Workplane('XY', origin=(0, 0, z)).circle(radius + 7).circle(radius - 7).extrude(5)
    assembly.add(ring, name='display-only-fastener-ring', color=black)
    head = (cq.Workplane('XY').circle(3.2).extrude(2.5).edges('>Z').fillet(0.5)
            .faces('>Z').workplane().polygon(6, 2.5).cutBlind(-1.2))
    for index in range(40):
        angle = 2 * math.pi * (index + 0.5) / 40
        assembly.add(head, name=f'display-only-rim-bolt-{index + 1:02}', color=silver,
                     loc=cq.Location(cq.Vector(radius * math.cos(angle), radius * math.sin(angle), z + 5)))
    assembly.export(str(output / 'presentation.glb'), tolerance=0.15, angularTolerance=0.1)
    return {'status': 'display_only', 'decorative_fastener_count': 40,
            'partition_check': {'volume_method': volume_method(), 'body_volume_mm3': body_volume,
                                'rim_volume_mm3': rim_volume, 'face_volume_mm3': face_volume,
                                'relative_residual': abs(total - body_volume) / total},
            'attachments': ['center_cap', 'cap_trim', 'fastener_ring', 'rim_fasteners'] + (['lug_inserts', 'ring_beads'] if refined else []),
            'style': 'photo-fit-v2' if refined else 'photo-fit-v1',
            'excluded_from': ['wheel.step', 'body_volume', 'weight', 'caliper_check', 'stock_check', 'machining_features'],
            'note': '附件尺寸、数量和位置为外观假设；未设计连接孔、密封或分体装配，不代表可制造的分体轮毂'}
