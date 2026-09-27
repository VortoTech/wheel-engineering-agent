"""Privacy scrubbing. All names, machines and paths here are made up."""
import numpy as np
from PIL import Image

from wheelcam.privacy import clean_image, leaks, order_from_rows, scan, scrub_step, scrub_xt

XT = """**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************
**PARASOLID !"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************
**PART1;
MC=x64/Windows NT;
MC_MODEL=some cpu @ 2.50ghz;
MC_ID=desktop-fake01;
OS=windows nt (x64);
USER=alice;
APPL=unigraphics;
FORMAT=text;
KEY=Z99-TEST CAR-2010-5-112-15-66.6;
FILE=D:\\Sync\\客户甲\\Z99 test.x_t;
DATE=1-jan-2026;
**PART2;
SCH=SCH_3000198_30000;
**PART3;
**END_OF_HEADER*****************************************************************
T51 : TRANSMIT FILE created by modeller version 300019817 SCH_1900000_190080 10
 1 7 0 0 29 68 113733 0 71 96 0 .0041 -.2376 .1042
"""


def test_scrub_xt_keeps_format_and_geometry_drops_machine_user_and_paths():
    out = scrub_xt(XT)
    for secret in ("desktop-fake01", "alice", "客户甲", "some cpu", "Z99-TEST", "1-jan-2026"):
        assert secret not in out
    assert "APPL=unigraphics;" in out and "SCH=SCH_3000198_30000;" in out and "FORMAT=text;" in out
    assert out.split("**END_OF_HEADER")[1] == XT.split("**END_OF_HEADER")[1]


def test_scrub_step_redacts_file_name_entity():
    step = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\n"
            "FILE_NAME('C:/Users/alice/z99.stp','2026-01-01T00:00:00',('alice'),('Fake Forging Co'),"
            "'NX 2306','NX','bob');\nFILE_SCHEMA(('AP214'));\nENDSEC;\n")
    out = scrub_step(step)
    assert not leaks(out, ["alice", "Fake Forging", "bob", "z99.stp"])
    assert "'2026-01-01T00:00:00'" in out and "FILE_SCHEMA" in out


def test_order_sheet_keeps_engineering_fields_only():
    factory = [["某某锻造轮毂客户确认单"], ["客户名称：", "测试客户", "收货地址："], ["产品描述"],
               ["产品型号", "规格SIZE", "偏距ET", "中心孔CB", "孔距H/PCD", "PCD孔型", "颜色", "数量", "毛坯"],
               [None, "20X10.5", 15, 66.6, "5/112", "15X32X60", "GLOSS BLACK", 2, "BLANK-2010"],
               [None, "21*11.5", 17, 66.6, "5*112", "15X32X60", "GLOSS BLACK", 2, None],
               ["刻字要求", "安装面刻字：ZZ-Z99 20X10.5 ET15 MAX LOAD 760KG FORGED"],
               ["气门嘴角度", 25], ["PCD孔深度", "8.0MM"], ["审核：", "张三", "制表：", "李四"]]
    order = order_from_rows(factory)
    assert order["variants"][0] == {"diameter_in": 20, "width_in": 10.5, "et_mm": 15.0, "center_bore_mm": 66.6,
                                    "bolts": 5, "pcd_mm": 112.0, "hole_form": "15X32X60", "finish": "GLOSS BLACK",
                                    "qty": 2, "blank": "BLANK-2010"}
    assert order["variants"][1]["width_in"] == 11.5 and order["variants"][1]["et_mm"] == 17.0
    assert order["valve_angle_deg"] == "25" and order["pcd_hole_depth"] == "8.0MM" and order["max_load_kg"] == 760
    assert not leaks(repr(order), ["测试客户", "张三", "李四", "ZZ-Z99"])
    customer = [["产品型号", "规格SIZE", "偏距ET", "孔距H/PCD", "中心孔CB", "颜色 finish color ", "数量QTY", "孔型", "",
                 "单价（元）", "SPECIAL NOTES:"],
                ["FRONTS", "20X10", 8.0, "5X112", "66.6", "SATIN BLACK", 2.0, "", "", "", "MAX CONCAVE"],
                ["REARS", "21X11", 15.0, "5X112", "66.6", "SATIN BLACK", 2.0, "", "", "", ""]]
    order = order_from_rows(customer)
    assert [v["position"] for v in order["variants"]] == ["front", "rear"]
    assert order["variants"][1]["center_bore_mm"] == 66.6 and order["style_notes"] == ["MAX CONCAVE"]


def test_clean_image_drops_metadata_and_blurs(tmp_path):
    src = tmp_path / "in.jpg"
    im = Image.fromarray((np.indices((80, 80)).sum(0) % 2 * 255).astype("uint8")).convert("RGB")
    exif = Image.Exif()
    exif[0x013B] = "alice"                               # Artist
    im.save(src, exif=exif.tobytes())
    assert "alice" in Image.open(src).getexif().get(0x013B, "")
    out = clean_image(src, tmp_path / "out.jpg", blur=[(40, 40, 15)])
    back = Image.open(out)
    assert 0x013B not in back.getexif() and not scan([out], ["alice"])
    a = np.asarray(back.convert("L"), float)
    assert a[35:45, 35:45].std() < a[0:10, 0:10].std() / 3       # the checkerboard is blurred there only


def test_scan_reports_files_holding_terms(tmp_path):
    (tmp_path / "a.json").write_text('{"note": "from desktop-fake01"}')
    (tmp_path / "b.json").write_text('{"note": "clean"}')
    assert scan([tmp_path], ["DESKTOP-FAKE01"]) == {str(tmp_path / "a.json"): ["DESKTOP-FAKE01"]}


def test_repository_holds_no_private_terms():
    """Tracked files never hold a term of the local deny list (skipped where there is none)."""
    import subprocess
    from pathlib import Path

    import pytest
    from wheelcam.privacy import private_terms
    terms = private_terms()
    if not terms:
        pytest.skip("no local deny list (~/.wheelcam/private_terms.txt)")
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True,
                             text=True, check=True).stdout.split()
    found = scan([root / f for f in tracked if (root / f).is_file()], terms)
    assert not found, sorted(Path(f).relative_to(root).as_posix() for f in found)   # terms themselves not shown
