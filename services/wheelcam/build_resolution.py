"""Requested recipe versus kernel-reported construction outcomes.

These records describe what the generator applied, not independent metrology.
They never change the provenance of input evidence or approve manufacturing.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .models import WheelSpec
from .template import layout


class FeatureApplication(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    feature: str
    parameter: str
    requested: JsonValue
    applied: JsonValue
    status: Literal["exact", "adjusted", "partial", "unverified"]
    details: dict[str, JsonValue] = Field(default_factory=dict)


class BuildResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["wheel-build-resolution-v1"] = "wheel-build-resolution-v1"
    status: Literal["exact", "degraded", "unverified"]
    requested_spec: dict[str, JsonValue]
    resolved_recipe: dict[str, JsonValue]
    feature_results: list[FeatureApplication]
    review_required: bool
    scope: str = ("Kernel-reported construction outcomes, not measured dimensions. "
                  "Exact means no reported adjustment in tracked operations, not engineering approval. "
                  "resolved_recipe is the final generator input; feature_results records per-feature outcomes.")


def resolve_build(requested: WheelSpec, resolved: WheelSpec, info: dict) -> BuildResolution:
    features = []

    def scalar(feature, parameter, applied):
        wanted = getattr(requested, parameter)
        status = ("unverified" if applied is None else
                  "exact" if math.isclose(wanted, applied, abs_tol=1e-7, rel_tol=1e-9) else "adjusted")
        features.append(FeatureApplication(feature=feature, parameter=parameter,
                                           requested=wanted, applied=applied, status=status))

    # A scalar minimum must not hide different radii at the two junctions.
    scalar("hub_junction", "junction_fillet_mm", info.get("hub_fillet_applied_mm"))
    scalar("rim_junction", "junction_fillet_mm", info.get("rim_fillet_applied_mm"))
    if requested.spoke_method == "window":
        scalar("window_edges", "window_edge_fillet_mm", info.get("window_edge_fillet_applied_mm"))
        edge_result = features[-1]
        total, rounded = info.get("window_edges_total"), info.get("window_edges_rounded")
        edge_result.details = {"edges_total": total, "edges_rounded": rounded,
                               "budget_hit": info.get("window_fillet_budget_hit", False),
                               "note": "Radius refers to rounded front edges; rear edge break is template-limited."}
        if requested.window_edge_fillet_mm > 0:
            if total is None or rounded is None:
                edge_result.status = "unverified"
            elif total == 0 or rounded < total:
                edge_result.status = "partial"
        ridge = info.get("spoke_ridge", {})
        scalar("spoke_ridges", "window_spoke_ridge_mm", ridge.get("applied_mm"))
        if requested.window_side_draft_deg > 0:
            features.append(FeatureApplication(
                feature="window_side_draft", parameter="window_side_draft_deg",
                requested=requested.window_side_draft_deg, applied=None, status="unverified",
                details={"construction": "centroid_radial_contraction",
                         "note": "Current cutter is not a normal offset; constant wall draft angle has not been verified."}))
    elif requested.paired_blade_root_mm:
        scalar("spoke_edges", "spoke_fillet_mm", info.get("spoke_fillet_applied_mm"))

    if requested.spoke_method != "window" and requested.pocket_depth_mm > 0:
        pockets = layout(resolved)["pockets"]
        depths = [max(p[1] for p in section["polygon"]) - section["back"] for section in pockets]
        features.append(FeatureApplication(
            feature="back_pockets", parameter="pocket_depth_mm", requested=requested.pocket_depth_mm,
            applied=depths, status=("exact" if depths and all(
                math.isclose(depth, requested.pocket_depth_mm, abs_tol=1e-7) for depth in depths) else "adjusted"),
            details={"station_radius_mm": [section["r"] for section in pockets],
                     "note": "Cut floor depth at construction sections; not a full 3D minimum-wall measurement."}))

    changed = requested.model_dump(mode="json") != resolved.model_dump(mode="json")
    degraded = changed or any(item.status in {"adjusted", "partial"} for item in features)
    unverified = any(item.status == "unverified" for item in features)
    status = "degraded" if degraded else "unverified" if unverified else "exact"
    return BuildResolution(status=status, requested_spec=requested.model_dump(mode="json"),
                           resolved_recipe=resolved.model_dump(mode="json"), feature_results=features,
                           review_required=status != "exact")
