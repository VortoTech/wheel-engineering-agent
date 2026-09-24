"""Bounded, executable master-sector design program for the window adapter.

This is *design intent*, not recovery of a photographed or manufactured wheel.
Named negative-space features compile to complete sampled outlines; the existing
WheelSpec validator and CAD kernel remain authoritative. No Python, arbitrary
coordinates, hidden changes to dimensional evidence, or persistence are accepted.

The adapter interpolates a periodic CAD spline through these samples. The checks
here apply to the sampled boundary, not a certified bound on that spline or on
three-dimensional wall thickness. A kernel build and STEP readback are required.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_cad import ConfirmedEvidenceConflict, confirmed_evidence_parameters
from .models import WheelSpec
from .template import INCH, layout
from .windows import HUB_KEEP_MM, MIN_WEB_MM

PROGRAM_SCHEMA = "wheel-sector-program-v1"
COMPILER_VERSION = "sampled-window-adapter-v1"
OUTLINE_SAMPLES = 256


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True, revalidate_instances="always")


class RadialWindow(_Model):
    """Rounded radial window with three tangential half-width stations.

    root/tip radii are extrema (including the caps). At the first/last side
    station the radius is root+end_round/tip-end_round. ``mid_half_width_mm``
    is the actual half-width at the middle side station, not a Bezier handle.
    Cubic smoothstep interpolation fixes station values with zero end slope.
    Sweep changes the centre angle between those stations. Half-width means
    tangential displacement: angle offset = asin(half-width / radius).
    """
    kind: Literal["radial_window"] = "radial_window"
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    role: Literal["main_window", "split_window"]
    root_radius_mm: float = Field(ge=60, le=240)
    tip_radius_mm: float = Field(ge=80, le=300)
    center_angle_deg: float = Field(ge=-180, le=180)
    sweep_deg: float = Field(0, ge=-20, le=20)
    root_half_width_mm: float = Field(ge=2, le=100)
    mid_half_width_mm: float = Field(ge=2, le=120)
    tip_half_width_mm: float = Field(ge=2, le=120)
    end_round_mm: float = Field(4, ge=1, le=15)

    @model_validator(mode="after")
    def feasible_span(self):
        if self.tip_radius_mm - self.root_radius_mm <= 2 * self.end_round_mm + 5:
            raise ValueError("Radial window must retain a positive side span between rounded ends.")
        if max(self.root_half_width_mm, self.mid_half_width_mm, self.tip_half_width_mm) >= self.root_radius_mm:
            raise ValueError("Window half-width must stay below its minimum radius.")
        return self


class RootSlot(_Model):
    """Radially oriented capsule; length includes both semicircular ends."""
    kind: Literal["root_slot"] = "root_slot"
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    center_radius_mm: float = Field(ge=70, le=180)
    center_angle_deg: float = Field(ge=-180, le=180)
    length_mm: float = Field(ge=8, le=50)
    width_mm: float = Field(ge=3, le=15)

    @model_validator(mode="after")
    def capsule_span(self):
        if self.length_mm <= self.width_mm:
            raise ValueError("Root slot length must exceed its width.")
        return self


SectorFeature = Annotated[RadialWindow | RootSlot, Field(discriminator="kind")]


class SectorProgram(_Model):
    schema_version: Literal["wheel-sector-program-v1"] = PROGRAM_SCHEMA
    family: Literal["single", "split_y"]
    groups: int = Field(ge=5, le=10, strict=True)
    phase_deg: float = Field(0, ge=0, lt=360)
    provenance: Literal["design_assumption"] = "design_assumption"
    features: tuple[SectorFeature, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def topology(self):
        if len({feature.id for feature in self.features}) != len(self.features):
            raise ValueError("Sector feature IDs must be unique.")
        windows = [feature for feature in self.features if isinstance(feature, RadialWindow)]
        if sum(feature.role == "main_window" for feature in windows) != 1:
            raise ValueError("A sector must contain exactly one main_window.")
        split_count = sum(feature.role == "split_window" for feature in windows)
        if split_count != (1 if self.family == "split_y" else 0):
            raise ValueError("single requires no split_window; split_y requires exactly one.")
        if sum(isinstance(feature, RootSlot) for feature in self.features) > 2:
            raise ValueError("This version supports at most two root slots per sector.")
        return self


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _polar(radius: float, degrees: float, half_width: float = 0.0) -> tuple[float, float]:
    angle = math.radians(degrees) + math.asin(half_width / radius)
    return radius * math.cos(angle), radius * math.sin(angle)


def sample_feature(feature: RadialWindow | RootSlot, count: int = OUTLINE_SAMPLES) -> list[tuple[float, float]]:
    """Sample the analytic design perimeter, including straight slot sections.

    Each of four boundary segments receives count/4 samples. This is a sample
    count, not an asserted approximation tolerance for native CAD splines.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 32 or count > 16384 or count % 4:
        raise ValueError("Sample count must be a multiple of four from 32 to 16384.")
    samples = count // 4
    if isinstance(feature, RootSlot):
        radius = feature.width_mm / 2
        half_straight = (feature.length_mm - feature.width_mm) / 2
        points = [(feature.center_radius_mm + half_straight + radius * math.cos(-math.pi / 2 + math.pi * i / samples),
                   radius * math.sin(-math.pi / 2 + math.pi * i / samples)) for i in range(samples)]
        points.extend((feature.center_radius_mm + half_straight - 2 * half_straight * i / samples, radius)
                      for i in range(samples))
        points.extend((feature.center_radius_mm - half_straight + radius * math.cos(math.pi / 2 + math.pi * i / samples),
                       radius * math.sin(math.pi / 2 + math.pi * i / samples)) for i in range(samples))
        points.extend((feature.center_radius_mm - half_straight + 2 * half_straight * i / samples, -radius)
                      for i in range(samples))
        angle = math.radians(feature.center_angle_deg)
        c, s = math.cos(angle), math.sin(angle)
        return [(x * c - y * s, x * s + y * c) for x, y in points]

    start = feature.root_radius_mm + feature.end_round_mm
    end = feature.tip_radius_mm - feature.end_round_mm

    def side(t, sign):
        if t <= .5:
            width = feature.root_half_width_mm + (feature.mid_half_width_mm - feature.root_half_width_mm) * _smooth(2 * t)
        else:
            width = feature.mid_half_width_mm + (feature.tip_half_width_mm - feature.mid_half_width_mm) * _smooth(2 * t - 1)
        return _polar(start + (end - start) * t,
                      feature.center_angle_deg + feature.sweep_deg * _smooth(t), sign * width)

    points = [side(index / samples, 1) for index in range(samples)]
    for index in range(samples):
        a = math.pi * index / samples
        points.append(_polar(end + feature.end_round_mm * math.sin(a),
                             feature.center_angle_deg + feature.sweep_deg,
                             feature.tip_half_width_mm * math.cos(a)))
    points.extend(side(1 - index / samples, -1) for index in range(samples))
    for index in range(samples):
        a = math.pi * index / samples
        points.append(_polar(start - feature.end_round_mm * math.sin(a), feature.center_angle_deg,
                             -feature.root_half_width_mm * math.cos(a)))
    return points


