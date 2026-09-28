"""The small, inspectable domain contract for the Wheel Engineering Skill.

This is a report/decision contract, not a claim that the descriptive feature plan is
an executable native CAD history. The existing WheelSpec and CAD adapter own geometry.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WheelInputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    diameter_in: float | None = Field(default=None, gt=0, le=40)
    width_in: float | None = Field(default=None, gt=0, le=30)
    pcd_mm: float | None = Field(default=None, gt=0, le=500)
    bolts: int | None = Field(default=None, ge=3, le=16)
    center_bore_mm: float | None = Field(default=None, gt=0, le=300)
    et_mm: float | None = Field(default=None, ge=-150, le=150)

    @model_validator(mode="after")
    def bore_inside_bolt_circle(self):
        if self.pcd_mm is not None and self.center_bore_mm is not None and self.center_bore_mm >= self.pcd_mm:
            raise ValueError("中心孔直径必须小于 PCD；请核对输入规格。")
        return self


class SpecEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    source: Literal["user", "drawing", "measurement", "catalog", "unspecified"] = "unspecified"
    confidence: float | None = Field(default=None, ge=0, le=1)
    reference: str | None = Field(default=None, max_length=300)
    note: str = Field(default="", max_length=300)


def validate_spec_evidence(spec: dict, evidence: dict | None) -> dict:
    evidence = evidence or {}
    extra = set(evidence) - set(spec)
    if extra:
        raise ValueError("规格来源含未提供的字段：" + ", ".join(sorted(extra)))
    return {name: SpecEvidence.model_validate(evidence.get(name, {})).model_dump(exclude_none=True)
            for name in spec}


ONTOLOGY = {
    "wheel": ["rim", "barrel", "hub", "center_bore", "bolt_pattern", "spoke_pattern", "flange", "back_side"],
    "relations": [
        "rim and barrel share the wheel axis",
        "center_bore is coaxial with the hub",
        "bolt holes lie on a PCD circle around the hub axis",
        "spoke pattern connects the hub to the rim",
        "back-side geometry needs separate evidence",
    ],
}


def input_evidence(front, oblique=None):
    def record(path):
        path = Path(path)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    return {"front": record(front), "oblique": record(oblique) if oblique else None}


def understanding(spec: dict, provenance: dict, unknown: dict, questions: list[str], recipe,
                  spec_evidence: dict | None = None) -> dict:
    """Group evidence without raising a visual guess to a supplied measurement."""
    groups = {"observed": {}, "supplied": {}, "estimated": {}, "defaulted": {}, "unknown": {}}
    source_groups = {"photo": "observed", "vlm": "observed", "user": "supplied", "spec": "supplied",
                     "drawing": "supplied", "measurement": "supplied",
                     "estimate": "estimated", "rule": "estimated", "default": "defaulted"}
    for name, record in provenance.items():
        groups[source_groups.get(record["source"], "estimated")][name] = record
    for name, reason in unknown.items():
        groups["unknown"][name] = {"value": None, "source": "unknown", "confidence": 0.0, "reason": reason}
    spec_evidence = validate_spec_evidence(spec, spec_evidence)
    for name in WheelInputSpec.model_fields:
        if name not in spec:
            groups["unknown"][name] = {"value": None, "source": "unknown", "confidence": 0.0,
                                       "reason": "not supplied; cannot be established from this photo"}
        else:
            groups["supplied"][name] = {"value": spec[name], **spec_evidence[name]}
    missing = [name for name in WheelInputSpec.model_fields if name not in spec]
    unconfirmed = [name for name, item in spec_evidence.items()
                   if item["source"] not in {"user", "drawing", "measurement"}]
    return {"ontology": ONTOLOGY, "evidence_groups": groups, "questions": questions,
            "spec_evidence": spec_evidence,
            "planning_decision": {
                "visual_candidate": True,
                "parametric_candidate": True,
                "key_dimensions_supplied": not missing,
                "key_dimensions_confirmed": not missing and not unconfirmed,
                "manufacturing_ready": False,
                "next_action": "request_measurement" if missing else
                               "confirm_specification" if unconfirmed else "build_and_verify",
                "missing_key_specifications": missing,
                "unconfirmed_key_specifications": unconfirmed,
                "note": "视觉候选可先构建；关键尺寸不全时不能声称尺寸约束完成。",
            },
            "constraint_graph": [
                {"rule": "bolt_center_radius", "inputs": ["pcd_mm"], "value_mm": spec["pcd_mm"] / 2 if "pcd_mm" in spec else None,
                 "status": "derived_from_supplied" if "pcd_mm" in spec else "unknown"},
                {"rule": "bolt_pattern_count", "inputs": ["bolts"], "value": spec.get("bolts"),
                 "status": "supplied" if "bolts" in spec else "unknown"},
                {"rule": "spoke_rotational_order", "inputs": ["front_photo"], "value": recipe.spokes,
                 "status": "observed_candidate"},
                {"rule": "mounting_face_offset", "inputs": ["et_mm"], "value_mm": spec.get("et_mm"),
                 "status": "supplied" if "et_mm" in spec else "unknown"},
            ],
            "reconstruction_plan": [
                {"id": "blank", "operation": "revolve", "purpose": "rim, barrel and hub blank"},
                {"id": "windows", "operation": "patterned_cut", "purpose": "spoke and window layout from traced front planform"},
                {"id": "style", "operation": "conditional_cuts", "purpose": "visible style candidates and template defaults"},
                {"id": "bolt_pattern", "operation": "circular_cut", "purpose": "holes and seats from supplied or default bolt data"},
                {"id": "validation", "operation": "verify", "purpose": "solid, STEP exchange, dimensions and pattern checks"},
            ],
            "plan_status": "descriptive; actual operations are recorded by the selected build kernel"}
