"""Auditable closed-solid volume integration, not an engineering certification.

The legacy adaptive Gauss integrator can under-sample periodic B-spline faces
while returning a very small error estimate. Use Gauss-Kronrod with B-spline
span splitting explicitly enabled. Analytic regressions remain necessary: the
kernel's reported error is a numerical diagnostic, not an independent bound on
the true volume, CAD accuracy, or manufacturing suitability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from numbers import Real

from OCP.BRepGProp import BRepGProp
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_OUT, TopAbs_SOLID
from OCP.TopoDS import TopoDS_Iterator


VOLUME_SCHEMA = "wheel-volume-measurement-v1"
VOLUME_METHOD = "BRepGProp.VolumePropertiesGK_s"
DEFAULT_EPSILON = 1e-7
MIN_EPSILON = 1e-12
MAX_EPSILON = 1e-3
CLASSIFIER_TOLERANCE_MM = 1e-7


class VolumeMeasurementError(ValueError):
    """A volume could not be measured under the declared numerical policy."""


@dataclass(frozen=True)
class VolumeMeasurement:
    volume_mm3: float
    reported_relative_error: float
    requested_epsilon: float
    reported_error_limit: float
    empty_shape: bool = False
    solid_count: int = 1
    aggregation: str = "per_solid_sum_mass_weighted_kernel_error"
    method: str = VOLUME_METHOD
    schema_version: str = VOLUME_SCHEMA

    def to_dict(self) -> dict:
        return asdict(self)


def _epsilon(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Volume epsilon must be a finite real number.")
    result = float(value)
    if not math.isfinite(result) or not MIN_EPSILON <= result <= MAX_EPSILON:
        raise ValueError(f"Volume epsilon must be between {MIN_EPSILON:g} and {MAX_EPSILON:g}.")
    return result


def _error_limit(epsilon: float) -> float:
    # OCCT Eps controls each face; the returned estimate describes the whole
    # shape and may exceed Eps. This generous fail-closed guard is a policy,
    # deliberately not a claim that true error is <= 10 * Eps.
    return max(10 * epsilon, 1e-9)


def volume_method(*, epsilon: float = DEFAULT_EPSILON) -> dict:
    """JSON-safe policy metadata to accompany stored volume-dependent results."""
    epsilon = _epsilon(epsilon)
    return {
        "schema_version": VOLUME_SCHEMA,
        "method": VOLUME_METHOD,
        "requested_epsilon": epsilon,
        "OnlyClosed": True,
        "IsUseSpan": True,
        "CGFlag": False,
        "IFlag": False,
        "SkipShared": False,
        "reported_error_limit": _error_limit(epsilon),
        "error_policy": "reject_nonfinite_negative_or_above_limit_no_hidden_refinement",
        "component_policy": "solid_leaves_only_each_integrated_once_positive_mass_required",
        "aggregation": "per_solid_sum_mass_weighted_kernel_error",
        "finite_solid_classifier": {"method": "BRepClass3d_SolidClassifier.PerformInfinitePoint",
                                    "required_state": "OUT", "tolerance_mm": CLASSIFIER_TOLERANCE_MM},
        "scope": "Kernel numerical estimate, not an independently certified error bound or engineering validation.",
    }


def _solid_leaves(wrapped) -> list:
    """Reject free topology rather than silently skipping it with OnlyClosed.

    Walk *direct* children: comparing flattened face sets would miss a face
    added a second time beside its own solid. The iterator explicitly composes
    parent orientation and placement so a reversed container stays reversed.
    Stop at solid leaves; their shells/faces are legitimate internal topology.
    """
    kind = wrapped.ShapeType()
    if kind == TopAbs_SOLID:
        return [wrapped]
    if kind not in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
        raise VolumeMeasurementError("Volume input must contain only closed solids, without free non-solid geometry.")
    solids = []
    children = TopoDS_Iterator(wrapped, True, True)
    while children.More():
        solids.extend(_solid_leaves(children.Value()))
        children.Next()
    if not solids and kind != TopAbs_COMPOUND:
        raise VolumeMeasurementError("Nonempty volume input must contain closed solids.")
    return solids


def _integrate_solid(wrapped, epsilon: float, error_limit: float) -> tuple[float, float]:
    # Positive numerical mass and isValid() do not rule out a malformed or
    # inside-out Boolean shell. CadQuery isInside() can also disagree with
    # native infinity classification. Only an OUT result is accepted here.
    try:
        classifier = BRepClass3d_SolidClassifier(wrapped)
        classifier.PerformInfinitePoint(CLASSIFIER_TOLERANCE_MM)
        state = classifier.State()
    except Exception as exc:
        raise VolumeMeasurementError("Could not classify the solid at infinity.") from exc
    if state != TopAbs_OUT:
        raise VolumeMeasurementError(f"Volume requires a finite solid: infinity must classify OUT, got {state}.")
    props = GProp_GProps()
    try:
        reported_error = float(BRepGProp.VolumePropertiesGK_s(
            wrapped, props, epsilon, True, True, False, False, False,
        ))
        mass = float(props.Mass())
    except Exception as exc:
        raise VolumeMeasurementError("Gauss-Kronrod volume integration failed.") from exc
    if not math.isfinite(reported_error) or reported_error < 0:
        raise VolumeMeasurementError("Volume integration reported a nonfinite error or kernel failure.")
    if reported_error > error_limit:
        raise VolumeMeasurementError(
            f"Volume integration error estimate {reported_error:g} exceeds policy limit {error_limit:g}."
        )
    if not math.isfinite(mass) or mass <= 0:
        raise VolumeMeasurementError("Each nonempty solid volume must be finite and positive.")
    return mass, reported_error


def measure_volume(shape, *, epsilon: float = DEFAULT_EPSILON) -> VolumeMeasurement:
    """Measure a valid CadQuery solid/solid compound in model units cubed (mm³).

    A genuinely empty compound, such as a disjoint Boolean intersection, is a
    legitimate zero. A null shape, invalid B-Rep, or nonempty shape without
    solids is not a legitimate zero and is rejected. Containers with any free
    non-solid geometry are also rejected. Every solid is integrated once and
    must classify native infinity OUT and have positive finite mass before aggregation, so positive components
    cannot hide a reversed component. No absolute value or fallback is used.

    ``epsilon`` is OCCT's *per-face integration request*. The returned whole-
    solid estimate must also satisfy a generous numerical guardrail. Multiple
    positive solid masses are summed; their kernel estimates are mass-weighted
    for reporting (still not an independently validated error bound). Failure
    raises ``VolumeMeasurementError`` instead of yielding a plausible number.
    No refinement occurs implicitly, so runtime and metadata stay reproducible.
    """
    epsilon = _epsilon(epsilon)
    error_limit = _error_limit(epsilon)
    if shape is None or not hasattr(shape, "wrapped"):
        raise VolumeMeasurementError("Volume requires a non-null CadQuery shape.")
    try:
        if shape.wrapped.IsNull():
            raise VolumeMeasurementError("Cannot measure a null shape.")
        if not shape.isValid():
            raise VolumeMeasurementError("Cannot measure an invalid B-Rep.")
        solids = _solid_leaves(shape.wrapped)
        if not solids:
            return VolumeMeasurement(0.0, 0.0, epsilon, error_limit, empty_shape=True, solid_count=0)
    except VolumeMeasurementError:
        raise
    except Exception as exc:
        raise VolumeMeasurementError("Could not inspect volume input topology.") from exc

    components = [_integrate_solid(solid, epsilon, error_limit) for solid in solids]
    try:
        mass = math.fsum(value for value, _ in components)
        reported_error = math.fsum((value / mass) * error for value, error in components)
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        raise VolumeMeasurementError("Solid volume aggregation failed.") from exc
    if not math.isfinite(mass) or mass <= 0:
        raise VolumeMeasurementError("Aggregated solid volume must be finite and positive.")
    return VolumeMeasurement(mass, reported_error, epsilon, error_limit, solid_count=len(components))


def volume(shape, *, epsilon: float = DEFAULT_EPSILON) -> float:
    """Convenience scalar; use ``measure_volume`` when persisting diagnostics."""
    return measure_volume(shape, epsilon=epsilon).volume_mm3
