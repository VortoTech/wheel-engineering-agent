from types import SimpleNamespace

from wheelcam.agent_cad import AgentCadPlan
from wheelcam.agent_tools import propose_with_tools, execute_tool
from wheelcam.models import WheelSpec, default_sources


def test_tool_results_reach_next_model_round_without_mutation(monkeypatch):
    project = {"revision": 1, "spec": WheelSpec().model_dump(mode="json"), "sources": default_sources()}
    calls = []
    def execute(store, current, name):
        calls.append(name)
        return {"status": "ambiguous", "can_apply": False}
    monkeypatch.setattr("wheelcam.agent_tools.execute_tool", execute)
    class Provider:
        def propose(self, context, goal, image):
            if context.get("agent_tool_results"):
                assert context["agent_tool_results"][0]["result"]["can_apply"] is False
                action = {"id": "unknown", "operation": "mark_unknown", "target": "spoke_count", "rationale": "ambiguous"}
            else:
                action = {"id": "detect", "operation": "request_tool", "target": "analyze_primary_image", "rationale": "inspect"}
            return AgentCadPlan(base_revision=1, goal=goal, actions=[action])
    plan, trace = propose_with_tools(Provider(), None, project, "inspect", None)
    assert plan.actions[0].operation == "mark_unknown"
    assert len(trace) == 1 and calls == ["analyze_primary_image"]
    assert project["revision"] == 1 and "agent_tool_results" not in project


def test_repeated_tool_requests_stop(monkeypatch):
    project = {"revision": 1, "spec": WheelSpec().model_dump(), "sources": default_sources()}
    monkeypatch.setattr("wheelcam.agent_tools.execute_tool", lambda *args: {"status": "unavailable"})
    class Provider:
        def propose(self, context, goal, image):
            return AgentCadPlan(base_revision=1, goal=goal, actions=[{
                "id": "fit", "operation": "request_tool", "target": "fit_window_sketch", "rationale": "inspect"}])
    plan, trace = propose_with_tools(Provider(), None, project, "inspect", None)
    assert len(trace) == 1
    assert plan.actions[0].operation == "request_tool"


def test_comparison_does_not_claim_unmeasured_visual_quality(tmp_path):
    project = {"spec": {"x": 1}, "jobs": [{"id": "test", "status": "succeeded",
               "snapshot": {"spec": {"x": 2}}, "report": {"checks": {"valid_brep": True}}}]}
    result = execute_tool(SimpleNamespace(root=tmp_path), project, "compare_latest_build")
    assert result["visual_comparison"] == "not_tested"
    assert result["matches_current_spec"] is False
    assert result["build_status"] == "unverified"


def test_comparison_returns_actual_feature_degradation(tmp_path):
    features = [{"feature": "hub_junction", "requested": 5, "applied": 2, "status": "adjusted"}]
    project = {"spec": {"x": 1}, "jobs": [{"id": "test", "status": "succeeded",
               "snapshot": {"spec": {"x": 1}}, "report": {"checks": {"valid_brep": True},
                   "build_status": "degraded", "build_resolution": {"feature_results": features}}}]}
    result = execute_tool(SimpleNamespace(root=tmp_path), project, "compare_latest_build")
    assert result["build_status"] == "degraded"
    assert result["feature_results"] == features
    assert result["visual_comparison"] == "not_tested"
