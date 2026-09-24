"""Typed, auditable CAD actions for an AI agent.

The agent is allowed to draw and edit a WheelCAM draft, but never by injecting
Python. It proposes operations against a revision; this module compiles them to
a validated WheelSpec and provenance map. Critical geometry and replacement
sketches require explicit approval before persistence.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .engineering_schema import CRITICAL_PARAMETERS
from .models import ParameterSource, WheelSpec


AgentOperation = Literal["set_parameter", "replace_sketch", "mark_unknown", "request_measurement", "request_tool"]
AgentEvidenceOrigin = Literal["observed", "inferred"]
AGENT_APPROVAL_PARAMETERS = CRITICAL_PARAMETERS | {"spoke_method", "spoke_style"}
AGENT_TOOL_TARGETS = {"analyze_primary_image", "fit_window_sketch", "compare_latest_build"}
# An approval authorizes a candidate edit, not replacement of supplied evidence.
# `specified` is a defensive alias for imported engineering provenance; current
# ParameterSource uses `manual`, `drawing`, and `measurement` for supplied values.
CONFIRMED_EVIDENCE_SOURCE_KINDS = frozenset({"manual", "drawing", "measurement", "specified"})


def confirmed_evidence_parameters(sources: dict) -> dict[str, str]:
    """Return fields the agent may question, but must never overwrite."""
    return {
        target: source["kind"] for target, source in sources.items()
        if target in WheelSpec.model_fields and source.get("kind") in CONFIRMED_EVIDENCE_SOURCE_KINDS
    }


class ConfirmedEvidenceConflict(ValueError):
    """A plan tried to overwrite user-supplied or confirmed evidence."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        fields = ", ".join(f"{item['target']} ({item['source_kind']})" for item in conflicts)
        super().__init__(
            f"Agent 不得覆盖已确认的参数或来源：{fields}。"
            "如证据有冲突，请请求复测；更正原始证据必须由用户在草稿编辑中完成，批准 Agent 动作不能解除此限制。"
        )

    @property
    def detail(self) -> dict:
        return {"code": "confirmed_evidence_locked", "message": str(self), "conflicts": self.conflicts}


class AgentCadAction(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    operation: AgentOperation
    target: str = Field(min_length=1, max_length=80)
    value: JsonValue | None = None
    source: AgentEvidenceOrigin | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    question: str | None = Field(None, max_length=300)

    @model_validator(mode="after")
    def operation_contract(self):
        parameter_names = set(WheelSpec.model_fields)
        if self.operation in {"set_parameter", "replace_sketch", "mark_unknown"} and self.target not in parameter_names:
            raise ValueError(f"Agent 参数 {self.target!r} 不在 WheelSpec 中。")
        if self.operation == "request_tool" and self.target not in AGENT_TOOL_TARGETS:
            raise ValueError(f"Agent 工具 {self.target!r} 不在受控工具注册表中。")
        if self.operation == "replace_sketch" and self.target != "window_outlines_mm":
            raise ValueError("replace_sketch 当前只允许替换 window_outlines_mm。")
        if self.operation == "set_parameter" and self.target == "window_outlines_mm":
            raise ValueError("window_outlines_mm 必须通过 replace_sketch 修改。")
        if self.operation in {"set_parameter", "replace_sketch"}:
            if self.value is None or self.source is None or self.confidence is None:
                raise ValueError("参数或草图修改必须包含 value、source 和 confidence。")
        elif self.value is not None:
            raise ValueError(f"{self.operation} 不允许携带 value。")
        if self.operation == "request_measurement" and not self.question:
            raise ValueError("request_measurement 必须给出明确问题。")
        return self


class AgentCadPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wheel-agent-cad-plan-v1"] = "wheel-agent-cad-plan-v1"
    base_revision: int = Field(ge=1)
    goal: str = Field(min_length=1, max_length=500)
    actions: list[AgentCadAction] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_actions_and_targets(self):
        ids = [action.id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent action id 必须唯一。")
        mutated = [action.target for action in self.actions
                   if action.operation in {"set_parameter", "replace_sketch", "mark_unknown"}]
        if len(mutated) != len(set(mutated)):
            raise ValueError("同一计划不能多次修改同一个参数。")
        return self


class AgentPlanApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: AgentCadPlan
    approved_action_ids: set[str] = Field(default_factory=set)


def evaluate_plan(spec: dict, sources: dict, plan: AgentCadPlan, approved_action_ids=()):
    """Compile an agent plan without persistence or kernel execution."""
    # Check the original provenance before compiling any action. This also blocks
    # same-value edits that would silently launder confirmed sources to inferred,
    # and source-only edits via mark_unknown, including explicitly approved ones.
    confirmed = confirmed_evidence_parameters(sources)
    conflicts = [{
        "action_id": action.id, "operation": action.operation,
        "target": action.target, "source_kind": confirmed[action.target],
    } for action in plan.actions
        if action.operation in {"set_parameter", "replace_sketch", "mark_unknown"}
        and action.target in confirmed]
    if conflicts:
        raise ConfirmedEvidenceConflict(conflicts)

    proposed = dict(spec)
    proposed_sources = {key: dict(value) for key, value in sources.items()}
    approvals = set(approved_action_ids)
    pending, questions, tools, audit = [], [], [], []

    for action in plan.actions:
        requires_approval = (
            action.operation == "replace_sketch"
            or action.target in AGENT_APPROVAL_PARAMETERS
            and action.operation == "set_parameter"
        )
        if requires_approval and action.id not in approvals:
            pending.append(action.id)
        if action.operation in {"set_parameter", "replace_sketch"}:
            proposed[action.target] = action.value
            proposed_sources[action.target] = ParameterSource(
                kind=action.source,
                confidence=action.confidence,
                note=f"Agent 候选：{action.rationale}",
            ).model_dump()
        elif action.operation == "mark_unknown":
            proposed_sources[action.target] = ParameterSource(
                kind="unknown", confidence=0, note=action.rationale,
            ).model_dump()
        elif action.operation == "request_measurement":
            questions.append({
                "action_id": action.id,
                "target": action.target,
                "question": action.question,
                "rationale": action.rationale,
            })
        else:
            tools.append({
                "action_id": action.id,
                "tool": action.target,
                "rationale": action.rationale,
            })
        audit.append({
            "action_id": action.id,
            "operation": action.operation,
            "target": action.target,
            "requires_approval": requires_approval,
            "approved": not requires_approval or action.id in approvals,
            "evidence_refs": action.evidence_refs,
        })

    validated = WheelSpec(**proposed)
    if any(action.operation == "replace_sketch" for action in plan.actions) and validated.spoke_method != "window":
        raise ValueError("替换窗口草图时，计划的最终 spoke_method 必须为 window。")
    return {
        "schema_version": "wheel-agent-cad-result-v1",
        "base_revision": plan.base_revision,
        "proposed_spec": validated.model_dump(mode="json"),
        "proposed_sources": proposed_sources,
        "pending_approval_action_ids": pending,
        "measurement_requests": questions,
        "tool_requests": tools,
        "can_apply": not pending,
        "can_build": not questions and not tools,
        "audit": audit,
        "limitations": [
            "Agent 修改的是参数化草稿，不是制造发布模型。",
            "通过 WheelSpec 校验不代表尺寸、强度、疲劳或法规正确。",
            "Agent 不执行任意 Python；所有几何必须由受控 CAD adapter 构造。",
        ],
    }
