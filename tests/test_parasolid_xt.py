"""Parasolid text reader on a made-up transmit file (no real CAD in the repo)."""
import math

import pytest

from wheelcam.parasolid_xt import read_xt, symmetry_order, wheel_truth


def fake_xt(*, width_in=10.5, et=15.0, bolts=5, pcd=112.0, hole=15.0, bore=66.6, lip_r=273.9, spokes=5):
    """A Parasolid-like text file holding the records the reader uses, in metres, wrapped at 80."""
    recs, idx = [], [100]

    def add(code, ints, sense, floats):
        idx[0] += 1
        head = f"{code} {idx[0]} {idx[0] + 5000} " + " ".join(str(v) for v in ints)
        nums = " ".join(f"{v:.10g}" for v in floats)
        recs.append(f"{head} {sense}{nums}" if sense else f"{head} {nums}")

    m = lambda v: v / 1000
    half = width_in * 25.4 / 2
    for z in (-half, half):                                   # flange inner faces, each with a flange edge
        add(50, [0, 1, 0, 0, 1], "+", [0, 0, m(z), 0, 0, 1, 1, 0, 0])
        add(31, [0, 1, 0, 0, 1], "", [0, 0, m(z), 0, 0, 1, 1, 0, 0, m(lip_r - 8)])
    for z in (-half - 13, half + 13):                         # the lip ends
        add(31, [0, 1, 0, 0, 1], "", [0, 0, m(z), 0, 0, 1, 1, 0, 0, m(lip_r)])
    add(50, [0, 1, 0, 0, 1], "+", [0, 0, m(et), 0, 0, -1, 1, 0, 0])            # mounting face
    add(31, [0, 1, 0, 0, 1], "", [0, 0, m(et), 0, 0, 1, 1, 0, 0, m(bore / 2 + 3)])
    add(31, [0, 1, 0, 0, 1], "", [0, 0, m(et + 3), 0, 0, 1, 1, 0, 0, m(bore / 2)])
    add(31, [0, 1, 0, 0, 1], "", [0, 0, m(et + 30), 0, 0, 1, 1, 0, 0, m(bore / 2 - 5)])   # cap recess, higher up
    for i in range(bolts):
        a = 2 * math.pi * i / bolts
        x, y = m(pcd / 2 * math.cos(a)), m(pcd / 2 * math.sin(a))
        add(51, [0, 1, 0, 0, 1], "-", [x, y, 0, 0, 0, 1, m(hole / 2), 1, 0, 0])
        add(52, [0, 1, 0, 0, 1], "-", [x, y, m(et + 10), 0, 0, 1, m(11), .5, math.sqrt(.75), 1, 0, 0])
        add(52, [0, 1, 0, 0, 1], "-", [x, y, m(et - 5), 0, 0, 1, m(hole / 2), math.sqrt(.5), math.sqrt(.5), 1, 0, 0])
    for i in range(3):                                        # decorative holes: same radius, uneven
        a = [.3, 1.1, 1.5][i]
        add(51, [0, 1, 0, 0, 1], "+", [m(80 * math.cos(a)), m(80 * math.sin(a)), 0, 0, 0, 1, m(hole / 2), 1, 0, 0])
    for i in range(spokes * 8):                               # spoke vertices
        a = 2 * math.pi * (i // 8) / spokes + .05 * (i % 8)
        r = 110 + 12 * (i % 8)
        add(29, [0, 1, 0, 0], "", [m(r * math.cos(a)), m(r * math.sin(a)), m(40)])
    body = " ".join(recs)
    wrapped = "\n".join(body[i:i + 80] for i in range(0, len(body), 80))    # tokens split across lines
    header = ("**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**\n**PARASOLID**\n**PART1;\nAPPL=test;\n"
              "**PART2;\nSCH=SCH_3000198_30000;\n**PART3;\n**END_OF_HEADER*****\n")
    return header + "T51 : TRANSMIT FILE created by modeller version 300019817 SCH_1900000_190080 10\n" + wrapped + "\n"


def test_reads_analytic_records_across_wrapped_lines():
    g = read_xt(fake_xt())
    assert len(g["cylinders"]) == 8 and len(g["cones"]) == 10 and len(g["planes"]) == 3
    assert g["cylinders"][0][6] == pytest.approx(7.5)                     # mm


def test_wheel_truth_matches_the_order():
    t = wheel_truth(read_xt(fake_xt()))
    assert t["width_in"] == pytest.approx(10.5) and t["et_mm"] == pytest.approx(15.0)
    assert (t["bolts"], t["pcd_mm"], t["bolt_hole_d_mm"], t["seat_cone_deg"]) == (5, 112.0, 15.0, 60.0)
    assert t["center_bore_mm"] == pytest.approx(66.6) and t["lip_od_mm"] == pytest.approx(547.8)
    assert t["rotational_order"] == 5
    t = wheel_truth(read_xt(fake_xt(width_in=10, et=-5, bolts=6, pcd=139.7, bore=93.4, spokes=12)))
    assert (t["width_in"], t["et_mm"], t["bolts"], t["pcd_mm"], t["rotational_order"]) == (10.0, -5.0, 6, 139.7, 12)


def test_symmetry_order_is_the_fundamental():
    angles = [2 * math.pi * k / 10 + d for k in range(10) for d in (0, .1, .13)]
    assert symmetry_order(angles) == 10


def test_rejects_other_files():
    with pytest.raises(ValueError):
        read_xt("ISO-10303-21;")
