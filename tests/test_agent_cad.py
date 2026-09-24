from copy import deepcopy

import pytest
from pydantic import ValidationError

from wheelcam.agent_cad import AgentCadPlan, ConfirmedEvidenceConflict, evaluate_plan
from wheelcam.models import WheelSpec, default_sources


def plan(*actions):
    return AgentCadPlan(base_revision=3, goal="根据正视图修正轮辐", actions=list(actions))


def test_agent_can_edit_noncritical_parameter_without_code_execution():
    candidate = plan({
        "id": "curve-1", "operation": "set_parameter", "target": "face_curve", "value": .7,
        "source": "inferred", "confidence": .72, "rationale": "重投影轮廓更接近参考图",
        "evidence_refs": ["image:front:sha256-example"],
    })
    result = evaluate_plan(WheelSpec().model_dump(), default_sources(), candidate)
    assert result["can_apply"] is True
    assert result["proposed_spec"]["face_curve"] == .7
    assert result["proposed_sources"]["face_curve"]["kind"] == "inferred"
    assert result["proposed_sources"]["face_curve"]["confidence"] == .72


def test_critical_parameter_and_replacement_sketch_require_approval():
    candidate = plan(
        {"id": "method-1", "operation": "set_parameter", "target": "spoke_method", "value": "window",
         "source": "inferred", "confidence": .8, "rationale": "使用窗口草图构造"},
        {"id": "pcd-1", "operation": "set_parameter", "target": "bolt_circle_mm", "value": 112,
         "source": "observed", "confidence": .91, "rationale": "孔圆候选；尚无标尺"},
        {"id": "sketch-1", "operation": "replace_sketch", "target": "window_outlines_mm",
         "value": [[[100, -10], [110, -8], [120, -5], [130, 0], [120, 5], [110, 8], [100, 10], [95, 0]]],
         "source": "observed", "confidence": .8, "rationale": "正视图分割轮廓"},
    )
    preview = evaluate_plan(WheelSpec().model_dump(), default_sources(), candidate)
    assert preview["can_apply"] is False
    assert preview["pending_approval_action_ids"] == ["method-1", "pcd-1", "sketch-1"]
    approved = evaluate_plan(
        WheelSpec().model_dump(), default_sources(), candidate, {"method-1", "pcd-1", "sketch-1"})
    assert approved["can_apply"] is True


def test_unknown_and_measurement_request_do_not_invent_dimensions():
    candidate = plan(
        {"id": "unknown-back", "operation": "mark_unknown", "target": "hub_thickness_mm",
         "rationale": "正视图不可观测背面厚度"},
        {"id": "ask-back", "operation": "request_measurement", "target": "hub_thickness_mm",
         "rationale": "制造关键尺寸缺失", "question": "请测量安装面至中心盘正面的厚度。"},
    )
    result = evaluate_plan(WheelSpec().model_dump(), default_sources(), candidate)
    assert result["proposed_sources"]["hub_thickness_mm"]["kind"] == "unknown"
    assert result["proposed_spec"]["hub_thickness_mm"] == WheelSpec().hub_thickness_mm
    assert result["can_build"] is False
    assert result["measurement_requests"][0]["question"].startswith("请测量")


def test_agent_cannot_target_unknown_field_or_repeat_mutation():
    with pytest.raises(ValidationError, match="不在 WheelSpec"):
        plan({"id": "bad", "operation": "mark_unknown", "target": "shell_command", "rationale": "bad"})
    with pytest.raises(ValidationError, match="不能多次修改"):
        plan(
            {"id": "a", "operation": "mark_unknown", "target": "offset_et_mm", "rationale": "unknown"},
            {"id": "b", "operation": "set_parameter", "target": "offset_et_mm", "value": 30,
             "source": "inferred", "confidence": .4, "rationale": "guess"},
        )
    with pytest.raises(ValidationError, match="replace_sketch"):
        plan({"id": "bad-sketch", "operation": "set_parameter", "target": "window_outlines_mm", "value": [],
              "source": "inferred", "confidence": .5, "rationale": "bypass"})


