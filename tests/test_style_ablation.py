import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("style_ablation", Path(__file__).parents[1] / "scripts/evaluate_style_ablation.py")
ablation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ablation)


def test_visual_improvement_does_not_hide_engineering_regression():
    result = ablation.compare({"pcd": 112}, {"pcd": 114.3},
                             {"solid": {"pass": True}}, {"solid": {"pass": False}},
                             {"edge_mm": 4, "window_iou": .9}, {"edge_mm": 2, "window_iou": .8})
    assert result["edge_reduction_percent"] == 50
    assert result["window_iou_delta"] == -.1
    assert result["valid_pair"] is False
    assert result["changed_locked_fields"] == ["pcd"]
    assert result["checks_failed"]["candidate"] == ["solid"]


def test_missing_checks_cannot_pass():
    result = ablation.compare({}, {}, {}, {}, {"edge_mm": 0, "window_iou": 1}, {"edge_mm": 0, "window_iou": 1})
    assert result["valid_pair"] is False
    assert result["edge_reduction_percent"] is None
