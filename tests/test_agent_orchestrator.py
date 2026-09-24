import json

import pytest

from wheelcam.agent_orchestrator import (
    AgentProposalRequest,
    OpenAICompatibleAgentProvider,
    _compact_spec,
    _parse_plan_response,
    _provider_safe_plan,
)


def test_primary_image_is_opt_in():
    request = AgentProposalRequest(expected_revision=1, goal="改进轮辐造型")
    assert request.include_primary_image is False


def test_dense_window_sketch_is_summarized_before_model_context():
    compact = _compact_spec({"spoke_count": 5, "window_outlines_mm": [[(1, 2), (3, 4)]]})
    assert "window_outlines_mm" not in compact
    assert compact["window_outlines_summary"]["outline_count"] == 1
    assert compact["window_outlines_summary"]["bounds_mm"] == {
        "x_min": 1, "x_max": 3, "y_min": 2, "y_max": 4,
    }


def test_provider_response_parser_accepts_wrapped_json_and_reports_schema_errors():
    valid = {
        "schema_version": "wheel-agent-cad-plan-v1",
        "base_revision": 1,
        "goal": "safe review",
        "actions": [{
            "id": "measure-depth",
            "operation": "request_measurement",
            "target": "spoke_depth_mm",
            "rationale": "not observable",
            "question": "Please measure spoke depth.",
        }],
    }
    body = {"choices": [{"message": {"content": "Plan:\n```json\n" + json.dumps(valid) + "\n```"},
                         "finish_reason": "stop"}]}
    assert _parse_plan_response(body).actions[0].operation == "request_measurement"

    invalid = dict(valid, actions=[dict(valid["actions"][0], unexpected=True)])
    with pytest.raises(RuntimeError, match="actions.0.unexpected"):
        _parse_plan_response({"choices": [{"message": {"content": json.dumps(invalid)}}]})

    dense = dict(valid, actions=[{
        "id": "sketch", "operation": "replace_sketch", "target": "window_outlines_mm",
        "value": [[[0, 0]] * 8], "source": "observed", "confidence": .8,
        "rationale": "model-generated points",
    }])
    with pytest.raises(RuntimeError, match="fit_window_sketch"):
        _provider_safe_plan({"choices": [{"message": {"content": json.dumps(dense)}}]})


def test_pinpawo_profile_can_be_overridden_without_copying_secret(tmp_path, monkeypatch):
    config_dir = tmp_path / ".pinpawo"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "models": {"profiles": {"primary": {
            "label": "Xiaomi MiMo",
            "provider": "xiaomi-mimo",
            "baseUrl": "https://token-plan.example/v1",
            "model": "mimo-v2.5-pro",
            "apiKey": "test-secret-that-must-not-be-exposed",
            "inputModalities": ["text"],
            "structuredOutputMethod": "jsonMode",
        }}},
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("WHEELCAM_AGENT_PROFILE", "pinpawo:primary")
    monkeypatch.setenv("WHEELCAM_AGENT_MODEL", "mimo-v2.6-pro")
    monkeypatch.setenv("WHEELCAM_AGENT_INPUT_MODALITIES", "text,image")

    provider = OpenAICompatibleAgentProvider.from_environment()
    status = provider.status()

    assert provider.base_url == "https://token-plan.example/v1"
    assert provider.api_key == "test-secret-that-must-not-be-exposed"
    assert provider.json_mode is True
    assert status == {
        "provider": "xiaomi-mimo",
        "configured": True,
        "model": "mimo-v2.6-pro",
        "profile": "Xiaomi MiMo",
        "supports_primary_image": True,
        "mode": "live_provider",
        "thinking": "provider_default",
    }
    assert "api_key" not in status
    assert "test-secret" not in json.dumps(status)
