"""Generic, bounded CSG interpreter. No wheel templates, inference, or executable model code."""
import math

SCHEMA = '''Return JSON only: {"known": {parameter_name: supplied_value}, "unknown": [parameter_name],
"assumptions": [text], "geometry": node}.
Units mm. Z is the rotation axis. Nodes:
{"op":"cylinder","r":positive,"h":positive,"xyz":[x,y,z]} (base at xyz),
{"op":"box","size":[x,y,z],"xyz":[x,y,z],"angle":degrees} (centred box, rotate about Z before translating),
{"op":"revolve","profile":[[radius,z],...]} (closed radial section revolved about Z),
{"op":"extrude","points":[[x,y],...],"h":positive,"z":base},
{"op":"union","items":[node,...]}, {"op":"cut","base":node,"tools":[node,...]},
{"op":"radial","count":integer,"node":node} (union of rotated instances about Z).
No expressions, Python, file paths or references. Maximum 160 nodes, depth 12, 64 points per polygon,
32 radial copies, coordinates within +/-1500, positive sizes <=1500. Use sensible intersections
for connected solids. Output an editable operation tree representing the requested object.'''


def build(tree):
    import cadquery as cq
    count = 0

    def number(v, positive=False):
        if type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1500 or (positive and v <= 0):
            raise ValueError('invalid bounded coordinate')
        return float(v)

    def vec(v, n):
        if not isinstance(v, list) or len(v) != n:
            raise ValueError('invalid vector')
        return [number(x) for x in v]

    def visit(n, depth=0):
        nonlocal count
        count += 1
        if count > 160 or depth > 12 or not isinstance(n, dict):
            raise ValueError('CSG limit exceeded')
        op = n.get('op')
        if op == 'cylinder':
            return cq.Solid.makeCylinder(number(n['r'], True), number(n['h'], True), cq.Vector(*vec(n['xyz'], 3)))
        if op == 'box':
            sizes = vec(n['size'], 3)
            if min(sizes) <= 0: raise ValueError('nonpositive box')
            return cq.Workplane('XY').box(*sizes).val().rotate((0,0,0),(0,0,1),number(n.get('angle',0))).translate(vec(n['xyz'],3))
        if op in ('revolve', 'extrude'):
            pts = n['profile'] if op == 'revolve' else n['points']
            if not isinstance(pts,list) or not 3 <= len(pts) <= 64: raise ValueError('polygon limit')
            pts = [vec(p,2) for p in pts]
            w = cq.Workplane('XZ' if op == 'revolve' else 'XY').polyline(pts).close()
            return w.revolve(360,(0,0),(0,1)).val() if op == 'revolve' else w.extrude(number(n['h'],True)).val().translate((0,0,number(n['z'])))
        if op == 'radial':
            copies=n['count']
            if type(copies) is not int or not 1 <= copies <= 32: raise ValueError('copy limit')
            base=visit(n['node'],depth+1)
            return base.fuse(*[base.rotate((0,0,0),(0,0,1),360*i/copies) for i in range(1,copies)]) if copies>1 else base
        if op in ('union','cut'):
            items=n['items'] if op=='union' else n['tools']
            if not isinstance(items,list) or not 1 <= len(items) <= 64: raise ValueError('boolean limit')
            shapes=[visit(x,depth+1) for x in items]
            if op=='union': return shapes[0].fuse(*shapes[1:]) if len(shapes)>1 else shapes[0]
            return visit(n['base'],depth+1).cut(*shapes)
        raise ValueError('unsupported CSG operation')
    return visit(tree).clean()
