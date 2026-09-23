"""Independent analytic checks plus fail-closed integration-policy tests."""
from dataclasses import FrozenInstanceError
import math
from types import SimpleNamespace

import cadquery as cq
from OCP.TopoDS import TopoDS_Shape
from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_OUT, TopAbs_UNKNOWN
import pytest

from wheelcam import mass_properties as mp


@pytest.fixture(scope="module")
def analytic_shapes():
    return [
        (cq.Workplane("XY").box(7, 11, 13).val(), 7 * 11 * 13),
        (cq.Solid.makeCylinder(5, 17), math.pi * 5**2 * 17),
        (cq.Workplane("XY").circle(9).circle(5).extrude(7).val(), math.pi * (9**2 - 5**2) * 7),
        (cq.Workplane("XY").slot2D(22, 6).extrude(6).val(), (6 * 16 + math.pi * 3**2) * 6),
    ]


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("transform", ["identity", "translation", "rotation", "both"])
def test_analytic_volume_and_rigid_transform_invariance(analytic_shapes, index, transform):
    shape, expected = analytic_shapes[index]
    if transform in {"rotation", "both"}:
        shape = shape.rotate((0, 0, 0), (1, 2, 3), 37)
    if transform in {"translation", "both"}:
        shape = shape.translate((325, -248, 411))
    result = mp.measure_volume(shape)
    assert result.volume_mm3 == pytest.approx(expected, rel=1e-9, abs=1e-7)
    assert math.isfinite(result.reported_relative_error)
    assert 0 <= result.reported_relative_error <= result.reported_error_limit
    assert result.requested_epsilon == 1e-7
    assert result.empty_shape is False


@pytest.mark.parametrize("width", [6, 8])
@pytest.mark.parametrize("rotated", [False, True])
def test_periodic_spline_capsule_regression(width, rotated):
    # Exactly the sampled master-sector geometry that defeats legacy Gauss
    # integration. Analytic tolerance includes native spline approximation.
    from wheelcam.sector_program import example_program, patch_feature, sample_feature

    program = patch_feature(example_program(), "root_slot_left", {"width_mm": width})
    feature = next(item for item in program.features if item.id == "root_slot_left")
    wire = cq.Wire.assembleEdges([cq.Edge.makeSpline(
        [cq.Vector(x, y, 0) for x, y in sample_feature(feature)], periodic=True,
    )])
    shape = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, 0, 6))
    if rotated:
        shape = shape.rotate((0, 0, 0), (1, 2, 3), 63).translate((230, -310, 97))
    expected = (width * (feature.length_mm - width) + math.pi * (width / 2)**2) * 6
    assert mp.volume(shape) == pytest.approx(expected, abs=.005)


def test_solid_compound_sums_disjoint_volumes():
    shape = cq.Compound.makeCompound([
        cq.Workplane("XY").box(2, 3, 4).val(),
        cq.Solid.makeCylinder(3, 7).translate((10, 0, 0)),
    ])
    result = mp.measure_volume(shape)
    assert result.volume_mm3 == pytest.approx(24 + math.pi * 3**2 * 7, rel=1e-9)
    assert result.solid_count == 2
    assert result.aggregation == "per_solid_sum_mass_weighted_kernel_error"


@pytest.mark.parametrize("nested", [False, True])
def test_mixed_orientation_cannot_cancel_into_a_positive_compound(nested):
    positive = cq.Workplane("XY").box(10, 10, 10).val()
    smaller = cq.Workplane("XY").box(2, 3, 4).val().translate((20, 0, 0))
    reversed_solid = cq.Shape.cast(smaller.wrapped.Reversed())
    if nested:
        reversed_solid = cq.Compound.makeCompound([reversed_solid])
    compound = cq.Compound.makeCompound([positive, reversed_solid])
    assert compound.isValid()  # Valid topology alone does not establish orientation.
    with pytest.raises(mp.VolumeMeasurementError, match="finite solid|finite and positive"):
        mp.measure_volume(compound)


def test_reversed_container_orientation_is_not_lost():
    box = cq.Workplane("XY").box(2, 3, 4).val()
    compound = cq.Compound.makeCompound([box])
    reversed_container = cq.Shape.cast(compound.wrapped.Reversed())
    assert reversed_container.isValid()
    with pytest.raises(mp.VolumeMeasurementError, match="finite solid|finite and positive"):
        mp.measure_volume(reversed_container)


