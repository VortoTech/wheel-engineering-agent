"""Bounded, non-mutating evidence tools for CAD proposals."""
import json
import os
from pathlib import Path

from .agent_cad import evaluate_plan
from .agent_orchestrator import _compact_analysis
from .models import WheelSpec


def execute_tool(store, project, name):
    if name == "compare_latest_build":
        job = next((j for j in project.get("jobs", []) if j["status"] == "succeeded"), None)
        if not job:
            return {"status": "unavailable", "reason": "没有成功构建。"}
        report = job.get("report") or {}
        return {"status": "partial", "job_id": job["id"],
                "checks": report.get("checks"), "limitations": report.get("limitations"),
                "build_status": report.get("build_status", "unverified"),
                "feature_results": report.get("build_resolution", {}).get("feature_results", []),
                "matches_current_spec": job["snapshot"]["spec"] == project["spec"],
                "visual_comparison": "not_tested",
                "reason": "已读取几何报告；没有独立轮廓标注时不能报告视觉相似度。"}
    ref = next((i for i in project["images"] if i["id"] == project["primary_image_id"]), None)
    if not ref:
        return {"status": "unavailable", "reason": "缺少主参考图。"}
    spec = WheelSpec(**project["spec"])
    if name == "analyze_primary_image":
        from .vision import detect
        result = detect(store.root / "images" / (ref["id"] + ".jpg"), spec, None)
    elif name == "fit_window_sketch":
        from .window_fit import candidate
        path = Path(os.getenv("WHEELCAM_LABEL_DIR", str(store.root / "annotations"))) / (ref["sha256"] + ".json")
        if not path.is_file():
            return {"status": "unavailable", "reason": "当前原图缺少窗口标注，需先补充标注。"}
        result = candidate(json.loads(path.read_text()), spec)
    else:
        raise ValueError("未注册的 Agent 工具。")
    summary = _compact_analysis(result)
    summary.update(can_apply=result.get("can_apply", False), image_sha256=ref["sha256"],
                   base_revision=project["revision"])
    proposed = result.get("suggested_parameters", {})
    summary["changes_current_spec"] = any(project["spec"].get(k) != v for k, v in proposed.items())
    return summary


def propose_with_tools(provider, store, project, goal, image_path, max_rounds=2):
    """Run at most two tool rounds; all mutations remain in the final preview."""
    context = dict(project)
    trace, seen = [], set()
    for iteration in range(max_rounds + 1):
        plan = provider.propose(context, goal, image_path)
        if plan.base_revision != project["revision"]:
            raise ValueError("Agent 返回了过期 revision。")
        evaluate_plan(project["spec"], project["sources"], plan)
        requested = list(dict.fromkeys(a.target for a in plan.actions if a.operation == "request_tool"))
        pending = [name for name in requested if name not in seen]
        if not pending or iteration == max_rounds:
            return plan, trace
        for name in pending:
            try:
                result = execute_tool(store, project, name)
            except (ValueError, OSError, KeyError) as exc:
                result = {"status": "failed", "reason": str(exc)}
            trace.append({"tool": name, "round": iteration + 1, "result": result})
            seen.add(name)
        context["agent_tool_results"] = trace
    raise AssertionError("unreachable")
