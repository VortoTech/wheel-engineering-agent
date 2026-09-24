"""Evidence-lock integration tests: fake providers, no external model calls."""
import io
import json

import pytest
from fastapi.testclient import TestClient

from wheelcam.agent_cad import AgentCadPlan
from wheelcam.agent_orchestrator import OpenAICompatibleAgentProvider
from wheelcam.app import create_app
from wheelcam.models import WheelSpec, default_sources


@pytest.mark.parametrize("target,source_kind,operation", [
    ("face_curve", "manual", "set_parameter"),
    ("face_curve", "drawing", "mark_unknown"),
    ("bolt_circle_mm", "measurement", "set_parameter"),
    ("bolt_circle_mm", "measurement", "mark_unknown"),
])
def test_preview_apply_and_propose_reject_confirmed_evidence_without_persistence(
        tmp_path, target, source_kind, operation):
    class ConflictingProvider:
        def status(self):
            return {"configured": True, "model": "test-fixture", "mode": "mock_provider"}

        def propose(self, project, goal, image_path):
            action = {"id": "conflict", "operation": operation, "target": target,
                      "rationale": "模型与已确认来源矛盾"}
            if operation == "set_parameter":
                # An unchanged numeric value must not bypass the provenance lock.
                action.update(value=project["spec"][target], source="inferred", confidence=.99)
            return AgentCadPlan(base_revision=project["revision"], goal=goal, actions=[action])

    provider = ConflictingProvider()
    with TestClient(create_app(tmp_path, start_worker=False, agent_provider=provider)) as client:
        project = client.post("/api/projects", json={"name": "证据保护"}).json()
        url = f"/api/projects/{project['id']}"
        project["sources"][target] = {"kind": source_kind, "confidence": 1, "note": "用户确认来源"}
        updated = client.put(url, json={
            "name": project["name"], "spec": project["spec"], "sources": project["sources"],
            "expected_revision": project["revision"],
        })
        assert updated.status_code == 200
        original = updated.json()
        plan = provider.propose(original, "复核照片和参数", None).model_dump(mode="json")
        calls = [
            ("preview", plan),
            ("apply", {"plan": plan, "approved_action_ids": ["conflict"]}),
            ("apply", {"plan": plan, "approved_action_ids": ["conflict"]}),
            ("propose", {"expected_revision": original["revision"], "goal": "复核照片和参数"}),
        ]
        for endpoint, payload in calls:
            response = client.post(f"{url}/agent-cad/{endpoint}", json=payload)
            assert response.status_code == 409, response.text
            detail = response.json()["detail"]
            assert detail["code"] == "confirmed_evidence_locked"
            assert detail["conflicts"][0]["target"] == target
            unchanged = client.get(url).json()
            assert unchanged["spec"] == original["spec"]
            assert unchanged["sources"] == original["sources"]
            assert unchanged["revision"] == original["revision"]
            assert unchanged["agent_cad_runs"] == []


def test_provider_context_names_protected_fields_and_remeasurement_policy(monkeypatch):
    captured = {}
    sources = default_sources()
    sources["face_curve"] = {"kind": "manual", "confidence": 1, "note": "人工确认"}
    sources["bolt_circle_mm"] = {"kind": "measurement", "confidence": 1, "note": "实测"}
    project = {"revision": 1, "spec": WheelSpec().model_dump(), "sources": sources}
    safe = AgentCadPlan(base_revision=1, goal="保留测量证据", actions=[{
        "id": "verify", "operation": "request_measurement", "target": "bolt_circle_mm",
        "rationale": "需要复测", "question": "请复核 PCD。",
    }])

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": safe.model_dump_json()}}]}).encode())

    monkeypatch.setattr("wheelcam.agent_orchestrator.urlopen", fake_urlopen)
    provider = OpenAICompatibleAgentProvider("https://example.invalid/v1", "test-fixture")
    returned = provider.propose(project, "保留测量证据", None)
    assert returned == safe
    system = captured["messages"][0]["content"]
    context = json.loads(captured["messages"][1]["content"][0]["text"])
    assert "same value or approval" in system
    assert "request_measurement instead" in system
    assert context["protected_parameters"] == {"face_curve": "manual", "bolt_circle_mm": "measurement"}