@pytest.mark.parametrize("free_kind", ["face", "shell", "wire", "edge", "vertex", "own_face"])
def test_solid_plus_free_non_solid_geometry_is_rejected(free_kind):
    box = cq.Workplane("XY").box(2, 3, 4).val()
    free = {
        "face": cq.Face.makePlane(2, 3),
        "shell": box.Shells()[0],
        "wire": cq.Wire.makeCircle(2, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1)),
        "edge": cq.Edge.makeLine(cq.Vector(10, 0, 0), cq.Vector(11, 0, 0)),
        "vertex": cq.Vertex.makeVertex(10, 0, 0),
        "own_face": box.Faces()[0],
    }[free_kind]
    # A duplicate face belonging to the solid must still be caught as a free
    # direct child, not hidden by flattened unique-face traversal.
    compound = cq.Compound.makeCompound([box, cq.Compound.makeCompound([free])])
    assert compound.isValid()
    with pytest.raises(mp.VolumeMeasurementError, match="free non-solid"):
        mp.volume(compound)


@pytest.mark.parametrize("boolean_empty", [False, True])
def test_legitimate_empty_compound_is_zero_without_kernel_call(monkeypatch, boolean_empty):
    shape = cq.Compound.makeCompound([])
    if boolean_empty:
        box = cq.Workplane("XY").box(2, 3, 4).val()
        shape = box.cut(box)
    monkeypatch.setattr(mp, "BRepGProp", SimpleNamespace(
        VolumePropertiesGK_s=lambda *args: pytest.fail("Empty topology needs no integration"),
    ))
    result = mp.measure_volume(shape)
    assert result.volume_mm3 == 0
    assert result.reported_relative_error == 0
    assert result.empty_shape is True
    assert result.solid_count == 0


@pytest.mark.parametrize("epsilon", [0, -1e-7, math.nan, math.inf, -math.inf, True, "1e-7", None, 1e-13, .01])
def test_invalid_epsilon_is_rejected_before_inspection(epsilon):
    with pytest.raises(ValueError, match="epsilon"):
        mp.measure_volume(None, epsilon=epsilon)
    with pytest.raises(ValueError, match="epsilon"):
        mp.volume_method(epsilon=epsilon)


@pytest.mark.parametrize("shape", [None, object(), SimpleNamespace(wrapped=TopoDS_Shape())])
def test_null_or_non_shape_rejected(shape):
    with pytest.raises(mp.VolumeMeasurementError, match="null"):
        mp.measure_volume(shape)


def test_invalid_shape_rejected_before_integration(monkeypatch):
    box = cq.Workplane("XY").box(2, 3, 4).val()
    monkeypatch.setattr(box, "isValid", lambda: False)
    with pytest.raises(mp.VolumeMeasurementError, match="invalid B-Rep"):
        mp.volume(box)


def test_nonempty_surface_cannot_be_misreported_as_zero():
    face = cq.Face.makePlane(2, 3)
    with pytest.raises(mp.VolumeMeasurementError, match="closed solids"):
        mp.volume(face)


def _fake_integrator(monkeypatch, *, mass=24.0, error=1e-8, raises=False):
    class Props:
        def Mass(self):
            return mass

    calls = []

    def integrate(*args):
        calls.append(args)
        if raises:
            raise RuntimeError("kernel failure")
        return error

    monkeypatch.setattr(mp, "GProp_GProps", Props)
    monkeypatch.setattr(mp, "BRepGProp", SimpleNamespace(VolumePropertiesGK_s=integrate))
    return calls


@pytest.mark.parametrize("error", [-1.0, -1e-30, math.nan, math.inf, -math.inf, .01])
def test_bad_kernel_error_cannot_be_silently_accepted(monkeypatch, error):
    _fake_integrator(monkeypatch, error=error)
    with pytest.raises(mp.VolumeMeasurementError, match="error|failure"):
        mp.volume(cq.Workplane("XY").box(2, 3, 4).val())


@pytest.mark.parametrize("mass", [-24, -1e-15, 0, math.nan, math.inf, -math.inf])
def test_nonpositive_or_nonfinite_nonempty_mass_rejected(monkeypatch, mass):
    _fake_integrator(monkeypatch, mass=mass)
    with pytest.raises(mp.VolumeMeasurementError, match="finite and positive"):
        mp.volume(cq.Workplane("XY").box(2, 3, 4).val())


def test_native_error_is_wrapped_without_fallback(monkeypatch):
    calls = _fake_integrator(monkeypatch, raises=True)
    with pytest.raises(mp.VolumeMeasurementError, match="integration failed") as caught:
        mp.volume(cq.Workplane("XY").box(2, 3, 4).val())
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert len(calls) == 1


