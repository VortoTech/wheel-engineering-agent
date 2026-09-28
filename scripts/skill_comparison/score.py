"""Readback evaluator shared by all arms; never imports wheel construction code."""
import math
from pathlib import Path


def evaluate(step, spec, truth):
    import cadquery as cq
    shape=cq.importers.importStep(str(step)).val()
    solids=shape.Solids(); box=shape.BoundingBox()
    volume=shape.Volume()
    target=Path(step).with_name('readback.step')
    cq.exporters.export(shape,str(target)); back=cq.importers.importStep(str(target)).val()
    delta=abs(back.Volume()-volume)/max(abs(volume),1)
    circles=[]
    for edge in shape.Edges():
        if edge.geomType()=='CIRCLE' and abs(edge.Length()-2*math.pi*edge.radius())<.01:
            c=edge.arcCenter(); circles.append({'r':round(edge.radius(),3),'x':round(c.x,3),'y':round(c.y,3),'z':round(c.z,3)})
    # Raw circular-edge evidence is retained. Matching checks are only candidate presence,
    # not a claim that all seats / hole axes or mounting-plane ET have been independently verified.
    cb=spec.get('center_bore_mm')
    bore=[e for e in circles if math.hypot(e['x'],e['y'])<.1 and cb and abs(2*e['r']-cb)<.1]
    bbox={'lip_od_mm':max(box.xlen,box.ylen),'overall_width_mm':box.zlen}
    dimensions={k:{'measured_mm':round(v,3),'truth_mm':truth.get(k),
                   'error_mm':round(v-truth[k],3) if isinstance(truth.get(k),(int,float)) else None} for k,v in bbox.items()}
    return {'valid_single_solid':shape.isValid() and len(solids)==1,'solid_count':len(solids),
            'step_roundtrip':back.isValid() and len(back.Solids())==1 and delta<.001,
            'volume_mm3':round(volume,3),'roundtrip_volume_delta':delta,'dimensions':dimensions,
            'center_bore_circular_edge_candidate':bool(bore) if cb else None,
            'circular_edges':circles,'et_independent_metrology':'unavailable',
            'pcd_hole_axis_metrology':'unavailable','visual_similarity':'unavailable',
            'manufacturing_status':'not_released'}