def feature_outline(feature: RadialWindow | RootSlot) -> list[tuple[float, float]]:
    """The compiler's fixed sampling policy; callers cannot truncate outlines."""
    return sample_feature(feature)


def program_hash(program: SectorProgram | dict) -> str:
    validated = SectorProgram.model_validate(program)
    encoded = json.dumps(validated.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _radial_extrema(outline):
    # Include segment interiors: a chord can enter the hub keep even if its
    # endpoints don't. The adapter spline still requires separate kernel QA.
    p = np.asarray(outline, dtype=float)
    d = np.roll(p, -1, axis=0) - p
    t = np.clip(-np.sum(p * d, axis=1) / np.maximum(np.sum(d * d, axis=1), 1e-15), 0, 1)
    return float(np.linalg.norm(p + t[:, None] * d, axis=1).min()), float(np.linalg.norm(p, axis=1).max())


def compile_sector(program: SectorProgram | dict, base_spec: WheelSpec | dict, sources: dict | None = None) -> tuple[WheelSpec, dict]:
    """Validate/compile without writing a draft or altering input/source maps.

    Caller must supply the real project's sources when using project data.
    ``sources=None`` is for isolated design fixtures, never evidence promotion.
    All owned adapter fields are protected if confirmed, including same-value
    ownership transfers, matching the Agent confirmed-evidence rule.
    """
    program = SectorProgram.model_validate(program)
    base = WheelSpec.model_validate(base_spec.model_dump() if isinstance(base_spec, WheelSpec) else base_spec)
    overrides = {
        "spoke_count": program.groups,
        "spoke_phase_deg": program.phase_deg,
        "spoke_style": "paired" if program.family == "split_y" else "single",
        "spoke_method": "window",
        "window_outlines_mm": [feature_outline(feature) for feature in program.features],
    }
    confirmed = confirmed_evidence_parameters(sources or {})
    conflicts = [{"action_id": "compile_sector", "operation": "compile_sector",
                  "target": field, "source_kind": confirmed[field]}
                 for field in overrides if field in confirmed]
    if conflicts:
        raise ConfirmedEvidenceConflict(conflicts)
    if base.window_spoke_ridge_mm:
        raise ValueError("Sector program v1 does not implement legacy three-window spoke ridges; set window_spoke_ridge_mm explicitly to 0.")
    minimum = base.hub_diameter_mm / 2 + HUB_KEEP_MM + MIN_WEB_MM
    maximum = base.rim_diameter_in * INCH / 2 - MIN_WEB_MM
    bounds = []
    for feature, outline in zip(program.features, overrides["window_outlines_mm"]):
        lo, hi = _radial_extrema(outline)
        if lo < minimum - 1e-8:
            raise ValueError(f"Feature {feature.id} reaches the hub keep/root connection: {lo:.3f} < {minimum:.3f} mm.")
        if hi > maximum + 1e-8:
            raise ValueError(f"Feature {feature.id} reaches the outer rim connection: {hi:.3f} > {maximum:.3f} mm.")
        bounds.append((lo, hi))
    # Reconstruct rather than model_copy(update=...): this runs all existing
    # WheelSpec/layout/window checks, including orbit collisions and min webs.
    spec = WheelSpec.model_validate({**base.model_dump(), **overrides})
    checks = layout(spec)["window_blank"]
    manifest = {
        "schema_version": PROGRAM_SCHEMA,
        "compiler_version": COMPILER_VERSION,
        "program_sha256": program_hash(program),
        "provenance": "design_assumption",
        "family": program.family,
        "groups": program.groups,
        "period_deg": 360 / program.groups,
        "phase_deg": program.phase_deg,
        "owned_spec_fields": sorted(overrides),
        "feature_map": {feature.id: {
            "outline_index": index, "kind": feature.kind,
            "role": feature.role if isinstance(feature, RadialWindow) else "root_slot",
            "sample_count": len(outline), "min_radius_mm": bounds[index][0], "max_radius_mm": bounds[index][1],
        } for index, (feature, outline) in enumerate(zip(program.features, spec.window_outlines_mm))},
        "sampled_outline_checks": {
            "status": "passed", "min_web_mm": checks["min_web_mm"],
            "hub_keep_with_margin_mm": minimum, "rim_keep_radius_mm": maximum,
            "scope": "sampled 2D boundary only; not native CAD spline or minimum 3D wall thickness",
        },
        "native_spline_fidelity": "not_validated",
        "photo_fidelity": "not_validated",
        "manufacturing_status": "not_released",
        "limitations": [
            "Negative-space design program; not automatic photo reconstruction.",
            "Depth, crown, face curve, fillets and rear geometry are inherited from base_spec, not inferred here.",
            "Sampled outlines are interpolated by the window adapter; native spline deviation requires kernel validation.",
            "Single-body and STEP readback checks are required after every build.",
        ],
    }
    return spec, manifest


def patch_feature(program: SectorProgram | dict, feature_id: str, changes: dict) -> SectorProgram:
    """Return a new typed program; IDs, roles and topology cannot be patched.

    Local field checks run here. Cross-feature/web checks still require
    compile_sector with the candidate's actual base spec and evidence map.
    """
    program = SectorProgram.model_validate(program)
    feature = next((item for item in program.features if item.id == feature_id), None)
    if feature is None:
        raise ValueError(f"Unknown sector feature ID: {feature_id}")
    allowed = set(type(feature).model_fields) - {"id", "kind", "role"}
    if not changes or set(changes) - allowed:
        raise ValueError(f"Feature edits must contain only numeric controls: {', '.join(sorted(allowed))}")
    updated = type(feature).model_validate({**feature.model_dump(), **changes})
    data = program.model_dump()
    data["features"] = [updated.model_dump() if item.id == feature_id else item.model_dump() for item in program.features]
    return SectorProgram.model_validate(data)


def example_program(family: Literal["single", "split_y"] = "split_y", groups: int = 5) -> SectorProgram:
    """A scalable design fixture, deliberately unrelated to any input photograph."""
    if isinstance(groups, bool) or not isinstance(groups, int) or not 5 <= groups <= 10:
        raise ValueError("groups must be an integer from 5 to 10")
    half_period = 180 / groups
    features = [RadialWindow(
        id="main_window", role="main_window", root_radius_mm=108, tip_radius_mm=214,
        center_angle_deg=half_period, root_half_width_mm=112 * math.sin(math.radians(.28 * half_period)),
        mid_half_width_mm=161 * math.sin(math.radians(.64 * half_period)),
        tip_half_width_mm=210 * math.sin(math.radians(.74 * half_period)), end_round_mm=4,
    )]
    if family == "split_y":
        features.extend([
            RadialWindow(id="split_window", role="split_window", root_radius_mm=140, tip_radius_mm=214,
                         center_angle_deg=0, root_half_width_mm=4 * 5 / groups,
                         mid_half_width_mm=8 * 5 / groups, tip_half_width_mm=11 * 5 / groups, end_round_mm=4),
            RootSlot(id="root_slot_left", center_radius_mm=102, center_angle_deg=8 * 5 / groups, length_mm=22, width_mm=6),
            RootSlot(id="root_slot_right", center_radius_mm=102, center_angle_deg=-8 * 5 / groups, length_mm=22, width_mm=6),
        ])
    return SectorProgram(family=family, groups=groups, features=tuple(features))