def test_explicit_span_flags_metadata_and_immutable_result(monkeypatch):
    calls = _fake_integrator(monkeypatch, error=2.2e-7)
    shape = cq.Workplane("XY").box(2, 3, 4).val()
    result = mp.measure_volume(shape)
    assert calls[0][0] is shape.wrapped
    assert calls[0][2:] == (1e-7, True, True, False, False, False)
    assert result.volume_mm3 == 24
    assert result.reported_relative_error == 2.2e-7
    assert result.to_dict()["schema_version"] == "wheel-volume-measurement-v1"
    with pytest.raises(FrozenInstanceError):
        result.volume_mm3 = 42
    metadata = mp.volume_method()
    assert metadata["IsUseSpan"] is True
    assert metadata["OnlyClosed"] is True
    assert metadata["requested_epsilon"] == result.requested_epsilon
    assert metadata["reported_error_limit"] == result.reported_error_limit
    assert "not an independently certified" in metadata["scope"]


def test_explicit_epsilon_propagated(monkeypatch):
    calls = _fake_integrator(monkeypatch, error=3e-9)
    result = mp.measure_volume(cq.Workplane("XY").box(2, 3, 4).val(), epsilon=1e-9)
    assert calls[0][2] == 1e-9
    assert result.requested_epsilon == 1e-9
    assert result.reported_error_limit == 1e-8


def test_components_are_integrated_once_and_errors_mass_weighted(monkeypatch):
    class Props:
        mass = 0

        def Mass(self):
            return self.mass

    calls = []
    answers = [(24, 1e-8), (72, 5e-8)]

    def integrate(wrapped, props, *options):
        props.mass, error = answers[len(calls)]
        calls.append((wrapped, options))
        return error

    monkeypatch.setattr(mp, "GProp_GProps", Props)
    monkeypatch.setattr(mp, "BRepGProp", SimpleNamespace(VolumePropertiesGK_s=integrate))
    compound = cq.Compound.makeCompound([
        cq.Workplane("XY").box(2, 3, 4).val(),
        cq.Compound.makeCompound([cq.Workplane("XY").box(6, 3, 4).val().translate((20, 0, 0))]),
    ])
    result = mp.measure_volume(compound)
    assert len(calls) == 2
    assert all(options == (1e-7, True, True, False, False, False) for _, options in calls)
    assert result.volume_mm3 == 96
    assert result.reported_relative_error == pytest.approx(4e-8)
    assert result.solid_count == 2
    assert mp.volume_method()["aggregation"] == result.aggregation


@pytest.mark.parametrize("state", [TopAbs_IN, TopAbs_ON, TopAbs_UNKNOWN])
def test_native_infinity_must_be_out_before_positive_mass_can_be_accepted(monkeypatch, state):
    calls = _fake_integrator(monkeypatch, mass=24.0)
    tolerances = []

    class Classifier:
        def __init__(self, wrapped):
            pass

        def PerformInfinitePoint(self, tolerance):
            tolerances.append(tolerance)

        def State(self):
            return state

    monkeypatch.setattr(mp, "BRepClass3d_SolidClassifier", Classifier)
    shape = cq.Workplane("XY").box(2, 3, 4).val()
    # A convenience finite-point query cannot override the native guard.
    monkeypatch.setattr(shape, "isInside", lambda *args, **kwargs: False)
    with pytest.raises(mp.VolumeMeasurementError, match="infinity must classify OUT"):
        mp.measure_volume(shape)
    assert calls == []
    assert tolerances == [1e-7]


def test_each_solid_gets_its_own_native_infinity_guard(monkeypatch):
    calls = _fake_integrator(monkeypatch, mass=24.0)
    states = iter([TopAbs_OUT, TopAbs_IN])

    class Classifier:
        def __init__(self, wrapped):
            self.state = next(states)

        def PerformInfinitePoint(self, tolerance):
            pass

        def State(self):
            return self.state

    monkeypatch.setattr(mp, "BRepClass3d_SolidClassifier", Classifier)
    shape = cq.Compound.makeCompound([
        cq.Workplane("XY").box(2, 3, 4).val(),
        cq.Workplane("XY").box(2, 3, 4).val().translate((20, 0, 0)),
    ])
    with pytest.raises(mp.VolumeMeasurementError, match="infinity must classify OUT"):
        mp.measure_volume(shape)
    assert len(calls) == 1  # No compound-level cancellation can hide component 2.


def test_native_infinity_failure_is_not_ignored(monkeypatch):
    def fail_classifier(wrapped):
        raise RuntimeError("classifier failed")

    monkeypatch.setattr(mp, "BRepClass3d_SolidClassifier", fail_classifier)
    with pytest.raises(mp.VolumeMeasurementError, match="classify the solid at infinity"):
        mp.volume(cq.Workplane("XY").box(2, 3, 4).val())


def test_finite_solid_policy_is_recorded():
    assert mp.volume_method()["finite_solid_classifier"] == {
        "method": "BRepClass3d_SolidClassifier.PerformInfinitePoint",
        "required_state": "OUT", "tolerance_mm": 1e-7,
    }
