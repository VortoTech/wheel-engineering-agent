"""Model-provider adapter that turns an engineering goal into Agent Action IR.

This module never applies a plan. Provider output must validate as
wheel-agent-cad-plan-v1 and then pass evaluate_plan before the UI can approve it.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agent_cad import AgentCadPlan, confirmed_evidence_parameters
from .models import WheelSpec


class AgentProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    goal: str = Field(min_length=3, max_length=1000)
    # Sending a reference image to a remote provider is always explicit opt-in.
    include_primary_image: bool = False


class AgentProvider(Protocol):
    def status(self) -> dict: ...
    def propose(self, project: dict, goal: str, image_path: Path | None) -> AgentCadPlan: ...


def _compact_analysis(value):
    if not value:
        return None
    keys = ("status", "suggested_parameters", "scale", "warnings", "ellipse", "window_fit", "root_fit")
    result = {key: value[key] for key in keys if key in value}
    if isinstance(result.get("suggested_parameters"), dict):
        result["suggested_parameters"] = _compact_spec(result["suggested_parameters"])
    return result


def _compact_spec(value: dict) -> dict:
    """Keep model context bounded; dense sketches are evidence artifacts, not LLM output."""
    compact = dict(value)
    outlines = compact.pop("window_outlines_mm", []) or []
    points = [point for outline in outlines for point in outline]
    compact["window_outlines_summary"] = {
        "outline_count": len(outlines),
        "points_per_outline": [len(outline) for outline in outlines],
        "bounds_mm": ({
            "x_min": min(point[0] for point in points),
            "x_max": max(point[0] for point in points),
            "y_min": min(point[1] for point in points),
            "y_max": max(point[1] for point in points),
        } if points else None),
        "note": "Dense coordinates omitted; request fit_window_sketch instead of inventing points.",
    }
    return compact


def _parse_plan_response(body: dict) -> AgentCadPlan:
    """Parse a provider response without echoing model text into user-facing errors."""
    try:
        choice = body["choices"][0]
        message = choice["message"]
        raw = message.get("content")
        if isinstance(raw, list):
            raw = "".join(item.get("text", "") for item in raw if isinstance(item, dict))
        if not isinstance(raw, str) or not raw.strip():
            reason = choice.get("finish_reason") or "unknown"
            raise RuntimeError(f"Agent provider 未返回计划内容（finish_reason={reason}）。")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        # Some compatible providers add a short preface even in JSON mode.
        if not raw.startswith("{"):
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start:end + 1]
        return AgentCadPlan.model_validate_json(raw)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Agent provider 返回结构不是有效的 chat completion。") from error
    except ValidationError as error:
        issues = []
        for item in error.errors(include_input=False)[:3]:
            location = ".".join(str(part) for part in item["loc"])
            issues.append(f"{location}: {item['msg']}")
        suffix = "; ".join(issues) or "schema validation failed"
        raise RuntimeError(f"Agent 计划未通过 wheel-agent-cad-plan-v1 校验：{suffix}") from error


def _provider_safe_plan(body: dict) -> AgentCadPlan:
    plan = _parse_plan_response(body)
    if any(action.operation == "replace_sketch" for action in plan.actions):
        raise RuntimeError("模型不得直接生成密集窗口草图；请改为请求 fit_window_sketch。")
    return plan


class OpenAICompatibleAgentProvider:
    """Small OpenAI-compatible VLM adapter configured entirely by server environment."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout_seconds: float = 90,
                 disable_thinking: bool = False, max_tokens: int = 1600,
                 supports_primary_image: bool = True, json_mode: bool = False,
                 provider_name: str = "openai-compatible", profile_label: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.disable_thinking = disable_thinking
        self.max_tokens = max_tokens
        self.supports_primary_image = supports_primary_image
        self.json_mode = json_mode
        self.provider_name = provider_name
        self.profile_label = profile_label

    @classmethod
    def from_environment(cls):
        profile_ref = os.getenv("WHEELCAM_AGENT_PROFILE", "").strip()
        if profile_ref.startswith("pinpawo:"):
            profile_id = profile_ref.split(":", 1)[1]
            config_path = Path.home() / ".pinpawo" / "config.json"
            try:
                config = json.loads(config_path.read_text())
                profile = config["models"]["profiles"][profile_id]
                base_url = profile["baseUrl"]
                model = os.getenv("WHEELCAM_AGENT_MODEL", "").strip() or profile["model"]
                api_key = profile["apiKey"]
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise RuntimeError(f"无法读取 PinPawo Model Profile {profile_id!r}。") from error
            configured_modalities = os.getenv("WHEELCAM_AGENT_INPUT_MODALITIES", "").strip()
            modalities = ({item.strip() for item in configured_modalities.split(",") if item.strip()}
                          if configured_modalities else set(profile.get("inputModalities", [])))
            return cls(
                base_url, model, api_key,
                float(os.getenv("WHEELCAM_AGENT_TIMEOUT_SECONDS", "120")),
                os.getenv("WHEELCAM_AGENT_DISABLE_THINKING", "").lower() in {"1", "true", "yes"},
                int(os.getenv("WHEELCAM_AGENT_MAX_TOKENS", "1600")),
                "image" in modalities,
                profile.get("structuredOutputMethod") == "jsonMode",
                profile.get("provider", "pinpawo-profile"),
                profile.get("label", profile_id),
            )
        base_url = os.getenv("WHEELCAM_AGENT_BASE_URL", "").strip()
        model = os.getenv("WHEELCAM_AGENT_MODEL", "").strip()
        if not base_url or not model:
            return None
        return cls(
            base_url, model, os.getenv("WHEELCAM_AGENT_API_KEY") or None,
            float(os.getenv("WHEELCAM_AGENT_TIMEOUT_SECONDS", "90")),
            os.getenv("WHEELCAM_AGENT_DISABLE_THINKING", "").lower() in {"1", "true", "yes"},
            int(os.getenv("WHEELCAM_AGENT_MAX_TOKENS", "1600")),
        )

    def status(self):
        return {
            "provider": self.provider_name,
            "configured": True,
            "model": self.model,
            "profile": self.profile_label,
            "supports_primary_image": self.supports_primary_image,
            "mode": "live_provider",
            "thinking": "disabled" if self.disable_thinking else "provider_default",
        }

    def propose(self, project: dict, goal: str, image_path: Path | None):
        system = (
            "You are the planning component of WheelCAM Engineering Reconstruction Agent. "
            "Return only one JSON object matching wheel-agent-cad-plan-v1. Never invent an inaccessible "
            "dimension. Use mark_unknown and request_measurement when evidence is insufficient. "
            "Parameters with manual, drawing, measurement or specified sources are protected evidence. "
            "Never set_parameter, replace_sketch, or mark_unknown on a protected parameter, even with the "
            "same value or approval. If it conflicts with the photo, request_measurement instead; "
            "only the user may correct the original evidence through explicit draft editing. "
            "Every request_measurement action MUST include a concrete non-empty question. "
            "Every set_parameter or replace_sketch action MUST include value, source, and confidence. "
            "mark_unknown and request_measurement MUST NOT include value. "
            "Never synthesize or repeat window_outlines_mm coordinates. Request fit_window_sketch instead. "
            "Use request_tool for deterministic image analysis, sketch fitting, or render comparison. "
            "Do not output Python, shell, "
            "FreeCAD macros, STEP claims, manufacturing approval, or fields outside WheelSpec."
        )
        wheel_schema = WheelSpec.model_json_schema()["properties"]
        allowed_parameters = {
            name: {key: schema[key] for key in ("type", "minimum", "maximum", "multipleOf", "enum") if key in schema}
            for name, schema in wheel_schema.items()
        }
        context = {
            "goal": goal,
            "base_revision": project["revision"],
            "current_spec": _compact_spec(project["spec"]),
            "current_sources": project["sources"],
            "protected_parameters": confirmed_evidence_parameters(project["sources"]),
            "photo_analysis": _compact_analysis(project.get("photo_analysis")),
            "tool_results": project.get("agent_tool_results", []),
            "tool_result_policy": "Use returned evidence; do not repeat completed tools. Ambiguous results are not observations. Unchanged sketch fitting is not an improvement. Geometry checks do not prove visual similarity.",
            "allowed_parameters": allowed_parameters,
            "agent_contract": {
                "schema_version": "wheel-agent-cad-plan-v1",
                "required_plan_fields": ["schema_version", "base_revision", "goal", "actions"],
                "operations": ["set_parameter", "mark_unknown", "request_measurement", "request_tool"],
                "required_action_fields": ["id", "operation", "target", "rationale"],
                "edit_fields": ["value", "source", "confidence"],
                "source_values": ["observed", "inferred"],
                "operation_contracts": {
                    "set_parameter": "requires value, source, confidence",
                    "replace_sketch": "requires value, source, confidence; target must be window_outlines_mm",
                    "mark_unknown": "no value; rationale explains why evidence is insufficient",
                    "request_measurement": "requires a concrete non-empty question; no value",
                    "request_tool": "target is analyze_primary_image, fit_window_sketch, or compare_latest_build; no value",
                },
            },
        }
        content: list[dict] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        if self.supports_primary_image and image_path and image_path.is_file():
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        }
        if self.disable_thinking:
            # Ollama accepts `think`; other compatible servers can leave this opt-in environment flag unset.
            payload["think"] = False
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.base_url}/chat/completions",
                          data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read())
        except HTTPError as error:
            detail = error.read(500).decode("utf-8", "replace")
            raise RuntimeError(f"Agent provider HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Agent provider 不可用：{error}") from error
        return _provider_safe_plan(body)


def provider_status(provider: AgentProvider | None):
    if provider is None:
        return {
            "provider": None,
            "configured": False,
            "model": None,
            "supports_primary_image": False,
            "mode": "not_connected",
            "reason": "未配置 WHEELCAM_AGENT_BASE_URL 和 WHEELCAM_AGENT_MODEL。",
        }
    return provider.status()
