"""The private-asset intake must distinguish design variants from duplicate files."""

from scripts.asset_eval_intake import cad_filename_spec, inventory


def test_cad_filename_spec_is_only_a_candidate():
    examples = {
        "HN191 20X10-5X112-8-66.6.x_t": (20, 10, 5, 112, 8, 66.6),
        "M59-2010.5-5-112-15-66.6.x_t": (20, 10.5, 5, 112, 15, 66.6),
        "M61-2210-6-139.7-(-5)-93.4.x_t": (22, 10, 6, 139.7, -5, 93.4),
    }
    for name, expected in examples.items():
        item = cad_filename_spec(name)
        assert item["source"] == "filename_unverified"
        assert tuple(item[key] for key in ("diameter_in", "width_in", "bolts", "pcd_mm", "et_mm",
                                          "center_bore_mm")) == expected
    assert cad_filename_spec("unlabeled.x_t") is None


def test_inventory_detects_duplicate_truth_without_copying_assets(tmp_path):
    root = tmp_path / "source"
    a = root / "HN191"
    b = root / "HN192"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "HN191 20X10-5X112-8-66.6.x_t").write_bytes(b"same design")
    (b / "HN192 20X10-5X112-8-66.6.x_t").write_bytes(b"same design")
    (a / "front.png").write_bytes(b"different render")
    report = inventory(root)
    assert report["summary"]["model_count"] == 2
    assert report["summary"]["unique_cad_truth_count"] == 1
    assert report["summary"]["duplicate_cad_groups"] == 1
    assert report["summary"]["kinds"] == {"cad_truth": 2, "render": 1}
    assert report["files"][0]["relative_path"].startswith("HN191/")
