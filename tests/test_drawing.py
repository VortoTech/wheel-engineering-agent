"""Engineering drawing SVG of a mesh-built wheel."""
from test_mesh_build import outline_recipe

from wheelcam.forged_blank import hole_form, recipe_from_dict


def test_drawing_has_views_dimensions_and_title_block():
    import xml.etree.ElementTree as ET
    from wheelcam.drawing import drawing_svg, section_polygons
    from wheelcam.mesh_build import build
    p = recipe_from_dict({**outline_recipe(), **hole_form("15X32X60")})
    body, _ = build(p)
    sec = section_polygons(body)
    s = [x for poly in sec for x, _ in poly]
    assert max(s) > p.lip_r - 2 and min(s) < -(p.lip_r - 2)                 # the cut spans the wheel
    svg = drawing_svg(body, p, spec={"diameter_in": 20, "width_in": 10.5, "et_mm": 15}, order={"blank": "B-1"},
                      drawing_no="case-00")
    ET.fromstring(svg)                                                         # well-formed
    for text in (f"Ø{2 * p.lip_r:.1f}", "ET ", f"PCD Ø{p.pcd:g}", "Ø32×60° 锥座", "A-A", "未发布", "B-1", "case-00"):
        assert text in svg, text