def test_agent_can_request_only_registered_deterministic_tools():
    candidate = plan({
        "id": "fit", "operation": "request_tool", "target": "fit_window_sketch",
        "rationale": "密集坐标应由本地标注拟合工具生成",
    })
    preview = evaluate_plan(WheelSpec().model_dump(), default_sources(), candidate)
    assert preview["tool_requests"] == [{
        "action_id": "fit", "tool": "fit_window_sketch", "rationale": "密集坐标应由本地标注拟合工具生成",
    }]
    assert preview["can_build"] is False
    with pytest.raises(ValidationError, match="不在受控工具注册表"):
        plan({"id": "bad", "operation": "request_tool", "target": "run_python", "rationale": "bad"})


@pytest.mark.parametrize("source_kind", ["manual", "drawing", "measurement", "specified"])
@pytest.mark.parametrize("target", ["face_curve", "bolt_circle_mm"])
@pytest.mark.parametrize("operation", ["set_parameter", "mark_unknown"])
def test_confirmed_evidence_cannot_be_overwritten_even_with_approval(source_kind, target, operation):
    spec, sources = WheelSpec().model_dump(), default_sources()
    # A low confidence does not let the agent revoke user-supplied evidence.
    sources[target] = {"kind": source_kind, "confidence": .3, "note": "用户提供；有冲突时需复测"}
    before = deepcopy((spec, sources))
    action = {"id": "overwrite", "operation": operation, "target": target, "rationale": "照片不一致"}
    if operation == "set_parameter":
        action.update(value=.7 if target == "face_curve" else 112, source="inferred", confidence=.99)
    candidate = plan(action)
    # Preview and repeated apply attempts must both reject; approval is no bypass.
    for approvals in [(), {"overwrite"}, {"overwrite"}]:
        with pytest.raises(ConfirmedEvidenceConflict) as caught:
            evaluate_plan(spec, sources, candidate, approvals)
        assert caught.value.detail["code"] == "confirmed_evidence_locked"
        assert caught.value.conflicts == [{
            "action_id": "overwrite", "operation": operation,
            "target": target, "source_kind": source_kind,
        }]
        assert (spec, sources) == before


def test_same_value_parameter_edit_cannot_launder_confirmed_source():
    spec, sources = WheelSpec().model_dump(), default_sources()
    sources["face_curve"] = {"kind": "manual", "confidence": 1, "note": "人工确认"}
    candidate = plan({
        "id": "same", "operation": "set_parameter", "target": "face_curve", "value": spec["face_curve"],
        "source": "observed", "confidence": .9, "rationale": "只是更换来源",
    })
    with pytest.raises(ConfirmedEvidenceConflict, match="face_curve"):
        evaluate_plan(spec, sources, candidate)
    assert sources["face_curve"]["kind"] == "manual"


def test_confirmed_sketch_blocks_whole_plan_without_mutating_other_parameters():
    outline = [[100, -10], [110, -8], [120, -5], [130, 0], [120, 5], [110, 8], [100, 10], [95, 0]]
    spec = WheelSpec(spoke_method="window", window_outlines_mm=[outline]).model_dump(mode="json")
    sources = default_sources()
    sources["window_outlines_mm"] = {"kind": "drawing", "confidence": 1, "note": "已确认工程草图"}
    before = deepcopy((spec, sources))
    candidate = plan(
        {"id": "curve", "operation": "set_parameter", "target": "face_curve", "value": .7,
         "source": "inferred", "confidence": .8, "rationale": "不应部分应用"},
        {"id": "sketch", "operation": "replace_sketch", "target": "window_outlines_mm", "value": [],
         "source": "inferred", "confidence": .8, "rationale": "不允许清空已确认草图"},
    )
    with pytest.raises(ConfirmedEvidenceConflict, match="window_outlines_mm"):
        evaluate_plan(spec, sources, candidate, {"sketch"})
    assert (spec, sources) == before


def test_agent_may_question_confirmed_evidence_without_replacing_it():
    spec, sources = WheelSpec().model_dump(), default_sources()
    sources["bolt_circle_mm"] = {"kind": "measurement", "confidence": 1, "note": "卡尺测量"}
    before = deepcopy((spec, sources))
    candidate = plan({
        "id": "verify", "operation": "request_measurement", "target": "bolt_circle_mm",
        "rationale": "照片与实测不一致，保留实测来源", "question": "请复核 PCD 测量值和测量方法。",
    })
    result = evaluate_plan(spec, sources, candidate)
    assert result["proposed_spec"] == spec
    assert result["proposed_sources"] == sources
    assert result["can_build"] is False
    assert (spec, sources) == before
