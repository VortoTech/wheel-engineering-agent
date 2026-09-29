"""REPORT.md is written from the run's own files: sources, questions, checks and failures as recorded."""
import json

from wheelcam.delivery_report import markdown, summarize, write


def _run(tmp_path, ok=True, text=None):
    run = tmp_path / "run"
    (run / "reconstruct").mkdir(parents=True)
    (run / "machining").mkdir()
    steps = [{"step": "reconstruct", "ok": True, "seconds": 1.0},
             {"step": "machining", "ok": ok, "seconds": 2.0, **({} if ok else {"error": "ValueError: 加工级 STEP 校验未通过"})}]
    (run / "chain.json").write_text(json.dumps({
        "text": text, "spec": {"diameter_in": 20, "width_in": 9, "et_mm": 35, "pcd_mm": 114.3, "bolts": 5},
        "hole_form": None, "seconds": 3.0, "steps": steps, "manufacturing_status": "not_released"}))
    (run / "reconstruct/engineering_report.json").write_text(json.dumps({
        "readiness": "L0", "questions": ["请提供斜视图"], "unknown": {"material": "材料未知。"},
        "engineering_understanding": {"spec_evidence": {"pcd_mm": {"source": "drawing", "reference": "确认单-01"}},
                                      "evidence_groups": {"observed": ["spokes"]}},
        "parameters": {"diameter_in": {"source": "user"}},
        "checks": {"offset_et": {"pass": True, "measured_mm": 35.0}}}))
    (run / "machining/machining_report.json").write_text(json.dumps({
        "checks": {"step_roundtrip": {"pass": ok, "relative_volume_delta": 1e-9}},
        "adjustments": ["锥座深度上提，须工程师确认。"], "in_step": ["螺栓孔与锥座"], "not_in_step": ["外圈盲槽"]}))
    return run


def test_photo_report_keeps_sources_questions_and_boundaries(tmp_path):
    run = _run(tmp_path)
    text = write(run).read_text()
    assert "| PCD mm | 114.3 | 图纸/确认单（确认单-01） |" in text
    assert "| 宽度 J | 9 | 来源未说明 |" in text              # supplied without evidence is not a measurement
    assert "| 中心孔 mm | 未知 |" in text
    assert "未给（模板孔，待确认）" in text
    assert "请提供斜视图" in text and "加工调整：锥座深度上提" in text
    assert "`not_released`" in text and "外圈盲槽" in text
    assert json.loads((run / "report_summary.json").read_text())["steps_ok"] == 2


def test_failed_step_is_reported_not_hidden(tmp_path):
    s = summarize(_run(tmp_path, ok=False))
    text = markdown(s)
    assert "1/2 步通过" in text and "## 失败的步骤" in text and "加工级 STEP 校验未通过" in text
    assert "**未通过**" in text


def test_text_report_uses_text_provenance(tmp_path):
    text = markdown(summarize(_run(tmp_path, text="做一个 20 寸 5 辐直辐轮毂")))
    assert "| 直径 inch | 20 | 用户提供 |" in text
    assert "输入：文字" in text
